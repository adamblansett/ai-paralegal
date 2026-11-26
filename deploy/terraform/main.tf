data "aws_caller_identity" "current" {}

# 1. Customer-Managed KMS Key for S3 Legal Vault, RDS, and Redis Encryption
resource "aws_kms_key" "legal_vault" {
  description             = "${var.name_prefix}-${var.environment} legal document encryption key"
  deletion_window_in_days = 30
  enable_key_rotation     = true
}

resource "aws_kms_alias" "legal_vault" {
  name          = "alias/${var.name_prefix}-${var.environment}-vault"
  target_key_id = aws_kms_key.legal_vault.key_id
}

# 2. SSE-KMS Encrypted, Versioned, Public-Blocked S3 Bucket for Legal Documents
resource "aws_s3_bucket" "documents" {
  bucket = "${var.name_prefix}-${var.environment}-vault-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_versioning" "documents" {
  bucket = aws_s3_bucket.documents.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.legal_vault.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "documents" {
  bucket                  = aws_s3_bucket.documents.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# 3. Managed PostgreSQL 16 (supports pgvector + Row-Level Security)
resource "aws_db_subnet_group" "postgres" {
  name       = "${var.name_prefix}-${var.environment}-pg-subnets"
  subnet_ids = var.private_subnet_ids
}

resource "aws_security_group" "postgres" {
  name        = "${var.name_prefix}-${var.environment}-pg-sg"
  description = "Allow PostgreSQL access only from EKS worker nodes"
  vpc_id      = var.vpc_id

  ingress {
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = var.eks_cluster_cidr_blocks
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_db_instance" "postgres" {
  identifier              = "${var.name_prefix}-${var.environment}-pg"
  engine                  = "postgres"
  engine_version          = "16.4"
  instance_class          = var.db_instance_class
  allocated_storage       = 50
  max_allocated_storage   = 500
  db_name                 = var.db_name
  username                = var.db_username
  password                = var.db_password
  db_subnet_group_name    = aws_db_subnet_group.postgres.name
  vpc_security_group_ids  = [aws_security_group.postgres.id]
  storage_encrypted       = true
  kms_key_id              = aws_kms_key.legal_vault.arn
  backup_retention_period = 14
  deletion_protection     = var.environment == "production"
  skip_final_snapshot     = var.environment != "production"
}

# 4. TLS-Enabled Redis Replication Group (Rate Limits, SSE Fanout & Celery)
resource "aws_elasticache_subnet_group" "redis" {
  name       = "${var.name_prefix}-${var.environment}-redis-subnets"
  subnet_ids = var.private_subnet_ids
}

resource "aws_security_group" "redis" {
  name        = "${var.name_prefix}-${var.environment}-redis-sg"
  description = "Allow Redis TLS access only from EKS nodes"
  vpc_id      = var.vpc_id

  ingress {
    from_port   = 6379
    to_port     = 6379
    protocol    = "tcp"
    cidr_blocks = var.eks_cluster_cidr_blocks
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_elasticache_replication_group" "redis" {
  replication_group_id       = "${var.name_prefix}-${var.environment}-redis"
  description                = "AI Paralegal distributed rate limit, SSE Pub/Sub, and Celery broker"
  node_type                  = var.redis_node_type
  num_cache_clusters         = 2
  engine                     = "redis"
  engine_version             = "7.1"
  port                       = 6379
  subnet_group_name          = aws_elasticache_subnet_group.redis.name
  security_group_ids         = [aws_security_group.redis.id]
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  kms_key_id                 = aws_kms_key.legal_vault.arn
}

# 5. Least-Privilege IAM Policy for S3 Vault + KMS Access (IRSA / Workload Identity)
resource "aws_iam_policy" "app_storage_kms" {
  name        = "${var.name_prefix}-${var.environment}-storage-kms"
  description = "Least-privilege S3 vault and KMS access for AI Paralegal API & Celery workers"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
        Resource = [aws_s3_bucket.documents.arn, "${aws_s3_bucket.documents.arn}/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey"]
        Resource = [aws_kms_key.legal_vault.arn]
      }
    ]
  })
}

# 6. Optional Helm Release Deploying Application Chart
resource "helm_release" "ai_paralegal" {
  count            = var.deploy_helm_release ? 1 : 0
  name             = "ai-paralegal"
  namespace        = "ai-paralegal"
  create_namespace = true
  chart            = "${path.module}/../helm/ai-paralegal"

  set {
    name  = "config.storageBackend"
    value = "s3"
  }
  set {
    name  = "secrets.s3Bucket"
    value = aws_s3_bucket.documents.id
  }
  set {
    name  = "secrets.s3KmsKeyId"
    value = aws_kms_key.legal_vault.arn
  }
  set {
    name  = "ingress.host"
    value = var.ingress_host
  }
}
