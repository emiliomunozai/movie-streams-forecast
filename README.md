# Movie Streams Forecast

Batch inference for the **Movie Consumption Prediction Challenge**. It uses the supplied model to predict
**June 2026 streams per movie × country × platform** from raw May 2026 consumption and movie metadata.
It reproduces the notebook's data preparation and never refits or retrains anything.

**Deliverable:** [`output/predictions.csv`](output/predictions.csv) (321 rows) + [`output/summary.json`](output/summary.json)

## Quickstart

Requires [uv](https://docs.astral.sh/uv/) (installs Python 3.13 and the pinned libraries).

```bash
uv sync                                   # create .venv from uv.lock
uv run python -m src.cli check            # validate the input files only
uv run python -m src.cli predict          # checks + predictions → output/
uv run pytest -q                          # 32 tests, ~1 s
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
docker run --rm -v "$PWD/output:/app/output" movie-streams-forecast            # predict
docker run --rm movie-streams-forecast check
```

## Output

| Column | Meaning |
|---|---|
| `TITLE_ID`, `country`, `platform` | Prediction grain: every combination observed in the input month |
| `predicted_june_streams` | Predicted streams for the following month (name kept from the brief) |
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
src/cli.py        Typer CLI: check, predict
tests/            checks + pipeline + CLI tests
docs/             brief, architecture, tools, checklist, AI log
```

## Docs

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): AWS/SageMaker design and the live demo
- [`infra/README.md`](infra/README.md): Terraform, how to check it without AWS, deploy steps, assumptions
- [`docs/AI_LOG.md`](docs/AI_LOG.md): every decision, finding and proof, in order
- [`docs/CHALLENGE.md`](docs/CHALLENGE.md): the brief, condensed
