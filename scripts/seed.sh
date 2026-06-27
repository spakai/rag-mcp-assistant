#!/usr/bin/env bash
# Upload seed documents for the evaluation harness.
# Requires Terraform state to be present (run scripts/deploy-aws.sh first).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SEED_DIR="$REPO_ROOT/evals/seed_docs"

BUCKET="$(cd "$REPO_ROOT/infra" && terraform output -raw bucket_name)"

echo "==> Seeding evaluation documents to s3://$BUCKET/documents/"
aws s3 cp "$SEED_DIR/aws-well-architected.txt" "s3://$BUCKET/documents/aws-well-architected.txt"
aws s3 cp "$SEED_DIR/aws-s3-overview.txt"      "s3://$BUCKET/documents/aws-s3-overview.txt"
echo "==> Seeded 2 documents. Allow ~30 s for Lambda ingestion to complete."
