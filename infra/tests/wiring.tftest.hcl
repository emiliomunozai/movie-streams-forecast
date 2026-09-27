# `terraform test`: runs against a mocked AWS provider (nothing is created, no account needed) and checks the wiring.

mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
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
    condition     = jsondecode(aws_cloudwatch_event_rule.new_consumption.event_pattern).detail.object.key[0].prefix == "consumption/"
    error_message = "only uploads under consumption/ should trigger the pipeline"
  }

  assert {
    condition     = one(one(aws_cloudwatch_event_target.start_pipeline.sagemaker_pipeline_target).pipeline_parameter_list).value == "$.detail.object.key"
    error_message = "the uploaded key must be passed to the pipeline as InputKey"
  }

  assert {
    condition = jsondecode(aws_sagemaker_pipeline.forecast.pipeline_definition).Steps[0].Arguments.AppSpecification.ContainerArguments == [
      "predict",
      "--consumption", "/opt/ml/processing/input/consumption",
      "--movies", "/opt/ml/processing/input/movies",
      "--model", "/opt/ml/processing/input/model",
      "--output-dir", "/opt/ml/processing/output",
    ]
    error_message = "the job must run the same CLI command as tested locally in Docker"
  }

  assert {
    condition     = [for i in jsondecode(aws_sagemaker_pipeline.forecast.pipeline_definition).Steps[0].Arguments.ProcessingInputs : i.S3Input.LocalPath] == ["/opt/ml/processing/input/consumption", "/opt/ml/processing/input/model", "/opt/ml/processing/input/movies"]
    error_message = "each input must be mounted where the CLI arguments point"
  }

  assert {
    condition     = endswith(jsondecode(aws_sagemaker_pipeline.forecast.pipeline_definition).Steps[0].Arguments.AppSpecification.ImageUri, ":abc123")
    error_message = "the pipeline must run the image tag passed in"
  }
}
