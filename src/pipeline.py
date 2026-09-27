"""Raw movies + consumption CSVs -> next-month stream predictions per TITLE_ID x country x platform.

Reproduces the data preparation of 02_model_development_reference.ipynb. The model itself carries the
fitted preprocessing; nothing is fitted here.
"""
import logging
import pickle

import pandas as pd

log = logging.getLogger(__name__)

GRAIN = ["TITLE_ID", "country", "platform"]
MOVIE_COLUMNS = {
    "TITLE_ID": "TITLE_ID",
    "YEAR": "release_year",
    "RUNTIME_MINUTES": "runtime_minutes",
    "PRIMARY_GENRE": "primary_genre",
    "RATING_VALUE": "rating_value",
    "RATING_VOTE_COUNT": "rating_vote_count",
}
CONSUMPTION_COLUMNS = ["imdb_id", "month", "country", "platform", "streams", "total_minutes"]


def _read_csv(path, required, id_column):
    df = pd.read_csv(path, encoding="utf-8-sig", dtype={id_column: "string"})  # files start with a BOM
    missing = set(required) - set(df.columns)
    if missing:
        raise ValueError(f"{path}: missing columns {sorted(missing)}")
    return df


def load_movies(path):
    movies = _read_csv(path, MOVIE_COLUMNS, "TITLE_ID")[list(MOVIE_COLUMNS)].rename(columns=MOVIE_COLUMNS)
    duplicated = movies["TITLE_ID"].duplicated()
    if duplicated.any():
        raise ValueError(f"{path}: duplicated TITLE_IDs {movies.loc[duplicated, 'TITLE_ID'].tolist()[:5]}")
    return movies


def load_consumption(path):
    df = _read_csv(path, CONSUMPTION_COLUMNS, "imdb_id").rename(columns={"imdb_id": "TITLE_ID"})
    df["month"] = pd.to_datetime(df["month"], errors="raise").dt.strftime("%Y-%m-%d")
    for column in ("streams", "total_minutes"):
        df[column] = pd.to_numeric(df[column], errors="raise")
        if (df[column] < 0).any():
            raise ValueError(f"{path}: negative values in {column}")
    if df[GRAIN].isna().any().any():
        raise ValueError(f"{path}: empty TITLE_ID/country/platform values")
    return df


def load_model(path):
    with open(path, "rb") as f:  # only load pickles from a trusted source
        return pickle.load(f)


def build_features(movies, consumption, month):
    """One row per TITLE_ID x country x platform observed in `month`, with the model's input features."""
    rows = consumption[consumption["month"].eq(month)]
    if rows.empty:
        raise ValueError(f"no consumption rows for {month}; months in file: {sorted(consumption['month'].unique())}")

    features = (
        rows.groupby(GRAIN, as_index=False, dropna=False)
        # the model names its inputs may_*; they hold whichever month is passed in
        .agg(may_streams=("streams", "sum"), may_total_minutes=("total_minutes", "sum"))
        .merge(movies, on="TITLE_ID", how="left", validate="many_to_one")
    )
    unmatched = sorted(set(features["TITLE_ID"]) - set(movies["TITLE_ID"]))
    if unmatched:
        log.warning("%d TITLE_IDs without movie metadata (imputed by the model): %s", len(unmatched), unmatched[:10])
    log.info("%s: %d consumption rows -> %d combinations", month, len(rows), len(features))
    return features


def predict(model, features, month):
    predictions = features[GRAIN].copy()
    predictions["predicted_june_streams"] = model.predict(features[list(model.feature_names_in_)])
    predictions["input_month"] = month
    predictions["target_month"] = (pd.Timestamp(month) + pd.DateOffset(months=1)).strftime("%Y-%m-%d")
    return predictions


def unseen_categories(model, features):
    """Categories the model never saw in training; its OneHotEncoder silently ignores them."""
    preprocessing = model.regressor_.named_steps["preprocessing"]
    encoder = preprocessing.named_transformers_["categorical"].named_steps["onehot"]
    columns = preprocessing.transformers_[0][2]
    return {
        column: sorted(set(features[column].dropna()) - set(known))
        for column, known in zip(columns, encoder.categories_)
        if set(features[column].dropna()) - set(known)
    }


def run(movies_path, consumption_path, model_path, month="2026-05-01"):
    """Full pipeline. Returns (predictions, summary)."""
    month = pd.Timestamp(month).strftime("%Y-%m-%d")
    model = load_model(model_path)
    features = build_features(load_movies(movies_path), load_consumption(consumption_path), month)
    predictions = predict(model, features, month)

    unseen = unseen_categories(model, features)
    if unseen:
        log.warning("categories unseen in training: %s", unseen)
    summary = {
        "input_month": month,
        "rows": len(predictions),
        "movies": int(predictions["TITLE_ID"].nunique()),
        "movies_without_metadata": int(features["release_year"].isna().groupby(features["TITLE_ID"]).any().sum()),
        "unseen_categories": unseen,
        "predicted_streams_total": round(float(predictions["predicted_june_streams"].sum()), 1),
        "input_streams_total": round(float(features["may_streams"].sum()), 1),
    }
    return predictions, summary
