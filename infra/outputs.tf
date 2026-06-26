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
  description = "ARN of the Aurora Serverless v2 cluster (empty on LocalStack)"
  value       = local.is_aws ? aws_rds_cluster.aurora[0].arn : ""
}

output "aurora_secret_arn" {
  description = "ARN of the Secrets Manager secret holding Aurora credentials (empty on LocalStack)"
  value       = local.is_aws ? aws_secretsmanager_secret.aurora[0].arn : ""
}

output "aurora_cluster_endpoint" {
  description = "Writer endpoint of the Aurora cluster (empty on LocalStack)"
  value       = local.is_aws ? aws_rds_cluster.aurora[0].endpoint : ""
}

output "aurora_database" {
  description = "Name of the PostgreSQL database"
  value       = var.aurora_database_name
}

output "api_endpoint" {
  description = "Invoke URL for POST /ask (empty on LocalStack)"
  value       = local.is_aws ? "${aws_apigatewayv2_api.query[0].api_endpoint}/ask" : ""
}

output "mcp_endpoint" {
  description = "MCP server endpoint — POST to this URL (empty on LocalStack)"
  value       = local.is_aws ? "${aws_apigatewayv2_api.mcp[0].api_endpoint}/mcp" : ""
}
