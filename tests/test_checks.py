import pandas as pd
import pytest

from src.checks import CHECKS, ERROR, WARNING, fix_prompt, validate
from src.pipeline import read_csv

MONTH = "2026-05-01"
MOVIES = read_csv("data/inference_movies.csv")
CONSUMPTION = read_csv("data/inference_consumption.csv")


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
    "metrics_valid": lambda m, c: (m, put(c, "streams", "abc")),
    "metrics_missing": lambda m, c: (m, put(c, "total_minutes", None)),
    "duplicate_keys": lambda m, c: (m, pd.concat([c, c.head(1)], ignore_index=True)),
    "category_spelling": lambda m, c: (m, put(c, "platform", "netflix ")),
    "ids_well_formed": lambda m, c: (put(m, "TITLE_ID", "TT0123"), put(c, "imdb_id", " tt0123")),
    "movie_ids_not_empty": lambda m, c: (put(m, "TITLE_ID", None), c),
    "movie_ids_unique": lambda m, c: (pd.concat([m, m.head(1)], ignore_index=True), c),
    "movie_features_numeric": lambda m, c: (put(m, "YEAR", "n/a"), c),
    "movie_features_missing": lambda m, c: (put(m, "PRIMARY_GENRE", None), c),
    "movies_without_metadata": lambda m, c: (m.iloc[1:], c),
    "movie_values_plausible": lambda m, c: (put(m, "YEAR", 3000), c),
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


def test_negative_metrics_are_errors():
    [issue] = validate(MOVIES, put(CONSUMPTION, "total_minutes", -1), MONTH)
    assert issue["check"] == "metrics_valid" and issue["severity"] == ERROR


@pytest.mark.parametrize("column, value", [("RATING_VALUE", 11), ("RATING_VOTE_COUNT", -5), ("RUNTIME_MINUTES", 0)])
def test_implausible_values_are_warnings(column, value):
    [issue] = validate(put(MOVIES, column, value), CONSUMPTION, MONTH)
    assert issue["severity"] == WARNING and column in issue["message"]


def test_spelling_names_the_expected_value():
    [issue] = validate(put(MOVIES, "PRIMARY_GENRE", "sci fi"), put(CONSUMPTION, "platform", "HBO-Max"), MONTH)
    assert "'HBO-Max': 'HBO Max'" in issue["message"] and "'sci fi': 'Sci-Fi'" in issue["message"]


def test_fix_prompt_lists_issues_and_files():
    issues = validate(MOVIES, put(CONSUMPTION, "country", "brazil"), MONTH)
    prompt = fix_prompt(issues, ["movies.csv", "consumption.csv"])
    assert "category_spelling" in prompt and "consumption.csv" in prompt and "src.cli check" in prompt
