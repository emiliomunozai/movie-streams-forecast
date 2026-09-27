"""Data checks on the raw input files. They run before the pipeline, or on their own with `cli check`.

To add a check: write `def name(movies, consumption, month)` that returns a message when something is wrong
(None when fine) and decorate it with @check(ERROR) or @check(WARNING). Errors stop the run; warnings are logged.
"""
import logging

import pandas as pd

log = logging.getLogger(__name__)

ERROR, WARNING = "error", "warning"
MOVIE_COLUMNS = {
    "TITLE_ID": "TITLE_ID",
    "YEAR": "release_year",
    "RUNTIME_MINUTES": "runtime_minutes",
    "PRIMARY_GENRE": "primary_genre",
    "RATING_VALUE": "rating_value",
    "RATING_VOTE_COUNT": "rating_vote_count",
}
MOVIE_NUMERIC = ["YEAR", "RUNTIME_MINUTES", "RATING_VALUE", "RATING_VOTE_COUNT"]
CONSUMPTION_COLUMNS = ["imdb_id", "month", "country", "platform", "streams", "total_minutes"]
KEYS = ["imdb_id", "country", "platform"]
METRICS = ["streams", "total_minutes"]
CHECKS = []


def check(severity):
    def register(fn):
        CHECKS.append((severity, fn))
        return fn
    return register


def validate(movies, consumption, month):
    """Run every check on the raw data; returns [{check, severity, message}]."""
    missing = [f"movies.{c}" for c in MOVIE_COLUMNS if c not in movies] + [
        f"consumption.{c}" for c in CONSUMPTION_COLUMNS if c not in consumption
    ]
    if missing:  # every other check needs these columns
        issues = [{"check": "required_columns", "severity": ERROR, "message": f"missing {missing}"}]
    else:
        issues = [
            {"check": fn.__name__, "severity": severity, "message": message}
            for severity, fn in CHECKS
            if (message := fn(movies, consumption, month))
        ]
    for issue in issues:
        log.log(logging.ERROR if issue["severity"] == ERROR else logging.WARNING, "%(check)s: %(message)s", issue)
    return issues


def infer_month(consumption):
    """The only month in the file (monthly drops); raises if there are none or several."""
    months = _months(consumption).dropna().unique() if "month" in consumption else []
    if len(months) != 1:
        raise ValueError(f"cannot infer the month: file has {len(months)} months, pass --month")
    return pd.Timestamp(months[0]).strftime("%Y-%m-%d")


def _rows(mask, what):
    """Message naming the CSV lines (header = line 1) where `mask` is true, or None."""
    if mask.any():
        return f"{what}: {int(mask.sum())} rows (e.g. lines {(mask[mask].index[:5] + 2).tolist()})"


def _num(frame):
    return frame.apply(pd.to_numeric, errors="coerce")


def _months(consumption):
    return pd.to_datetime(consumption["month"], errors="coerce", format="ISO8601")


# consumption

@check(ERROR)
def month_parseable(movies, consumption, month):
    return _rows(_months(consumption).isna(), "unreadable or empty month")


@check(ERROR)
def month_present(movies, consumption, month):
    if not _months(consumption).eq(pd.Timestamp(month)).any():
        return f"no rows for {month}; months in file: {sorted(_months(consumption).dropna().dt.strftime('%Y-%m-%d').unique())}"


@check(ERROR)
def keys_not_empty(movies, consumption, month):
    return _rows(consumption[KEYS].isna().any(axis=1), "empty imdb_id/country/platform")


@check(ERROR)
def metrics_numeric(movies, consumption, month):
    return _rows((_num(consumption[METRICS]).isna() & consumption[METRICS].notna()).any(axis=1), "non-numeric streams/total_minutes")


@check(ERROR)
def metrics_non_negative(movies, consumption, month):
    return _rows(_num(consumption[METRICS]).lt(0).any(axis=1), "negative streams/total_minutes")


@check(WARNING)
def metrics_missing(movies, consumption, month):
    return _rows(consumption[METRICS].isna().any(axis=1), "empty streams/total_minutes (summed as 0)")


@check(WARNING)
def duplicate_keys(movies, consumption, month):
    return _rows(consumption.duplicated(KEYS + ["month"], keep=False), "repeated movie x country x platform x month (summed)")


# movies

@check(ERROR)
def movie_ids_not_empty(movies, consumption, month):
    return _rows(movies["TITLE_ID"].isna(), "empty TITLE_ID")


@check(ERROR)
def movie_ids_unique(movies, consumption, month):
    return _rows(movies["TITLE_ID"].duplicated(keep=False) & movies["TITLE_ID"].notna(), "duplicated TITLE_ID")


@check(ERROR)
def movie_features_numeric(movies, consumption, month):
    return _rows((_num(movies[MOVIE_NUMERIC]).isna() & movies[MOVIE_NUMERIC].notna()).any(axis=1), "non-numeric YEAR/RUNTIME/RATING/VOTES")


@check(WARNING)
def movie_features_missing(movies, consumption, month):
    return _rows(movies[MOVIE_NUMERIC + ["PRIMARY_GENRE"]].isna().any(axis=1), "empty movie attributes (imputed by the model)")


@check(WARNING)
def movies_without_metadata(movies, consumption, month):
    ids = sorted(set(consumption["imdb_id"].dropna()) - set(movies["TITLE_ID"].dropna()))
    if ids:
        return f"{len(ids)} consumption ids have no movie row (attributes imputed by the model): {ids[:5]}"


@check(WARNING)
def rating_in_range(movies, consumption, month):
    return _rows(~_num(movies["RATING_VALUE"]).between(0, 10) & movies["RATING_VALUE"].notna(), "RATING_VALUE outside 0-10")


@check(WARNING)
def votes_non_negative(movies, consumption, month):
    return _rows(_num(movies["RATING_VOTE_COUNT"]).lt(0), "negative RATING_VOTE_COUNT")


@check(WARNING)
def runtime_plausible(movies, consumption, month):
    return _rows(~_num(movies["RUNTIME_MINUTES"]).between(1, 600) & movies["RUNTIME_MINUTES"].notna(), "RUNTIME_MINUTES outside 1-600")


@check(WARNING)
def release_year_plausible(movies, consumption, month):
    years = _num(movies["YEAR"])
    return _rows(~years.between(1900, pd.Timestamp(month).year) & years.notna(), f"YEAR outside 1900-{pd.Timestamp(month).year}")
