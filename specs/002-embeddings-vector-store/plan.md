# Plan — Spec 002: Embeddings + vector store

**Status:** Approved
**Spec:** [spec.md](spec.md)

## Context

Spec 001 proved the ingestion trigger and chunking: text chunks land in DynamoDB. Spec 002 adds the
semantic layer — each chunk is embedded with Bedrock Titan V2 and stored in Aurora Serverless v2 +
pgvector via the Data API (HTTP/IAM, no VPC required for Lambda). The LocalStack path stays
completely green because the embedding block is gated behind `BEDROCK_EMBEDDING_MODEL_ID`.

**Dimension note:** The spec was initially drafted with 1536 dims (Titan V1). Titan V2
(`amazon.titan-embed-text-v2:0`) supports 256 / 512 / 1024. This plan uses **1024** throughout.

---

## Key decisions

### Feature flag — `BEDROCK_EMBEDDING_MODEL_ID`
When the env var is absent, `_process()` returns after the DynamoDB write; no Bedrock or Aurora
clients are ever instantiated. When it is set, the embedding block runs. This is explicit
feature-flagging, not environment detection — the LocalStack tfvars leave the var unset.

### Aurora accessed via Data API only
`enable_http_endpoint = true` on the `aws_rds_cluster` resource. Lambda calls `rds-data` over
HTTPS/IAM; no VPC attachment, no psycopg2 driver. All SQL is passed as strings in
`execute_statement` / `begin_transaction` / `commit_transaction` calls.

### Default VPC for Aurora
Aurora always needs a subnet group. Using `data "aws_vpc" "default"` and `data "aws_subnets"
"default"` avoids creating VPC resources in this spec. Aurora lands in public subnets — an
accepted risk documented here; spec 008 is the explicit VPC-hardening step.

### Idempotency via transaction
`replace_document_vectors` opens a Data API transaction, deletes by `source_key`, inserts new
rows, then commits. Any exception leaves the transaction un-committed; the Data API expires it
automatically.

### DynamoDB status updated last
`update_document_status(..., "embedded")` is the final step in `_process()`. Because it only runs
after a successful Aurora write, the integration test can poll DynamoDB for `status == "embedded"`
as a reliable signal.

### CI — manual-only for real-AWS tests
A `workflow_dispatch`-gated `integration-aws` job in CI. Uses a `aws-integration` GitHub
Environment requiring manual approval. Keeps every push/PR cheap; running real-AWS tests is an
explicit human action.

---

## Files to create

### Application code

```
src/ingestion/
  embedder.py       # embed_chunks() — Bedrock Titan V2 with retry
  vector_store.py   # replace_document_vectors() — Aurora Data API writes
```

**`embedder.py`**

```python
def embed_chunks(bedrock_client, chunks: list[dict], model_id: str, max_retries: int = 3) -> list[dict]
```

- Inner `_embed_one(client, text, model_id, max_retries)` calls `invoke_model` with body
  `{"inputText": text, "dimensions": 1024, "normalize": True}`, parses response JSON, returns
  `body["embedding"]` as `list[float]`.
- Retry: catch `botocore.exceptions.ClientError` where code is `"ThrottlingException"`,
  sleep `2 ** attempt` seconds, re-raise after `max_retries` exhausted.
- Mutates each chunk dict in place, adding `"embedding": list[float]`.

**`vector_store.py`**

```python
def replace_document_vectors(
    rdsdata_client,
    cluster_arn: str,
    secret_arn: str,
    database: str,
    source_key: str,
    document_id: str,
    chunks_with_embeddings: list[dict],
) -> None
```

- `begin_transaction` → DELETE chunks by `source_key` → DELETE documents by `source_key` →
  INSERT document row (`status = 'embedded'`) → INSERT each chunk row (embedding as
  `'[...]'::vector` stringValue) → `commit_transaction`.
- `transactionId` threaded through every `execute_statement` call.

### Tests

```
tests/
  test_embedder.py      # 4 unit tests, MagicMock bedrock client
  test_vector_store.py  # 5 unit tests, MagicMock rdsdata client

tests/integration/
  test_embeddings.py    # 2 tests, gated by RUN_AWS_INTEGRATION=1
```

**`test_embedder.py`** (MagicMock, no network):
1. `test_embed_chunks_returns_embeddings` — mock returns `{"embedding": [0.1]*1024}`; assert each chunk has `"embedding"` of length 1024.
2. `test_embed_chunks_throttling_retries` — mock raises `ThrottlingException` twice then succeeds; assert `invoke_model` called 3 times.
3. `test_embed_chunks_throttling_exhausted` — mock always raises; assert `ClientError` propagates.
4. `test_embed_chunks_empty_list` — empty input → empty output, no Bedrock calls.

**`test_vector_store.py`** (MagicMock, no network):
1. `test_begins_transaction` — assert `begin_transaction` called once.
2. `test_deletes_before_inserts` — assert DELETE SQL appears before INSERT SQL in call args.
3. `test_commits` — assert `commit_transaction` called after inserts.
4. `test_idempotent_second_call_deletes` — call twice; DELETE appears in both sequences.
5. `test_embedding_passed_as_string_value` — assert embedding param uses `stringValue`.

**`test_embeddings.py`** (real AWS, skipped unless `RUN_AWS_INTEGRATION=1`):
- `stack` fixture reads Terraform outputs; scope=`module`.
- `test_upload_creates_aurora_rows`: upload `.txt` to S3 `documents/`, poll DynamoDB until
  `status == "embedded"` (120 s timeout, 5 s interval), then assert Aurora `chunks` rows exist
  with `embedding` length 1024.
- `test_reupload_replaces_rows`: upload same key twice; assert exactly 1 Aurora `documents` row
  for that `source_key`.

### Scripts

```
scripts/
  init_db.py        # idempotent DDL: CREATE EXTENSION, CREATE TABLE IF NOT EXISTS
  deploy-aws.sh     # build → terraform apply → init_db.py
```

---

## Files to modify

### `src/ingestion/handler.py`

Add after `replace_document` returns `document_id` in `_process()`:

```python
model_id = os.environ.get("BEDROCK_EMBEDDING_MODEL_ID")
if not model_id:
    return  # LocalStack path — skip embedding

cluster_arn = os.environ["AURORA_CLUSTER_ARN"]
secret_arn  = os.environ["AURORA_SECRET_ARN"]
database    = os.environ.get("AURORA_DATABASE", "rag")

bedrock = boto3.client("bedrock-runtime")
rdsdata = boto3.client("rds-data")

embed_chunks(bedrock, chunks, model_id)
replace_document_vectors(rdsdata, cluster_arn, secret_arn, database,
                         key, document_id, chunks)
update_document_status(dynamo, documents_table, document_id, "embedded")
```

New imports at top: `embed_chunks` from `embedder`, `replace_document_vectors` from
`vector_store`, `update_document_status` from `store`.

### `src/ingestion/store.py`

Add one new function (no changes to existing functions):

```python
def update_document_status(dynamo_client, documents_table: str, document_id: str, status: str) -> None
```

Issues `UpdateItem` with `UpdateExpression = "SET #s = :s"`.

Add one test to `tests/test_store.py`: `test_update_document_status` — call
`replace_document` then `update_document_status(..., "embedded")`; assert `status["S"] == "embedded"`.

### `infra/main.tf`

Add `random` to `required_providers`. Add resources (after DynamoDB section):

- `data.aws_vpc.default`, `data.aws_subnets.default`
- `aws_db_subnet_group.aurora`, `aws_security_group.aurora`
- `aws_rds_cluster_parameter_group.aurora_pgvector` (family `aurora-postgresql16`,
  `shared_preload_libraries = "pgvector"`)
- `random_password.aurora` (length 32, special = false)
- `aws_rds_cluster.aurora` — `enable_http_endpoint = true`, `min_capacity = 0`,
  `max_capacity = var.aurora_max_capacity`, `skip_final_snapshot = true`
- `aws_rds_cluster_instance.aurora_writer` — `instance_class = "db.serverless"`
- `aws_secretsmanager_secret.aurora` + `aws_secretsmanager_secret_version.aurora`
- Extend `aws_iam_role_policy.ingest_lambda` with Bedrock, rds-data, and secretsmanager permissions.
- Extend `aws_lambda_function.ingest` env vars: `BEDROCK_EMBEDDING_MODEL_ID`,
  `AURORA_CLUSTER_ARN`, `AURORA_SECRET_ARN`, `AURORA_DATABASE`.

### `infra/variables.tf`

Add: `aurora_database_name` (default `"rag"`), `aurora_max_capacity` (default `1`),
`bedrock_embedding_model_id` (default `""`). Raise `lambda_timeout_seconds` default from 60 → 120
(Aurora cold start after `min_capacity = 0` pause takes ~30 s).

### `infra/outputs.tf`

Add: `aurora_cluster_arn`, `aurora_secret_arn`, `aurora_cluster_endpoint`, `aurora_database`.

### `.github/workflows/ci.yml`

Add `integration-aws` job triggered only by `workflow_dispatch`, using `aws-integration` GitHub
Environment. Runs `deploy-aws.sh` then `pytest tests/integration/test_embeddings.py -v` with
`RUN_AWS_INTEGRATION=1`.

---

## Aurora schema (applied by `scripts/init_db.py`)

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS documents (
    document_id  TEXT        PRIMARY KEY,
    source_key   TEXT        NOT NULL UNIQUE,
    chunk_count  INTEGER     NOT NULL,
    status       TEXT        NOT NULL DEFAULT 'embedded',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chunks (
    document_id  TEXT        NOT NULL,
    chunk_index  INTEGER     NOT NULL,
    text         TEXT        NOT NULL,
    char_count   INTEGER     NOT NULL,
    source_key   TEXT        NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL,
    embedding    vector(1024),
    PRIMARY KEY (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS chunks_source_key_idx
    ON chunks (source_key);
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw_idx
    ON chunks USING hnsw (embedding vector_cosine_ops);
```

---

## Implementation sequence

1. Fix spec.md: `1536` → `1024`
2. `embedder.py` + `test_embedder.py` (pure Python, iterate until green)
3. `store.py` — add `update_document_status` + extend `test_store.py`
4. `vector_store.py` + `test_vector_store.py` (MagicMock, iterate until green)
5. `handler.py` — wire new modules; run `pytest tests/ -q` + `ruff check .`
6. Terraform: `main.tf`, `variables.tf`, `outputs.tf`
7. `scripts/init_db.py` + `scripts/deploy-aws.sh`
8. `tests/integration/test_embeddings.py`
9. `.github/workflows/ci.yml` — add `integration-aws` job

---

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Aurora cold start exceeds Lambda timeout | Raise `lambda_timeout_seconds` default to 120 s |
| `enable_http_endpoint` defaults to false | Set explicitly in TF resource; silent failure otherwise |
| Titan V2 max dim is 1024, not 1536 | Fixed in spec; use 1024 everywhere |
| LocalStack accidentally hits Bedrock | Feature flag: `BEDROCK_EMBEDDING_MODEL_ID` unset in LocalStack tfvars |
| Aurora in default-VPC public subnets | Accepted for this spec; spec 008 is the documented hardening step |

---

## Cost impact

- Aurora Serverless v2 at `min_capacity = 0`: pauses when idle, ~$0 while paused. ACU charges only
  during active ingestion (~seconds per document).
- Bedrock Titan V2 embeddings: ~$0.00002 per 1K tokens (~negligible at dev/test scale).
- Secrets Manager: $0.40/secret/month.
- No always-on resources introduced.

---

## Verification

```bash
# Unit tests (no infra needed)
pytest tests/ -q
ruff check .

# Real-AWS integration (run manually — costs money)
bash scripts/deploy-aws.sh
RUN_AWS_INTEGRATION=1 pytest tests/integration/test_embeddings.py -v

# Teardown
aws s3 rm s3://<bucket> --recursive
terraform -chdir=infra destroy -auto-approve
```
