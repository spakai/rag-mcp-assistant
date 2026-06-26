# Spec 002 — Embeddings + vector store

- **Status:** Done
- **Tracking issue:** #5
- **Author:** human (principal)

## Context

Spec 001 proved the ingestion trigger and chunking: documents land in S3, a
Lambda splits them into text chunks, and those chunks are written to DynamoDB.
This spec adds the next layer — embeddings and a vector store — so that chunks
become semantically searchable.

Each chunk from DynamoDB is embedded using Amazon Bedrock Titan Embeddings V2
(`amazon.titan-embed-text-v2:0`, 1536-dimensional vectors). The resulting vector
and metadata are stored in Aurora Serverless v2 + pgvector, reached via the
Aurora Data API (HTTP/IAM — no VPC needed yet). Secrets Manager holds the
Aurora credentials.

Integration tests for this spec require real AWS because Bedrock and Aurora are
not available on LocalStack's free tier. They are gated behind an environment
flag (`RUN_AWS_INTEGRATION=1`) so the LocalStack path stays green.

## User story

As a knowledge worker, I want each ingested text chunk to be embedded and stored
as a vector, so that later stages can retrieve the most semantically relevant
chunks for a given question.

## Acceptance criteria

Each criterion must be verifiable by an automated test.

- [x] After a document is ingested (spec 001 path), the ingestion function embeds
      each chunk using Bedrock Titan Embeddings V2 and writes the vector to Aurora
      pgvector alongside the chunk metadata.
- [x] Each row in the `chunks` table contains: `document_id`, `chunk_index`,
      `text`, `char_count`, `source_key`, `created_at`, and `embedding` (vector,
      1536 dims).
- [x] A `documents` table row exists with `document_id`, `source_key`,
      `chunk_count`, `status = "embedded"`, and `created_at`.
- [x] Re-uploading the same object key re-embeds and replaces that document's
      rows rather than duplicating them (idempotent per source key).
- [x] The Bedrock model ID is read from an environment variable
      (`BEDROCK_EMBEDDING_MODEL_ID`), not hardcoded; changing it requires no
      code edit.
- [x] Bedrock throttling (HTTP 429 / `ThrottlingException`) is handled with
      exponential backoff and retried at least 3 times before failing.
- [x] The Aurora connection string and credentials are fetched from Secrets
      Manager at cold start; the secret ARN is read from an environment variable
      (`AURORA_SECRET_ARN`).
- [x] `min_capacity = 0` is set on the Aurora cluster so it can pause when idle.
- [x] Unit tests cover the embedding call and the Aurora write using mocked
      clients (no network); they verify the retry logic triggers on throttling.
- [x] An integration test (gated by `RUN_AWS_INTEGRATION=1`) uploads a document,
      waits for ingestion, and asserts the expected rows exist in Aurora with
      non-null embeddings of dimension 1024.
- [x] `ruff check .` is clean and CI is green on the pull request.

## Out of scope

- Vector similarity search and the query API (spec 003).
- MCP server (spec 004).
- Authentication and multi-tenancy (spec 006).
- Moving Aurora into a VPC (spec 008) — the Data API removes that need for now.

## Constraints

- Follow all guardrails in `AGENTS.md`. In particular: no secrets committed,
  `min_capacity = 0` on Aurora, no OpenSearch Serverless.
- The ingestion handler must remain environment-agnostic: when
  `RUN_AWS_INTEGRATION` is not set (LocalStack path), Bedrock and Aurora calls
  must be skipped gracefully (feature-flagged, not environment-detected in code).
- All infrastructure changes go through Terraform in `infra/`; no imperative
  resource creation in scripts.
- Aurora is accessed exclusively via the Data API — do not add VPC configuration
  in this spec.
- The `documents/` S3 event filter must not be widened (re-trigger loop risk).
