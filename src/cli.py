"""CLI: uv run python -m src.cli {check,predict,evaluate,ui} ..."""
import json
import logging
import os
import sys
from pathlib import Path
from typing import Annotated

import pandas as pd
import typer

from src.checks import ERROR, infer_month, validate
from src.monitoring import score_predictions
from src.pipeline import load_model, read_consumption, read_movies, run

ROOT = Path(__file__).resolve().parents[1]
MOVIES = ROOT / "data/inference_movies.csv"
CONSUMPTION = ROOT / "data/inference_consumption.csv"
app = typer.Typer(no_args_is_help=True)
MONTH_HELP = "Input month (YYYY-MM-DD). Default: the only month in the consumption file."


def single_file(path):
    """A file, or a folder holding exactly one file (SageMaker mounts each S3 input as a folder)."""
    if not path.is_dir():
        return path
    files = [p for p in path.iterdir() if p.is_file() and not p.name.startswith(".")]
    if len(files) != 1:
        raise ValueError(f"{path}: expected exactly one file, found {len(files)}")
    return files[0]


@app.callback()
def main():
    """Movie streams forecast: next-month streams per movie x country x platform."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


@app.command()
def check(
    movies: Path = MOVIES,
    consumption: Path = CONSUMPTION,
    month: Annotated[str | None, typer.Option(help=MONTH_HELP)] = None,
):
    """Validate the input files without predicting (e.g. before uploading them)."""
    try:
        consumption_raw = read_consumption(single_file(consumption))
        issues = validate(read_movies(single_file(movies)), consumption_raw, month or infer_month(consumption_raw))
    except (OSError, ValueError) as error:
        logging.error(error)
        raise typer.Exit(1)
    errors = sum(issue["severity"] == ERROR for issue in issues)
    logging.info("%d errors, %d warnings", errors, len(issues) - errors)
    raise typer.Exit(1 if errors else 0)


@app.command()
def predict(
    movies: Path = MOVIES,
    consumption: Path = CONSUMPTION,
    model: Path = ROOT / "artifacts/movie_consumption_model.pkl",
    month: Annotated[str | None, typer.Option(help=MONTH_HELP)] = None,
    output_dir: Path = ROOT / "output",
    partition_by_month: Annotated[bool, typer.Option(help="Write to OUTPUT_DIR/input_month=YYYY-MM-DD/ (reruns overwrite).")] = False,
):
    """Check the inputs, then write predictions.csv and summary.json to OUTPUT_DIR."""
    try:
        predictions, summary, _ = run(
            read_movies(single_file(movies)), read_consumption(single_file(consumption)), load_model(single_file(model)), month
        )
    except (OSError, ValueError) as error:
        logging.error(error)
        raise typer.Exit(1)

    if partition_by_month:
        output_dir = output_dir / f"input_month={summary['input_month']}"
    output_dir.mkdir(parents=True, exist_ok=True)
    predictions.to_csv(output_dir / "predictions.csv", index=False, float_format="%.2f")
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    logging.info("wrote %d predictions to %s", len(predictions), output_dir)


@app.command()
def evaluate(
    actuals: Annotated[Path, typer.Option(help="Consumption file of the month that was predicted.")],
    predictions: Annotated[Path, typer.Option(help="predictions.csv, or a folder searched recursively.")] = ROOT / "output",
    history_dir: Path = ROOT / "artifacts/performance",
):
    """Score earlier predictions once their target month's consumption arrives; writes HISTORY_DIR/<month>.json."""
    try:
        actuals_raw = read_consumption(single_file(actuals))
        month = infer_month(actuals_raw)
        files = [predictions] if predictions.is_file() else sorted(predictions.rglob("predictions.csv"))
        found = pd.concat([pd.read_csv(f, dtype={"TITLE_ID": "string"}) for f in files]) if files else pd.DataFrame()
        found = found[found["target_month"].eq(month)] if not found.empty else found
        if found.empty:
            logging.warning("no predictions targeting %s, nothing to evaluate", month)
            return
        result = score_predictions(found, actuals_raw)
    except (OSError, ValueError) as error:
        logging.error(error)
        raise typer.Exit(1)

    history_dir.mkdir(parents=True, exist_ok=True)
    (history_dir / f"{month}.json").write_text(json.dumps(result, indent=2))
    logging.info("%s: model WAPE %.3f vs baseline %.3f on %d rows", month, result["model_wape"],
                 result["baseline_wape"], result["rows_scored"])


@app.command()
def ui(port: int = 8501):
    """Launch the Streamlit app (predictions + ModelOps dashboard)."""
    os.execv(sys.executable, [sys.executable, "-m", "streamlit", "run", str(ROOT / "src/app.py"),
                              "--server.port", str(port), "--server.address", "0.0.0.0", "--browser.gatherUsageStats", "false"])


if __name__ == "__main__":
    app()
