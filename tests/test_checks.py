import pandas as pd
import pytest

from src.checks import CHECKS, ERROR, WARNING, validate
from src.pipeline import read_consumption, read_movies

MONTH = "2026-05-01"
MOVIES = read_movies("data/inference_movies.csv")
CONSUMPTION = read_consumption("data/inference_consumption.csv")


def put(df, column, value, row=0):
    df = df.copy()
    df[column] = df[column].astype(object)
    df.loc[row, column] = value
    return df


# check name -> how to break the (movies, consumption) data so that check fires
BREAKS = {
    "required_columns": lambda m, c: (m, c.drop(columns="streams")),
    "month_parseable": lambda m, c: (m, put(c, "month", "not-a-date")),
    "month_present": lambda m, c: (m, c.assign(month="2026-07-01")),
    "keys_not_empty": lambda m, c: (m, put(c, "country", None)),
    "metrics_numeric": lambda m, c: (m, put(c, "streams", "abc")),
    "metrics_non_negative": lambda m, c: (m, put(c, "streams", -1)),
    "metrics_missing": lambda m, c: (m, put(c, "total_minutes", None)),
    "duplicate_keys": lambda m, c: (m, pd.concat([c, c.head(1)], ignore_index=True)),
    "movie_ids_not_empty": lambda m, c: (put(m, "TITLE_ID", None), c),
    "movie_ids_unique": lambda m, c: (pd.concat([m, m.head(1)], ignore_index=True), c),
    "movie_features_numeric": lambda m, c: (put(m, "YEAR", "n/a"), c),
    "movie_features_missing": lambda m, c: (put(m, "PRIMARY_GENRE", None), c),
    "movies_without_metadata": lambda m, c: (m.iloc[1:], c),
    "rating_in_range": lambda m, c: (put(m, "RATING_VALUE", 11), c),
    "votes_non_negative": lambda m, c: (put(m, "RATING_VOTE_COUNT", -5), c),
    "runtime_plausible": lambda m, c: (put(m, "RUNTIME_MINUTES", 0), c),
    "release_year_plausible": lambda m, c: (put(m, "YEAR", 3000), c),
}


def test_clean_data_passes():
    assert validate(MOVIES, CONSUMPTION, MONTH) == []


def test_every_check_has_a_break_case():
    assert {fn.__name__ for _, fn in CHECKS} | {"required_columns"} == set(BREAKS)


@pytest.mark.parametrize("name", BREAKS)
def test_check_fires(name):
    issues = validate(*BREAKS[name](MOVIES, CONSUMPTION), MONTH)
    assert name in {issue["check"] for issue in issues}


def test_messages_point_to_csv_lines():
    [issue] = validate(MOVIES, put(CONSUMPTION, "streams", -1, row=4), MONTH)
    assert issue["severity"] == ERROR and "lines [6]" in issue["message"]


def test_warnings_are_warnings():
    [issue] = validate(put(MOVIES, "RATING_VALUE", 11), CONSUMPTION, MONTH)
    assert issue["severity"] == WARNING
