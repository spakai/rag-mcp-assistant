# Spec 003 — Example Payloads

Concrete examples of the data shapes and API contracts introduced by the
query / retrieval API. All values are illustrative; UUIDs, embeddings, and
timestamps will differ in practice.

---

## 1. HTTP request — `POST /ask`

```http
POST /ask HTTP/1.1
Content-Type: application/json

{
  "question": "What are the five pillars of the AWS Well-Architected Framework?"
}
```

---

## 2. Bedrock embedding request (question → vector)

The question is embedded using the same model as ingestion
(`BEDROCK_EMBEDDING_MODEL_ID`). The request shape is identical to spec 002.

```json
{
  "inputText": "What are the five pillars of the AWS Well-Architected Framework?",
  "dimensions": 1024,
  "normalize": true
}
```

---

## 3. pgvector similarity search

The question embedding is used to find the top-k closest chunks using cosine
distance (`<=>`). The `vector_cosine_ops` index created in spec 002 covers this.

```sql
SELECT chunk_index, text, source_key
FROM chunks
ORDER BY embedding <=> '[0.0123, -0.0456, 0.0789, ...]'::vector
LIMIT 5;
```

Example result rows returned by the Data API:

```json
[
  {
    "chunk_index": 0,
    "text": "The AWS Well-Architected Framework is organized around five pillars: Operational Excellence, Security, Reliability, Performance Efficiency, and Cost Optimization.",
    "source_key": "documents/aws-well-architected.txt"
  },
  {
    "chunk_index": 1,
    "text": "Each pillar contains design principles and best practices that help architects understand the trade-offs involved in building systems on AWS.",
    "source_key": "documents/aws-well-architected.txt"
  }
]
```

---

## 4. Bedrock generation request (Converse API)

Retrieved chunks are assembled into a prompt and sent to the generation model
(`BEDROCK_GENERATION_MODEL_ID`) via the Bedrock **Converse API** — a
model-agnostic interface that works across Claude, Amazon Nova, and other
families without requiring per-model JSON format handling.

```python
bedrock.converse(
    modelId="amazon.nova-micro-v1:0",
    messages=[{"role": "user", "content": [{"text": "<prompt>"}]}],
    inferenceConfig={"maxTokens": 1024},
)
```

The prompt instructs the model to answer only from context and to cite sources:

```
Answer the question using ONLY the context below. Cite sources by their source_key.
If the answer is not in the context, say 'No relevant documents found.'

Context:
[source: documents/aws-well-architected.txt, chunk 0]
The AWS Well-Architected Framework is organized around five pillars: Operational Excellence, Security, Reliability, Performance Efficiency, and Cost Optimization.

[source: documents/aws-well-architected.txt, chunk 1]
Each pillar contains design principles and best practices that help architects understand the trade-offs involved in building systems on AWS.

Question: What are the five pillars of the AWS Well-Architected Framework?
```

---

## 5. Bedrock generation response (Converse API)

```json
{
  "output": {
    "message": {
      "role": "assistant",
      "content": [
        {
          "text": "According to documents/aws-well-architected.txt, the five pillars of the AWS Well-Architected Framework are: Operational Excellence, Security, Reliability, Performance Efficiency, and Cost Optimization."
        }
      ]
    }
  },
  "stopReason": "end_turn"
}
```

---

## 6. HTTP response — 200 OK (happy path)

```json
{
  "answer": "According to documents/aws-well-architected.txt, the five pillars of the AWS Well-Architected Framework are: Operational Excellence, Security, Reliability, Performance Efficiency, and Cost Optimization.",
  "sources": [
    {
      "source_key": "documents/aws-well-architected.txt",
      "chunk_index": 0,
      "text": "The AWS Well-Architected Framework is organized around five pillars: Operational Excellence, Security, Reliability, Performance Efficiency, and Cost Optimization."
    },
    {
      "source_key": "documents/aws-well-architected.txt",
      "chunk_index": 1,
      "text": "Each pillar contains design principles and best practices that help architects understand the trade-offs involved in building systems on AWS."
    }
  ]
}
```

---

## 7. HTTP response — no relevant documents found

When the vector search returns no chunks (the knowledge base is empty or the
question is entirely unrelated to stored documents), the retrieval core returns
a fixed answer rather than calling the generation model.

```json
{
  "answer": "No relevant documents found.",
  "sources": []
}
```

---

## 8. HTTP response — 400 Bad Request (missing question)

```json
{
  "error": "Missing required field: question"
}
```

---

## 9. HTTP response — 503 Service Unavailable (models not configured)

Returned when the Lambda is deployed without `BEDROCK_EMBEDDING_MODEL_ID` or
`BEDROCK_GENERATION_MODEL_ID` set (e.g. on LocalStack).

```json
{
  "error": "Query service not configured"
}
```

---

## 10. Lambda log example (happy path)

```text
INFO  Received question (64 chars)
INFO  Embedded question using amazon.titan-embed-text-v2:0
INFO  Vector search returned 2 chunks (top_k=5)
INFO  Generated answer using anthropic.claude-3-haiku-20240307-v1:0
```
