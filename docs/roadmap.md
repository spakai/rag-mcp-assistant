# Roadmap

The build is sequenced into features, each one a full pass of the agentic loop
(spec -> plan -> approve -> implement test-first -> PR -> CI -> merge). Each
feature is small and shippable; complexity rises gradually. Numbers match the
`specs/NNN-*/` folders.

Difficulty and where integration tests run are noted so cost and scope are clear
up front.

## Core sequence

### 001 — Document ingestion skeleton  ·  LocalStack
Upload a `.txt`/`.pdf` under `documents/`; a Lambda extracts text, splits it into
overlapping chunks, and stores them in a key/value store (DynamoDB). No
embeddings, no vectors yet. Proves the ingestion trigger and chunking. Runs
entirely on LocalStack — cheap, fast, no Bedrock or Aurora.
*New surface:* the project skeleton; text extraction; chunking strategy.

### 002 — Embeddings + vector store  ·  real AWS
Add Bedrock Titan embeddings; provision Aurora Serverless v2 + pgvector (Data
API); store each chunk's vector and metadata. Integration tests now need real
AWS (Bedrock + Aurora aren't in LocalStack free tier), gated by an env flag.
*New surface:* Bedrock embeddings, Aurora Serverless v2, pgvector, Secrets
Manager, the Data API.

### 003 — Query / retrieval API  ·  real AWS
API Gateway + Lambda. Embed the incoming question, run a top-k vector search,
pass the retrieved chunks to Bedrock to generate an answer that cites its
sources.
*New surface:* API Gateway, the RAG read path, prompt construction, citations.

### 004 — MCP server  ·  real AWS
Expose the knowledge base as an MCP server with two tools: `search_documents`
(returns relevant chunks) and `ask_question` (returns a grounded answer). Host
it on Lambda over the same retrieval core as the REST API.
*New surface:* MCP (the highest-weighted GH-600 domain), tool schemas, the
single-core/two-transports pattern.

### 005 — Evaluation harness  ·  CI + real AWS
A labelled question/answer set and an evaluation that scores retrieval relevance
and answer quality, run as a gate. Lets you measure whether a change improves or
regresses the system instead of guessing.
*New surface:* evaluation / error analysis (a core GH-600 domain), regression
gating for an AI system.

## Optional hardening (any order, each its own spec + ADR)

### 006 — Auth + multi-tenancy
Cognito on the API and MCP endpoints; per-user document isolation so one tenant
can't retrieve another's chunks.
*New surface:* Cognito, authorization, tenant isolation.

### 007 — Step Functions orchestration
Replace the single ingestion Lambda with a Step Functions state machine
(extract -> chunk -> embed -> store) for retries, partial-failure handling, and
visibility. Write an ADR recording the change from 002's design.
*New surface:* Step Functions, saga/retry patterns, orchestration vs choreography.

### 008 — VPC hardening
Move Aurora into private subnets; reach Bedrock and S3 via VPC endpoints; place
the Lambdas in the VPC. Removes the Data-API-avoids-VPC shortcut in favour of the
production networking posture.
*New surface:* VPC, subnets, security groups, VPC endpoints, NAT trade-offs —
the SAA-C03 networking block the earlier specs deliberately skipped.

## How to use this roadmap

Do 001 first to get the skeleton and loop running, then 002–005 in order for the
full RAG-plus-MCP system. Pick up the optional specs when you want to drill a
specific exam area. Each feature begins by writing `specs/NNN-name/spec.md` as
testable acceptance criteria, exactly as 001 below.
