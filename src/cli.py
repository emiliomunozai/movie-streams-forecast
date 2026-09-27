"""CLI: uv run python -m src.cli {check,predict} [--month 2026-05-01] ..."""
import json
import logging
from pathlib import Path

import typer

from src.checks import ERROR, validate
from src.pipeline import load_model, read_consumption, read_movies, run

ROOT = Path(__file__).resolve().parents[1]
MOVIES = ROOT / "data/inference_movies.csv"
CONSUMPTION = ROOT / "data/inference_consumption.csv"
app = typer.Typer(no_args_is_help=True)


@app.callback()
def main():
    """Movie streams forecast: next-month streams per movie x country x platform."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


@app.command()
def check(movies: Path = MOVIES, consumption: Path = CONSUMPTION, month: str = "2026-05-01"):
    """Validate the input files without predicting (e.g. before uploading them)."""
    try:
        issues = validate(read_movies(movies), read_consumption(consumption), month)
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
    month: str = "2026-05-01",
    output_dir: Path = ROOT / "output",
):
    """Check the inputs, then write predictions.csv and summary.json to OUTPUT_DIR."""
    try:
        predictions, summary = run(read_movies(movies), read_consumption(consumption), load_model(model), month)
    except (OSError, ValueError) as error:
        logging.error(error)
        raise typer.Exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)
    predictions.to_csv(output_dir / "predictions.csv", index=False, float_format="%.2f")
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    logging.info("wrote %d predictions to %s", len(predictions), output_dir)


if __name__ == "__main__":
    app()
