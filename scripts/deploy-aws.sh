#!/usr/bin/env bash
# Build and deploy to real AWS, then initialise the Aurora schema.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

BEDROCK_MODEL="${BEDROCK_EMBEDDING_MODEL_ID:-amazon.titan-embed-text-v2:0}"

bash "$REPO_ROOT/scripts/build.sh"

echo "==> Deploying to AWS"
cd "$REPO_ROOT/infra"
terraform init -upgrade -input=false > /dev/null
terraform apply -auto-approve -input=false \
  -var "bedrock_embedding_model_id=$BEDROCK_MODEL"

echo "==> Terraform outputs:"
terraform output

CLUSTER_ARN="$(terraform output -raw aurora_cluster_arn)"
SECRET_ARN="$(terraform output -raw aurora_secret_arn)"
DATABASE="$(terraform output -raw aurora_database)"

echo "==> Initialising Aurora schema"
AURORA_CLUSTER_ARN="$CLUSTER_ARN" \
AURORA_SECRET_ARN="$SECRET_ARN" \
AURORA_DATABASE="$DATABASE" \
  python3 "$REPO_ROOT/scripts/init_db.py"

echo "==> Deploy complete."
