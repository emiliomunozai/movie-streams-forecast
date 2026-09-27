# Infra (Terraform)

The AWS side of [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md): a monthly file under `consumption/` → EventBridge →
SageMaker Pipeline (one Processing Job running our image) → `predictions/<execution-id>/`. A failed run → SNS email.

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
- only uploads under `consumption/` trigger the pipeline
- the uploaded key is passed through as `InputKey`
- the job runs exactly the CLI command we tested in Docker
- each input is mounted where that command expects it
- the requested image tag is used

## Deploy (with an AWS account)

```bash
# 1. infrastructure (also uploads the model as models/v1/model.pkl)
TAG=$(git rev-parse --short HEAD)
terraform apply -var image_tag=$TAG -var alert_email=you@example.com

# 2. image: SageMaker runs linux/amd64
REPO=$(terraform output -raw ecr_repository_url)
aws ecr get-login-password | docker login --username AWS --password-stdin ${REPO%%/*}
docker build --platform linux/amd64 -t $REPO:$TAG .. && docker push $REPO:$TAG

# 3. data: catalog once, then a consumption file every month (this upload triggers a run)
BUCKET=$(terraform output -raw bucket)
aws s3 cp ../data/inference_movies.csv s3://$BUCKET/reference/movies.csv
aws s3 cp ../data/inference_consumption.csv s3://$BUCKET/consumption/2026-05.csv

# 4. result
aws s3 ls s3://$BUCKET/predictions/ --recursive
```

**Update the model:** upload `models/v2/model.pkl`, then change the pipeline's `ModelUri` default, or pass it on a manual run. No image rebuild.
**Update the code:** push a new image tag, then `terraform apply -var image_tag=<new>`.

## Assumptions

- Credentials come from the environment (`AWS_PROFILE` etc.), and nothing is hardcoded. State is local; a team would add an S3 backend.
- One account and region (`eu-west-1` default). No VPC: the job only talks to S3/ECR/CloudWatch over AWS endpoints.
- Each monthly file contains one month (the container infers it). The movie catalog at `reference/movies.csv` covers the month's titles; missing ones are predicted with imputed attributes and reported as warnings.
- The default S3-managed encryption is enough (no KMS key).

## Not verified without an account

- **AWS-side acceptance:** that AWS accepts the pipeline definition JSON as written, and that the IAM policies are complete. These are the main unknowns.
- **The event path:** S3 "Object Created" events actually reaching the rule with `eventbridge = true`, and the pipeline-status event field names used by the failure rule.

How to close these: `terraform plan`/`apply` in a sandbox account, upload one file, and check that the run succeeds and `predictions/` appears. Then upload a broken file and confirm the alert email arrives.

Also not included on purpose: the log group retention for `/aws/sagemaker/ProcessingJobs` (shared by every job in the account, so it's usually managed centrally), a CI pipeline for build/push, and dev/prod environments.
