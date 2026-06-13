#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="$REPO_ROOT/dist"
PACKAGE_DIR="$DIST_DIR/package"
ZIP_PATH="$DIST_DIR/ingest.zip"

LOCALSTACK_ENDPOINT="${LOCALSTACK_ENDPOINT:-http://127.0.0.1:4566}"

bash "$REPO_ROOT/scripts/build.sh"

echo "==> Deploying to LocalStack at $LOCALSTACK_ENDPOINT"
cd "$REPO_ROOT/infra"
terraform init -upgrade -input=false > /dev/null
terraform apply -auto-approve -input=false \
  -var "localstack_endpoint=$LOCALSTACK_ENDPOINT"

echo "==> Done. Stack outputs:"
terraform output
