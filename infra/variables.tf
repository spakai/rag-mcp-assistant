variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "documents_table_name" {
  description = "DynamoDB table name for document records"
  type        = string
  default     = "rag-documents"
}

variable "chunks_table_name" {
  description = "DynamoDB table name for chunk records"
  type        = string
  default     = "rag-chunks"
}

variable "chunk_size" {
  description = "Maximum characters per chunk"
  type        = number
  default     = 1000
}

variable "chunk_overlap" {
  description = "Overlap characters between adjacent chunks"
  type        = number
  default     = 100
}

variable "lambda_memory_mb" {
  description = "Memory allocated to the ingestion Lambda (MB)"
  type        = number
  default     = 256
}

variable "lambda_timeout_seconds" {
  description = "Ingestion Lambda timeout (seconds)"
  type        = number
  default     = 120
}

variable "aurora_database_name" {
  description = "Name of the PostgreSQL database inside Aurora"
  type        = string
  default     = "rag"
}

variable "aurora_max_capacity" {
  description = "Maximum Aurora Serverless v2 capacity units"
  type        = number
  default     = 1
}

variable "bedrock_embedding_model_id" {
  description = "Bedrock model ID for embeddings (empty string disables embedding on LocalStack)"
  type        = string
  default     = ""
}

variable "localstack_endpoint" {
  description = "LocalStack endpoint URL (empty string for real AWS)"
  type        = string
  default     = ""
}
