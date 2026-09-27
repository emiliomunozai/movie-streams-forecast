"""ModelOps without actuals: compare this run's model inputs with the training inputs (drift)."""
import pandas as pd

NUMERIC = ["may_streams", "may_total_minutes", "release_year", "runtime_minutes", "rating_value", "rating_vote_count"]
CATEGORICAL = ["country", "platform", "primary_genre"]
SHIFT_THRESHOLD = 0.25  # by construction ~10% of training rows fall outside its own p5-p95


def numeric_drift(reference, current):
    """Per numeric feature: training range vs this run, and the share of rows outside training p5-p95."""
    rows = []
    for column in NUMERIC:
        low, high = reference[column].quantile([0.05, 0.95])
        outside = float((~current[column].between(low, high)).mean())
        rows.append({
            "feature": column,
            "training p5": low,
            "training median": reference[column].median(),
            "training p95": high,
            "current median": current[column].median(),
            "outside training p5-p95": outside,
            "status": "shifted" if outside > SHIFT_THRESHOLD else "ok",
        })
    return pd.DataFrame(rows)


def category_mix(reference, current):
    """Share of rows per category value, training vs this run."""
    frames = []
    for column in CATEGORICAL:
        mix = pd.concat(
            {"training": reference[column].value_counts(normalize=True), "current": current[column].value_counts(normalize=True)},
            axis=1,
        ).fillna(0)
        frames.append(mix.rename_axis("value").reset_index().assign(feature=column))
    return pd.concat(frames, ignore_index=True)[["feature", "value", "training", "current"]]
