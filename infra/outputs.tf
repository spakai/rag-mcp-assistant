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
