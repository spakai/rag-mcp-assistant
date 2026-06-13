terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
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
      DOCUMENTS_TABLE = var.documents_table_name
      CHUNKS_TABLE    = var.chunks_table_name
      CHUNK_SIZE      = tostring(var.chunk_size)
      CHUNK_OVERLAP   = tostring(var.chunk_overlap)
    }
  }
}
