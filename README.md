# Movie Streams Forecast

Batch inference for the **Movie Consumption Prediction Challenge**. It uses the supplied model to predict
**June 2026 streams per movie × country × platform** from raw May 2026 consumption and movie metadata.
It reproduces the notebook's data preparation and never refits or retrains anything.
Monthly in production: each new consumption file triggers the prediction for next month **and** scores last month's predictions against it.

**Deliverable:** [`output/predictions.csv`](output/predictions.csv) (321 rows) + [`output/summary.json`](output/summary.json)

**Live demo:** https://movie-streams-forecast.onrender.com (predictions + ModelOps dashboard; free tier, so the first load after idle takes ~1 min).
Try it: use the sample data, or choose *Upload CSVs* with `data/inference_movies.csv` and `data/inference_consumption.csv`.

## Quickstart

Requires [uv](https://docs.astral.sh/uv/) (installs Python 3.13 and the pinned libraries).

```bash
uv sync                                   # create .venv from uv.lock
uv run python -m src.cli check            # validate the input files only
uv run python -m src.cli predict          # checks + predictions → output/
uv run python -m src.cli ui               # Streamlit app: predictions + ModelOps dashboard
uv run python -m src.cli evaluate --actuals next_month.csv   # score predictions once their month's actuals arrive
uv run pytest -q                          # 36 tests, ~2 s
```

With your own files or another month:

```bash
uv run python -m src.cli predict \
  --movies path/movies.csv --consumption path/consumption.csv \
  --model artifacts/movie_consumption_model.pkl --output-dir output/
```

The month is read from the consumption file; pass `--month YYYY-MM-DD` if the file holds several months.
Each input can also be a folder holding exactly one file (that is how SageMaker mounts S3 inputs).
Exit code `0` = success, `1` = missing file or failed data check (the log names the check and the CSV lines).

### Docker

```bash
docker build -t movie-streams-forecast .
docker run --rm -p 7860:7860 movie-streams-forecast                                  # UI (default) → localhost:7860
docker run --rm -v "$PWD/output:/app/output" movie-streams-forecast predict          # batch
docker run --rm movie-streams-forecast check
```

## Output

| Column | Meaning |
|---|---|
| `TITLE_ID`, `country`, `platform` | Prediction grain: every combination observed in the input month |
| `predicted_june_streams` | Predicted streams for the following month (name kept from the brief) |
| `input_streams` | Streams in the input month (the baseline, and handy next to the prediction) |
| `input_month`, `target_month` | Which month went in and which month is predicted |

`summary.json`: row and movie counts, data-check warnings, categories unseen in training, input vs predicted totals.

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
src/cli.py        Typer CLI: check, predict, evaluate, ui
src/app.py        Streamlit UI (adapter over the same pipeline)
src/monitoring.py drift vs training + scoring predictions against actuals
artifacts/performance/  accuracy history, one JSON per evaluated month (seeded with the notebook's v1 holdout)
tests/            checks, pipeline, monitoring, CLI and app tests
infra/            Terraform for AWS (+ mocked-provider tests)
docs/             brief, architecture, tools, checklist, AI log
```

## Docs

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): **AWS/SageMaker architecture** (diagrams, one run step by step, decisions and why), code structure, ModelOps, and future retraining
- [`infra/README.md`](infra/README.md): Terraform, how to check it without AWS, deploy steps, assumptions
- [`docs/AI_LOG.md`](docs/AI_LOG.md): every decision, finding and proof, in order
- [`docs/CHALLENGE.md`](docs/CHALLENGE.md): the brief, condensed

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
- **Metrics in CloudWatch:** publish `summary.json` counts and `performance/` WAPE as metrics, with alarms, e.g. when WAPE is worse than the baseline.
- **CI:** GitHub Actions running `pytest` + `terraform test` on every PR, and building and pushing the image on merge.
- **A safer model format** (e.g. skops) plus a model card recording the training data hash and metrics.

## AI-assisted development

Built with **Claude Code** (Anthropic, Claude Opus 5.5) as a pair programmer in the terminal:

- **What the AI did:** read the brief and notebook, profiled the data, proposed options with trade-offs, and wrote the code, tests, Dockerfile, Terraform and docs.
- **What I decided,** after discussing the options: the design (one core, CLI + UI, one image), the month-agnostic behaviour, a separate pluggable checks module, Typer, a Streamlit dashboard with monthly accuracy tracking, and Render for the live demo.
- **How it was checked:** tests, deliberate-bug checks, a fresh-clone run, Docker runs on arm64 and amd64, a simulated SageMaker folder layout, and mocked Terraform tests.

Every decision, finding and proof is recorded in order in [`docs/AI_LOG.md`](docs/AI_LOG.md).
