# Monthly batch: S3 data/ upload -> EventBridge -> SageMaker Pipeline (Predict + Evaluate) -> S3 output/.
# See docs/ARCHITECTURE.md for the process and reasoning.

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { project = var.name }
  }
}

data "aws_caller_identity" "current" {}

locals {
  account = data.aws_caller_identity.current.account_id
  bucket  = "${var.name}-${local.account}" # bucket names are global
  s3      = "s3://${local.bucket}"
  image   = "${aws_ecr_repository.image.repository_url}:${var.image_tag}"
  input   = "/opt/ml/processing/input"
  # S3 prefixes: the same layout as the repo (data/, models/, output/)
  prefix = {
    consumption = "data/consumption"
    movies      = "data/movies"
    models      = "models"
    predictions = "output/predictions"
    performance = "output/performance"
  }
  sagemaker_trust = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "sagemaker.amazonaws.com" } }]
  })
}

# ---------- Storage: one versioned bucket; overwritten outputs stay recoverable as old versions ----------

resource "aws_s3_bucket" "data" {
  bucket = local.bucket
}

resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    id     = "expire-old-versions" # versioning would otherwise keep every overwritten object forever
    status = "Enabled"
    filter {}
    noncurrent_version_expiration { noncurrent_days = 365 }
  }
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_notification" "data" {
  bucket      = aws_s3_bucket.data.id
  eventbridge = true # every object event goes to EventBridge; the rule below filters
}

resource "aws_s3_object" "model_v1" {
  for_each = fileset("${path.module}/../models/v1", "*") # the supplied model, so a fresh apply can run
  bucket   = aws_s3_bucket.data.id
  key      = "${local.prefix.models}/v1/${each.value}"
  source   = "${path.module}/../models/v1/${each.value}"
  etag     = filemd5("${path.module}/../models/v1/${each.value}")
}

resource "aws_s3_object" "placeholder" {
  for_each = toset(["consumption", "movies", "predictions"]) # a mounted input prefix must not be empty (first month)
  bucket   = aws_s3_bucket.data.id
  key      = "${local.prefix[each.key]}/README.txt" # not *.csv, so it doesn't trigger a run
  content  = "Monthly files for ${local.prefix[each.key]}/.\n"
}

# ---------- Image: immutable tags (git SHA), last 20 kept ----------

resource "aws_ecr_repository" "image" {
  name                 = var.name
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
}

resource "aws_ecr_lifecycle_policy" "image" {
  repository = aws_ecr_repository.image.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "keep the last 20 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 20 }
      action       = { type = "expire" }
    }]
  })
}

# ---------- IAM: the pipeline role only starts jobs; the job role only touches data ----------

resource "aws_iam_role" "pipeline" {
  name               = "${var.name}-pipeline"
  assume_role_policy = local.sagemaker_trust
}

resource "aws_iam_role_policy" "pipeline" {
  role = aws_iam_role.pipeline.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["sagemaker:CreateProcessingJob", "sagemaker:DescribeProcessingJob", "sagemaker:StopProcessingJob", "sagemaker:AddTags"]
        Resource = "arn:aws:sagemaker:${var.region}:${local.account}:processing-job/*"
      },
      {
        Effect    = "Allow"
        Action    = "iam:PassRole"
        Resource  = aws_iam_role.job.arn
        Condition = { StringEquals = { "iam:PassedToService" = "sagemaker.amazonaws.com" } }
      },
    ]
  })
}

resource "aws_iam_role" "job" {
  name               = "${var.name}-job"
  assume_role_policy = local.sagemaker_trust
}

resource "aws_iam_role_policy" "job" {
  role = aws_iam_role.job.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "s3:GetObject"
        Resource = [for name in ["consumption", "movies", "models", "predictions"] : "${aws_s3_bucket.data.arn}/${local.prefix[name]}/*"]
      },
      { Effect = "Allow", Action = "s3:ListBucket", Resource = aws_s3_bucket.data.arn },
      {
        Effect   = "Allow"
        Action   = "s3:PutObject"
        Resource = [for name in ["predictions", "performance"] : "${aws_s3_bucket.data.arn}/${local.prefix[name]}/*"]
      },
      { Effect = "Allow", Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"], Resource = aws_ecr_repository.image.arn },
      { Effect = "Allow", Action = "ecr:GetAuthorizationToken", Resource = "*" }, # no resource-level scoping exists
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
        Resource = "arn:aws:logs:${var.region}:${local.account}:log-group:/aws/sagemaker/*"
      },
    ]
  })
}

# ---------- Pipeline: two parallel steps, same image, triggered by a file of month t ----------
#   Predict:  month t inputs (consumption + movies)             -> output/predictions/input_month=t/
#   Evaluate: predictions that target t vs month t consumption  -> output/performance/t.json
# TriggerKey is the uploaded S3 key (data/{consumption,movies}/YYYY-MM.csv); the CLI reads the month from its name.

locals {
  proc = "/opt/ml/processing"
  step_inputs = {                                           # name -> S3 URI (pipeline expressions allowed)
    consumption = "${local.s3}/${local.prefix.consumption}" # whole prefix (small); the CLI picks month t's file
    movies      = "${local.s3}/${local.prefix.movies}"
    model       = { Get = "Parameters.ModelUri" }
    predictions = "${local.s3}/${local.prefix.predictions}" # whole prefix (small); Evaluate picks those targeting t
  }
  processing_input = { for name, uri in local.step_inputs : name => {
    InputName = name
    S3Input = {
      S3Uri                  = uri
      LocalPath              = "${local.input}/${name}"
      S3DataType             = "S3Prefix"
      S3InputMode            = "File"
      S3DataDistributionType = "FullyReplicated"
    }
  } }
  processing_output = { for name in ["predictions", "performance"] : name => {
    OutputName = name
    S3Output   = { S3Uri = "${local.s3}/${local.prefix[name]}", LocalPath = "${local.proc}/${name}", S3UploadMode = "EndOfJob" }
  } }
  step_common = {
    RoleArn             = aws_iam_role.job.arn
    ProcessingResources = { ClusterConfig = { InstanceType = var.instance_type, InstanceCount = 1, VolumeSizeInGB = 10 } }
    StoppingCondition   = { MaxRuntimeInSeconds = 1800 }
  }
}

resource "aws_sagemaker_pipeline" "forecast" {
  pipeline_name         = var.name
  pipeline_display_name = var.name
  role_arn              = aws_iam_role.pipeline.arn

  pipeline_definition = jsonencode({
    Version = "2020-12-01"
    Parameters = [
      { Name = "TriggerKey", Type = "String", DefaultValue = "data/consumption/2026-05.csv" }, # set by EventBridge
      { Name = "ModelUri", Type = "String", DefaultValue = "${local.s3}/${aws_s3_object.model_v1["model.pkl"].key}" },
    ]
    Steps = [
      {
        Name = "Predict"
        Type = "Processing"
        Arguments = merge(local.step_common, {
          AppSpecification = {
            ImageUri = local.image # ENTRYPOINT python -m src.cli
            ContainerArguments = [
              "predict",
              "--consumption", "${local.input}/consumption",
              "--movies", "${local.input}/movies",
              "--model", "${local.input}/model",
              "--output-dir", "${local.proc}/predictions",
              "--trigger-key", { Get = "Parameters.TriggerKey" }, # exits 0 ("waiting") until both files of month t exist
            ]
          }
          ProcessingInputs       = [for name in ["consumption", "movies", "model"] : local.processing_input[name]]
          ProcessingOutputConfig = { Outputs = [local.processing_output["predictions"]] }
        })
      },
      {
        Name = "Evaluate"
        Type = "Processing"
        Arguments = merge(local.step_common, {
          AppSpecification = {
            ImageUri = local.image
            ContainerArguments = [
              "evaluate",
              "--actuals", "${local.input}/consumption",
              "--predictions", "${local.input}/predictions",
              "--history-dir", "${local.proc}/performance",
              "--trigger-key", { Get = "Parameters.TriggerKey" },
            ]
          }
          ProcessingInputs       = [for name in ["consumption", "predictions"] : local.processing_input[name]]
          ProcessingOutputConfig = { Outputs = [local.processing_output["performance"]] }
        })
      },
    ]
  })
}

# ---------- Trigger: each monthly file starts an execution (two per month). The first one finds the ----------
# ---------- other file missing and exits 0; the second does the work. No dedup: reruns overwrite.   ----------

resource "aws_iam_role" "events" {
  name = "${var.name}-events"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "events.amazonaws.com" } }]
  })
}

resource "aws_iam_role_policy" "events" {
  role = aws_iam_role.events.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sagemaker:StartPipelineExecution", Resource = aws_sagemaker_pipeline.forecast.arn }]
  })
}

resource "aws_cloudwatch_event_rule" "new_monthly_file" {
  name = "${var.name}-new-monthly-file"
  event_pattern = jsonencode({
    source        = ["aws.s3"]
    "detail-type" = ["Object Created"]
    detail = {
      bucket = { name = [aws_s3_bucket.data.id] }
      object = { key = [{ wildcard = "${local.prefix.consumption}/*.csv" }, { wildcard = "${local.prefix.movies}/*.csv" }] }
    }
  })
}

resource "aws_cloudwatch_event_target" "start_pipeline" {
  rule     = aws_cloudwatch_event_rule.new_monthly_file.name
  arn      = aws_sagemaker_pipeline.forecast.arn
  role_arn = aws_iam_role.events.arn
  sagemaker_pipeline_target {
    pipeline_parameter_list {
      name  = "TriggerKey"
      value = "$.detail.object.key" # JSON path into the S3 event (no transforms possible, hence the key, not the month)
    }
  }
}

# ---------- Alerting: execution Failed or Stopped -> SNS (email) ----------

resource "aws_sns_topic" "alerts" {
  name = "${var.name}-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alert_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email" # the recipient must confirm the subscription email
  endpoint  = var.alert_email
}

resource "aws_cloudwatch_event_rule" "pipeline_failed" {
  name = "${var.name}-pipeline-failed"
  event_pattern = jsonencode({
    source        = ["aws.sagemaker"]
    "detail-type" = ["SageMaker Model Building Pipeline Execution Status Change"]
    detail = {
      pipelineArn                    = [aws_sagemaker_pipeline.forecast.arn]
      currentPipelineExecutionStatus = ["Failed", "Stopped"]
    }
  })
}

resource "aws_cloudwatch_event_target" "alert" {
  rule = aws_cloudwatch_event_rule.pipeline_failed.name
  arn  = aws_sns_topic.alerts.arn
}

resource "aws_sns_topic_policy" "alerts" {
  arn = aws_sns_topic.alerts.arn
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sns:Publish"
      Resource  = aws_sns_topic.alerts.arn
      Condition = { ArnEquals = { "aws:SourceArn" = aws_cloudwatch_event_rule.pipeline_failed.arn } }
    }]
  })
}
