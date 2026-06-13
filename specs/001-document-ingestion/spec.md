# Spec 001 — Document ingestion skeleton

- **Status:** Ready for planning
- **Tracking issue:** #1
- **Author:** human (principal)

## Context

This is the first slice of the RAG pipeline: get documents into the system as
retrievable text chunks, before any embeddings or vectors exist. Keeping it to
S3 + Lambda + a key/value store means it runs entirely on LocalStack for a fast,
free inner loop. Embeddings and the vector store come in spec 002.

## User story

As a knowledge worker, I want an uploaded document split into stored text chunks,
so that later stages can embed and retrieve them.

## Acceptance criteria

Each criterion must be verifiable by an automated test.

- [ ] Uploading a `.txt` or `.pdf` object under `documents/` to the bucket
      triggers the ingestion function.
- [ ] The document's text is extracted: UTF-8 text for `.txt`; the embedded text
      layer for `.pdf`.
- [ ] The text is split into chunks of a configurable size (default ~1000
      characters) with a configurable overlap (default ~100 characters); the
      chunk size and overlap are read from environment variables.
- [ ] Each chunk is stored as its own record with: `document_id`, `chunk_index`,
      `text`, `char_count`, `source_key`, and `created_at`.
- [ ] A document-level record is stored with: `document_id`, `source_key`,
      `chunk_count`, `status = "ingested"`, and `created_at`.
- [ ] Re-uploading the same object key replaces that document's chunks rather
      than duplicating them (ingestion is idempotent per source key).
- [ ] Unit tests cover the chunking logic: chunk sizes, overlap, and the final
      short chunk, using mocked AWS clients (no network).
- [ ] An integration test confirms the chunk records and the document record
      appear after an upload to a live LocalStack stack.
- [ ] `ruff check .` is clean and CI is green on the pull request.

## Out of scope

- Embeddings and vector storage (spec 002).
- Vector search, the query API, and the MCP server (specs 003–004).
- Authentication and multi-tenancy (spec 006).
- File types beyond `.txt` and `.pdf`.

## Constraints

- Follow all guardrails in `AGENTS.md`. In particular: the S3 event filter stays
  on `documents/`, no secrets are committed, and the handler stays
  environment-agnostic.
- No new managed services beyond S3, Lambda, and the key/value store — this spec
  must run on LocalStack's free tier.
- PDF text extraction should use a pure-Python library where possible to keep
  Lambda packaging simple; if a native dependency is needed, package it for the
  Lambda runtime (the manylinux pattern from the prior project).
