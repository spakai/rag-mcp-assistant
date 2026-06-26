import json
import time

from botocore.exceptions import ClientError


def retrieve_and_answer(
    rdsdata_client,
    bedrock_client,
    cluster_arn: str,
    secret_arn: str,
    database: str,
    question: str,
    embedding_model_id: str,
    generation_model_id: str,
    top_k: int = 5,
) -> dict:
    query_embedding = _embed_question(bedrock_client, question, embedding_model_id)
    chunks = _search_chunks(rdsdata_client, cluster_arn, secret_arn, database, query_embedding, top_k)
    answer = _generate_answer(bedrock_client, chunks, question, generation_model_id)
    sources = [
        {"source_key": c["source_key"], "chunk_index": c["chunk_index"], "text": c["text"]}
        for c in chunks
    ]
    return {"answer": answer, "sources": sources}


def _embed_question(bedrock_client, question: str, model_id: str, max_retries: int = 3) -> list[float]:
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            response = bedrock_client.invoke_model(
                modelId=model_id,
                body=json.dumps({"inputText": question, "dimensions": 1024, "normalize": True}),
                accept="application/json",
                contentType="application/json",
            )
            body = response["body"]
            raw = body.read() if hasattr(body, "read") else body
            payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
            return payload["embedding"]
        except ClientError as exc:
            last_error = exc
            if (
                exc.response.get("Error", {}).get("Code") == "ThrottlingException"
                and attempt < max_retries
            ):
                time.sleep(2**attempt)
                continue
            raise
    if last_error is not None:
        raise last_error
    raise RuntimeError("Embedding failed")


def _search_chunks(
    rdsdata_client,
    cluster_arn: str,
    secret_arn: str,
    database: str,
    query_embedding: list[float],
    top_k: int,
) -> list[dict]:
    response = rdsdata_client.execute_statement(
        resourceArn=cluster_arn,
        secretArn=secret_arn,
        database=database,
        sql=(
            "SELECT chunk_index, text, source_key "
            "FROM chunks "
            "ORDER BY embedding <=> :qv::vector "
            "LIMIT :k"
        ),
        parameters=[
            {"name": "qv", "value": {"stringValue": json.dumps(query_embedding)}},
            {"name": "k", "value": {"longValue": top_k}},
        ],
        includeResultMetadata=True,
    )

    columns = [col["label"] for col in response.get("columnMetadata", [])]
    rows = response.get("records", [])
    chunks = []
    for row in rows:
        record = {}
        for col, field in zip(columns, row):
            value = list(field.values())[0]
            record[col] = value
        chunks.append(record)
    return chunks


def _generate_answer(
    bedrock_client,
    chunks: list[dict],
    question: str,
    model_id: str,
    max_retries: int = 3,
) -> str:
    if not chunks:
        return "No relevant documents found."

    context_parts = []
    for chunk in chunks:
        context_parts.append(
            f"[source: {chunk['source_key']}, chunk {chunk['chunk_index']}]\n{chunk['text']}"
        )
    context = "\n\n".join(context_parts)

    prompt = (
        "Answer the question using ONLY the context below. "
        "Cite sources by their source_key. "
        "If the answer is not in the context, say 'No relevant documents found.'\n\n"
        f"Context:\n{context}\n\n"
        f"Question: {question}"
    )

    # Use the Converse API — model-agnostic across Claude, Nova, and other families.
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            response = bedrock_client.converse(
                modelId=model_id,
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"maxTokens": 1024},
            )
            return response["output"]["message"]["content"][0]["text"]
        except ClientError as exc:
            last_error = exc
            if (
                exc.response.get("Error", {}).get("Code") == "ThrottlingException"
                and attempt < max_retries
            ):
                time.sleep(2**attempt)
                continue
            raise
    if last_error is not None:
        raise last_error
    raise RuntimeError("Generation failed")
