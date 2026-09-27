output "bucket" {
  value = aws_s3_bucket.data.id
}

output "ecr_repository_url" {
  value = aws_ecr_repository.image.repository_url
}

output "pipeline_arn" {
  value = aws_sagemaker_pipeline.forecast.arn
}

output "alerts_topic_arn" {
  value = aws_sns_topic.alerts.arn
}
