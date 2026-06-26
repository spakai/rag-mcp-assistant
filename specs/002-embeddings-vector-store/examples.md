# Spec 002 — Example Payloads

Concrete examples of the additional data shapes introduced by the embeddings +
vector-store pipeline.
All values are illustrative; UUIDs, timestamps, and embedding values will differ
in practice.

---

## 1. Input chunk payloads (from spec 001)

The embedding step starts from the chunk records already written by spec 001.
Each chunk is passed to Bedrock Titan V2, and the resulting vector is stored
alongside the chunk metadata in Aurora + pgvector.

```json
{
  "document_id": "f9a3c21e-84b1-4d2a-9c7f-0e1b2a3d4e5f",
  "chunk_index": 0,
  "text": "The AWS Well-Architected Framework describes key concepts, design principles, and architectural best practices for designing and running workloads in the cloud.",
  "char_count": 1000,
  "source_key": "documents/aws-well-architected.txt",
  "created_at": "2026-06-13T10:42:00.123456+00:00"
}
```

```json
{
  "document_id": "f9a3c21e-84b1-4d2a-9c7f-0e1b2a3d4e5f",
  "chunk_index": 1,
  "text": "Each pillar contains a set of design principles and best practices that help architects understand the trade-offs involved in building systems on AWS.",
  "char_count": 900,
  "source_key": "documents/aws-well-architected.txt",
  "created_at": "2026-06-13T10:42:00.123456+00:00"
}
```

---

## 2. Bedrock embedding request

The ingestion code calls Bedrock with the chunk text and requests a vector
using the plan's expected request shape.

```json
{
  "inputText": "The AWS Well-Architected Framework describes key concepts, design principles, and architectural best practices for designing and running workloads in the cloud.",
  "dimensions": 1024,
  "normalize": true
}
```

The model ID is supplied from the environment variable
`BEDROCK_EMBEDDING_MODEL_ID`, and the handler uses the response field
`body["embedding"]` as the vector payload.

---

## 3. Bedrock embedding response

Example response shape for `invoke_model(...)`.

```json
{
  "embedding": [
    0.0123,
    -0.0456,
    0.0789,
    0.0001,
    -0.0034,
    0.9999
  ]
}
```

For the actual implementation, the embedding array is expected to contain
1024 floats, matching the plan's use of Titan V2 with 1024 dimensions.

---

## 4. Aurora — `documents` table row

After a successful embed + write, the `documents` table stores one row per
uploaded `source_key`.

```sql
INSERT INTO documents (
  document_id,
  source_key,
  chunk_count,
  status,
  created_at
) VALUES (
  'f9a3c21e-84b1-4d2a-9c7f-0e1b2a3d4e5f',
  'documents/aws-well-architected.txt',
  2,
  'embedded',
  '2026-06-13T10:42:00.123456+00:00'
);
```

Equivalent conceptual JSON representation:

```json
{
  "document_id": "f9a3c21e-84b1-4d2a-9c7f-0e1b2a3d4e5f",
  "source_key": "documents/aws-well-architected.txt",
  "chunk_count": 2,
  "status": "embedded",
  "created_at": "2026-06-13T10:42:00.123456+00:00"
}
```

---

## 5. Aurora — `chunks` table rows

Each chunk row stores the chunk text, metadata, and the embedding vector in
the `embedding` column (`vector(1024)` in the Aurora schema).

```sql
INSERT INTO chunks (
  document_id,
  chunk_index,
  text,
  char_count,
  source_key,
  created_at,
  embedding
) VALUES (
  'f9a3c21e-84b1-4d2a-9c7f-0e1b2a3d4e5f',
  0,
  'The AWS Well-Architected Framework describes key concepts, design principles, and architectural best practices for designing and running workloads in the cloud.',
  1000,
  'documents/aws-well-architected.txt',
  '2026-06-13T10:42:00.123456+00:00',
  '[0.0123,-0.0456,0.0789,...]'
);
```

Conceptually, the row looks like:

```json
{
  "document_id": "f9a3c21e-84b1-4d2a-9c7f-0e1b2a3d4e5f",
  "chunk_index": 0,
  "text": "The AWS Well-Architected Framework describes key concepts, design principles, and architectural best practices for designing and running workloads in the cloud.",
  "char_count": 1000,
  "source_key": "documents/aws-well-architected.txt",
  "created_at": "2026-06-13T10:42:00.123456+00:00",
  "embedding": [0.0123, -0.0456, 0.0789, "..."]
}
```

The `embedding` field is stored in the pgvector column and is used later for
similarity search.

---

## 6. Idempotency example — same key uploaded twice

The second upload replaces the first document's rows rather than duplicating
records.

| Step | Effect |
|---|---|
| Upload #1 | New `document_id` created, chunks + document row inserted |
| Upload #2 with same `source_key` | Old rows for that `source_key` are deleted |
| Upload #2 completes | New rows are inserted for the new `document_id` |

Conceptual outcome after the second upload:

```json
{
  "source_key": "documents/aws-well-architected.txt",
  "document_count": 1,
  "chunk_count": 2,
  "status": "embedded"
}
```

---

## 7. Lambda log example (happy path)

```text
INFO  Embedding 2 chunks for document_id=f9a3c21e-84b1-4d2a-9c7f-0e1b2a3d4e5f source_key=documents/aws-well-architected.txt
INFO  Stored embeddings in Aurora for document_id=f9a3c21e-84b1-4d2a-9c7f-0e1b2a3d4e5f
INFO  Updated documents status to embedded
```

If the embedding env var is not set, the handler should skip the embedding step
and leave the flow in the spec 001 state.
