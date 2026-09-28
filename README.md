# Movie Streams Forecast

Batch inference for the **Movie Consumption Prediction Challenge**. It uses the supplied model to predict
**June 2026 streams per movie × country × platform** from raw May 2026 consumption and movie metadata.
It reproduces the notebook's data preparation and never refits or retrains anything.
Monthly in production: two files arrive each month (a movie metadata snapshot and the consumption file, in any order), and once both are in S3 the run produces the prediction for next month **and** scores last month's predictions against it.

**Deliverable:** [`output/predictions/input_month=2026-05-01/predictions.csv`](output/predictions/input_month=2026-05-01/predictions.csv) (321 rows) and [`output/predictions/input_month=2026-05-01/summary.json`](output/predictions/input_month=2026-05-01/summary.json)

## Folder layout: the same locally and in S3

The repo holds one example month (the brief's inference files) in exactly the layout of the S3 bucket; in AWS each path starts with `s3://…/`.

```
data/movies/2026-05.csv                                     input: movie metadata snapshot (YYYY-MM.csv)
data/consumption/2026-05.csv                                input: consumption of that month
models/v1/model.pkl                                         the supplied model
models/v1/feature_schema.json                               its input schema
models/v1/drift_reference.json                              training stats the dashboard compares against
output/predictions/input_month=2026-05-01/predictions.csv   predictions for the next month
output/predictions/input_month=2026-05-01/summary.json      counts, warnings, unseen categories, totals
output/performance/2026-06-01_notebook_holdout.json         accuracy history (seeded with the notebook's v1 holdout)
```

Training data is not in the repo: nothing at inference uses it. Three tests that prove the notebook is reproduced read it from the challenge package (`../instructions/data`, or `TRAINING_DATA=...`) and skip without it.

**Live demo:** https://movie-streams-forecast.onrender.com (predictions + ModelOps dashboard; free tier, so the first load after idle takes ~1 min).
Try it: use the sample data, or choose *Upload CSVs* with `data/movies/2026-05.csv` and `data/consumption/2026-05.csv`.

## Quickstart

Requires [uv](https://docs.astral.sh/uv/) (installs Python 3.13 and the pinned libraries).

```bash
uv sync                                   # create .venv from uv.lock
uv run python -m src.cli check            # validate the input files only
uv run python -m src.cli predict          # checks + predictions → output/predictions/input_month=2026-05-01/
uv run python -m src.cli ui               # Streamlit app: predictions + ModelOps dashboard
uv run python -m src.cli evaluate --actuals data/consumption/2026-06.csv   # once June arrives: score May's predictions
uv run pytest -q                          # 41 tests, ~2 s (3 need the challenge package, see above)
```

Without uv (Python 3.13 required; `requirements.txt` is exported from `uv.lock`):

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m src.cli predict
```

Another month: add `data/movies/2026-06.csv` and `data/consumption/2026-06.csv`, then `predict --month 2026-06`.
Inputs are folders of `YYYY-MM.csv` files (that is how SageMaker mounts S3); with one file per folder `--month` can be left out.
`--movies`, `--consumption`, `--model` and `--output-dir` also accept other paths, and a plain file works too.
Exit code `0` = success, `1` = missing file or failed data check (the log names the check and the CSV lines).
A failed check also logs a short **fix prompt**: paste it into an AI coding agent (e.g. Claude Code) to repair the files. The UI shows the same prompt.

### Docker

```bash
docker build -t movie-streams-forecast .
docker run --rm -p 7860:7860 movie-streams-forecast                                  # UI (default) → localhost:7860
docker run --rm -v "$PWD/output:/app/output" movie-streams-forecast predict          # batch → ./output/predictions/
docker run --rm movie-streams-forecast check
```

## Output

| Column | Meaning |
|---|---|
| `TITLE_ID`, `country`, `platform` | Prediction grain: every combination observed in the input month |
| `predicted_june_streams` | Predicted streams for the following month (name kept from the brief) |
| `input_streams` | Streams in the input month (the baseline, and handy next to the prediction) |
| `input_month`, `target_month` | Which month went in and which month is predicted |

`summary.json` (next to `predictions.csv`): row and movie counts, data-check warnings, categories unseen in training, input vs predicted totals.

## How it works

1. **Checks** (`src/checks.py`) run on the raw files. Errors stop the run; warnings are logged and added to the summary.
   To add a check, write one function decorated with `@check(ERROR)` or `@check(WARNING)`.
2. **Prepare** (`src/pipeline.py`), as in the notebook:
   - read with `utf-8-sig` (the files start with a BOM)
   - rename `imdb_id → TITLE_ID` and the movie columns
   - keep the chosen month
   - sum `streams` and `total_minutes` per `TITLE_ID × country × platform`
   - left-join the movie attributes
3. **Predict** with the supplied model. Its own fitted preprocessing does the encoding and imputation, and the columns are passed in `model.feature_names_in_` order (the same as `feature_schema.json`).

```
src/checks.py     data checks (pluggable)
src/pipeline.py   read → prepare → features → predict → summary
src/cli.py        Typer CLI: check, predict, evaluate, reference (rebuild drift stats from training files), ui
src/app.py        Streamlit UI (adapter over the same pipeline)
src/monitoring.py drift vs training (saved stats, models/v1/drift_reference.json) + scoring vs actuals
tests/            checks, pipeline, monitoring, CLI and app tests
infra/            Terraform for AWS (+ mocked-provider tests)
docs/             architecture (+ tools), decisions, brief → solution
```

## Docs

- [`docs/RESULT.md`](docs/RESULT.md): **every point of the brief and how it was solved**, with where to check
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): **AWS/SageMaker architecture** (process step by step for local, app and AWS; each AWS choice in the brief's order and why), code structure, ModelOps, and future retraining
- [`infra/README.md`](infra/README.md): Terraform, how to check it without AWS, deploy steps, assumptions
- [`docs/DECISIONS.md`](docs/DECISIONS.md): the 15 key decisions and why

## Known limitations

- **One transition, one month of history.** The model was trained only on May → June 2026, with last month's streams as its only history. For other months the pipeline runs (month-agnostic), but predictions are extrapolation: it knows nothing about seasonality, and movie age is hidden inside the absolute `release_year`.
- **Totals come out low.** The model is trained on `log1p(streams)`, so it predicts a typical value, not the mean. In-sample, predictions sum to 75% of actual June. Use per-row predictions; don't sum them for totals without recalibration.
- **Modest accuracy.** From the notebook, on unseen films: WAPE 0.62 vs 0.85 for "June = May", R² 0.24. Improving it was out of scope.
- **Possible look-ahead.** Ratings and votes have no capture timestamp; the brief says to assume end-of-May.
- **Unseen categories** (a new country, platform or genre) are silently zeroed by the model's encoder. We report them in `summary.json`, but the model can't use them.
- **Coverage.** Predictions exist only for movie × country × platform combinations with streams in the input month (as the brief asks).
- **Pickle.** Only load it from a trusted source. It is tied to Python 3.13 and scikit-learn 1.8.0, hence the pinned image.
- **The container runs as root.** Not yet verified whether SageMaker Processing's mounted folders are writable by a non-root user, so this stays until it's tested in a sandbox.
- **AWS is untested end to end.** Terraform passes `validate` and mocked tests. See [`infra/README.md`](infra/README.md#not-verified-without-an-account).

## With more time

- **Monthly retraining** as a step in the same SageMaker Pipeline, trained on all transitions so far and split by time, with a model-registry gate against the current model and the baseline. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#future-model-updates-not-in-scope-the-brief-says-use-the-existing-model).
- **Better features:** 3 months of history, month-of-year, movie age, and as-of-month metadata snapshots.
- **Metrics in CloudWatch:** publish the counts in `s3://…/output/predictions/input_month=YYYY-MM-DD/summary.json` and the WAPE in `s3://…/output/performance/YYYY-MM-DD.json` as metrics, with alarms, e.g. when WAPE is worse than the baseline.
- **CI:** GitHub Actions running `pytest` + `terraform test` on every PR, and building and pushing the image on merge.
- **A safer model format** (e.g. skops) plus a model card recording the training data hash and metrics.

## AI-assisted development

Built with **Claude Code** (Anthropic, Claude Opus 5.5) as a pair programmer in the terminal:

- **What the AI did:** read the brief and notebook, profiled the data, proposed options with trade-offs, and wrote the code, tests, Dockerfile, Terraform and docs.
- **What I decided,** after discussing the options: the design (one core, CLI + UI, one image), the month-agnostic behaviour, a separate pluggable checks module, Typer, a Streamlit dashboard with monthly accuracy tracking, and Render for the live demo.
- **How it was checked:** tests, deliberate-bug checks, a fresh-clone run, Docker runs on arm64 and amd64, a simulated SageMaker folder layout, and mocked Terraform tests.

The key decisions and their reasons are in [`docs/DECISIONS.md`](docs/DECISIONS.md).
