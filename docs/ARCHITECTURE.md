# Architecture

**AWS, in one line:** two monthly files, `data/movies/YYYY-MM.csv` and `data/consumption/YYYY-MM.csv`, land in S3 in any order → each upload triggers EventBridge → SageMaker Pipeline. The run for the first file waits; the second runs our image twice in parallel (**Predict** this month, **Evaluate** last month) → `output/`. A failed run → SNS email.

The same code (`run()`: checks → features → predict) runs locally, in the app and on SageMaker. The repo and the bucket share one folder layout ([paths](#paths-locally-and-in-s3)); only how data comes in and goes out differs.

## 1 · The process, step by step

| Step | Local (CLI) | Streamlit app (Render) | AWS (SageMaker) |
|---|---|---|---|
| **Data arrives** | `data/movies/` and `data/consumption/` (the repo ships `2026-05`), or `--movies` / `--consumption` | Uploaded on the start page (with a preview of the expected format), or the sample data | Upstream uploads both monthly files to S3, in any order |
| **Run starts** | `uv run python -m src.cli predict` | **Run forecast** button | S3 "Object Created" → EventBridge → `StartPipelineExecution` with `TriggerKey` = the file key. If the month's other file is missing, the run logs "waiting" and exits 0 |
| **Month** | The only file in the folders, or `--month YYYY-MM` | Inferred from the file, or typed | From the key's file name (`2026-05.csv` → `2026-05-01`); `month_present` confirms the file holds it |
| **Model** | `models/v1/model.pkl`, or `--model` | Baked into the image | `s3://…/models/v1/model.pkl` via the `ModelUri` parameter |
| **Checks** | 15 checks; errors exit 1 with the check, CSV lines and a fix prompt | Same; errors show a table and the fix prompt | Same; errors fail the step |
| **Results** | `output/predictions/input_month=YYYY-MM-DD/` | Tables, charts, CSV download | Same path in S3 |
| **Accuracy** | `cli evaluate --actuals <next month's consumption>` | Monitoring tab | Evaluate step: this month's file scores last month's predictions |
| **Monitoring** | Log + `summary.json` | Drift, data quality, accuracy tabs | CloudWatch Logs; pipeline `Failed`/`Stopped` → SNS email |

## 2 · AWS design (Part 2)

| Brief asks | Choice | Why |
|---|---|---|
| **Storing data and model** | One private, versioned S3 bucket ([paths](#paths-locally-and-in-s3)); one movie metadata snapshot per month | Versioning keeps every input and model. Per-month snapshots mean reruns of an old month use that month's metadata (no look-ahead) |
| **Packaging** | Our Docker image in ECR, `linux/amd64`, immutable tags = git SHA | Prebuilt SageMaker scikit-learn images stop at 1.4-2; the pickle needs 1.8.0 + Python 3.13. Same image locally and on Render (identical predictions on arm64 and amd64) |
| **Executing** | **SageMaker Processing Job** (`ml.t3.medium`) inside a **SageMaker Pipeline**: Predict + Evaluate | Processing runs our script as-is on files. Batch Transform expects prepared rows (we need a groupby and a join); endpoints are real-time, not needed. The Pipeline gives history, parameters and a native EventBridge target, with no servers |
| **Starting** | S3 → EventBridge rule (`data/consumption/*.csv`, `data/movies/*.csv`) → pipeline, key passed as `TriggerKey`. Two executions per month: the first exits 0 ("waiting"), the second does the work | Upload order doesn't matter, a corrected re-upload re-runs its month. No Lambda, no schedule. EventBridge can only pass the key as-is (no transforms), so the CLI reads the month from its name |
| **Storing predictions** | `output/predictions/input_month=YYYY-MM-DD/predictions.csv` + `summary.json` | Predictable path, idempotent reruns, Athena-friendly. Retrieve with `aws s3 cp`, Athena or any S3 reader |
| **Permissions** | **Pipeline role:** start processing jobs and pass the job role, nothing else. **Job role:** read the input prefixes, write `output/`, pull this ECR repo, write its logs. **EventBridge role:** start this one pipeline | Least privilege: orchestration and data access are separate trust boundaries |
| **Configuration** | Pipeline parameters `TriggerKey`, `ModelUri`; Terraform variables `image_tag`, `region`, `instance_type`, `alert_email` | Every execution records its file and model. New code = new image tag |
| **Monitoring** | CloudWatch Logs; `Failed`/`Stopped` → SNS; `summary.json` per run; Evaluate writes MAE/RMSE/WAPE/R² vs the "next month = this month" baseline | The file that starts month *t+1* is the ground truth for month *t*, so accuracy needs no extra trigger |

### Paths, locally and in S3

In S3 each path starts with `s3://…/`.

| Path | What | Written by | Read by |
|---|---|---|---|
| `data/movies/YYYY-MM.csv` | Movie metadata snapshot | Upstream (triggers) | Predict |
| `data/consumption/YYYY-MM.csv` | Consumption of the month | Upstream (triggers) | Predict; Evaluate (actuals for last month) |
| `models/v1/model.pkl` | The supplied model (+ `feature_schema.json`, `drift_reference.json` = training stats) | Terraform uploads the repo's `models/v1/` | Predict |
| `output/predictions/input_month=YYYY-MM-DD/` | `predictions.csv`, `summary.json` | Predict | Evaluate, downstream |
| `output/performance/YYYY-MM-DD.json` | Accuracy for that target month | Evaluate | Dashboard, downstream |
| `data/*/README.txt`, `output/predictions/README.txt` | S3 only: placeholders so mounted prefixes aren't empty; not `*.csv`, so they never trigger | Terraform | — |

### Production path (out of scope)

**Orchestration:** a small Lambda or Step Functions step between EventBridge and the pipeline that derives the month from the key, starts one execution per month once both files exist, and passes exact file URIs (no whole-prefix mounts, no placeholders). Outputs per run (`input_month=…/run_id=…/`) with model and image version.


**Model updates:** the model learned one transition (May → June 2026). In production, each month's actuals give a new training pair *t-1 → t*: add a Training step after Evaluate (split by time, not by movie), register the model in the SageMaker Model Registry only if it beats the current one and the baseline, and pass the approved version's URI as `ModelUri` (CI, not Terraform, uploads models). Better features: 3 months of history, month-of-year, movie age.

## 3 · Terraform (Part 3)

`infra/main.tf` (S3 with versioning and old-version expiry, ECR with a keep-20 policy, 3 IAM roles, SageMaker Pipeline, EventBridge trigger, SNS alerting), `variables.tf` (`image_tag` required), `outputs.tf`, `tests/wiring.tftest.hcl`.

**Check without AWS** (creates nothing; runs against a mocked provider):

```bash
cd infra && terraform init -backend=false
terraform fmt -check -recursive && terraform validate && terraform test
```

The test asserts that only the two monthly CSVs trigger, the key reaches both steps as `--trigger-key`, Predict runs exactly the CLI command tested in Docker, every path a step uses is mounted, and the image tag is used.

**Deploy:**

```bash
TAG=$(git rev-parse --short HEAD)
terraform apply -var image_tag=$TAG -var alert_email=you@example.com   # also uploads models/v1/
REPO=$(terraform output -raw ecr_repository_url)
aws ecr get-login-password | docker login --username AWS --password-stdin ${REPO%%/*}
docker build --platform linux/amd64 -t $REPO:$TAG .. && docker push $REPO:$TAG
BUCKET=$(terraform output -raw bucket)
aws s3 cp ../data/movies/2026-05.csv s3://$BUCKET/data/movies/2026-05.csv            # run waits
aws s3 cp ../data/consumption/2026-05.csv s3://$BUCKET/data/consumption/2026-05.csv  # run predicts
```

New model: upload `models/v2/model.pkl` and change `ModelUri`. New code: `terraform apply -var image_tag=<new>`.

**Assumptions:** credentials from the environment, nothing hardcoded; local state (a team would add an S3 backend); one account and region (`eu-west-1`); no VPC (the job only reaches S3/ECR/CloudWatch); default S3 encryption (SSE-S3), no KMS; files named `YYYY-MM.csv`, one month each.

**Not verified without an account:** that AWS accepts the pipeline-definition JSON and the IAM policies are complete; that `{Get: Parameters.TriggerKey}` resolves inside `ContainerArguments`; that S3 events reach the wildcard rule and the failure rule's field names; that the placeholders are enough for the first month's mounts. To close them: `terraform apply` in a sandbox, upload the movies file (expect "waiting"), then the consumption file (expect `predictions.csv`), then a broken file (expect the alert email).

## 4 · Code, demo and tools

| Part | What |
|---|---|
| Core | `checks.py`, `pipeline.py`, `monitoring.py`: pure functions on DataFrames + a model; no AWS, UI or paths |
| Adapters | `cli.py` (Typer), `app.py` (Streamlit), tests: they call the core, so the batch job and the app can't drift apart. Changing platform changes Terraform, not Python |
| Data checks | 15: required columns, month, empty keys, numeric/negative metrics, duplicates, id format, spelling variants of known categories (`netflix` → `Netflix`), movie attributes and ranges |
| Drift | Unseen categories and shifts in inputs, against saved training stats (`drift_reference.json`), so no training data at inference |
| CI/CD | GitHub Actions (`.github/workflows/ci.yml`): pytest + Terraform `fmt`/`validate`/`test` on every push. Render auto-deploys `main` after CI passes |
| Live demo | Render free web service built from the same `Dockerfile`; sleeps when idle |
| Tools | uv (lockfile), Python 3.13 + pandas 2.2.3 / numpy 2.3.5 / scikit-learn 1.8.0 (the pickle's versions), Typer, Streamlit, pytest, Docker, Terraform, GitHub Actions, Claude Code |

Not used, on purpose: FastAPI (downstream reads S3), Batch Transform / endpoints (see §2), MLflow (one fixed model; S3 versioning is enough).
