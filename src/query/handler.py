import json
import logging
import os

import boto3

from .retrieval import retrieve_and_answer

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handler(event, context):
    embedding_model_id = os.environ.get("BEDROCK_EMBEDDING_MODEL_ID")
    generation_model_id = os.environ.get("BEDROCK_GENERATION_MODEL_ID")

    if not embedding_model_id or not generation_model_id:
        return _response(503, {"error": "Query service not configured"})

    cluster_arn = os.environ["AURORA_CLUSTER_ARN"]
    secret_arn = os.environ["AURORA_SECRET_ARN"]
    database = os.environ.get("AURORA_DATABASE", "rag")
    top_k = int(os.environ.get("RETRIEVAL_TOP_K", "5"))

    body_raw = event.get("body") or "{}"
    try:
        body = json.loads(body_raw)
    except json.JSONDecodeError:
        return _response(400, {"error": "Invalid JSON body"})

    question = body.get("question", "").strip()
    if not question:
        return _response(400, {"error": "Missing required field: question"})

    logger.info("Received question (%d chars)", len(question))

    bedrock = boto3.client("bedrock-runtime")
    rdsdata = boto3.client("rds-data")

    result = retrieve_and_answer(
        rdsdata,
        bedrock,
        cluster_arn,
        secret_arn,
        database,
        question,
        embedding_model_id,
        generation_model_id,
        top_k=top_k,
    )

    logger.info(
        "Returned %d sources using embed=%s gen=%s",
        len(result["sources"]),
        embedding_model_id,
        generation_model_id,
    )

    return _response(200, result)


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
