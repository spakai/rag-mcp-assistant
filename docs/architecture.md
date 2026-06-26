# Architecture — RAG knowledge assistant with MCP

A serverless retrieval-augmented-generation (RAG) system on AWS. Documents are
ingested into a vector store; users (and other AI agents, via MCP) ask questions
and get answers grounded in the documents, with citations. Built as a learning
project targeting AWS SAA-C03 and GitHub GH-600.

Diagrams are Mermaid (C4 model) and render on GitHub. Mermaid C4 is experimental,
so the tables describe the same thing in text.

## Two paths

- **Ingestion (write path):** document -> extract text -> chunk -> embed -> store
  vectors + metadata.
- **Query (read path):** question -> embed -> vector search (top-k) -> generate
  an answer from the retrieved context, with citations.

The query path is exposed two ways over the same core logic: a REST API for
humans/apps, and an **MCP server** so any agent can use it as a tool.

## Level 1 — System Context

```mermaid
---
config:
  layout: elk
---
flowchart TB
    user["Knowledge worker<br><i>Uploads documents, asks questions</i>"]
    agent["External AI agent<br><i>Uses the assistant as an MCP tool</i>"]
    rag["RAG Knowledge Assistant<br><i>Ingests docs; answers with citations</i>"]
    bedrock["Amazon Bedrock<br><i>Embeddings + text generation</i>"]

    user -->|"Uploads docs, asks questions<br>HTTPS / S3"| rag
    agent -->|"Queries via tools · MCP"| rag
    rag -->|"Embeds text, generates answers · HTTPS"| bedrock

    classDef internal fill:#1168bd,stroke:#0b4884,color:#fff
    classDef external fill:#8a8a8a,stroke:#5e5e5e,color:#fff
    class rag internal
    class user,agent,bedrock external
```

## Level 2 — Container

```mermaid
C4Container
    title Container Diagram — RAG knowledge assistant

    Person(user, "Knowledge worker")
    System_Ext(agent, "External AI agent", "MCP client")
    System_Ext(bedrock, "Amazon Bedrock", "Embeddings + LLM")

    System_Boundary(rag, "RAG Knowledge Assistant") {
        Container(docs, "Document Bucket", "Amazon S3", "Holds uploaded documents under documents/")
        Container(ingest, "Ingestion", "AWS Lambda (or Step Functions)", "Extract text, chunk, embed, store")
        ContainerDb(store, "Vector Store", "Aurora PostgreSQL Serverless v2 + pgvector", "Chunks, embeddings, document metadata")
        Container(api, "Query API", "API Gateway + Lambda", "Embed question, retrieve, generate answer")
        Container(mcp, "MCP Server", "Lambda", "Exposes search + ask as MCP tools")
    }

    Rel(user, docs, "Uploads documents", "S3 PutObject")
    Rel(docs, ingest, "ObjectCreated (documents/)", "S3 event")
    Rel(ingest, bedrock, "Embeds chunks", "HTTPS")
    Rel(ingest, store, "Writes chunks + vectors", "Data API")
    Rel(user, api, "Asks questions", "HTTPS")
    Rel(agent, mcp, "Calls tools", "MCP")
    Rel(api, store, "Vector search", "Data API")
    Rel(api, bedrock, "Generates answer", "HTTPS")
    Rel(mcp, api, "Reuses query core", "internal")
```

| Container | Technology | Notes |
|---|---|---|
| Document Bucket | Amazon S3 | `documents/` prefix triggers ingestion; same loop-prevention discipline as before. |
| Ingestion | Lambda (later Step Functions) | Single Lambda first; refactor to a state machine when retries/observability matter (a future ADR). |
| Vector Store | Aurora Serverless v2 + pgvector | Accessed via the **Data API** (HTTP/IAM) so Lambda needs no VPC. `min_capacity = 0` lets it idle near-free. |
| Query API | API Gateway + Lambda | Embeds the question, runs top-k search, calls Bedrock to answer with citations. |
| MCP Server | Lambda | Exposes `search_documents` and `ask_question` as MCP tools over the same retrieval core. |

## Cross-cutting

- **Secrets Manager** — Aurora credentials (no secrets in code).
- **Cognito** — auth on the API/MCP endpoints (introduced in a later spec).
- **CloudWatch** — logs, metrics, and traces (the GH-600 observability story).
- **VPC** — not required early thanks to the Aurora Data API; an optional later
  spec moves Aurora into private subnets with VPC endpoints (SAA-C03 networking).

## Key decisions (to become ADRs)

- **Vector store: Aurora Serverless v2 + pgvector, via the Data API.**
  OpenSearch Serverless was rejected on cost (a ~$700/month floor). Aurora can
  scale to zero when idle and the Data API removes the need to put Lambda in a
  VPC, while still teaching Aurora, Secrets Manager, and pgvector. **S3 Vectors**
  is the documented cheaper alternative (zero idle cost) if cost outweighs the
  relational/SAA-C03 learning value.
- **Bedrock for embeddings and generation.** Managed models; no model hosting,
  no GPUs, no inference servers to run.
- **One retrieval core, two transports.** The REST API and the MCP server call
  the same query logic, so behaviour can't drift between them.

## LocalStack vs real AWS

The same split as the prior project applies. The ingestion skeleton (S3 +
Lambda + a key/value store) runs on LocalStack for the fast inner loop. Bedrock
and the Aurora Data API are not in LocalStack's free tier, so from the embeddings
spec onward, integration tests that need them run against **real AWS**, gated by
an environment flag — exactly the pattern used for Rekognition previously.
