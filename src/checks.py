"""Data checks on the raw input files. They run before the pipeline, or on their own with `cli check`.

To add a check: write `def name(movies, consumption, month)` that returns a message when something is wrong
(None when fine) and decorate it with @check(ERROR) or @check(WARNING). Errors stop the run; warnings are logged.
"""
import json
import logging
import re
from pathlib import Path

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
# category values seen in training, per model feature (the model silently ignores any other value)
KNOWN = {
    column: set(shares) for column, shares in
    json.loads((Path(__file__).resolve().parents[1] / "models/v1/drift_reference.json").read_text())["categorical"].items()
}


def check(severity):
    def register(fn):
        CHECKS.append((severity, fn))
        return fn
    return register


class CheckError(ValueError):
    """Raised by the pipeline when a check with severity ERROR fails; carries every issue found."""
    def __init__(self, issues):
        self.issues = issues
        super().__init__(f"data checks failed: {[i['check'] for i in issues if i['severity'] == ERROR]}")


def validate(movies, consumption, month=None):
    """Run every check on the raw data; returns [{check, severity, message}]."""
    month = resolve_month(consumption, month)
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


def fix_prompt(issues, files):
    """Ready-to-paste prompt for a coding assistant to repair the input files."""
    found = "\n".join(f"- [{i['severity']}] {i['check']}: {i['message']}" for i in issues)
    return (
        f"The movie_streams_forecast data checks failed on {', '.join(map(str, files))}:\n{found}\n"
        "Line numbers are examples (CSV lines, header = line 1): fix every row with each issue. Correct only obvious "
        "mistakes (case, whitespace, typos, id format); never invent numbers, list those rows for me instead. Save each "
        "changed file next to the original as <name>.fixed.csv, list every change, then rerun "
        "`uv run python -m src.cli check --movies <movies file> --consumption <consumption file>` until it passes."
    )


def resolve_month(consumption, month=None):
    """`month` as YYYY-MM-DD; default = the only month in the file (monthly drops), raises if there are none or several."""
    if month:
        return pd.Timestamp(month).strftime("%Y-%m-%d")
    if "month" not in consumption:
        raise ValueError(f"consumption file has no 'month' column; columns found: {list(consumption.columns)}")
    months = _months(consumption).dropna().unique()
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
def metrics_valid(movies, consumption, month):
    values = _num(consumption[METRICS])
    bad = (values.isna() & consumption[METRICS].notna()) | values.lt(0)
    return _rows(bad.any(axis=1), "non-numeric or negative streams/total_minutes")


@check(WARNING)
def metrics_missing(movies, consumption, month):
    return _rows(consumption[METRICS].isna().any(axis=1), "empty streams/total_minutes (summed as 0)")


@check(WARNING)
def duplicate_keys(movies, consumption, month):
    return _rows(consumption.duplicated(KEYS + ["month"], keep=False), "repeated movie x country x platform x month (summed)")


@check(ERROR)
def category_spelling(movies, consumption, month):
    """'netflix', ' Brazil', 'HBO-Max': variants of a training value that the model would silently ignore."""
    values = {"country": consumption["country"], "platform": consumption["platform"], "primary_genre": movies["PRIMARY_GENRE"]}
    typos = {}
    for column, found in values.items():
        canonical = {_normalize(known): known for known in KNOWN[column]}
        typos |= {v: canonical[_normalize(v)] for v in found.dropna().unique()
                  if v not in KNOWN[column] and _normalize(v) in canonical}
    if typos:
        return f"variants of training values (found -> expected): {typos}"


def _normalize(value):
    return re.sub(r"\W", "", str(value)).casefold()


@check(ERROR)
def ids_well_formed(movies, consumption, month):
    """IMDb ids look like tt0123456; 'TT123' or ' tt123' would silently miss the movie join."""
    def bad(ids):
        return ~ids.astype("string").str.fullmatch(r"tt\d+").fillna(True).astype(bool)
    found = [m for m in (_rows(bad(consumption["imdb_id"]), "consumption imdb_id"), _rows(bad(movies["TITLE_ID"]), "movies TITLE_ID")) if m]
    if found:
        return "ids not like tt0123456: " + "; ".join(found)


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
def movie_values_plausible(movies, consumption, month):
    ranges = {"RATING_VALUE": (0, 10), "RATING_VOTE_COUNT": (0, float("inf")), "RUNTIME_MINUTES": (1, 600),
              "YEAR": (1900, pd.Timestamp(month).year)}
    values = _num(movies[list(ranges)])
    outside = pd.DataFrame({c: ~values[c].between(*r) & values[c].notna() for c, r in ranges.items()})
    return _rows(outside.any(axis=1), f"outside plausible range {({c: r for c, r in ranges.items() if outside[c].any()})}")
