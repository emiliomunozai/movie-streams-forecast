# Movie Streams Forecast

Batch inference with the supplied model: **June 2026 streams per movie × country × platform** from the raw May 2026 files.
It reproduces the notebook's data preparation and never refits anything.

**Output:** [`output/predictions/input_month=2026-05-01/predictions.csv`](output/predictions/input_month=2026-05-01/predictions.csv) (321 rows) and [`summary.json`](output/predictions/input_month=2026-05-01/summary.json)

**Live demo:** https://movie-streams-forecast.onrender.com (the first load after idle takes ~1 min)

## Run it

```bash
uv sync                              # Python 3.13 + pinned libraries (or: pip install -r requirements.txt)
uv run python -m src.cli predict     # checks, then writes output/predictions/input_month=2026-05-01/
uv run python -m src.cli ui          # dashboard on localhost:8501
uv run pytest -q                     # 35 tests, ~2 s
```

With Docker: `docker build -t msf . && docker run --rm -p 7860:7860 msf` (dashboard), or `docker run --rm msf predict`.

Exit code `1` means a missing file or a failed data check. The log names the check and the CSV lines, and gives a **fix prompt** to paste into an AI coding agent.

## Layout: the same locally and in S3

```
data/movies/2026-05.csv                                     movie metadata of the month
data/consumption/2026-05.csv                                consumption of the month
models/v1/model.pkl                                         the supplied model (+ feature_schema.json, drift_reference.json)
output/predictions/input_month=2026-05-01/predictions.csv   TITLE_ID, country, platform, predicted_june_streams, input_streams, input_month, target_month
output/predictions/input_month=2026-05-01/summary.json      counts, warnings, unseen categories, totals
output/performance/                                         accuracy per month (seeded with the notebook's v1 holdout)
```

In AWS each path starts with `s3://…/`. Training data isn't in the repo; the 3 tests that rebuild the notebook's table read it from the challenge package (`../instructions/data`) and skip without it.

## Docs

- [`docs/RESULT.md`](docs/RESULT.md): every point of the brief and how it was solved
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): local vs app vs AWS step by step, the AWS design, every path
- [`docs/DECISIONS.md`](docs/DECISIONS.md): the 15 key decisions and why
- [`infra/README.md`](infra/README.md): Terraform, checking it without AWS, deploying

## Known limitations

- **One transition learned** (May → June 2026): other months run, but the model knows no seasonality.
- **Totals come out low** (log target: in-sample, predictions sum to 75% of actual). Use per-row values.
- **Modest accuracy:** WAPE 0.62 vs 0.85 for "June = May" on unseen films (notebook).
- **Possible look-ahead:** ratings have no capture date; the brief says to assume end of May.
- **Unseen categories** are ignored by the model's encoder; they're reported in `summary.json`.
- **AWS untested end to end:** Terraform passes `validate` and mocked tests ([what's unverified](infra/README.md#not-verified-without-an-account)). The container runs as root until tested there.

## With more time

Monthly retraining with a registry gate ([plan](docs/ARCHITECTURE.md#future-model-updates-not-in-scope-the-brief-says-use-the-existing-model)), richer features (3 months of history, movie age), CloudWatch metrics and alarms, CI, and a safer model format than pickle.

## AI-assisted development

Built with **Claude Code** (Claude Opus 5.5) as a pair programmer: it read the brief and notebook, proposed options, and wrote the code, tests, Terraform and docs. I made the decisions ([`docs/DECISIONS.md`](docs/DECISIONS.md)), and everything was checked with tests, deliberate-bug checks, Docker runs and mocked Terraform.
