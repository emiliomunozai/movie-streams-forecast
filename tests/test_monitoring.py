import json

from typer.testing import CliRunner

from src.cli import app
from src.monitoring import drift_reference, numeric_drift, score_predictions
from src.pipeline import load_model, read_csv, run

MODEL = load_model("models/v1/model.pkl")
MOVIES = read_csv("data/movies/2026-05.csv")
CONSUMPTION = read_csv("data/consumption/2026-05.csv")


def test_scoring_matches_the_notebook_rules(training):
    movies, consumption = training  # has May and June: lets us score against real actuals
    predictions, _, _ = run(movies, consumption, MODEL, "2026-05-01")
    result = score_predictions(predictions, consumption)
    assert result["rows_scored"] == 2005  # May x June pairs, like the notebook's training table
    assert result["model_wape"] < result["baseline_wape"]  # in-sample, so this is only a sanity check


def test_drift_reference_is_up_to_date_and_stable(training):
    _, _, features = run(*training, MODEL, "2026-05-01")
    reference = drift_reference(features)
    assert reference == json.loads(open("models/v1/drift_reference.json").read())  # rebuild: `cli reference`
    assert (numeric_drift(reference, features)["status"] == "ok").all()


def test_monthly_cycle_via_cli(tmp_path):
    """Month t: predict into its month folder. Month t+1 arrives: evaluate finds the t predictions and scores them."""
    june = tmp_path / "2026-06.csv"  # made-up June actuals: May's rows, one month later
    CONSUMPTION.assign(month="2026-06-01", streams=CONSUMPTION["streams"] * 0.6).to_csv(june, index=False)
    runner = CliRunner()
    predictions, history = tmp_path / "predictions", tmp_path / "performance"
    assert runner.invoke(app, ["predict", "--output-dir", str(predictions)]).exit_code == 0
    assert (predictions / "input_month=2026-05-01/predictions.csv").exists()

    args = ["evaluate", "--predictions", str(predictions), "--history-dir", str(history)]
    assert runner.invoke(app, [*args, "--actuals", str(june)]).exit_code == 0
    assert json.loads((history / "2026-06-01.json").read_text())["rows_scored"] == 321

    # the first month has nothing to score yet: not an error
    assert runner.invoke(app, [*args, "--actuals", "data/consumption/2026-05.csv"]).exit_code == 0
    assert not (history / "2026-05-01.json").exists()
