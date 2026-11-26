output "kms_key_arn" {
  description = "ARN of the customer-managed KMS key encrypting S3, RDS, and Redis."
  value       = aws_kms_key.legal_vault.arn
}

output "s3_bucket_name" {
  description = "S3 document vault bucket name."
  value       = aws_s3_bucket.documents.id
}

output "postgres_endpoint" {
  description = "Managed PostgreSQL endpoint."
  value       = aws_db_instance.postgres.endpoint
}

output "redis_primary_endpoint" {
  description = "TLS-enabled ElastiCache Redis primary endpoint."
  value       = aws_elasticache_replication_group.redis.primary_endpoint_address
}

output "storage_kms_iam_policy_arn" {
  description = "Least-privilege IAM policy ARN for Kubernetes service account IRSA binding."
  value       = aws_iam_policy.app_storage_kms.arn
}
