import json

import pandas as pd
import pytest
from typer.testing import CliRunner

from src.cli import app
from src.pipeline import (GRAIN, build_features, load_model, prepare_consumption, prepare_movies,
                          read_consumption, read_movies, run)

MODEL = load_model("artifacts/movie_consumption_model.pkl")
MOVIES = read_movies("data/inference_movies.csv")
CONSUMPTION = read_consumption("data/inference_consumption.csv")


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
    consumption = prepare_consumption(read_consumption("data/train_consumption.csv"))
    features = build_features(prepare_movies(read_movies("data/train_movies.csv")), consumption, "2026-05-01")
    june = consumption[consumption["month"].eq("2026-06-01")].groupby(GRAIN, as_index=False)["streams"].sum()
    table = features.merge(june, on=GRAIN, validate="one_to_one")
    assert (len(table), table["TITLE_ID"].nunique()) == (2005, 738)  # feature_schema.json


def test_other_months_are_ignored(predictions):
    june = CONSUMPTION.assign(month="2026-06-01", streams=1e6, total_minutes=1e8)
    result = run(MOVIES, pd.concat([CONSUMPTION, june], ignore_index=True), MODEL)[0]
    pd.testing.assert_frame_equal(result, predictions)


def test_same_month_rows_are_summed():
    doubled = pd.concat([CONSUMPTION, CONSUMPTION], ignore_index=True)
    features = build_features(prepare_movies(MOVIES), prepare_consumption(doubled), "2026-05-01")
    assert features["may_streams"].sum() == 2 * CONSUMPTION["streams"].sum()


def test_movie_without_metadata_is_still_predicted(predictions):
    result, summary = run(MOVIES.iloc[1:], CONSUMPTION, MODEL)
    assert len(result) == len(predictions) and result["predicted_june_streams"].notna().all()
    assert summary["warnings"][0]["check"] == "movies_without_metadata"


def test_failed_check_stops_the_run():
    with pytest.raises(ValueError, match="metrics_non_negative"):
        run(MOVIES, CONSUMPTION.assign(streams=-1), MODEL)


def test_cli(tmp_path):
    runner = CliRunner()
    assert runner.invoke(app, ["check"]).exit_code == 0
    assert runner.invoke(app, ["predict", "--output-dir", str(tmp_path)]).exit_code == 0
    assert len(pd.read_csv(tmp_path / "predictions.csv")) == 321
    assert runner.invoke(app, ["predict", "--month", "2026-07", "--output-dir", str(tmp_path)]).exit_code == 1
