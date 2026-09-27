# Challenge — condensed brief

Source: `../instructions/` (original .docx, notebook, data, model; untouched). Time box ~6h.

## Goal
Use the **supplied** model (no retraining, no refitting encoders) to predict **June 2026 streams** per
**TITLE_ID × country × platform**, from May 2026 consumption + movie metadata.

## Deliverables
1. Python inference solution (CLI is fine), runnable locally → `predictions.csv`
   with columns `TITLE_ID, country, platform, predicted_june_streams` (extra cols allowed).
2. README: setup + run instructions.
3. AWS/SageMaker architecture (monthly batch, no real-time) + reasoning.
4. Terraform for the main infra (no apply needed; document what's unverified).
5. Limitations, improvements, AI tools used. No secrets.
Bonus: error handling, tests, logging, reproducibility. Optional: deploy / tiny UI.

## Pipeline to reproduce (from notebook)
1. Read CSVs with `encoding="utf-8-sig"` (files have a BOM); ids as `string`.
2. Movies: rename `YEAR→release_year, RUNTIME_MINUTES→runtime_minutes, PRIMARY_GENRE→primary_genre,
   RATING_VALUE→rating_value, RATING_VOTE_COUNT→rating_vote_count`.
3. Consumption: rename `imdb_id→TITLE_ID`; parse `month` to `YYYY-MM-DD`; `streams`, `total_minutes` numeric.
4. Filter `month == 2026-05-01`; groupby `[TITLE_ID, country, platform]` →
   `may_streams = sum(streams)`, `may_total_minutes = sum(total_minutes)`.
5. Left-join movie attributes on `TITLE_ID` (`validate="many_to_one"`) — grain must not change.
6. Features in order (see `artifacts/feature_schema.json`):
   `country, platform, primary_genre, may_streams, may_total_minutes, release_year, runtime_minutes, rating_value, rating_vote_count`
7. `model.predict(X)` → already back in stream units (model applies log1p/expm1 internally).

## Rules / traps
- `TITLE_ID` is a key, **never** a feature.
- **All** May combinations get a prediction; never use June to pick rows (inference has no June anyway).
- Don't touch training data at inference time.
- Model = `TransformedTargetRegressor(Pipeline(ColumnTransformer → RandomForest))`; pickled with
  Python 3.13.5 + scikit-learn 1.8.0 → pin those.
- Imputers inside the model handle NaNs; unknown categories are ignored by the OneHotEncoder.

## Inference data facts (profiled 2026-09-27)
- 321 consumption rows, all May; no nulls; no duplicate grain rows; 100 movies, all ids match.
- Countries: Argentina, Brazil, Colombia, Mexico. Platforms: Amazon, Disney+, HBO Max, Netflix.

## Month handling (our decision)
The model is `t → t+1` trained on one pair (May → June 2026). The pipeline takes `--month` (default `2026-05-01`),
maps that month into the model's `may_*` features, and outputs `input_month` / `target_month` next to `predicted_june_streams`.
