import io


def extract_text(s3_client, bucket: str, key: str) -> str:
    response = s3_client.get_object(Bucket=bucket, Key=key)
    body = response["Body"].read()

    if key.lower().endswith(".pdf"):
        return _extract_pdf(body)
    return body.decode("utf-8")


def _extract_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages)
