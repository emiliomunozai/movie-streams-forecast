"""ModelOps: input drift vs training (no actuals needed) and accuracy once the target month's actuals arrive."""
import json

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

from src.checks import CONSUMPTION_COLUMNS
from src.pipeline import GRAIN, prepare_consumption

SHIFT_THRESHOLD = 0.25  # by construction ~10% of training rows fall outside its own p5-p95


def drift_reference(features):
    """Input statistics of the training month, saved once so runs are compared without reading training data."""
    inputs = features.drop(columns="TITLE_ID")
    return {
        "numeric": {c: dict(zip(["p5", "median", "p95"], inputs[c].quantile([0.05, 0.5, 0.95]).tolist()))
                    for c in inputs.select_dtypes("number")},
        "categorical": {c: inputs[c].value_counts(normalize=True).to_dict()
                        for c in inputs.select_dtypes(exclude="number")},
    }


def numeric_drift(reference, current):
    """Per numeric feature: training range vs this run, and the share of rows outside training p5-p95."""
    rows = []
    for column, ref in reference["numeric"].items():
        outside = float((~current[column].between(ref["p5"], ref["p95"])).mean())
        rows.append({
            "feature": column,
            "training p5": ref["p5"],
            "training median": ref["median"],
            "training p95": ref["p95"],
            "current median": current[column].median(),
            "outside training p5-p95": outside,
            "status": "shifted" if outside > SHIFT_THRESHOLD else "ok",
        })
    return pd.DataFrame(rows)


def category_mix(reference, current):
    """Share of rows per category value, training vs this run."""
    frames = []
    for column, shares in reference["categorical"].items():
        mix = pd.concat(
            {"training": pd.Series(shares), "current": current[column].value_counts(normalize=True)}, axis=1
        ).fillna(0)
        frames.append(mix.rename_axis("value").reset_index().assign(feature=column))
    return pd.concat(frames, ignore_index=True)[["feature", "value", "training", "current"]]


def _metrics(y, predicted, prefix):
    """Same definitions as the notebook's metric_summary."""
    return {
        f"{prefix}_mae": round(float(mean_absolute_error(y, predicted)), 3),
        f"{prefix}_rmse": round(float(np.sqrt(mean_squared_error(y, predicted))), 3),
        f"{prefix}_wape": round(float(np.abs(y - predicted).sum() / max(y.sum(), 1)), 4),
        f"{prefix}_r2": round(float(r2_score(y, predicted)), 4),
    }


def score_predictions(predictions, actuals_raw):
    """Score one month's predictions against the consumption file of their target month.

    Like the notebook, only combinations present in both are scored: a missing next-month row is not a zero.
    Baseline = "next month = this month" (input_streams).
    """
    missing = set(CONSUMPTION_COLUMNS) - set(actuals_raw.columns)
    if missing:
        raise ValueError(f"actuals: missing columns {sorted(missing)}")
    target = predictions["target_month"].iloc[0]
    actuals = (
        prepare_consumption(actuals_raw).query("month == @target")
        .groupby(GRAIN, as_index=False).agg(actual_streams=("streams", "sum"))
    )
    scored = predictions.merge(actuals, on=GRAIN, how="inner", validate="one_to_one")
    if scored.empty:
        raise ValueError(f"no actuals for {target} matching the predictions")
    y = scored["actual_streams"]
    return {
        "input_month": predictions["input_month"].iloc[0],
        "target_month": target,
        "source": "evaluate",
        "rows_predicted": len(predictions),
        "rows_scored": len(scored),
        **_metrics(y, scored["predicted_june_streams"], "model"),
        **_metrics(y, scored["input_streams"], "baseline"),
    }


def load_history(folder):
    """One JSON per evaluated month -> table sorted by target month."""
    rows = [json.loads(path.read_text()) for path in sorted(folder.glob("*.json"))]
    return pd.DataFrame(rows).sort_values("target_month", ignore_index=True) if rows else pd.DataFrame()
