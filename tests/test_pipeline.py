import json
import shutil

import pandas as pd
import pytest
from typer.testing import CliRunner

from src.checks import CheckError
from src.cli import app
from src.pipeline import GRAIN, build_features, load_model, prepare_consumption, read_csv, run

MODEL = load_model("artifacts/movie_consumption_model.pkl")
MOVIES = read_csv("data/inference_movies.csv")
CONSUMPTION = read_csv("data/inference_consumption.csv")


@pytest.fixture(scope="module")
def predictions():
    return run(MOVIES, CONSUMPTION, MODEL)[0]


def test_one_prediction_per_may_combination(predictions):
    expected = CONSUMPTION.rename(columns={"imdb_id": "TITLE_ID"})[GRAIN].drop_duplicates()
    assert len(predictions) == len(expected) == 321
    assert not predictions.duplicated(GRAIN).any()
    assert predictions[GRAIN].merge(expected, how="outer", indicator=True)["_merge"].eq("both").all()


def test_output_columns_and_values(predictions):
    assert {"TITLE_ID", "country", "platform", "predicted_june_streams"} <= set(predictions.columns)
    assert predictions["predicted_june_streams"].notna().all()
    assert predictions["predicted_june_streams"].ge(0).all()


def test_feature_order_matches_schema():
    schema = json.load(open("artifacts/feature_schema.json"))
    assert list(MODEL.feature_names_in_) == schema["input_features_in_order"]


def test_reproduces_notebook_training_table():
    consumption = read_csv("data/train_consumption.csv")
    features = build_features(read_csv("data/train_movies.csv"), consumption, "2026-05-01")
    june = prepare_consumption(consumption).query("month == '2026-06-01'").groupby(GRAIN, as_index=False)["streams"].sum()
    table = features.merge(june, on=GRAIN, validate="one_to_one")
    assert (len(table), table["TITLE_ID"].nunique()) == (2005, 738)  # feature_schema.json


def test_other_months_are_ignored(predictions):
    june = CONSUMPTION.assign(month="2026-06-01", streams=1e6, total_minutes=1e8)
    result = run(MOVIES, pd.concat([CONSUMPTION, june], ignore_index=True), MODEL, "2026-05-01")[0]
    pd.testing.assert_frame_equal(result, predictions)


def test_month_is_inferred_only_when_unambiguous(predictions):
    assert predictions["input_month"].eq("2026-05-01").all()  # inferred: the file only has May
    two_months = pd.concat([CONSUMPTION, CONSUMPTION.assign(month="2026-06-01")], ignore_index=True)
    with pytest.raises(ValueError, match="2 months, pass --month"):
        run(MOVIES, two_months, MODEL)


def test_same_month_rows_are_summed():
    doubled = pd.concat([CONSUMPTION, CONSUMPTION], ignore_index=True)
    features = build_features(MOVIES, doubled, "2026-05-01")
    assert features["may_streams"].sum() == 2 * CONSUMPTION["streams"].sum()


def test_movie_without_metadata_is_still_predicted(predictions):
    result, summary, _ = run(MOVIES.iloc[1:], CONSUMPTION, MODEL)
    assert len(result) == len(predictions) and result["predicted_june_streams"].notna().all()
    assert summary["warnings"][0]["check"] == "movies_without_metadata"


def test_failed_check_stops_the_run():
    with pytest.raises(CheckError, match="metrics_valid"):
        run(MOVIES, CONSUMPTION.assign(streams=-1), MODEL)


def test_cli(tmp_path):
    runner = CliRunner()
    assert runner.invoke(app, ["check"]).exit_code == 0
    assert runner.invoke(app, ["predict", "--output-dir", str(tmp_path)]).exit_code == 0
    assert len(pd.read_csv(tmp_path / "predictions.csv")) == 321
    assert runner.invoke(app, ["predict", "--month", "2026-07", "--output-dir", str(tmp_path)]).exit_code == 1


def test_cli_accepts_folders_like_sagemaker(tmp_path):
    for name, source in [("consumption", "data/inference_consumption.csv"), ("model", "artifacts/movie_consumption_model.pkl")]:
        (tmp_path / name).mkdir()
        shutil.copy(source, tmp_path / name)
    (tmp_path / "movies").mkdir()  # monthly snapshots: the one of the input month is used
    shutil.copy("data/inference_movies.csv", tmp_path / "movies/2026-05.csv")
    shutil.copy("data/train_movies.csv", tmp_path / "movies/2026-04.csv")
    args = [f"--{name}={tmp_path / name}" for name in ("movies", "consumption", "model")]
    result = CliRunner().invoke(app, ["predict", *args, f"--output-dir={tmp_path / 'out'}"])
    assert result.exit_code == 0 and len(pd.read_csv(tmp_path / "out/predictions.csv")) == 321
    shutil.copy("data/train_consumption.csv", tmp_path / "consumption")  # two files -> ambiguous
    assert CliRunner().invoke(app, ["predict", *args, f"--output-dir={tmp_path / 'out'}"]).exit_code == 1
    (tmp_path / "consumption/train_consumption.csv").unlink()
    (tmp_path / "movies/2026-05.csv").unlink()  # no snapshot for May -> fail, never an older one
    assert CliRunner().invoke(app, ["predict", *args, f"--output-dir={tmp_path / 'out'}"]).exit_code == 1


def test_cli_fails_cleanly_on_bad_model_or_columns(tmp_path):
    (tmp_path / "bad.pkl").write_bytes(b"junk")
    (tmp_path / "wrong.csv").write_text("a,b\n1,2\n")
    runner = CliRunner()
    assert runner.invoke(app, ["predict", f"--model={tmp_path / 'bad.pkl'}", f"--output-dir={tmp_path}"]).exit_code == 1
    with pytest.raises(ValueError, match="no 'month' column"):
        run(MOVIES, pd.read_csv(tmp_path / "wrong.csv"), MODEL)
