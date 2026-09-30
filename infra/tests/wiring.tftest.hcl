# `terraform test`: runs against a mocked AWS provider (nothing is created, no account needed) and checks the wiring.

mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  # computed ARNs must look like ARNs to pass the provider's validation
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/mock" }
  }
  mock_resource "aws_s3_bucket" {
    defaults = { arn = "arn:aws:s3:::mock" }
  }
  mock_resource "aws_ecr_repository" {
    defaults = { arn = "arn:aws:ecr:eu-west-1:123456789012:repository/mock", repository_url = "123456789012.dkr.ecr.eu-west-1.amazonaws.com/movie-streams-forecast" }
  }
  mock_resource "aws_sagemaker_pipeline" {
    defaults = { arn = "arn:aws:sagemaker:eu-west-1:123456789012:pipeline/mock" }
  }
  mock_resource "aws_sns_topic" {
    defaults = { arn = "arn:aws:sns:eu-west-1:123456789012:mock" }
  }
  mock_resource "aws_cloudwatch_event_rule" {
    defaults = { arn = "arn:aws:events:eu-west-1:123456789012:rule/mock" }
  }
}

variables {
  image_tag = "abc123"
}

run "wiring" {
  command = apply # mocked: computed values (ARNs) get fake values

  assert {
    condition     = aws_s3_bucket.data.bucket == "movie-streams-forecast-123456789012"
    error_message = "bucket name should be <name>-<account>"
  }

  assert {
    condition     = jsondecode(aws_cloudwatch_event_rule.new_monthly_file.event_pattern).detail.object.key == [{ wildcard = "data/consumption/*.csv" }, { wildcard = "data/movies/*.csv" }]
    error_message = "only the two monthly CSVs (data/consumption/, data/movies/) should trigger the pipeline"
  }

  assert {
    condition     = one(one(aws_cloudwatch_event_target.start_pipeline.sagemaker_pipeline_target).pipeline_parameter_list).value == "$.detail.object.key"
    error_message = "the uploaded key must be passed to the pipeline as TriggerKey"
  }

  assert {
    condition     = [for step in jsondecode(aws_sagemaker_pipeline.forecast.pipeline_definition).Steps : step.Arguments.AppSpecification.ContainerArguments[0]] == ["predict", "evaluate"]
    error_message = "the pipeline should run predict and evaluate"
  }

  assert {
    condition = jsondecode(aws_sagemaker_pipeline.forecast.pipeline_definition).Steps[0].Arguments.AppSpecification.ContainerArguments == [
      "predict",
      "--consumption", "/opt/ml/processing/input/consumption",
      "--movies", "/opt/ml/processing/input/movies",
      "--model", "/opt/ml/processing/input/model",
      "--output-dir", "/opt/ml/processing/predictions",
      "--trigger-key", { Get = "Parameters.TriggerKey" },
    ]
    error_message = "predict must run the same CLI command as tested locally in Docker"
  }

  assert {
    condition = alltrue([
      for step in jsondecode(aws_sagemaker_pipeline.forecast.pipeline_definition).Steps : alltrue([
        for arg in step.Arguments.AppSpecification.ContainerArguments : contains(concat(
          [for i in step.Arguments.ProcessingInputs : i.S3Input.LocalPath],
          [for o in step.Arguments.ProcessingOutputConfig.Outputs : o.S3Output.LocalPath],
        ), arg) if try(startswith(arg, "/opt/ml/processing/"), false)
      ])
    ])
    error_message = "every path a step's command uses must be mounted as an input or output of that step"
  }

  assert {
    condition     = { for i in jsondecode(aws_sagemaker_pipeline.forecast.pipeline_definition).Steps[0].Arguments.ProcessingInputs : i.InputName => i.S3Input.S3Uri if i.InputName != "model" } == { consumption = "s3://movie-streams-forecast-123456789012/data/consumption", movies = "s3://movie-streams-forecast-123456789012/data/movies" }
    error_message = "predict must mount both monthly folders (it picks the triggering month's files)"
  }

  assert {
    condition     = alltrue([for step in jsondecode(aws_sagemaker_pipeline.forecast.pipeline_definition).Steps : contains(step.Arguments.AppSpecification.ContainerArguments, "--trigger-key")])
    error_message = "both steps must get the triggering key, so they wait until both monthly files exist"
  }

  assert {
    condition     = jsondecode(aws_sagemaker_pipeline.forecast.pipeline_definition).Steps[1].Arguments.ProcessingOutputConfig.Outputs[0].S3Output.S3Uri == "s3://movie-streams-forecast-123456789012/output/performance"
    error_message = "evaluations must land in output/performance/"
  }

  assert {
    condition     = endswith(jsondecode(aws_sagemaker_pipeline.forecast.pipeline_definition).Steps[0].Arguments.AppSpecification.ImageUri, ":abc123")
    error_message = "the pipeline must run the image tag passed in"
  }
}
