import json
import time

from botocore.exceptions import ClientError


def embed_chunks(
    bedrock_client,
    chunks: list[dict],
    model_id: str,
    max_retries: int = 3,
) -> list[dict]:
    for chunk in chunks:
        text = chunk["text"]
        chunk["embedding"] = _embed_text(
            bedrock_client,
            text,
            model_id,
            max_retries=max_retries,
        )
    return chunks


def _embed_text(bedrock_client, text: str, model_id: str, max_retries: int) -> list[float]:
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            response = bedrock_client.invoke_model(
                modelId=model_id,
                body=json.dumps(
                    {
                        "inputText": text,
                        "dimensions": 1024,
                        "normalize": True,
                    }
                ),
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
