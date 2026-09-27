"""Raw movies + consumption CSVs -> next-month stream predictions per TITLE_ID x country x platform.

Reproduces the data preparation of 02_model_development_reference.ipynb. The model itself carries the
fitted preprocessing; nothing is fitted here.
"""
import logging
import pickle

import pandas as pd

from src.checks import ERROR, MOVIE_COLUMNS, infer_month, validate

log = logging.getLogger(__name__)
GRAIN = ["TITLE_ID", "country", "platform"]


def read_movies(path):
    return pd.read_csv(path, encoding="utf-8-sig", dtype={"TITLE_ID": "string"})  # files start with a BOM


def read_consumption(path):
    return pd.read_csv(path, encoding="utf-8-sig", dtype={"imdb_id": "string"})


def load_model(path):
    with open(path, "rb") as f:  # only load pickles from a trusted source
        return pickle.load(f)


def prepare_movies(raw):
    return raw[list(MOVIE_COLUMNS)].rename(columns=MOVIE_COLUMNS)


def prepare_consumption(raw):
    consumption = raw.rename(columns={"imdb_id": "TITLE_ID"})
    consumption["month"] = pd.to_datetime(consumption["month"], format="ISO8601").dt.strftime("%Y-%m-%d")
    for column in ("streams", "total_minutes"):
        consumption[column] = pd.to_numeric(consumption[column])
    return consumption


def build_features(movies, consumption, month):
    """One row per TITLE_ID x country x platform observed in `month`, with the model's input features."""
    return (
        consumption[consumption["month"].eq(month)]
        .groupby(GRAIN, as_index=False, dropna=False)
        # the model names its inputs may_*; they hold whichever month is passed in
        .agg(may_streams=("streams", "sum"), may_total_minutes=("total_minutes", "sum"))
        .merge(movies, on="TITLE_ID", how="left", validate="many_to_one")
    )


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


def run(movies_raw, consumption_raw, model, month=None):
    """Checks + full pipeline on raw frames. Returns (predictions, summary); raises ValueError on failed checks.

    month: input month to predict from; default = the only month in the consumption file.
    """
    month = pd.Timestamp(month).strftime("%Y-%m-%d") if month else infer_month(consumption_raw)
    issues = validate(movies_raw, consumption_raw, month)
    errors = [issue["check"] for issue in issues if issue["severity"] == ERROR]
    if errors:
        raise ValueError(f"data checks failed: {errors}")

    features = build_features(prepare_movies(movies_raw), prepare_consumption(consumption_raw), month)
    predictions = predict(model, features, month)
    unseen = unseen_categories(model, features)
    if unseen:
        log.warning("categories unseen in training (ignored by the model): %s", unseen)
    log.info("%s: %d combinations predicted", month, len(predictions))
    summary = {
        "input_month": month,
        "rows": len(predictions),
        "movies": int(predictions["TITLE_ID"].nunique()),
        "warnings": issues,
        "unseen_categories": unseen,
        "predicted_streams_total": round(float(predictions["predicted_june_streams"].sum()), 1),
        "input_streams_total": round(float(features["may_streams"].sum()), 1),
    }
    return predictions, summary
