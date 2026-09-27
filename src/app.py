"""Streamlit showcase: predictions + ModelOps dashboard. Run: uv run streamlit run src/app.py"""
import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # streamlit runs this file as a script

from src.checks import CHECKS, ERROR, infer_month, validate  # noqa: E402
from src.monitoring import category_mix, load_history, numeric_drift  # noqa: E402
from src.pipeline import (build_features, load_model, prepare_consumption, prepare_movies,  # noqa: E402
                          read_consumption, read_movies, run)

ACCENT, REFERENCE = "#2a78d6", "#8a8984"  # one series + gray reference line (dataviz palette)


@st.cache_resource
def model():
    return load_model(ROOT / "artifacts/movie_consumption_model.pkl")


@st.cache_data
def training_inputs():
    """Reference for drift: the model's inputs as built from the training files (May 2026)."""
    movies = prepare_movies(read_movies(ROOT / "data/train_movies.csv"))
    return build_features(movies, prepare_consumption(read_consumption(ROOT / "data/train_consumption.csv")), "2026-05-01")


st.set_page_config(page_title="Movie Streams Forecast", layout="wide")
st.title("Movie Streams Forecast")
st.caption("Next-month streams per movie × country × platform, from last month's consumption and movie metadata. "
           "Same checks and pipeline as the batch job.")

with st.sidebar:
    source = st.radio("Data", ["Sample: May 2026", "Upload CSVs"])
    if source == "Upload CSVs":
        movies_file = st.file_uploader("Movie metadata CSV", type="csv")
        consumption_file = st.file_uploader("Monthly consumption CSV", type="csv")
    else:
        movies_file, consumption_file = ROOT / "data/inference_movies.csv", ROOT / "data/inference_consumption.csv"
    month = st.text_input("Input month (optional)", placeholder="YYYY-MM-DD, default: the file's only month") or None

if movies_file is None or consumption_file is None:
    st.info("Upload both files to run the forecast, or pick the sample data.")
    st.stop()

try:
    movies_raw, consumption_raw = read_movies(movies_file), read_consumption(consumption_file)
    issues = validate(movies_raw, consumption_raw, month or infer_month(consumption_raw))
except ValueError as error:
    st.error(str(error))
    st.stop()

errors = [issue for issue in issues if issue["severity"] == ERROR]
if errors:
    st.error(f"{len(errors)} data check(s) failed: fix the files and upload again.")
    st.dataframe(pd.DataFrame(issues), hide_index=True, width="stretch")
    st.stop()

predictions, summary, features = run(movies_raw, consumption_raw, model(), month)
titles = movies_raw[["TITLE_ID", "ORIGINAL_TITLE"]].rename(columns={"ORIGINAL_TITLE": "title"}) \
    if "ORIGINAL_TITLE" in movies_raw else pd.DataFrame(columns=["TITLE_ID", "title"])
table = predictions.merge(titles, on="TITLE_ID", how="left")
table = table[["TITLE_ID", "title", "country", "platform", "input_streams", "predicted_june_streams", "input_month", "target_month"]]

tab_predictions, tab_ops, tab_checks = st.tabs(["Predictions", "ModelOps", "Data checks"])

with tab_predictions:
    c1, c2, c3 = st.columns(3)
    c1.metric("Combinations predicted", f"{summary['rows']:,}")
    c2.metric("Movies", f"{summary['movies']:,}")
    c3.metric("Predicting", f"{predictions['target_month'].iloc[0][:7]} from {summary['input_month'][:7]}")
    st.dataframe(
        table.sort_values("predicted_june_streams", ascending=False),
        hide_index=True, width="stretch",
        column_config={"predicted_june_streams": st.column_config.NumberColumn("predicted streams", format="%.1f")},
    )
    st.download_button("Download predictions.csv", predictions.to_csv(index=False, float_format="%.2f"),
                       "predictions.csv", "text/csv")

with tab_ops:
    drift = numeric_drift(training_inputs(), features)
    ratio = (table["predicted_june_streams"] / table["input_streams"]).median()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Data checks", "passed" if not issues else f"{len(issues)} warning(s)")
    c2.metric("Unseen categories", sum(len(v) for v in summary["unseen_categories"].values()))
    c3.metric("Shifted features", f"{(drift['status'] == 'shifted').sum()} of {len(drift)}")
    c4.metric("Median predicted ÷ input", f"{ratio:.2f}")

    st.subheader("Predicted vs input streams")
    st.caption("One dot per movie × country × platform. Dots under the dashed line are predicted to decline. "
               "In training, June was 59% of May overall.")
    low, high = table[["input_streams", "predicted_june_streams"]].min().min(), table["input_streams"].max()
    diagonal = alt.Chart(pd.DataFrame({"v": [low, high]})).mark_line(color=REFERENCE, strokeDash=[4, 4], strokeWidth=2) \
        .encode(x="v:Q", y="v:Q")
    dots = alt.Chart(table).mark_circle(size=64, color=ACCENT, opacity=0.7).encode(
        x=alt.X("input_streams:Q", scale=alt.Scale(type="log"), title="Streams in input month (log)"),
        y=alt.Y("predicted_june_streams:Q", scale=alt.Scale(type="log"), title="Predicted streams next month (log)"),
        tooltip=["title", "country", "platform", alt.Tooltip("input_streams:Q", format=",.0f"),
                 alt.Tooltip("predicted_june_streams:Q", title="predicted", format=",.1f")],
    )
    st.altair_chart(diagonal + dots, width="stretch")

    st.subheader("Input drift vs training")
    st.caption("Model inputs of this run compared with the inputs the model was trained on. "
               "'shifted' = more than 25% of rows fall outside the training p5-p95 (about 10% is normal).")
    st.dataframe(drift, hide_index=True, width="stretch",
                 column_config={"outside training p5-p95": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1)})
    st.dataframe(category_mix(training_inputs(), features), hide_index=True, width="stretch",
                 column_config={c: st.column_config.NumberColumn(format="percent") for c in ("training", "current")})

    st.subheader("Accuracy by month")
    st.caption("Each month's predictions are scored when the next consumption file arrives (`cli evaluate`), "
               "against the baseline 'next month = this month'. WAPE = total absolute error ÷ total actual streams; lower is better.")
    history = load_history(ROOT / "artifacts/performance")
    if len(history) >= 2:
        trend = history.melt("target_month", ["model_wape", "baseline_wape"], "series", "wape")
        trend["series"] = trend["series"].map({"model_wape": "model", "baseline_wape": "baseline"})
        st.altair_chart(alt.Chart(trend).mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=64)).encode(
            x=alt.X("target_month:T", title="Predicted month"), y=alt.Y("wape:Q", title="WAPE"),
            color=alt.Color("series:N", scale=alt.Scale(domain=["model", "baseline"], range=[ACCENT, REFERENCE]),
                            legend=alt.Legend(orient="top", title=None)),
            tooltip=["target_month:T", "series:N", alt.Tooltip("wape:Q", format=".3f")],
        ), width="stretch")
    else:
        st.caption("The trend chart appears once a second month has been evaluated.")
    st.dataframe(history, hide_index=True, width="stretch")

with tab_checks:
    if issues:
        st.warning(f"{len(issues)} warning(s): the run continued; see the details below.")
        st.dataframe(pd.DataFrame(issues), hide_index=True, width="stretch")
    else:
        st.success(f"All {len(CHECKS) + 1} checks passed.")
    st.caption("Checks run on every file (add one in src/checks.py):")
    st.dataframe(pd.DataFrame([{"check": fn.__name__, "severity": severity} for severity, fn in CHECKS]),
                 hide_index=True, width="stretch")
