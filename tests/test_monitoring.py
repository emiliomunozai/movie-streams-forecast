import json

from typer.testing import CliRunner

from src.cli import app
from src.monitoring import drift_reference, numeric_drift, score_predictions
from src.pipeline import load_model, read_csv, run

MODEL = load_model("artifacts/movie_consumption_model.pkl")
TRAIN_MOVIES = read_csv("data/train_movies.csv")
TRAIN_CONSUMPTION = read_csv("data/train_consumption.csv")  # has May and June: lets us test scoring


def test_scoring_matches_the_notebook_rules():
    predictions, _, _ = run(TRAIN_MOVIES, TRAIN_CONSUMPTION, MODEL, "2026-05-01")
    result = score_predictions(predictions, TRAIN_CONSUMPTION)
    assert result["rows_scored"] == 2005  # May x June pairs, like the notebook's training table
    assert result["model_wape"] < result["baseline_wape"]  # in-sample, so this is only a sanity check


def test_drift_reference_is_up_to_date_and_stable():
    _, _, features = run(TRAIN_MOVIES, TRAIN_CONSUMPTION, MODEL, "2026-05-01")
    reference = drift_reference(features)
    assert reference == json.loads(open("artifacts/drift_reference.json").read())  # rebuild: `cli reference`
    assert (numeric_drift(reference, features)["status"] == "ok").all()


def test_monthly_cycle_via_cli(tmp_path):
    """Month t: predict into a month partition. Month t+1 arrives: evaluate finds the t predictions and scores them."""
    may, june = tmp_path / "may.csv", tmp_path / "june.csv"
    TRAIN_CONSUMPTION[TRAIN_CONSUMPTION["month"].eq("2026-05-01")].to_csv(may, index=False)
    TRAIN_CONSUMPTION[TRAIN_CONSUMPTION["month"].eq("2026-06-01")].to_csv(june, index=False)
    runner = CliRunner()
    base = ["--movies", "data/train_movies.csv", "--output-dir", str(tmp_path / "predictions")]
    assert runner.invoke(app, ["predict", "--consumption", str(may), *base, "--partition-by-month"]).exit_code == 0
    assert (tmp_path / "predictions/input_month=2026-05-01/predictions.csv").exists()

    history = tmp_path / "performance"
    args = ["evaluate", "--actuals", str(june), "--predictions", str(tmp_path / "predictions"), "--history-dir", str(history)]
    assert runner.invoke(app, args).exit_code == 0
    assert json.loads((history / "2026-06-01.json").read_text())["rows_scored"] == 2005

    # the first month has nothing to score yet: not an error
    args = ["evaluate", "--actuals", str(may), "--predictions", str(tmp_path / "predictions"), "--history-dir", str(history)]
    assert runner.invoke(app, args).exit_code == 0 and not (history / "2026-05-01.json").exists()
