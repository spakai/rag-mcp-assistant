output "bucket_name" {
  description = "S3 bucket for document uploads"
  value       = aws_s3_bucket.documents.id
}

output "documents_table" {
  description = "DynamoDB table for document records"
  value       = aws_dynamodb_table.documents.name
}

output "chunks_table" {
  description = "DynamoDB table for chunk records"
  value       = aws_dynamodb_table.chunks.name
}

output "ingest_lambda_arn" {
  description = "ARN of the ingestion Lambda"
  value       = aws_lambda_function.ingest.arn
}

output "aurora_cluster_arn" {
  description = "ARN of the Aurora Serverless v2 cluster"
  value       = aws_rds_cluster.aurora.arn
}

output "aurora_secret_arn" {
  description = "ARN of the Secrets Manager secret holding Aurora credentials"
  value       = aws_secretsmanager_secret.aurora.arn
}

output "aurora_cluster_endpoint" {
  description = "Writer endpoint of the Aurora cluster"
  value       = aws_rds_cluster.aurora.endpoint
}

output "aurora_database" {
  description = "Name of the PostgreSQL database"
  value       = var.aurora_database_name
}
