# Plan — Spec 001: Document ingestion skeleton

**Status:** Awaiting human approval
**Spec:** [spec.md](spec.md)

## Context

First slice of the RAG pipeline: documents uploaded to S3 under `documents/` are
extracted, chunked, and stored so later specs can embed and search them. Scope is
limited to S3 + Lambda + DynamoDB so everything runs on LocalStack's free tier
(no Bedrock, no Aurora yet).

---

## Key decisions

### Key/value store — DynamoDB
Roadmap explicitly names DynamoDB for spec 001. Two tables (clear separation):
- `documents` — one record per ingested document
- `chunks` — one record per chunk, composite key `(document_id, chunk_index)`

A GSI on `source_key` in the documents table enables idempotency lookups without a
full scan.

### PDF extraction — `pypdf`
Pure-Python, no native deps, no manylinux packaging needed. Sufficient for
extracting the embedded text layer (the spec's requirement). `pdfplumber` is an
alternative but pulls in `pdfminer.six` and native `Pillow`; overkill here.

### Idempotency strategy
On every upload, before writing:
1. Query the `source_key` GSI in the documents table.
2. If a previous record exists, delete all its chunk records and the document record.
3. Generate a fresh UUID as `document_id`, process, store new records.

---

## Files to create

### Application code

```
src/
  ingestion/
    __init__.py
    handler.py      # Lambda entry point — parses S3 event, orchestrates
    extractor.py    # extract_text(s3_client, bucket, key) -> str
    chunker.py      # chunk_text(text, size, overlap) -> list[dict]
    store.py        # DynamoDB read/write; idempotency delete + put
```

**Environment variables consumed by the handler:**

| Variable | Default | Purpose |
|---|---|---|
| `DOCUMENTS_TABLE` | — | DynamoDB documents table name |
| `CHUNKS_TABLE` | — | DynamoDB chunks table name |
| `CHUNK_SIZE` | 1000 | Max chars per chunk |
| `CHUNK_OVERLAP` | 100 | Overlap chars between adjacent chunks |
| `AWS_ENDPOINT_URL` | — | Set to LocalStack URL on local runs |

### Tests

```
tests/
  __init__.py
  test_chunker.py       # unit: chunk sizes, overlap, final short chunk, env-var defaults
  test_extractor.py     # unit: .txt passthrough, .pdf extraction (mock S3 + BytesIO)
  test_store.py         # unit: idempotency delete logic, put records (moto)

tests/integration/
  __init__.py
  test_ingestion.py     # integration: upload .txt + .pdf to LocalStack S3,
                        #   assert DynamoDB chunk + document records appear;
                        #   re-upload same key and assert record count unchanged
```

### Infrastructure (Terraform)

```
infra/
  main.tf       # S3 bucket, Lambda, DynamoDB tables, IAM role
  variables.tf  # aws_region, bucket_suffix, table names, Lambda config
  outputs.tf    # bucket_name, documents_table, chunks_table
```

Key Terraform resources:
- `aws_s3_bucket` — name suffixed with `${data.aws_caller_identity.current.account_id}`
- `aws_s3_bucket_notification` — filter prefix `documents/`, triggers ingestion Lambda
- `aws_dynamodb_table` "documents" — PK `document_id` (S), GSI on `source_key`
- `aws_dynamodb_table` "chunks" — PK `document_id` (S), SK `chunk_index` (N)
- `aws_lambda_function` "ingest" — runtime python3.12, env vars wired from TF variables
- `aws_iam_role` + policies — S3 GetObject, DynamoDB read/write, CloudWatch logs

### Scripts and config

```
scripts/
  deploy-local.sh   # pip install deps into package dir, zip, terraform init+apply (LocalStack)

docker-compose.yml  # LocalStack (localstack/localstack image)

requirements.txt    # pypdf  boto3  (runtime Lambda deps)
```

`requirements-dev.txt` gains `moto[s3,dynamodb]` for unit-test mocking.

---

## Data model

### `documents` table

| Attribute | Type | Notes |
|---|---|---|
| `document_id` | String (PK) | UUID v4 |
| `source_key` | String | S3 object key, e.g. `documents/report.pdf` |
| `chunk_count` | Number | How many chunks were stored |
| `status` | String | `"ingested"` |
| `created_at` | String | ISO-8601 UTC |

GSI: `source_key_index` — PK `source_key`, projects ALL.

### `chunks` table

| Attribute | Type | Notes |
|---|---|---|
| `document_id` | String (PK) | Same UUID as parent document |
| `chunk_index` | Number (SK) | 0-based integer |
| `text` | String | Chunk text |
| `char_count` | Number | `len(text)` |
| `source_key` | String | Denormalised for easy filtering |
| `created_at` | String | ISO-8601 UTC |

---

## Chunking algorithm

Sliding-window over the full document text:

```python
start = 0
while start < len(text):
    end = min(start + chunk_size, len(text))
    yield text[start:end]
    if end == len(text):
        break
    start += chunk_size - chunk_overlap
```

Produces the final short chunk naturally; no special-case needed.

---

## Ingestion flow (handler.py)

1. Parse S3 event → `bucket`, `key`.
2. Skip if key doesn't end in `.txt` or `.pdf` (safety guard beyond the prefix filter).
3. Call `extractor.extract_text(s3_client, bucket, key)` → raw text string.
4. Read `CHUNK_SIZE`, `CHUNK_OVERLAP` from env (defaults 1000 / 100).
5. Call `chunker.chunk_text(text, size, overlap)` → list of chunk dicts.
6. Call `store.replace_document(dynamo, documents_table, chunks_table, source_key, chunks)`:
   a. Query GSI for existing `document_id` by `source_key`.
   b. If found: batch-delete all chunk records and the document record.
   c. Generate new `document_id` = `uuid.uuid4()`.
   d. Batch-write chunk records.
   e. Write document record with `chunk_count` and `status = "ingested"`.

---

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| PDF with only scanned images (no text layer) | `pypdf` returns empty string; handler logs a warning and stores 0 chunks with `status = "no_text"` |
| Very large document exceeds Lambda memory | Out of scope for spec 001; noted for a future spec |
| Partial failure mid-write leaves orphan chunks | Idempotency on next upload cleans them up; noted in comments |

---

## Cost impact

- DynamoDB on-demand: effectively zero at dev/test scale.
- Lambda: free tier (fires only on upload).
- S3: negligible storage cost.
- No always-on resources introduced.

---

## Verification

1. `pytest tests/ -q` — unit tests green, no network calls.
2. `ruff check .` — zero lint errors.
3. `docker compose up -d && bash scripts/deploy-local.sh` — LocalStack stack up.
4. `pytest tests/integration/ -q` — uploads a .txt and a .pdf, asserts DynamoDB records match expected schema and counts.
5. Re-upload the same key — assert record counts unchanged (idempotency).
6. CI green on the pull request.
