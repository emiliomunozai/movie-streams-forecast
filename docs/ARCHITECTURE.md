# Architecture

**AWS, in one line:** two files arrive monthly, `s3://…/data/movies/YYYY-MM.csv` and `s3://…/data/consumption/YYYY-MM.csv`, in any order → each upload triggers EventBridge → SageMaker Pipeline; the run for the first file waits, the second one runs our image twice in parallel (**Predict** this month, **Evaluate** last month) → `s3://…/output/predictions/input_month=YYYY-MM-DD/predictions.csv`, `s3://…/output/predictions/input_month=YYYY-MM-DD/summary.json`, `s3://…/output/performance/YYYY-MM-DD.json`; a failed run → SNS email.

The same code (`run()`: checks → features → predict) runs in three places, and the repo and the bucket share one folder layout ([every path](#every-path-locally-and-in-s3)). Only the way data comes in and goes out differs.

## 1 · The process, step by step

| Step | Local (CLI) | Streamlit app (Render) | AWS (SageMaker) |
|---|---|---|---|
| **1. Data arrives** | `data/movies/YYYY-MM.csv` and `data/consumption/YYYY-MM.csv` (the repo ships `2026-05`), or any path via `--movies` / `--consumption` | The user uploads the movies and consumption CSVs in the sidebar (or picks the sample files) | Upstream uploads `s3://…/data/movies/YYYY-MM.csv` and `s3://…/data/consumption/YYYY-MM.csv`, in any order |
| **2. Run starts** | You run `uv run python -m src.cli predict` (or `docker run <image> predict`) | Streamlit reruns the script as soon as both files are there | Each upload: S3 "Object Created" → EventBridge rule (`data/consumption/*.csv` or `data/movies/*.csv`) → `StartPipelineExecution` with `InputKey` = the file key. If the month's other file isn't there yet, the run logs "waiting" and ends successfully; the second upload does the real run |
| **3. Files reach the code** | The CLI picks the month's `YYYY-MM.csv` in each folder (or reads a given file) | Uploaded files are read in memory | SageMaker downloads `s3://…/data/consumption/` and `s3://…/data/movies/` into folders under `/opt/ml/processing/input/`; the CLI picks the two `YYYY-MM.csv` files of the triggering month |
| **4. Model loaded** | `models/v1/model.pkl`, or `--model <path>` | The same file, baked into the image, loaded once per process | `s3://…/models/v1/model.pkl` (`ModelUri` parameter), so a new model needs no rebuild |
| **5. Month** | The only file in the folders, or `--month YYYY-MM` | Inferred from the file, or typed in the sidebar | From the file name in the key (`2026-05.csv` → `2026-05-01`); the `month_present` check confirms the file holds that month |
| **6. Data checks** | 15 checks (also alone with `cli check`); errors exit 1, the log has the check, CSV lines and fix prompt | Same checks; errors show a table + a copyable fix prompt and stop | Same checks; errors fail the step, the log has the check, CSV lines and fix prompt |
| **7. Features + predict** | `run()` via `cli predict` | `run()` | `run()` via `cli predict` (Predict step) |
| **8. Results out** | `output/predictions/input_month=YYYY-MM-DD/predictions.csv`, `output/predictions/input_month=YYYY-MM-DD/summary.json` | Tables and charts on screen + CSV download | `s3://…/output/predictions/input_month=YYYY-MM-DD/predictions.csv`, `s3://…/output/predictions/input_month=YYYY-MM-DD/summary.json` |
| **9. Accuracy** | `cli evaluate --actuals data/consumption/<next month>.csv` → `output/performance/YYYY-MM-DD.json` | Monitoring tab shows the accuracy history | Evaluate step, in parallel: this month's file scores last month's predictions → `s3://…/output/performance/YYYY-MM-DD.json` |
| **10. Monitoring** | Terminal log + `output/predictions/input_month=YYYY-MM-DD/summary.json` | Tabs: drift vs training, data quality, accuracy by month | CloudWatch Logs; pipeline `Failed` → EventBridge → SNS email |
| **11. Who consumes it** | You, or any script reading `output/predictions/input_month=YYYY-MM-DD/predictions.csv` | The person using the app | Downstream: Athena / BI / apps reading `s3://…/output/predictions/input_month=YYYY-MM-DD/predictions.csv` and `s3://…/output/performance/YYYY-MM-DD.json` |
| **Defined in** | `pyproject.toml` + `uv.lock` (or `requirements.txt`, or the `Dockerfile`) | `Dockerfile` + Render service (auto-deploy on push to `main`) | `infra/*.tf` (S3, ECR, IAM, Pipeline, EventBridge, SNS) |

## 2 · AWS design (Part 2), in the brief's order

| Brief asks | Choice | Why |
|---|---|---|
| **Storing the data and the model** | One private, versioned S3 bucket; every path is in the table below. Inputs: `s3://…/data/movies/YYYY-MM.csv` (metadata snapshot), `s3://…/data/consumption/YYYY-MM.csv`, `s3://…/models/v1/model.pkl` | Versioning keeps every input and model. Titles and ratings change monthly, so each month has its own metadata snapshot: reruns of an old month use that month's metadata (no look-ahead), and a missing snapshot fails instead of using a stale one. |
| **Packaging the model and dependencies** | Our Docker image in ECR, `linux/amd64`, tagged by git SHA (immutable tags) | Prebuilt SageMaker scikit-learn images stop at **1.4-2**; the pickle needs **1.8.0** + Python 3.13. Same image as local and Render (verified: identical predictions on arm64 and amd64). |
| **Executing it** | **SageMaker Processing Job** (`ml.t3.medium`), inside a **SageMaker Pipeline** with two steps: Predict and Evaluate | Processing runs our script as-is on files. Batch Transform expects already-prepared rows (we need a groupby and a join first); endpoints are for real-time, which isn't needed. The Pipeline gives execution history, parameters, retries and a native EventBridge target, with no servers of our own. |
| **Starting the inference** | S3 "Object Created" → EventBridge rule matching `data/consumption/*.csv` or `data/movies/*.csv` → `StartPipelineExecution`, passing the key as `InputKey` (`$.detail.object.key`). The CLI reads the month from the key's file name; if the month's other file is missing it logs "waiting" and exits 0 | **Upload order doesn't matter:** whichever file lands second starts the real run, and re-uploading a corrected file re-runs its month. Event-driven, **no Lambda**, no schedule. Reruns are safe because output overwrites its month. |
| **Storing and retrieving predictions** | `s3://…/output/predictions/input_month=YYYY-MM-DD/predictions.csv` and `s3://…/output/predictions/input_month=YYYY-MM-DD/summary.json` | One folder per month: a predictable path for downstream, idempotent reruns (a rerun overwrites its month), Athena/Hive-partition friendly. Retrieve with `aws s3 cp`, Athena, or any S3 reader. |
| **Permissions** | **SageMaker role:** read `s3://…/data/consumption/*`, `s3://…/data/movies/*`, `s3://…/models/*`, `s3://…/output/predictions/*`; write `s3://…/output/predictions/*`, `s3://…/output/performance/*`; pull from this ECR repo; write its logs; pass itself only to SageMaker. **EventBridge role:** start this one pipeline. | Least privilege, scoped to prefixes and one pipeline. |
| **Configuration** | Pipeline parameters `InputKey` (set by EventBridge) and `ModelUri` (default v1); Terraform variables `image_tag`, `region`, `instance_type`, `alert_email` | Every execution records which file and model it used. New model = upload + change `ModelUri`, no image rebuild. New code = new image tag. |
| **Operational monitoring** | CloudWatch Logs; pipeline `Failed` → EventBridge → SNS email; `summary.json` per run; **Evaluate** scores last month's predictions (MAE, RMSE, WAPE, R², each vs the "next month = this month" baseline) into `s3://…/output/performance/YYYY-MM-DD.json` | Enough for one run a month. No extra trigger for accuracy: the file that starts month *t+1*'s prediction is the ground truth for month *t*. |

### Every path, locally and in S3

The repo and the bucket use the same layout; in S3 each path starts with `s3://…/`. The repo ships one example month (`2026-05`).

| Path | What | Written by | Read by |
|---|---|---|---|
| `data/movies/YYYY-MM.csv` | Movie metadata snapshot of that month | Upstream (in S3 it triggers a run) | Predict |
| `data/consumption/YYYY-MM.csv` | Consumption of that month | Upstream (in S3 it triggers a run) | Predict (input), Evaluate (actuals for last month's predictions) |
| `models/v1/model.pkl` | The supplied model (later `v2/`, …) | In S3: Terraform uploads the repo's `models/v1/` | Predict (in S3 via `ModelUri`) |
| `models/v1/feature_schema.json` | The model's input schema | Supplied with the model | Tests |
| `models/v1/drift_reference.json` | Training statistics of v1 | `cli reference` (from the training files) | Checks (known categories), dashboard (drift) |
| `output/predictions/input_month=YYYY-MM-DD/predictions.csv` | One prediction per movie × country × platform | Predict | Evaluate (next month), downstream |
| `output/predictions/input_month=YYYY-MM-DD/summary.json` | Row counts, warnings, unseen categories, totals | Predict | Downstream, monitoring |
| `output/performance/YYYY-MM-DD.json` | Accuracy of the predictions for that month vs the baseline | Evaluate | Downstream, dashboard |
| `data/consumption/README.txt`, `data/movies/README.txt`, `output/predictions/README.txt` | S3 only: placeholders, because a mounted folder can't be empty; not `*.csv`, so they never trigger | Terraform | — |

**Checked against AWS docs:** EventBridge → SageMaker Pipeline supports dynamic parameters via JSON path ([docs](https://docs.aws.amazon.com/sagemaker/latest/dg/pipeline-eventbridge.html)); prebuilt scikit-learn containers stop at 1.4-2 ([docs](https://docs.aws.amazon.com/sagemaker/latest/dg/sklearn.html)).
**Unverified without an AWS account:** the exact pipeline-definition JSON, IAM completeness, and the trigger end to end. To validate: `terraform plan`, then one upload in a sandbox account. See [`infra/README.md`](../infra/README.md).

## 3 · Code structure

| Part | Files | Role |
|---|---|---|
| Core | `checks.py`, `pipeline.py`, `monitoring.py` | Pure functions on DataFrames + a model; no AWS, UI or paths |
| Adapters | `cli.py` (Typer), `app.py` (Streamlit), tests | Call the core, so the batch job and the app can't drift apart |
| Runtime contract | "files in folders + a CLI command" | SageMaker meets it by mounting S3 as folders; AWS Batch, ECS or Airflow would too. **Changing platform changes Terraform, not Python.** |

## 4 · Live demo (Render)

| Concern | Choice | Why |
|---|---|---|
| Hosting | Render free web service built from our `Dockerfile`, command `ui`, port `$PORT` | Same image as local and SageMaker. Hugging Face Docker Spaces needed PRO. |
| Deploy | Auto-deploy on every push to `main` | No pipeline to maintain for a demo. |
| Limits | Free tier sleeps when idle (~30–60 s cold start) | Enough for a demo; no cloud bill. |

## 5 · ModelOps

| What | How |
|---|---|
| Data quality | 15 checks: required columns, month, empty keys, numeric/negative metrics, duplicates, id format, spelling variants of known categories (`netflix` → `Netflix`), movie attributes and plausible ranges |
| Drift vs training | Unseen categories (silently ignored by the encoder) and shifts in input streams and ratings, against saved training stats (`models/v1/drift_reference.json`), so no training data at inference |
| Accuracy by month | Evaluate step, one JSON per month. Seeded with the one real measurement: the notebook's v1 holdout (WAPE 0.62 vs 0.85 baseline, 310 rows); nothing back-filled or invented |

## Future: model updates (not in scope; the brief says use the existing model)
The model learned one transition (May → June 2026) with one month of history. In production the calendar drives retraining: at the end of month *t* you know *t*'s actuals, so the pair *t-1 → t* becomes a new training example.
- **Monthly retrain step** in the same SageMaker Pipeline, after Evaluate: a Training Job fits the notebook's estimator on all transitions so far, split by **time**, not by movie.
- **Gate:** register the new model in the SageMaker Model Registry only if it beats the current one and the baseline on the latest month; Predict then uses the approved version.
- **Better features:** last 3 months of streams, month-of-year, movie age (instead of absolute `release_year`).

## Tools

| Tool | Used for | Why this one |
|---|---|---|
| **uv** | Environment + dependencies | Fast, with a lockfile (`uv.lock`) for reproducibility; `requirements.txt` is exported from it for pip users. |
| **Python 3.13, pandas 2.2.3, numpy 2.3.5, scikit-learn 1.8.0** | Runtime, data prep, model | Pinned to the versions the pickle was saved with. |
| **Typer** | CLI | Typed commands (`check`, `predict`, `evaluate`, `reference`, `ui`) with help text for free. |
| **Streamlit** | UI + ModelOps dashboard | Upload, tables, charts and download in plain Python. |
| **pytest** | Tests | Standard, minimal boilerplate. |
| **Docker** | Packaging | One image for local, SageMaker and the live demo. |
| **Terraform** | AWS infrastructure as code | Required by the brief; tested against a mocked AWS provider. |
| **AWS:** S3, ECR, SageMaker (Pipeline + Processing), EventBridge, IAM, CloudWatch, SNS | Monthly batch | See section 2. |
| **Render** | Live URL | Free Docker web service built from our Dockerfile, so it's the same image. |
| **GitHub** | Repository | Source of truth; Render deploys from it. |
| **Claude Code** | AI pair programmer | See the README's *AI-assisted development* section and [`DECISIONS.md`](DECISIONS.md). |

Not used, on purpose: FastAPI (downstream reads S3, so no HTTP consumer), Batch Transform / endpoints (see section 2), MLflow (one fixed model; S3 versioning is enough).
