import logging
import os
import urllib.parse

import boto3

from .chunker import chunk_text
from .embedder import embed_chunks
from .extractor import extract_text
from .store import replace_document, update_document_status
from .vector_store import replace_document_vectors

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

SUPPORTED_EXTENSIONS = {".txt", ".pdf"}


def handler(event, context):
    documents_table = os.environ["DOCUMENTS_TABLE"]
    chunks_table = os.environ["CHUNKS_TABLE"]
    chunk_size = int(os.environ.get("CHUNK_SIZE", "1000"))
    chunk_overlap = int(os.environ.get("CHUNK_OVERLAP", "100"))

    endpoint_url = os.environ.get("AWS_ENDPOINT_URL")
    s3 = boto3.client("s3", endpoint_url=endpoint_url)
    dynamo = boto3.client("dynamodb", endpoint_url=endpoint_url)

    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        _process(s3, dynamo, bucket, key, documents_table, chunks_table, chunk_size, chunk_overlap)


def _process(s3, dynamo, bucket, key, documents_table, chunks_table, chunk_size, chunk_overlap):
    ext = _extension(key)
    if ext not in SUPPORTED_EXTENSIONS:
        logger.warning("Skipping unsupported file type: %s", key)
        return

    logger.info("Ingesting %s", key)
    text = extract_text(s3, bucket, key)

    if not text.strip():
        logger.warning("No extractable text in %s", key)

    chunks = list(chunk_text(text, chunk_size, chunk_overlap))
    document_id = replace_document(dynamo, documents_table, chunks_table, key, chunks)
    logger.info("Stored %d chunks for document_id=%s source_key=%s", len(chunks), document_id, key)

    model_id = os.environ.get("BEDROCK_EMBEDDING_MODEL_ID")
    if not model_id:
        return

    bedrock = boto3.client("bedrock-runtime")
    rdsdata = boto3.client("rds-data")

    embed_chunks(bedrock, chunks, model_id)
    replace_document_vectors(
        rdsdata,
        os.environ["AURORA_CLUSTER_ARN"],
        os.environ["AURORA_SECRET_ARN"],
        os.environ.get("AURORA_DATABASE", "rag"),
        key,
        document_id,
        chunks,
    )
    update_document_status(dynamo, documents_table, document_id, "embedded")
    logger.info(
        "Embedded %d chunks for document_id=%s source_key=%s",
        len(chunks),
        document_id,
        key,
    )


def _extension(key: str) -> str:
    dot = key.rfind(".")
    return key[dot:].lower() if dot != -1 else ""
