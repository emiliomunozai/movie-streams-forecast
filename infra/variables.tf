variable "name" {
  description = "Prefix for every resource name."
  type        = string
  default     = "movie-streams-forecast"
}

variable "region" {
  type    = string
  default = "eu-west-1"
}

variable "image_tag" {
  description = "Image tag in ECR to run (the git SHA pushed by the build step)."
  type        = string
}

variable "instance_type" {
  description = "Processing instance. The monthly file is a few hundred rows."
  type        = string
  default     = "ml.t3.medium"
}

variable "alert_email" {
  description = "Email notified when a pipeline run fails. Empty = no subscription."
  type        = string
  default     = ""
}
