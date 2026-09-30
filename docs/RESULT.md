# Result: the brief, point by point

## Part 1 · Inference solution

| Brief asks | How | Evidence |
|---|---|---|
| Read the inference datasets | The brief's files, unchanged, as the example month `data/*/2026-05.csv`; read with `utf-8-sig` (BOM), ids as strings | `src/pipeline.py` |
| Reproduce the notebook's preparation | Rename `imdb_id → TITLE_ID` and movie columns, keep the input month, sum `streams`/`total_minutes` per grain, left-join movie attributes | `test_reproduces_notebook_training_table` (2005 rows / 738 movies); an independent rebuild matched 321/321 rows |
| Keep movie × country × platform | Group by the grain; `many_to_one` join fails on duplicated movies | `test_one_prediction_per_may_combination`, `test_same_month_rows_are_summed` |
| No retraining, no new preprocessing | `pickle.load` + `predict`; the model's fitted encoders and imputers do the rest; columns in `feature_names_in_` order | `test_feature_order_matches_schema` |
| One prediction per May combination | 321 combinations → 321 predictions, 0 duplicates, 0 NaN; other months ignored | `predictions.csv` · `test_other_months_are_ignored` |
| Required columns | `TITLE_ID, country, platform, predicted_june_streams` + `input_streams, input_month, target_month` | `test_output_columns_and_values` |
| Runs locally with instructions | uv, pip or Docker; CLI `check`, `predict`, `evaluate`, `ui` | [`README.md`](../README.md) |
| Only inference-file information | June is never read at inference; drift uses saved stats, not training data | `test_drift_reference_is_up_to_date_and_stable` |

**Production practices:** 15 data checks before predicting (errors stop with the CSV lines and a ready-to-paste fix prompt; warnings go to `summary.json`); clean exit 1 for missing files, corrupt model or bad input; 35 pytest (~2 s) + mocked `terraform test`; standard `logging` (CloudWatch in AWS); GitHub Actions CI runs the tests and Terraform checks on every push; `uv.lock` + one Docker image for local, SageMaker and Render.

## Parts 2 and 3 · AWS and Terraform

Design, reasoning, paths, Terraform usage, assumptions and what's unverified: [`ARCHITECTURE.md`](ARCHITECTURE.md). Choices: [`DECISIONS.md`](DECISIONS.md).

## Optional

Live demo: https://movie-streams-forecast.onrender.com (Streamlit: sample or upload, forecast table + download, charts, drift, data quality, accuracy by month).

## Known limitations

**Model**
- **One transition learned** (May → June 2026): other months run, but the model knows no seasonality.
- **Modest accuracy:** WAPE 0.62 vs 0.85 for "June = May" on unseen films (notebook holdout).
- **Totals come out low:** the log target predicts typical values (in-sample, predictions sum to 75% of actual). Use per-row values, not sums.
- **Possible look-ahead:** ratings have no capture date; the brief says to assume end of May.
- **Unseen categories** are ignored by the model's encoder; they're only reported in `summary.json`.

**Code**
- **A new model isn't fully "no rebuild":** the spelling check and the app read known categories and drift stats from `models/v1/drift_reference.json` baked into the image, so pointing `ModelUri` at v2 still validates against v1. Fix: take the categories from the loaded model's encoder.
- **Fixed column names:** for any input month the output column is still `predicted_june_streams` and features are `may_*` (the model's names).

**AWS (untested end to end; see [what's unverified](ARCHITECTURE.md#3--terraform-part-3))**
- **A missing movie snapshot fails silently:** the brief names only a consumption file; if the month's movies file never arrives, the run "waits" with exit 0, so no alert and no predictions. Fix: an alarm when `predictions.csv` is missing by day N.
- **Warnings don't alert:** unseen categories and drift only land in `summary.json` and the dashboard; SNS fires only on `Failed`/`Stopped`.
- **Each run mounts every past month** of inputs and predictions: fine at this size, grows without bound.
- **No deduplication:** each monthly file starts an execution; the first exits "waiting", and near-simultaneous uploads can both run the full job (harmless, outputs overwrite). The trigger passes the S3 key, not the month, because EventBridge can't transform it without a Lambda.
- **Reruns overwrite** a month's output: old versions stay in S3 (expire after 365 days), but `summary.json` doesn't record the model or image that produced it.
- **Model promotion is manual:** Terraform uploads v1, and event-triggered runs use the `ModelUri` default, so a new model means changing that default.
- **Deploy order:** `apply` before the image push, so an upload in between fails the job.
- `s3:ListBucket` is bucket-wide, not limited to prefixes; the container runs as root.

## With more time

An orchestration step (one execution per month, exact files, per-run outputs) and monthly retraining with a registry gate ([plan](ARCHITECTURE.md#production-path-out-of-scope)), known categories from the model, richer features (3 months of history, movie age), CloudWatch metrics and alarms (missing month, drift), and a safer model format than pickle.
