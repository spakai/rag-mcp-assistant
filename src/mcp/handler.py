import logging
import os

import boto3
from mangum import Mangum
from mcp.server.fastmcp import FastMCP

from src.query.retrieval import retrieve_and_answer, retrieve_chunks

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

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


handler = Mangum(mcp.streamable_http_app())
