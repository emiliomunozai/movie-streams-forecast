# Movie Streams Forecast

[![CI](https://github.com/emiliomunozai/movie-streams-forecast/actions/workflows/ci.yml/badge.svg)](https://github.com/emiliomunozai/movie-streams-forecast/actions/workflows/ci.yml)

Batch inference with the supplied model: **June 2026 streams per movie × country × platform** from the raw May 2026 files. It reproduces the notebook's data preparation and never refits anything.

**Output:** [`output/predictions/input_month=2026-05-01/predictions.csv`](output/predictions/input_month=2026-05-01/predictions.csv) (321 rows) and [`summary.json`](output/predictions/input_month=2026-05-01/summary.json). Delete them and run `predict` to regenerate; other runs' outputs are git-ignored.

**Live demo:** https://movie-streams-forecast.onrender.com (first load after idle takes ~1 min)

## Run it

```bash
uv sync                              # Python 3.13 + pinned libraries (or: pip install -r requirements.txt)
uv run python -m src.cli predict     # checks, then writes output/predictions/input_month=2026-05-01/
uv run python -m src.cli ui          # dashboard on localhost:8501
uv run pytest -q                     # 35 tests, ~2 s
```

Docker: `docker build -t msf . && docker run --rm -p 7860:7860 msf` (dashboard), or `docker run --rm msf predict`.

Exit code `1` means a missing file or a failed data check; the log names the check, the CSV lines and a fix prompt for an AI coding agent. The 3 tests that rebuild the notebook's table read the training files from the challenge package (`../instructions/data`) and skip without it.

## Docs

- [`docs/RESULT.md`](docs/RESULT.md): each point of the brief, known limitations, what's next
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): the AWS design, paths, Terraform (check, deploy, assumptions)
- [`docs/DECISIONS.md`](docs/DECISIONS.md): the 15 key decisions and why

## AI-assisted development

I designed the solution and made the technical decisions ([`DECISIONS.md`](docs/DECISIONS.md)). I used **Claude Code** (Claude Opus 5.5) as a coding assistant to draft the code, tests, Terraform and docs from that design and to compare alternatives. I reviewed, refined and verified every part with tests, deliberate-bug checks, Docker runs and mocked Terraform.
