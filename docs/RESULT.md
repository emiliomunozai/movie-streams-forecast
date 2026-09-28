# Result: the brief, point by point

Each requirement of *Movie Consumption Prediction Challenge* (the .docx) and how this repo meets it, with where to check.

## Part 1 · Inference solution

| Brief asks | How we solved it | Evidence |
|---|---|---|
| Read the supplied inference datasets | `read_csv` reads both raw files as they are (`utf-8-sig` for the BOM, ids kept as strings) | `src/pipeline.py` |
| Reproduce the notebook's data preparation and feature engineering | Rename `imdb_id → TITLE_ID` and the movie columns, keep the input month, sum `streams`/`total_minutes` per grain, left-join the movie attributes | `build_features` · `test_reproduces_notebook_training_table` (2005 rows / 738 movies, same as `feature_schema.json`); an independent rebuild from the notebook matched 321/321 rows |
| Keep the movie × country × platform grain | Group by `TITLE_ID × country × platform`, `many_to_one` join (fails on duplicated movies), duplicate keys summed and reported | `test_one_prediction_per_may_combination`, `test_same_month_rows_are_summed` |
| Load the model, no retraining, no new preprocessing | `pickle.load` + `model.predict`; the model's own fitted encoders do the encoding and imputation; columns passed in `model.feature_names_in_` order | `test_feature_order_matches_schema` |
| One prediction per combination observed in May | Only rows of the input month are used; 321 combinations → 321 predictions, 0 duplicates, 0 NaN | `output/predictions.csv` · `test_other_months_are_ignored` |
| Columns `TITLE_ID, country, platform, predicted_june_streams` | All four, plus `input_streams`, `input_month`, `target_month` for usability | `test_output_columns_and_values` |
| Runs locally with clear instructions | `uv sync` + `uv run python -m src.cli predict`, or `pip install -r requirements.txt`, or Docker; Typer CLI with `check`, `predict`, `evaluate`, `ui` | `README.md` Quickstart · `test_cli` |
| Use only inference-file information (no June data) | Features come only from the input month's file; June is never read at inference; the UI's drift reference is saved stats, not training data | `test_drift_reference_is_up_to_date_and_stable` |
| Reuse fitted transformations | Nothing is fitted anywhere in `src/`; unseen categories are reported, not re-encoded | `unseen_categories` in `summary.json` |
| Ratings/votes assumed available end of May | Accepted and listed as a limitation (possible look-ahead) | `README.md` Known limitations |

## Production-oriented practices (extra consideration)

| Practice | How |
|---|---|
| Error handling | 15 pluggable data checks run before predicting (errors stop, warnings go to `summary.json`); messages name the CSV lines; the CLI exits 1 with one log line instead of a traceback, also for a corrupt/incompatible model or a file without a `month` column |
| Self-repair prompt | A failed check also produces a short prompt to paste into an AI coding agent that fixes the files (CLI log, CloudWatch, UI) |
| Spelling/format safety | `category_spelling` (`netflix`, ` Brazil`, `HBO-Max` would be silently ignored by the model) and `ids_well_formed` (`tt\d+`) |
| Automated tests | 40 pytest (~2 s): every check has a break case, grain, notebook rebuild, CLI, SageMaker folder layout, the app; plus `terraform test` against a mocked AWS provider |
| Logging | Standard `logging`, one line per issue; CloudWatch in AWS |
| Reproducible deployment | `uv.lock` pins the pickle's versions (Python 3.13, scikit-learn 1.8.0); one Docker image for local, SageMaker and Render, verified identical on arm64 and amd64 |

## Part 2 · AWS architecture (SageMaker)

Monthly file under `consumption/` → EventBridge → SageMaker Pipeline (Predict + Evaluate Processing Jobs, our image) → S3. Diagram and full reasoning: [`ARCHITECTURE.md`](ARCHITECTURE.md).

| Brief asks | How we solved it |
|---|---|
| Storage of input data and model | One versioned, private S3 bucket: `consumption/`, `movies/YYYY-MM.csv` (monthly metadata snapshot, no look-ahead on reruns), `models/v1/model.pkl` |
| Packaging and execution | Our Docker image in ECR (`linux/amd64`, tag = git SHA, immutable), run as a **SageMaker Processing Job**. Prebuilt SageMaker sklearn images stop at 1.4-2; the pickle needs 1.8.0 |
| How inference starts | S3 "Object Created" → EventBridge rule (prefix `consumption/`) → `StartPipelineExecution`, the file key passed as `InputKey`. No Lambda, no schedule |
| Where predictions go, how to get them | `s3://…/predictions/input_month=YYYY-MM-DD/predictions.csv + summary.json`: predictable path, idempotent reruns, Athena-friendly |
| Permissions | SageMaker role: read/write only the prefixes it needs, pull from this ECR repo, write its logs, `PassRole` only to SageMaker. EventBridge role: start this one pipeline |
| Configuration | Pipeline parameters `InputKey` and `ModelUri`; Terraform variables `image_tag`, `region`, `instance_type`, `alert_email` |
| Monitoring | CloudWatch Logs, pipeline `Failed` → EventBridge → SNS email, `summary.json` per run, and **Evaluate** scores last month's predictions (MAE, WAPE vs baseline) into `performance/` |
| No real-time need | Batch Processing Job, no endpoint |
| Future updates, versioning | New model = upload `models/v2/` + change `ModelUri` (no rebuild); image tags by git SHA; bucket versioning; retrain step + Model Registry gate described under *Future* |

## Part 3 · Terraform

| Brief asks | How we solved it |
|---|---|
| Principal infrastructure, specific to this solution | `infra/main.tf`: S3 (versioning, public access block, EventBridge notifications), model upload, ECR, 2 least-privilege IAM roles, SageMaker Pipeline (both steps), EventBridge trigger, SNS alerting |
| Consistent with Part 2 | Predict runs exactly the CLI command tested in Docker; `terraform test` asserts the wiring (trigger prefix, `InputKey`, mounted paths, image tag) |
| Assumptions documented | `infra/README.md`: credentials from the environment, local state, one region, no VPC, one month per file, snapshot uploaded first |
| What's unverified and how to validate | `fmt`, `validate` and mocked `test` pass without AWS. Unverified: the pipeline JSON, IAM completeness and the end-to-end trigger; validate with `plan` + one upload in a sandbox account |

## Optional enhancements

| Brief suggests | Done |
|---|---|
| Deploy with a working URL | https://movie-streams-forecast.onrender.com (same image, free tier) |
| Lightweight UI | Streamlit: sample or CSV upload, forecast table + download, charts, ModelOps (drift, data quality, accuracy by month) |

## Deliverables

| Deliverable | Where |
|---|---|
| Source repository | GitHub (this repo) |
| Generated predictions CSV | [`output/predictions.csv`](../output/predictions.csv) (321 rows) |
| README with local run instructions | [`README.md`](../README.md) |
| Architecture description + reasoning | [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| Terraform + instructions/assumptions | [`infra/`](../infra/), [`infra/README.md`](../infra/README.md) |
| Limitations, improvements, AI tools | `README.md`: *Known limitations*, *With more time*, *AI-assisted development*; full trail in [`AI_LOG.md`](AI_LOG.md) |
| No credentials in the repo | None; AWS credentials come from the environment |
