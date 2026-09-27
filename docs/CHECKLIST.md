# Checklist

Ordered by what the brief evaluates: correctness → engineering → AWS → Terraform. Extras last.

## 0 · Setup
- [x] Project scaffold (uv, Python 3.13, pinned deps, data + artifacts copied)
- [x] Docs: `CHALLENGE.md`, `ARCHITECTURE.md`, `TOOLS.md`, `AI_LOG.md`
- [x] git init

## 1 · Inference (must)
- [x] `src/pipeline.py` (month-agnostic, `--month`): validate → aggregate month → join → features → predict (+ summary)
- [ ] `src/cli.py`: Typer `predict` → `output/predictions.csv` + `summary.json`
- [ ] Logging + clear errors on bad input
- [x] **Proof:** pipeline on training files rebuilds the notebook table (2005 rows, same features) (manual check done; also goes into tests)
- [ ] Tests: grain, required columns, no June used, unmatched movie still predicted, bad input fails
- [ ] Generate `predictions.csv` (deliverable)

## 2 · Packaging (must)
- [ ] `Dockerfile` (uv, pinned), runs `predict` locally
- [ ] README: setup, run (uv + Docker), output

## 3 · AWS (must)
- [ ] Finalize `ARCHITECTURE.md` (resolve open points)
- [ ] `infra/` Terraform: S3, ECR, IAM roles, SageMaker Pipeline, EventBridge rule, SNS alarm
- [ ] `terraform validate` + notes on what's unverified

## 4 · Wrap-up (must)
- [ ] Limitations (incl. single-transition model: retrain on many month pairs + month-of-year + movie age), improvements (monthly retrain step + registry gate, see ARCHITECTURE), AI usage
- [ ] Final review + package (repo / zip)

## 5 · Showcase (after the musts)
- [ ] Streamlit app: upload or sample data → predictions table + download
- [ ] ModelOps dashboard: data quality, drift vs training, prediction distribution
- [ ] Deploy to HF Spaces, add the live URL to README
