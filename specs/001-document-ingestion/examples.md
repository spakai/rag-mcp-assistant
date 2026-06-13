# Spec 001 — Example Payloads

Concrete examples of every data shape in the ingestion pipeline.
All values are illustrative; UUIDs and timestamps will differ in practice.

---

## 1. S3 event (triggers the Lambda)

S3 delivers this to the Lambda when an object is uploaded under `documents/`.

```json
{
  "Records": [
    {
      "s3": {
        "bucket": {
          "name": "rag-documents-123456789012"
        },
        "object": {
          "key": "documents/aws-well-architected.txt"
        }
      }
    }
  ]
}
```

The handler reads `bucket.name` and `object.key`, then calls
`extract_text(s3_client, bucket, key)`.

---

## 2. Source document (uploaded .txt)

```
The AWS Well-Architected Framework describes key concepts, design principles,
and architectural best practices for designing and running workloads in the cloud.

The framework is organized around six pillars: Operational Excellence, Security,
Reliability, Performance Efficiency, Cost Optimization, and Sustainability.

Each pillar contains a set of design principles and best practices that help
architects understand the trade-offs involved in building systems on AWS.
```

~440 characters. With default settings (`CHUNK_SIZE=1000`, `CHUNK_OVERLAP=100`)
this fits in a single chunk.

---

## 3. Chunker output

`chunk_text(text, chunk_size=1000, overlap=100)` yields one dict per chunk.

### Single-chunk document (≤ 1000 chars)

```python
[
    {
        "chunk_index": 0,
        "text": "The AWS Well-Architected Framework describes key concepts ...",
        "char_count": 440
    }
]
```

### Multi-chunk document (> 1000 chars, e.g. 2200 chars)

```python
[
    {
        "chunk_index": 0,
        "text": "The AWS Well-Architected Framework describes key concepts, design principles ...",
        "char_count": 1000
    },
    {
        "chunk_index": 1,
        "text": "... and architectural best practices for designing and running workloads ...",
        "char_count": 1000   # overlap of 100 chars with chunk 0
    },
    {
        "chunk_index": 2,
        "text": "... Each pillar contains a set of design principles and best practices ...",
        "char_count": 300    # final short chunk
    }
]
```

Window boundaries for a 2200-char document with size=1000, overlap=100:

| chunk_index | start | end  | char_count |
|-------------|-------|------|------------|
| 0           | 0     | 1000 | 1000       |
| 1           | 900   | 1900 | 1000       |
| 2           | 1800  | 2200 | 400        |

---

## 4. DynamoDB — `rag-documents` table

One record per ingested document. Written last, after all chunk records succeed.

```json
{
  "document_id": { "S": "f9a3c21e-84b1-4d2a-9c7f-0e1b2a3d4e5f" },
  "source_key":  { "S": "documents/aws-well-architected.txt" },
  "chunk_count": { "N": "3" },
  "status":      { "S": "ingested" },
  "created_at":  { "S": "2026-06-13T10:42:00.123456+00:00" }
}
```

If the file had no extractable text (e.g. a scanned PDF with no text layer):

```json
{
  "document_id": { "S": "a1b2c3d4-e5f6-7890-abcd-ef1234567890" },
  "source_key":  { "S": "documents/scanned-invoice.pdf" },
  "chunk_count": { "N": "0" },
  "status":      { "S": "no_text" },
  "created_at":  { "S": "2026-06-13T10:43:15.000000+00:00" }
}
```

**GSI `source_key_index`** — the handler queries this index by `source_key` before
writing to detect and delete any previous version of the same document (idempotency).

---

## 5. DynamoDB — `rag-chunks` table

One record per chunk. PK = `document_id`, SK = `chunk_index`.

```json
{
  "document_id": { "S": "f9a3c21e-84b1-4d2a-9c7f-0e1b2a3d4e5f" },
  "chunk_index": { "N": "0" },
  "text":        { "S": "The AWS Well-Architected Framework describes key concepts, design principles, and architectural best practices for designing and running workloads in the cloud.\n\nThe framework is organized around six pillars: Operational Excellence, Security, Reliability, Performance Efficiency, Cost Optimization, and Sustainability.\n\nEach pillar contains a set of design principles and best practices..." },
  "char_count":  { "N": "1000" },
  "source_key":  { "S": "documents/aws-well-architected.txt" },
  "created_at":  { "S": "2026-06-13T10:42:00.123456+00:00" }
}
```

```json
{
  "document_id": { "S": "f9a3c21e-84b1-4d2a-9c7f-0e1b2a3d4e5f" },
  "chunk_index": { "N": "1" },
  "text":        { "S": "...and architectural best practices for designing and running workloads in the cloud. Each pillar contains a set of design principles..." },
  "char_count":  { "N": "1000" },
  "source_key":  { "S": "documents/aws-well-architected.txt" },
  "created_at":  { "S": "2026-06-13T10:42:00.123456+00:00" }
}
```

```json
{
  "document_id": { "S": "f9a3c21e-84b1-4d2a-9c7f-0e1b2a3d4e5f" },
  "chunk_index": { "N": "2" },
  "text":        { "S": "...Each pillar contains a set of design principles and best practices that help architects understand the trade-offs involved in building systems on AWS." },
  "char_count":  { "N": "400" },
  "source_key":  { "S": "documents/aws-well-architected.txt" },
  "created_at":  { "S": "2026-06-13T10:42:00.123456+00:00" }
}
```

Note: `source_key` is denormalised on each chunk so spec 002 can filter chunks by
source document without a join back to the documents table.

---

## 6. Idempotency — re-upload sequence

Same `source_key` uploaded twice. The second ingestion replaces the first.

| Event | `document_id` | `chunk_count` | Action |
|---|---|---|---|
| Upload #1 | `f9a3c21e-...` | 3 | Written fresh |
| Upload #2 detected | `f9a3c21e-...` found via GSI | — | Delete 3 chunk records + document record |
| Upload #2 completed | `7c4d9b01-...` (new UUID) | 3 | Written fresh |

After upload #2 the documents table has exactly **1** record for this `source_key`,
with a new `document_id` and a new `created_at`.

---

## 7. Lambda CloudWatch log (happy path)

```
INFO  Ingesting documents/aws-well-architected.txt
INFO  Stored 3 chunks for document_id=f9a3c21e-84b1-4d2a-9c7f-0e1b2a3d4e5f source_key=documents/aws-well-architected.txt
```

No-text path:

```
INFO  Ingesting documents/scanned-invoice.pdf
WARNING  No extractable text in documents/scanned-invoice.pdf
INFO  Stored 0 chunks for document_id=a1b2c3d4-e5f6-7890-abcd-ef1234567890 source_key=documents/scanned-invoice.pdf
```

Unsupported extension (skipped entirely, no DynamoDB writes):

```
WARNING  Skipping unsupported file type: documents/spreadsheet.xlsx
```
