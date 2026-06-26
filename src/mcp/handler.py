import asyncio
import base64
import logging
import os

import boto3
from mcp.server.fastmcp import FastMCP
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings

from src.query.retrieval import retrieve_and_answer, retrieve_chunks

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Tool registration only — session manager is created fresh per invocation
# because StreamableHTTPSessionManager.run() is one-shot per instance.
mcp = FastMCP("rag-assistant")


def _is_configured() -> bool:
    return bool(os.environ.get("BEDROCK_EMBEDDING_MODEL_ID"))


def _env() -> dict:
    return {
        "cluster_arn": os.environ["AURORA_CLUSTER_ARN"],
        "secret_arn": os.environ["AURORA_SECRET_ARN"],
        "database": os.environ.get("AURORA_DATABASE", "rag"),
        "embed_model": os.environ["BEDROCK_EMBEDDING_MODEL_ID"],
        "gen_model": os.environ["BEDROCK_GENERATION_MODEL_ID"],
        "top_k": int(os.environ.get("RETRIEVAL_TOP_K", "5")),
    }


def _clients():
    return boto3.client("rds-data"), boto3.client("bedrock-runtime")


@mcp.tool()
def search_documents(query: str) -> list[dict]:
    """Return the most relevant document chunks for a query."""
    if not _is_configured():
        return []
    e = _env()
    rds, br = _clients()
    chunks = retrieve_chunks(
        rds, br,
        e["cluster_arn"], e["secret_arn"], e["database"],
        query, e["embed_model"], e["top_k"],
    )
    logger.info("search_documents: returned %d chunks for query (%d chars)", len(chunks), len(query))
    return chunks


@mcp.tool()
def ask_question(question: str) -> dict:
    """Ask a question and receive a grounded answer with citations."""
    if not _is_configured():
        return {"answer": "Service not configured.", "sources": []}
    e = _env()
    rds, br = _clients()
    result = retrieve_and_answer(
        rds, br,
        e["cluster_arn"], e["secret_arn"], e["database"],
        question, e["embed_model"], e["gen_model"],
        top_k=e["top_k"],
    )
    logger.info(
        "ask_question: %d sources, embed=%s gen=%s",
        len(result["sources"]), e["embed_model"], e["gen_model"],
    )
    return result


# ── Lambda ASGI adapter ───────────────────────────────────────────────────────
#
# FastMCP's StreamableHTTPSessionManager.run() is one-shot per instance: it
# initialises an anyio task group tied to the current event loop, and refuses
# to run again after shutdown.  asyncio.run() creates a new event loop per
# Lambda invocation, so we cannot reuse a cached session manager across warm
# invocations.  Instead we keep the FastMCP object (which holds tool
# registrations via _mcp_server) and create a fresh StreamableHTTPSessionManager
# on every call, wrapping the same underlying server.
#
# stateless=True creates a new transport per request (no mcp-session-id needed),
# which matches Lambda's request-per-invocation model.
#
# DNS rebinding protection is disabled: it only makes sense for local-network
# servers, not for Lambda behind API Gateway.

_NO_REBIND = TransportSecuritySettings(enable_dns_rebinding_protection=False)


async def _asgi_call(session_manager: StreamableHTTPSessionManager, event: dict) -> dict:
    """Translate an API Gateway v2 event into ASGI and call the session manager."""
    request_context = event.get("requestContext", {})
    http_info = request_context.get("http", {})

    raw_headers = event.get("headers") or {}
    asgi_headers = [(k.lower().encode(), v.encode()) for k, v in raw_headers.items()]

    body_raw = event.get("body") or b""
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body_raw)
    elif isinstance(body_raw, str):
        body = body_raw.encode()
    else:
        body = body_raw

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "method": http_info.get("method", "POST"),
        "path": http_info.get("path", "/"),
        "query_string": event.get("rawQueryString", "").encode(),
        "root_path": "",
        "scheme": "https",
        "server": (request_context.get("domainName", "localhost"), 443),
        "client": (http_info.get("sourceIp", "127.0.0.1"), 0),
        "headers": asgi_headers,
    }

    status = 500
    resp_headers: dict[str, str] = {}
    chunks: list[bytes] = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict) -> None:
        nonlocal status
        if message["type"] == "http.response.start":
            status = message["status"]
            for k, v in message.get("headers", []):
                resp_headers[k.decode()] = v.decode()
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    await session_manager.handle_request(scope, receive, send)

    return {
        "statusCode": status,
        "headers": resp_headers,
        "body": b"".join(chunks).decode("utf-8", errors="replace"),
        "isBase64Encoded": False,
    }


async def _dispatch(event: dict) -> dict:
    """Create a fresh session manager, serve one request, tear it down."""
    # json_response=True: return responses as a single JSON body instead of SSE.
    # SSE streaming requires a persistent connection; Lambda BUFFERED mode must
    # return a complete response, so SSE would deadlock waiting for the stream
    # to be consumed.
    session_manager = StreamableHTTPSessionManager(
        app=mcp._mcp_server,
        stateless=True,
        json_response=True,
        security_settings=_NO_REBIND,
    )
    async with session_manager.run():
        return await _asgi_call(session_manager, event)


def handler(event, context):
    logger.debug(
        "MCP: method=%s path=%s",
        event.get("requestContext", {}).get("http", {}).get("method"),
        event.get("requestContext", {}).get("http", {}).get("path"),
    )
    return asyncio.run(_dispatch(event))
