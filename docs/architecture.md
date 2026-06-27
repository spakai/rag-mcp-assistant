# Architecture — RAG knowledge assistant with MCP

A serverless retrieval-augmented-generation (RAG) system on AWS. Documents are
ingested into a vector store; users (and other AI agents, via MCP) ask questions
and get answers grounded in the documents, with citations. Built as a learning
project targeting AWS SAA-C03 and GitHub GH-600.

Diagrams are Mermaid flowcharts and render on GitHub.

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
---
config:
  layout: elk
---
flowchart TB
    user["Knowledge worker<br><i>Uploads docs · asks questions</i>"]
    agent["External AI agent<br><i>MCP client</i>"]
    bedrock["Amazon Bedrock<br><i>Titan Embed V2 · Nova Micro</i>"]

    subgraph rag["RAG Knowledge Assistant"]
        s3["S3 Document Bucket<br><i>documents/ prefix triggers ingestion</i>"]
        ingest["Ingestion Lambda<br><i>extract → chunk → embed → store</i>"]
        store[("Aurora Serverless v2<br><i>pgvector · chunks + vectors</i>")]
        queryapi["Query API<br><i>API Gateway v2 + Lambda · POST /ask</i>"]
        mcpserver["MCP Server<br><i>API Gateway v2 + Lambda · FastMCP</i>"]
    end

    user -->|"S3 PutObject"| s3
    s3 -->|"ObjectCreated event"| ingest
    ingest -->|"embed chunks · HTTPS"| bedrock
    ingest -->|"write vectors · Data API"| store
    user -->|"POST /ask · HTTPS"| queryapi
    agent -->|"tools/call · MCP"| mcpserver
    mcpserver -. "shared retrieval core" .-> queryapi
    queryapi -->|"vector search · Data API"| store
    queryapi -->|"generate answer · HTTPS"| bedrock

    classDef internal fill:#1168bd,stroke:#0b4884,color:#fff
    classDef db fill:#2d6a4f,stroke:#1b4332,color:#fff
    classDef external fill:#8a8a8a,stroke:#5e5e5e,color:#fff
    class s3,ingest,queryapi,mcpserver internal
    class store db
    class user,agent,bedrock external
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
