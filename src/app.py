"""Streamlit UI over the same pipeline as the batch job. Run: uv run python -m src.cli ui"""
import json
import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # streamlit runs this file as a script

from src.checks import CHECKS, ERROR, CheckError, fix_prompt  # noqa: E402
from src.monitoring import category_mix, load_history, numeric_drift  # noqa: E402
from src.pipeline import load_model, read_csv, run  # noqa: E402

ACCENT, REFERENCE = "#2a78d6", "#8a8984"
SEQUENTIAL = ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"]
PADDING = {"left": 32, "right": 12, "top": 8, "bottom": 8}  # room for labels measured before the web font loads
LOG_TICKS = [1, 10, 100, 1000, 10000]
REPO = "https://github.com/emiliomunozai/movie-streams-forecast"
FEATURES = {
    "may_streams": "Input streams", "may_total_minutes": "Input minutes", "release_year": "Release year",
    "runtime_minutes": "Runtime (min)", "rating_value": "Rating", "rating_vote_count": "Rating votes",
    "country": "Market", "platform": "Platform", "primary_genre": "Genre",
}


@st.cache_resource
def model():
    return load_model(ROOT / "models/v1/model.pkl")


@st.cache_data
def drift_reference():
    """Training-month input statistics (built by `cli reference`); the UI never reads training data."""
    return json.loads((ROOT / "models/v1/drift_reference.json").read_text())


HOW_TO_READ = {
    "top": "Ten titles with the highest forecast streams, summed across markets and platforms. "
           "Longer bar = more forecast streams. Hover a bar for the exact value. Use for ranking: "
           "the model predicts typical values, so summed totals understate actual volume.",
    "grid": "Forecast streams summed per market (rows) and platform (columns). Darker cell = more streams; "
            "the number in each cell is the total. Compare cells within the grid to see where demand concentrates.",
    "table": "One row per title × market × platform observed in the input month. Streams = actual streams in the "
             "input month; Forecast = predicted streams next month; Change = Forecast ÷ Streams − 1 "
             "(negative = expected decline). Filter above, sort by clicking a column header.",
    "accuracy": "One row per evaluated month: forecasts are scored once that month's actual consumption arrives. "
                "WAPE = total absolute error ÷ total actual streams (lower is better). Baseline = 'next month equals "
                "this month'; the model adds value when its WAPE is below the baseline's. MAE = average error in streams.",
    "scatter": "Each point is one title × market × platform. Horizontal: actual streams in the input month; vertical: "
               "forecast for next month; both on log scales. The dashed diagonal means no change: points below it "
               "are forecast to decline, points above it to grow.",
    "drift": "Compares this run's model inputs with the data the model was trained on. The bar shows the share of rows "
             "outside the training 5th–95th percentile range; about 10% is expected by definition. Status 'Shifted' "
             "(above 25%) means the inputs differ from training and forecasts for those rows are less reliable.",
    "mix": "Share of rows per market, platform and genre: training data vs. this run. Large differences indicate a "
           "change in portfolio mix. Values absent from training are ignored by the model.",
    "checks": "Every rule applied to the input files before forecasting. Errors stop the run; warnings are recorded "
              "and the run continues. Result shows 'Passed' or the affected CSV lines.",
}


def month_label(month):
    return pd.Timestamp(month).strftime("%b %Y")


def label(name):
    return name.replace("_", " ").capitalize()


st.set_page_config(page_title="Streams Forecast", page_icon=":material/insights:", layout="wide")

with st.sidebar:
    st.subheader("Input data")
    source = st.segmented_control("Source", ["Sample", "Upload"], default="Sample", label_visibility="collapsed") or "Sample"
    if source == "Upload":
        movies_file = st.file_uploader("Title metadata (CSV)", type="csv")
        consumption_file = st.file_uploader("Monthly consumption (CSV)", type="csv")
    else:
        movies_file, consumption_file = ROOT / "data/movies/2026-05.csv", ROOT / "data/consumption/2026-05.csv"
        st.caption("Sample: 100 titles, May 2026 consumption.")
    month = st.text_input("Input month", placeholder="Auto-detect",
                          help="YYYY-MM-DD. Defaults to the single month in the consumption file.") or None
    st.divider()
    st.subheader("Model")
    st.caption("Random forest on log streams · scikit-learn 1.8 · v1  \n"
               "Trained on May → June 2026 · 2,005 observations, 738 titles")
    st.caption(f"[Source code and documentation]({REPO})")

st.title("Streams Forecast")
st.caption("Next-month streaming volume by title, market and platform")

if movies_file is None or consumption_file is None:
    st.info("Upload title metadata and monthly consumption files to generate a forecast.", icon=":material/upload_file:")
    st.stop()

try:
    movies_raw = read_csv(movies_file)
    predictions, summary, features = run(movies_raw, read_csv(consumption_file), model(), month)
except CheckError as error:
    st.error("Input validation failed. Correct the files and upload again.", icon=":material/error:")
    st.dataframe(pd.DataFrame(error.issues).assign(check=lambda d: d["check"].map(label)), hide_index=True, width="stretch")
    st.caption("Or paste this prompt into an AI coding agent (e.g. Claude Code) to repair the files:")
    st.code(fix_prompt(error.issues, [getattr(f, "name", f) for f in (movies_file, consumption_file)]), language=None, wrap_lines=True)
    st.stop()
except ValueError as error:
    st.error(f"Input could not be read: {error}", icon=":material/error:")
    st.stop()

issues = summary["warnings"]
titles = (movies_raw[["TITLE_ID", "ORIGINAL_TITLE"]].rename(columns={"ORIGINAL_TITLE": "title"})
          if "ORIGINAL_TITLE" in movies_raw else pd.DataFrame(columns=["TITLE_ID", "title"]))
table = predictions.merge(titles, on="TITLE_ID", how="left")
table["title"] = table["title"].fillna(table["TITLE_ID"])
table["change"] = table["predicted_june_streams"] / table["input_streams"].where(table["input_streams"] > 0) - 1
input_month, target_month = summary["input_month"], predictions["target_month"].iloc[0]
drift = numeric_drift(drift_reference(), features)
unseen = sum(len(values) for values in summary["unseen_categories"].values())

with st.container(horizontal=True):
    st.badge(f"{month_label(input_month)} → {month_label(target_month)}", icon=":material/calendar_month:", color="blue")
    if issues:
        st.badge(f"{len(issues)} data warning(s)", icon=":material/warning:", color="orange")
    else:
        st.badge("All data checks passed", icon=":material/check_circle:", color="green")
    st.badge("Model v1", icon=":material/deployed_code:", color="gray")

overview, forecast, monitoring, quality = st.tabs(["Overview", "Forecast", "Monitoring", "Data quality"])

with overview:
    tiles = st.columns(5)
    tiles[0].metric("Forecast month", month_label(target_month), border=True)
    tiles[1].metric("Input month", month_label(input_month), border=True)
    tiles[2].metric("Titles", f"{summary['movies']:,}", border=True)
    tiles[3].metric("Title × market × platform", f"{summary['rows']:,}", border=True)
    tiles[4].metric("Median change", f"{table['change'].median():+.0%}", border=True,
                    help="Median of forecast ÷ input-month streams, minus 1.")

    left, right = st.columns(2, gap="large")
    with left:
        st.subheader("Top titles by forecast streams", help=HOW_TO_READ["top"])
        top = (table.groupby(["TITLE_ID", "title"], as_index=False)["predicted_june_streams"].sum()
               .nlargest(10, "predicted_june_streams"))
        st.altair_chart(alt.Chart(top).mark_bar(color=ACCENT, cornerRadiusEnd=4, height=18).encode(
            x=alt.X("predicted_june_streams:Q", title="Forecast streams (all markets)"),
            y=alt.Y("title:N", sort="-x", title=None, axis=alt.Axis(labelLimit=170)),
            tooltip=[alt.Tooltip("title:N", title="Title"), alt.Tooltip("predicted_june_streams:Q", title="Forecast", format=",.0f")],
        ).properties(height=340, padding=PADDING), width="stretch")
    with right:
        st.subheader("Forecast streams by market and platform", help=HOW_TO_READ["grid"])
        grid = table.groupby(["country", "platform"], as_index=False)["predicted_june_streams"].sum()
        midpoint = grid["predicted_june_streams"].max() / 2
        base = alt.Chart(grid).encode(
            x=alt.X("platform:N", title=None, scale=alt.Scale(paddingInner=0.06), axis=alt.Axis(labelAngle=0, orient="top")),
            y=alt.Y("country:N", title=None, scale=alt.Scale(paddingInner=0.06)))
        cells = base.mark_rect(cornerRadius=4).encode(
            color=alt.Color("predicted_june_streams:Q", scale=alt.Scale(range=SEQUENTIAL), legend=None),
            tooltip=[alt.Tooltip("country:N", title="Market"), alt.Tooltip("platform:N", title="Platform"),
                     alt.Tooltip("predicted_june_streams:Q", title="Forecast", format=",.0f")])
        values = base.mark_text(fontSize=13, fontWeight=500).encode(
            text=alt.Text("predicted_june_streams:Q", format=",.0f"),
            color=alt.condition(alt.datum.predicted_june_streams > midpoint, alt.value("white"), alt.value("#0b0b0b")))
        st.altair_chart((cells + values).properties(height=340, padding=PADDING), width="stretch")

with forecast:
    st.subheader("Forecast by title, market and platform", help=HOW_TO_READ["table"])
    with st.container(horizontal=True, vertical_alignment="bottom"):
        markets = st.multiselect("Market", sorted(table["country"].unique()), placeholder="All markets")
        platforms = st.multiselect("Platform", sorted(table["platform"].unique()), placeholder="All platforms")
        search = st.text_input("Title", placeholder="Search titles")
    keep = pd.Series(True, index=table.index)
    if markets:
        keep &= table["country"].isin(markets)
    if platforms:
        keep &= table["platform"].isin(platforms)
    if search:
        keep &= table["title"].str.contains(search, case=False, regex=False)
    view = table[keep].sort_values("predicted_june_streams", ascending=False)
    st.dataframe(
        view[["title", "TITLE_ID", "country", "platform", "input_streams", "predicted_june_streams", "change"]],
        hide_index=True, width="stretch", height=520,
        column_config={
            "title": st.column_config.TextColumn("Title", width="large"),
            "TITLE_ID": st.column_config.TextColumn("Title ID"),
            "country": "Market",
            "platform": "Platform",
            "input_streams": st.column_config.NumberColumn(f"Streams {month_label(input_month)}", format="%d"),
            "predicted_june_streams": st.column_config.NumberColumn(f"Forecast {month_label(target_month)}", format="%.1f"),
            "change": st.column_config.NumberColumn("Change", format="percent"),
        },
    )
    with st.container(horizontal=True, vertical_alignment="center"):
        st.caption(f"{len(view):,} of {len(table):,} rows")
        st.download_button("Download CSV", predictions.to_csv(index=False, float_format="%.2f"),
                           f"predictions_{target_month[:7]}.csv", "text/csv", icon=":material/download:")

with monitoring:
    tiles = st.columns(3)
    tiles[0].metric("Data checks", "Passed" if not issues else f"{len(issues)} warnings", border=True)
    tiles[1].metric("Unseen categories", unseen, border=True, help="Markets, platforms or genres absent from training.")
    tiles[2].metric("Shifted features", f"{(drift['status'] == 'shifted').sum()} of {len(drift)}", border=True,
                    help="More than 25% of rows outside the training 5th–95th percentile range.")

    st.subheader("Accuracy by month", help=HOW_TO_READ["accuracy"])
    history = load_history(ROOT / "output/performance")
    if history.empty:
        st.caption("No evaluated months yet.")
    else:
        st.dataframe(
            history.assign(target_month=pd.to_datetime(history["target_month"]))[["target_month", "source", "rows_scored", "model_wape", "baseline_wape", "model_mae", "baseline_mae", "model_r2"]],
            hide_index=True, width="stretch",
            column_config={
                "target_month": st.column_config.DateColumn("Target month", format="MMM YYYY"),
                "source": st.column_config.TextColumn("Source", width="large"),
                "rows_scored": "Rows", "model_wape": st.column_config.NumberColumn("WAPE", format="%.3f"),
                "baseline_wape": st.column_config.NumberColumn("Baseline WAPE", format="%.3f"),
                "model_mae": st.column_config.NumberColumn("MAE", format="%.1f"),
                "baseline_mae": st.column_config.NumberColumn("Baseline MAE", format="%.1f"),
                "model_r2": st.column_config.NumberColumn("R²", format="%.3f"),
            },
        )

    st.subheader("Forecast vs. input streams", help=HOW_TO_READ["scatter"])
    low, high = table[["input_streams", "predicted_june_streams"]].min().min(), table["input_streams"].max()
    diagonal = alt.Chart(pd.DataFrame({"v": [low, high]})).mark_line(color=REFERENCE, strokeDash=[4, 4], strokeWidth=2) \
        .encode(x="v:Q", y="v:Q")
    dots = alt.Chart(table).mark_circle(size=64, color=ACCENT, opacity=0.65).encode(
        x=alt.X("input_streams:Q", scale=alt.Scale(type="log"), title=f"Streams, {month_label(input_month)}",
                axis=alt.Axis(values=LOG_TICKS, format=",")),
        y=alt.Y("predicted_june_streams:Q", scale=alt.Scale(type="log"), title=f"Forecast, {month_label(target_month)}",
                axis=alt.Axis(values=LOG_TICKS, format=",")),
        tooltip=[alt.Tooltip("title:N", title="Title"), alt.Tooltip("country:N", title="Market"), "platform:N",
                 alt.Tooltip("input_streams:Q", title="Input", format=",.0f"),
                 alt.Tooltip("predicted_june_streams:Q", title="Forecast", format=",.1f")],
    )
    st.altair_chart((diagonal + dots).properties(height=380, padding=PADDING), width="stretch")

    st.subheader("Input drift vs. training", help=HOW_TO_READ["drift"])
    st.dataframe(
        drift.assign(feature=drift["feature"].map(FEATURES), status=drift["status"].str.capitalize()),
        hide_index=True, width="stretch",
        column_config={
            "feature": "Feature",
            "training p5": st.column_config.NumberColumn("Training p5", format="%.1f"),
            "training median": st.column_config.NumberColumn("Training median", format="%.1f"),
            "training p95": st.column_config.NumberColumn("Training p95", format="%.1f"),
            "current median": st.column_config.NumberColumn("Current median", format="%.1f"),
            "outside training p5-p95": st.column_config.ProgressColumn("Outside training range", format="percent",
                                                                      min_value=0, max_value=1),
            "status": "Status",
        },
    )
    st.subheader("Category mix vs. training", help=HOW_TO_READ["mix"])
    mix = category_mix(drift_reference(), features)
    st.dataframe(
        mix.assign(feature=mix["feature"].map(FEATURES)), hide_index=True, width="stretch",
        column_config={"feature": "Feature", "value": "Value",
                       "training": st.column_config.NumberColumn("Training share", format="percent"),
                       "current": st.column_config.NumberColumn("Current share", format="percent")},
    )

with quality:
    found = {issue["check"]: issue for issue in issues}
    rows = [{"check": "required_columns", "severity": ERROR}] + [{"check": fn.__name__, "severity": sev} for sev, fn in CHECKS]
    checks = pd.DataFrame([
        {"Check": label(r["check"]), "Severity": r["severity"].capitalize(),
         "Result": found[r["check"]]["message"] if r["check"] in found else "Passed"}
        for r in rows
    ])
    passed = (checks["Result"] == "Passed").sum()
    st.metric("Checks passed", f"{passed} of {len(checks)}", border=True, width="content")
    st.subheader("Input validation rules", help=HOW_TO_READ["checks"])
    st.dataframe(checks, hide_index=True, width="stretch", height=38 * (len(checks) + 1),
                 column_config={"Result": st.column_config.TextColumn(width="large")})
