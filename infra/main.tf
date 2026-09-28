# Monthly batch: S3 data/ drop -> EventBridge -> SageMaker Pipeline (Predict + Evaluate) -> S3 output/.
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
}

# ---------- Storage: one bucket, one prefix per role ----------
# s3://…/data/movies/YYYY-MM.csv                                    monthly metadata snapshot (trigger)
# s3://…/data/consumption/YYYY-MM.csv                               monthly consumption (trigger)
# s3://…/models/v1/model.pkl                                        model versions (+ feature_schema.json, drift_reference.json)
# s3://…/output/predictions/input_month=YYYY-MM-DD/predictions.csv
# s3://…/output/predictions/input_month=YYYY-MM-DD/summary.json
# s3://…/output/performance/YYYY-MM-DD.json                         accuracy of the predictions for that month

resource "aws_s3_bucket" "data" {
  bucket = local.bucket
}

resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id
  versioning_configuration { status = "Enabled" } # keeps every model / input version
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
  for_each = fileset("${path.module}/../models/v1", "*") # the repo's models/v1/, mirrored
  bucket   = aws_s3_bucket.data.id
  key      = "${local.prefix.models}/v1/${each.value}"
  source   = "${path.module}/../models/v1/${each.value}"
  etag     = filemd5("${path.module}/../models/v1/${each.value}")
}

# ---------- Image ----------

resource "aws_ecr_repository" "image" {
  name                 = var.name
  image_tag_mutability = "IMMUTABLE" # a tag (git SHA) always means the same code
  image_scanning_configuration { scan_on_push = true }
}

# ---------- SageMaker role: used by the pipeline and by its processing job ----------

resource "aws_iam_role" "sagemaker" {
  name = "${var.name}-sagemaker"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "sagemaker.amazonaws.com" } }]
  })
}

data "aws_iam_policy_document" "sagemaker" {
  statement {
    sid       = "ReadInputs"
    actions   = ["s3:GetObject"]
    resources = [for name in ["consumption", "movies", "models", "predictions"] : "${aws_s3_bucket.data.arn}/${local.prefix[name]}/*"]
  }
  statement {
    sid       = "ListInputs"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.data.arn]
  }
  statement {
    sid       = "WriteOutputs"
    actions   = ["s3:PutObject"]
    resources = [for name in ["predictions", "performance"] : "${aws_s3_bucket.data.arn}/${local.prefix[name]}/*"]
  }
  statement {
    sid       = "PullImage"
    actions   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
    resources = [aws_ecr_repository.image.arn]
  }
  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"] # this action has no resource-level scoping
  }
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
    resources = ["arn:aws:logs:${var.region}:${local.account}:log-group:/aws/sagemaker/*"]
  }
  statement {
    sid       = "RunProcessingJob" # the pipeline starts the job on our behalf
    actions   = ["sagemaker:CreateProcessingJob", "sagemaker:DescribeProcessingJob", "sagemaker:StopProcessingJob", "sagemaker:AddTags"]
    resources = ["arn:aws:sagemaker:${var.region}:${local.account}:processing-job/*"]
  }
  statement {
    sid       = "PassRoleToJob"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.sagemaker.arn]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["sagemaker.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "sagemaker" {
  role   = aws_iam_role.sagemaker.id
  policy = data.aws_iam_policy_document.sagemaker.json
}

# ---------- Pipeline: Predict (this month) + Evaluate (last month's predictions vs this month's actuals) ----------
# Both steps run the same image with a different CLI command, in parallel (no dependency between them).

locals {
  proc = "/opt/ml/processing"
  step_inputs = {                                           # name -> S3 URI (pipeline expressions allowed)
    consumption = "${local.s3}/${local.prefix.consumption}" # all months (small); the CLI picks the triggering month's file
    movies      = "${local.s3}/${local.prefix.movies}"
    model       = { Get = "Parameters.ModelUri" }
    predictions = "${local.s3}/${local.prefix.predictions}" # all past runs (small), evaluate picks the month it needs
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
    RoleArn             = aws_iam_role.sagemaker.arn
    ProcessingResources = { ClusterConfig = { InstanceType = var.instance_type, InstanceCount = 1, VolumeSizeInGB = 10 } }
    StoppingCondition   = { MaxRuntimeInSeconds = 1800 }
  }
}

resource "aws_s3_object" "placeholder" {
  for_each = toset(["consumption", "movies", "predictions"]) # a mounted input prefix must not be empty (first month)
  bucket   = aws_s3_bucket.data.id
  key      = "${local.prefix[each.key]}/README.txt" # not *.csv, so it doesn't trigger a run
  content  = "Monthly files for ${local.prefix[each.key]}/.\n"
}

resource "aws_sagemaker_pipeline" "forecast" {
  pipeline_name         = var.name
  pipeline_display_name = var.name
  role_arn              = aws_iam_role.sagemaker.arn

  pipeline_definition = jsonencode({
    Version = "2020-12-01"
    Parameters = [
      { Name = "InputKey", Type = "String", DefaultValue = "data/consumption/2026-05.csv" }, # set by EventBridge
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
              "--output-dir", "${local.proc}/predictions",      # -> input_month=YYYY-MM-DD/, reruns overwrite
              "--trigger-key", { Get = "Parameters.InputKey" }, # waits (exit 0) until both monthly files exist
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
              "--actuals", "${local.input}/consumption", # this month's file = actuals for last month
              "--predictions", "${local.input}/predictions",
              "--history-dir", "${local.proc}/performance", # -> output/performance/YYYY-MM-DD.json
              "--trigger-key", { Get = "Parameters.InputKey" },
            ]
          }
          ProcessingInputs       = [for name in ["consumption", "predictions"] : local.processing_input[name]]
          ProcessingOutputConfig = { Outputs = [local.processing_output["performance"]] }
        })
      },
    ]
  })
}

# ---------- Trigger: either monthly file starts the pipeline; it runs once both exist ----------

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
      name  = "InputKey"
      value = "$.detail.object.key" # JSON path, resolved from the S3 event
    }
  }
}

# ---------- Alerting: pipeline failed -> SNS (email) ----------

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
      currentPipelineExecutionStatus = ["Failed"]
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
