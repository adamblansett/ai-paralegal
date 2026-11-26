variable "aws_region" {
  description = "AWS region for AI Paralegal managed infrastructure."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment label (staging, production)."
  type        = string
  default     = "production"
}

variable "name_prefix" {
  description = "Resource name prefix."
  type        = string
  default     = "ai-paralegal"
}

variable "vpc_id" {
  description = "Target VPC ID for RDS and ElastiCache subnets."
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnet IDs for PostgreSQL and Redis."
  type        = list(string)
}

variable "eks_cluster_cidr_blocks" {
  description = "CIDR blocks allowed to connect to PostgreSQL (5432) and Redis (6379)."
  type        = list(string)
}

variable "db_name" {
  description = "PostgreSQL database name."
  type        = string
  default     = "ai_paralegal"
}

variable "db_username" {
  description = "PostgreSQL admin/bootstrap username."
  type        = string
  default     = "paralegal_admin"
}

variable "db_password" {
  description = "PostgreSQL bootstrap password (provide via TF_VAR_db_password or Secrets Manager)."
  type        = string
  sensitive   = true
}

variable "db_instance_class" {
  description = "RDS PostgreSQL instance class."
  type        = string
  default     = "db.t4g.medium"
}

variable "redis_node_type" {
  description = "ElastiCache Redis node type."
  type        = string
  default     = "cache.t4g.small"
}

variable "deploy_helm_release" {
  description = "Set true when Kubernetes/Helm provider is configured to deploy the chart."
  type        = bool
  default     = false
}

variable "ingress_host" {
  description = "Public FQDN for the AI Paralegal Ingress."
  type        = string
  default     = "paralegal.example.com"
}
