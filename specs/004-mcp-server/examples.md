# Spec 004 — Example Payloads

Concrete examples of the MCP protocol interactions and data shapes introduced by
the MCP server. All values are illustrative; session IDs, embeddings, and chunk
content will differ in practice.

The MCP server exposes its endpoint at `<mcp_function_url>/mcp` (Streamable HTTP
transport, JSON-RPC 2.0).

---

## 1. MCP handshake — `initialize`

Every MCP session begins with an `initialize` / `initialized` exchange. The
`mcp` Python client SDK handles this automatically when you call
`session.initialize()`.

**Client → Server:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2025-03-26",
    "capabilities": {},
    "clientInfo": { "name": "mcp-client", "version": "1.0" }
  }
}
```

**Server → Client:**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": "2025-03-26",
    "capabilities": { "tools": {} },
    "serverInfo": { "name": "rag-assistant", "version": "1.0.0" }
  }
}
```

---

## 2. `tools/list` — tool schema discovery

**Client → Server:**
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/list",
  "params": {}
}
```

**Server → Client:**
```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "tools": [
      {
        "name": "search_documents",
        "description": "Return the most relevant document chunks for a query.",
        "inputSchema": {
          "type": "object",
          "properties": {
            "query": { "type": "string" }
          },
          "required": ["query"]
        }
      },
      {
        "name": "ask_question",
        "description": "Ask a question and receive a grounded answer with citations.",
        "inputSchema": {
          "type": "object",
          "properties": {
            "question": { "type": "string" }
          },
          "required": ["question"]
        }
      }
    ]
  }
}
```

---

## 3. `tools/call` — `search_documents`

Returns the top-k most semantically similar chunks for a query. Uses the same
Bedrock embedding + pgvector cosine search as the REST API; generation is
**not** called.

**Client → Server:**
```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    "name": "search_documents",
    "arguments": {
      "query": "What are the five pillars of the AWS Well-Architected Framework?"
    }
  }
}
```

**Server → Client (happy path):**
```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "[{\"source_key\": \"documents/aws-well-architected.txt\", \"chunk_index\": 0, \"text\": \"The AWS Well-Architected Framework is organized around five pillars: Operational Excellence, Security, Reliability, Performance Efficiency, and Cost Optimization.\"}, {\"source_key\": \"documents/aws-well-architected.txt\", \"chunk_index\": 1, \"text\": \"Each pillar contains design principles and best practices that help architects understand the trade-offs involved in building systems on AWS.\"}]"
      }
    ],
    "isError": false
  }
}
```

The `text` field is JSON-encoded. Callers should `json.loads(result.content[0].text)`
to get the list of chunk dicts.

**Server → Client (empty knowledge base / no match):**
```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "content": [{ "type": "text", "text": "[]" }],
    "isError": false
  }
}
```

**Server → Client (service not configured — env vars absent):**
```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "content": [{ "type": "text", "text": "[]" }],
    "isError": false
  }
}
```

---

## 4. `tools/call` — `ask_question`

Runs the full RAG pipeline: embed → vector search → Bedrock generation.
Returns a grounded answer with source citations.

**Client → Server:**
```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "method": "tools/call",
  "params": {
    "name": "ask_question",
    "arguments": {
      "question": "What are the five pillars of the AWS Well-Architected Framework?"
    }
  }
}
```

**Server → Client (happy path):**
```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "{\"answer\": \"According to documents/aws-well-architected.txt, the five pillars are: Operational Excellence, Security, Reliability, Performance Efficiency, and Cost Optimization.\", \"sources\": [{\"source_key\": \"documents/aws-well-architected.txt\", \"chunk_index\": 0, \"text\": \"The AWS Well-Architected Framework is organized around five pillars...\"}, {\"source_key\": \"documents/aws-well-architected.txt\", \"chunk_index\": 1, \"text\": \"Each pillar contains design principles...\"}]}"
      }
    ],
    "isError": false
  }
}
```

The `text` field is JSON-encoded. Callers should `json.loads(result.content[0].text)`
to get `{"answer": "...", "sources": [...]}`.

**Server → Client (no relevant documents):**
```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "{\"answer\": \"No relevant documents found.\", \"sources\": []}"
      }
    ],
    "isError": false
  }
}
```

**Server → Client (service not configured — env vars absent):**
```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "{\"answer\": \"Service not configured.\", \"sources\": []}"
      }
    ],
    "isError": false
  }
}
```

---

## 5. Python client usage (mcp SDK)

```python
import json
import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

MCP_URL = "https://<id>.lambda-url.<region>.on.aws/mcp"

async def main():
    async with streamablehttp_client(MCP_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # List available tools
            tools = await session.list_tools()
            print([t.name for t in tools.tools])
            # → ['search_documents', 'ask_question']

            # Search for relevant chunks
            result = await session.call_tool(
                "search_documents",
                {"query": "What are the five pillars of the AWS Well-Architected Framework?"},
            )
            chunks = json.loads(result.content[0].text)
            for chunk in chunks:
                print(chunk["source_key"], chunk["chunk_index"])

            # Get a grounded answer
            result = await session.call_tool(
                "ask_question",
                {"question": "What are the five pillars of the AWS Well-Architected Framework?"},
            )
            payload = json.loads(result.content[0].text)
            print(payload["answer"])
            print(payload["sources"])

asyncio.run(main())
```

---

## 6. Terraform output — MCP endpoint

After `bash scripts/deploy-aws.sh`, the Function URL is available as a Terraform
output. Append `/mcp` to reach the MCP endpoint:

```text
Outputs:

mcp_function_url = "https://abcdef1234567890.lambda-url.us-east-1.on.aws/"

# MCP endpoint:  https://abcdef1234567890.lambda-url.us-east-1.on.aws/mcp
```

---

## 7. Lambda log examples

**`search_documents` call:**
```text
INFO  search_documents: returned 2 chunks for query (64 chars)
```

**`ask_question` call:**
```text
INFO  ask_question: 2 sources, embed=amazon.titan-embed-text-v2:0 gen=amazon.nova-micro-v1:0
```
