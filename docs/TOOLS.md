# Tools

| Tool | Used for | Why this one |
|---|---|---|
| **uv** | Env + dependencies | Fast, lockfile (`uv.lock`) for reproducibility. Same as our other projects. |
| **Python 3.13** | Runtime | The model was pickled with 3.13.5. |
| **pandas 2.2.3 / numpy 2.3.5 / scikit-learn 1.8.0** | Data prep + model | Pinned to the versions the pickle was saved with. |
| **Typer** | CLI | Two commands (`check`, `predict`): typed functions, help text for free. |
| **Streamlit** | UI + ModelOps dashboard | Upload, table, charts, download in plain Python. Runs natively on HF Spaces. |
| **pytest** | Tests | Standard, minimal boilerplate. |
| **Docker** | Packaging | One image for local, SageMaker and HF Spaces. |
| **Terraform** | AWS infra as code | Required by the brief. |
| **AWS**: S3, ECR, SageMaker (Pipeline + Processing), EventBridge, IAM, CloudWatch, SNS | Monthly batch | See `ARCHITECTURE.md`. |
| **Hugging Face Spaces** | Live URL | Free Docker hosting. No server to manage. |
| **Claude Code** | AI pair programmer | Decisions and proofs recorded in `AI_LOG.md`. |

Not used, on purpose: FastAPI (no HTTP consumer, since downstream reads S3), Batch Transform / endpoints (see architecture), MLflow (only one fixed model, S3 versioning is enough).
