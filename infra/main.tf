terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
  }
  required_version = ">= 1.6"
}

provider "aws" {
  region = var.aws_region

  # LocalStack overrides — set via TF_VAR_ env vars or tfvars when deploying locally
  dynamic "endpoints" {
    for_each = var.localstack_endpoint != "" ? [1] : []
    content {
      s3       = var.localstack_endpoint
      dynamodb = var.localstack_endpoint
      lambda   = var.localstack_endpoint
      iam      = var.localstack_endpoint
      sts      = var.localstack_endpoint
    }
  }

  # LocalStack does not need real credentials
  skip_credentials_validation = var.localstack_endpoint != ""
  skip_requesting_account_id  = var.localstack_endpoint != ""
  skip_metadata_api_check     = var.localstack_endpoint != ""
  access_key                  = var.localstack_endpoint != "" ? "test" : null
  secret_key                  = var.localstack_endpoint != "" ? "test" : null
}

data "aws_caller_identity" "current" {}

locals {
  # true on real AWS, false when deploying to LocalStack
  is_aws = var.localstack_endpoint == ""
}

# ── S3 bucket ────────────────────────────────────────────────────────────────

resource "aws_s3_bucket" "documents" {
  bucket        = "rag-documents-${data.aws_caller_identity.current.account_id}"
  force_destroy = true
}

resource "aws_s3_bucket_notification" "ingest_trigger" {
  bucket = aws_s3_bucket.documents.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.ingest.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "documents/"
  }

  depends_on = [aws_lambda_permission.allow_s3]
}

resource "aws_lambda_permission" "allow_s3" {
  statement_id  = "AllowS3Invoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingest.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.documents.arn
}

# ── DynamoDB ─────────────────────────────────────────────────────────────────

resource "aws_dynamodb_table" "documents" {
  name         = var.documents_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "document_id"

  attribute {
    name = "document_id"
    type = "S"
  }

  attribute {
    name = "source_key"
    type = "S"
  }

  global_secondary_index {
    name            = "source_key_index"
    hash_key        = "source_key"
    projection_type = "ALL"
  }
}

resource "aws_dynamodb_table" "chunks" {
  name         = var.chunks_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "document_id"
  range_key    = "chunk_index"

  attribute {
    name = "document_id"
    type = "S"
  }

  attribute {
    name = "chunk_index"
    type = "N"
  }
}

# ── Aurora Serverless v2 + pgvector (real AWS only — skipped on LocalStack) ───

data "aws_vpc" "default" {
  count   = local.is_aws ? 1 : 0
  default = true
}

data "aws_availability_zones" "available" {
  count = local.is_aws ? 1 : 0
  state = "available"
}

# Aurora requires a subnet group spanning at least 2 AZs.
# Create two dedicated /24 subnets from the tail of the default VPC's CIDR.
resource "aws_subnet" "aurora" {
  count             = local.is_aws ? 2 : 0
  vpc_id            = data.aws_vpc.default[0].id
  cidr_block        = cidrsubnet(data.aws_vpc.default[0].cidr_block, 8, 96 + count.index)
  availability_zone = data.aws_availability_zones.available[0].names[count.index]

  tags = { Name = "rag-aurora-${count.index}" }
}

resource "aws_db_subnet_group" "aurora" {
  count      = local.is_aws ? 1 : 0
  name       = "rag-aurora-subnet-group"
  subnet_ids = aws_subnet.aurora[*].id
}

resource "aws_security_group" "aurora" {
  count       = local.is_aws ? 1 : 0
  name        = "rag-aurora-sg"
  description = "Aurora sg for rag (Data API - no Lambda VPC attachment needed)"
  vpc_id      = data.aws_vpc.default[0].id
}

# pgvector is installed via CREATE EXTENSION, not shared_preload_libraries.
# No custom parameter group needed.

resource "random_password" "aurora" {
  count   = local.is_aws ? 1 : 0
  length  = 32
  special = false
}

resource "aws_rds_cluster" "aurora" {
  count                  = local.is_aws ? 1 : 0
  cluster_identifier     = "rag-aurora"
  engine                 = "aurora-postgresql"
  engine_mode            = "provisioned"
  engine_version         = "16.6"
  database_name          = var.aurora_database_name
  master_username        = "ragadmin"
  master_password        = random_password.aurora[0].result
  db_subnet_group_name   = aws_db_subnet_group.aurora[0].name
  vpc_security_group_ids = [aws_security_group.aurora[0].id]
  enable_http_endpoint   = true
  skip_final_snapshot    = true

  serverlessv2_scaling_configuration {
    min_capacity = 0
    max_capacity = var.aurora_max_capacity
  }
}

resource "aws_rds_cluster_instance" "aurora_writer" {
  count              = local.is_aws ? 1 : 0
  cluster_identifier = aws_rds_cluster.aurora[0].id
  instance_class     = "db.serverless"
  engine             = aws_rds_cluster.aurora[0].engine
  engine_version     = aws_rds_cluster.aurora[0].engine_version
}

resource "aws_secretsmanager_secret" "aurora" {
  count                   = local.is_aws ? 1 : 0
  name                    = "rag-aurora-credentials"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "aurora" {
  count     = local.is_aws ? 1 : 0
  secret_id = aws_secretsmanager_secret.aurora[0].id
  secret_string = jsonencode({
    username = aws_rds_cluster.aurora[0].master_username
    password = random_password.aurora[0].result
    host     = aws_rds_cluster.aurora[0].endpoint
    port     = aws_rds_cluster.aurora[0].port
    dbname   = var.aurora_database_name
  })
}

# ── IAM ──────────────────────────────────────────────────────────────────────

resource "aws_iam_role" "ingest_lambda" {
  name = "rag-ingest-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "ingest_lambda" {
  name = "rag-ingest-lambda-policy"
  role = aws_iam_role.ingest_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${aws_s3_bucket.documents.arn}/*"
      },
      {
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:GetItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem",
          "dynamodb:Query",
          "dynamodb:BatchWriteItem",
        ]
        Resource = [
          aws_dynamodb_table.documents.arn,
          "${aws_dynamodb_table.documents.arn}/index/*",
          aws_dynamodb_table.chunks.arn,
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel"]
        Resource = "arn:aws:bedrock:${var.aws_region}::foundation-model/*"
      },
      {
        Effect = "Allow"
        Action = [
          "rds-data:ExecuteStatement",
          "rds-data:BeginTransaction",
          "rds-data:CommitTransaction",
          "rds-data:RollbackTransaction",
        ]
        Resource = local.is_aws ? [aws_rds_cluster.aurora[0].arn] : ["arn:aws:rds:*:*:cluster:placeholder"]
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = local.is_aws ? [aws_secretsmanager_secret.aurora[0].arn] : ["arn:aws:secretsmanager:*:*:secret:placeholder"]
      },
    ]
  })
}

# ── Lambda ────────────────────────────────────────────────────────────────────

resource "aws_lambda_function" "ingest" {
  function_name    = "rag-ingest"
  role             = aws_iam_role.ingest_lambda.arn
  filename         = "${path.module}/../dist/ingest.zip"
  source_code_hash = filebase64sha256("${path.module}/../dist/ingest.zip")
  handler          = "src.ingestion.handler.handler"
  runtime          = "python3.12"
  memory_size      = var.lambda_memory_mb
  timeout          = var.lambda_timeout_seconds

  environment {
    variables = {
      DOCUMENTS_TABLE             = var.documents_table_name
      CHUNKS_TABLE                = var.chunks_table_name
      CHUNK_SIZE                  = tostring(var.chunk_size)
      CHUNK_OVERLAP               = tostring(var.chunk_overlap)
      BEDROCK_EMBEDDING_MODEL_ID  = var.bedrock_embedding_model_id
      AURORA_CLUSTER_ARN          = local.is_aws ? aws_rds_cluster.aurora[0].arn : ""
      AURORA_SECRET_ARN           = local.is_aws ? aws_secretsmanager_secret.aurora[0].arn : ""
      AURORA_DATABASE             = var.aurora_database_name
    }
  }
}
