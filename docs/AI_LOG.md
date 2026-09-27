# AI Log

Every decision, change, finding and proof made while building this, in order.
Types: **Decision** (a choice, with the reason) · **Development** (code/files) · **Finding** (something learned from the data or docs) · **Proof** (a check that verifies something) · **Setup** (environment/tooling).

| Date | Type | Name | Description |
|---|---|---|---|
| 2026-09-27 | Finding | Read brief | Read the .docx, notebook, `feature_schema.json` and CSV headers. The model has the fitted preprocessing built in, but not the May aggregation or the movie join. |
| 2026-09-27 | Decision | Project name | `movie_streams_forecast`: the model forecasts stream counts, it doesn't recommend. |
| 2026-09-27 | Decision | Layout | Same pattern as other dev projects: uv + `pyproject.toml` + `.python-version`, `src/`, `tests/`, `docs/`. |
| 2026-09-27 | Decision | Self-contained inputs | Copied `data/` and `artifacts/` into the project (originals are read-only). The deliverable must run on its own. |
| 2026-09-27 | Decision | Pinned versions | Python 3.13 + pandas 2.2.3, numpy 2.3.5, scikit-learn 1.8.0 (from `requirements.txt`). A pickle is only safe to load with the versions it was saved with. |
| 2026-09-27 | Setup | uv env | `uv sync` created `.venv` with the pinned libs (Python 3.13.14). |
| 2026-09-27 | Proof | Model loads | Unpickled as `TransformedTargetRegressor`. Its `feature_names_in_` match `feature_schema.json` exactly, in the same order. |
| 2026-09-27 | Finding | Inference data profile | 321 consumption rows, all `2026-05-01`, no nulls, no duplicate keys. 100 movies, every consumption id joins. 4 countries × 4 platforms. |
| 2026-09-27 | Finding | Traps | CSVs start with a BOM, so read with `utf-8-sig`. The key is named `imdb_id` in consumption and `TITLE_ID` in movies. |
| 2026-09-27 | Development | Docs | `docs/CHALLENGE.md` (condensed brief), `docs/CHECKLIST.md`, this log. |
| 2026-09-27 | Setup | Git | `git init`. The log was renamed to `AI_LOG.md` and changed to a 4-column table (the user asked for this). |
| 2026-09-27 | Decision | One core, two entrypoints | All logic lives in `src/pipeline.py`. A CLI (batch) and a UI (demo) both call it, packaged as one Docker image. The batch and the live app can't drift apart, and any Docker host can run it. |
| 2026-09-27 | Decision | Typer for the CLI | Typed functions and help text for free. It's worth the dependency once there's more than one command. |
| 2026-09-27 | Decision | Streamlit over FastAPI | The user wants a UI + ModelOps dashboard. Streamlit does both in plain Python. Downstream consumers read S3, so no HTTP API is needed. |
| 2026-09-27 | Decision | HF Spaces for live URL | Free Docker hosting, no server. Dokploy was considered but needs our own VPS. |
| 2026-09-27 | Decision | SageMaker Processing Job | Runs our script as-is on files. Batch Transform expects rows that are already prepared (we need a groupby + join first). Endpoints are real-time, which isn't needed. |
| 2026-09-27 | Decision | Trigger S3 → EventBridge → SageMaker Pipeline | Event-driven, no Lambda, and execution history comes for free. Open: can the month key be passed as a parameter? |
| 2026-09-27 | Decision | Custom ECR image | The pickle needs sklearn 1.8.0 + Py3.13, and the prebuilt images likely lag (to verify). The same image runs locally. |
| 2026-09-27 | Decision | Model in versioned S3, not in the image (AWS) | Swapping the model doesn't need a rebuild, and each run records which model version it used. HF baked-in copy is fine for the demo. |
| 2026-09-27 | Decision | Unmatched movie ID: keep + warn | The brief needs a prediction for every May combination. The model's imputers handle the missing metadata. |
| 2026-09-27 | Decision | Commit data + model to git | ~8 MB total. The repo is runnable straight after cloning. |
| 2026-09-27 | Decision | ModelOps without June actuals | Monitor data quality, drift vs training, and prediction health through `summary.json`. Performance (MAE/WAPE) only once actuals land. |
| 2026-09-27 | Development | Docs | Wrote `ARCHITECTURE.md` and `TOOLS.md`. `CHECKLIST.md` was re-planned by evaluation weight, with the showcase dashboard last. |
| 2026-09-27 | Setup | instructions/ folder | Original challenge files moved to `Sony_DS_test/instructions/` (untouched). The project uses its own copies. |
| 2026-09-27 | Development | Mermaid diagrams | Replaced the ASCII diagrams in `ARCHITECTURE.md` with Mermaid, which GitHub/VS Code/HF render. Not machine-rendered locally (no Node): syntax checked by hand, and they still need a visual check on GitHub. |
| 2026-09-27 | Finding | Model is a single-transition "t → t+1" model | No date feature, so it's mechanically monthless. But it was trained only on May → June 2026, so it only learned that transition. Other months are extrapolation (seasonality; movie age is hidden inside `release_year`). |
| 2026-09-27 | Decision | Month-agnostic pipeline | `--month` parameter (default `2026-05-01`). That month's numbers go into the model's `may_*` columns. Output keeps `predicted_june_streams` + adds `input_month`, `target_month`. Fails loudly if the month isn't in the file. Needed for the monthly AWS job without code changes. |
