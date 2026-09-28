"""CLI: uv run python -m src.cli {check,predict,evaluate,reference,ui} ..."""
import json
import logging
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import pandas as pd
import typer

from src.checks import ERROR, CheckError, fix_prompt, resolve_month, validate
from src.monitoring import drift_reference, score_predictions
from src.pipeline import build_features, load_model, read_csv, run

ROOT = Path(__file__).resolve().parents[1]
MOVIES = ROOT / "data/inference_movies.csv"
CONSUMPTION = ROOT / "data/inference_consumption.csv"
app = typer.Typer(no_args_is_help=True)
MONTH_HELP = "Input month (YYYY-MM-DD). Default: the only month in the consumption file."


def single_file(path, month=None):
    """A file, or a folder holding exactly one file (SageMaker mounts each S3 input as a folder).

    With `month`, the folder holds monthly snapshots and only the one named YYYY-MM.* counts.
    """
    if not path.is_dir():
        return path
    files = [p for p in path.iterdir() if p.is_file() and not p.name.startswith(".")]
    if month:
        files = [p for p in files if p.stem == month[:7]]
    if len(files) != 1:
        raise ValueError(f"{path}: expected exactly one {month[:7] + ' ' if month else ''}file, found {len(files)}")
    return files[0]


@contextmanager
def exit_on_error(*inputs):
    """Missing files and failed checks: log one line and exit 1 instead of a traceback.

    Failed checks also log a prompt to paste into an AI coding agent to repair the input files.
    """
    try:
        yield
    except (OSError, ValueError) as error:
        logging.error(error)
        if isinstance(error, CheckError):
            logging.error("fix prompt:\n%s", fix_prompt(error.issues, inputs))
        raise typer.Exit(1)


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
    with exit_on_error(movies, consumption):
        consumption_raw = read_csv(single_file(consumption))
        month = resolve_month(consumption_raw, month)
        issues = validate(read_csv(single_file(movies, month)), consumption_raw, month)
        errors = sum(issue["severity"] == ERROR for issue in issues)
        logging.info("%d errors, %d warnings", errors, len(issues) - errors)
        if errors:
            raise CheckError(issues)


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
    with exit_on_error(movies, consumption):
        consumption_raw = read_csv(single_file(consumption))
        month = resolve_month(consumption_raw, month)  # also picks the movies snapshot of that month
        predictions, summary, _ = run(
            read_csv(single_file(movies, month)), consumption_raw, load_model(single_file(model)), month
        )

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
    with exit_on_error():
        actuals_raw = read_csv(single_file(actuals))
        month = resolve_month(actuals_raw)
        files = [predictions] if predictions.is_file() else sorted(predictions.rglob("predictions.csv"))
        found = pd.concat([pd.read_csv(f, dtype={"TITLE_ID": "string"}) for f in files] or [pd.DataFrame(columns=["target_month"])])
        found = found[found["target_month"].eq(month)]
        if found.empty:
            logging.warning("no predictions targeting %s, nothing to evaluate", month)
            return
        result = score_predictions(found, actuals_raw)

    history_dir.mkdir(parents=True, exist_ok=True)
    (history_dir / f"{month}.json").write_text(json.dumps(result, indent=2))
    logging.info("%s: model WAPE %.3f vs baseline %.3f on %d rows", month, result["model_wape"],
                 result["baseline_wape"], result["rows_scored"])


@app.command()
def reference(
    movies: Path = ROOT / "data/train_movies.csv",
    consumption: Path = ROOT / "data/train_consumption.csv",
    month: str = "2026-05-01",
    output: Path = ROOT / "artifacts/drift_reference.json",
):
    """Save the input statistics of the training month: the drift reference the UI compares each run against."""
    with exit_on_error():
        features = build_features(read_csv(movies), read_csv(consumption), month)
    output.write_text(json.dumps(drift_reference(features), indent=2))
    logging.info("wrote %s", output)


@app.command()
def ui(port: Annotated[int, typer.Option(envvar="PORT", help="Also read from $PORT (Render, Cloud Run).")] = 8501):
    """Launch the Streamlit app (predictions + ModelOps dashboard)."""
    os.execv(sys.executable, [sys.executable, "-m", "streamlit", "run", str(ROOT / "src/app.py"),
                              "--server.port", str(port), "--server.address", "0.0.0.0", "--browser.gatherUsageStats", "false"])


if __name__ == "__main__":
    app()
