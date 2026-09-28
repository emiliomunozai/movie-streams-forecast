# Infra (Terraform)

The AWS side of [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md): two monthly files, `s3://…/data/movies/YYYY-MM.csv` and `s3://…/data/consumption/YYYY-MM.csv`, in any order. Each upload → EventBridge →
SageMaker Pipeline; the run for the first file waits, the second runs two parallel Processing Jobs with our image: **Predict** → `s3://…/output/predictions/input_month=YYYY-MM-DD/predictions.csv` and `s3://…/output/predictions/input_month=YYYY-MM-DD/summary.json`, and **Evaluate** (last month's predictions vs this month's consumption) → `s3://…/output/performance/YYYY-MM-DD.json`. A failed run → SNS email. Every path: [`ARCHITECTURE.md`](../docs/ARCHITECTURE.md#every-path-locally-and-in-s3).

| File | What |
|---|---|
| `main.tf` | S3 bucket, ECR, 2 IAM roles, SageMaker Pipeline, EventBridge trigger, SNS alerting |
| `variables.tf` | `image_tag` (required), `region`, `name`, `instance_type`, `alert_email` |
| `outputs.tf` | bucket, ECR URL, pipeline ARN, alerts topic |
| `tests/wiring.tftest.hcl` | `terraform test` against a mocked AWS provider |

## Check it without AWS

```bash
cd infra
terraform init -backend=false
terraform fmt -check -recursive && terraform validate && terraform test
```

`terraform test` creates nothing. It runs the config against a fake AWS provider and asserts that:
- only the two monthly CSVs (`data/consumption/*.csv`, `data/movies/*.csv`) trigger the pipeline
- the uploaded key is passed through as `InputKey`, and both steps get it (`--trigger-key`)
- predict mounts both monthly folders
- the pipeline runs predict and evaluate, and predict runs exactly the CLI command we tested in Docker
- every path a step's command uses is mounted as an input or output of that step
- evaluations land in `s3://…/output/performance/`
- the requested image tag is used

## Deploy (with an AWS account)

```bash
# 1. infrastructure (also mirrors the repo's models/v1/ to s3://$BUCKET/models/v1/)
TAG=$(git rev-parse --short HEAD)
terraform apply -var image_tag=$TAG -var alert_email=you@example.com

# 2. image: SageMaker runs linux/amd64
REPO=$(terraform output -raw ecr_repository_url)
aws ecr get-login-password | docker login --username AWS --password-stdin ${REPO%%/*}
docker build --platform linux/amd64 -t $REPO:$TAG .. && docker push $REPO:$TAG

# 3. data, every month, in any order (same paths as the repo): each upload triggers a run; the first one waits
BUCKET=$(terraform output -raw bucket)
aws s3 cp ../data/movies/2026-05.csv s3://$BUCKET/data/movies/2026-05.csv
aws s3 cp ../data/consumption/2026-05.csv s3://$BUCKET/data/consumption/2026-05.csv

# 4. result
aws s3 ls s3://$BUCKET/output/predictions/ --recursive
#   s3://$BUCKET/output/predictions/input_month=2026-05-01/predictions.csv
#   s3://$BUCKET/output/predictions/input_month=2026-05-01/summary.json
# next month's files also write s3://$BUCKET/output/performance/2026-06-01.json (accuracy of these predictions)
```

**Update the model:** upload `s3://…/models/v2/model.pkl`, then change the pipeline's `ModelUri` default, or pass it on a manual run. No image rebuild.
**Update the code:** push a new image tag, then `terraform apply -var image_tag=<new>`.

## Assumptions

- Credentials come from the environment (`AWS_PROFILE` etc.), and nothing is hardcoded. State is local; a team would add an S3 backend.
- One account and region (`eu-west-1` default). No VPC: the job only talks to S3/ECR/CloudWatch over AWS endpoints.
- Both monthly files are named `YYYY-MM.csv` (`s3://…/data/movies/YYYY-MM.csv`, `s3://…/data/consumption/YYYY-MM.csv`); the month comes from that name, and the consumption file must hold that month (checked). They can arrive in any order. Titles missing from the snapshot are predicted with imputed attributes and reported as warnings.
- The default S3-managed encryption is enough (no KMS key).

## Not verified without an account

- **AWS-side acceptance:** that AWS accepts the pipeline definition JSON as written, and that the IAM policies are complete. These are the main unknowns.
- **The event path:** S3 "Object Created" events actually reaching the rule with `eventbridge = true`, and the pipeline-status event field names used by the failure rule.
- **The empty-prefix case:** that the `README.txt` placeholders in `s3://…/data/consumption/`, `s3://…/data/movies/` and `s3://…/output/predictions/` are enough for the mounted inputs on the first month.
- **The parameter in the container arguments:** that `{ Get = "Parameters.InputKey" }` inside `ContainerArguments` is resolved at run time (the SageMaker Python SDK allows parameters in job arguments, which serialize to this form).
- **The wildcard pattern:** that the S3 event key matches `{"wildcard": "consumption/*.csv"}` as expected (EventBridge supports wildcard matching).

How to close these: `terraform plan`/`apply` in a sandbox account, upload the movies file (the run should end with "waiting"), then the consumption file, and check that `s3://…/output/predictions/input_month=2026-05-01/predictions.csv` appears. Then upload a broken file and confirm the alert email arrives.

Also not included on purpose: the log group retention for `/aws/sagemaker/ProcessingJobs` (shared by every job in the account, so it's usually managed centrally), a CI pipeline for build/push, and dev/prod environments.
