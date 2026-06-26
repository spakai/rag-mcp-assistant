"""
Real-AWS integration tests for spec 004 — MCP server.

Skipped unless RUN_AWS_INTEGRATION=1 is set.

Prerequisites:
    bash scripts/deploy-aws.sh   # deploys infra (includes rag-mcp Lambda + API Gateway)
    # At least one document must be ingested so search/ask return real results.
"""
import json
import os

import pytest

RUN_AWS = os.environ.get("RUN_AWS_INTEGRATION") == "1"
SKIP_REASON = "Set RUN_AWS_INTEGRATION=1 and deploy via scripts/deploy-aws.sh to run"

INFRA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "infra")


def _tf_outputs() -> dict:
    raw = os.popen(f"cd {INFRA_DIR} && terraform output -json 2>/dev/null").read()
    return json.loads(raw)


@pytest.fixture(scope="module")
def mcp_url():
    outputs = _tf_outputs()
    url = outputs.get("mcp_endpoint", {}).get("value", "")
    assert url, "mcp_endpoint Terraform output is empty — has deploy-aws.sh been run?"
    return url


@pytest.mark.skipif(not RUN_AWS, reason=SKIP_REASON)
@pytest.mark.asyncio
async def test_tools_list(mcp_url):
    """MCP server must advertise search_documents and ask_question."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(mcp_url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()

    tool_names = {t.name for t in result.tools}
    assert "search_documents" in tool_names
    assert "ask_question" in tool_names


@pytest.mark.skipif(not RUN_AWS, reason=SKIP_REASON)
@pytest.mark.asyncio
async def test_search_documents_returns_list(mcp_url):
    """search_documents returns a list (may be empty if no docs ingested)."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(mcp_url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("search_documents", {"query": "test"})

    assert not result.isError
    # Empty content means the tool returned [] (no docs ingested yet); that's valid.
    if result.content:
        chunks = json.loads(result.content[0].text)
        assert isinstance(chunks, list)


@pytest.mark.skipif(not RUN_AWS, reason=SKIP_REASON)
@pytest.mark.asyncio
async def test_ask_question_returns_answer_and_sources(mcp_url):
    """ask_question returns a dict with answer (str) and sources (list)."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(mcp_url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "ask_question", {"question": "What documents are in the knowledge base?"}
            )

    assert not result.isError
    payload = json.loads(result.content[0].text)
    assert "answer" in payload
    assert isinstance(payload["answer"], str)
    assert len(payload["answer"]) > 0
    assert "sources" in payload
    assert isinstance(payload["sources"], list)
