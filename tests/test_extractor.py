import io
from unittest.mock import MagicMock

import pytest

from src.ingestion.extractor import extract_text


def _mock_s3(body: bytes) -> MagicMock:
    s3 = MagicMock()
    s3.get_object.return_value = {"Body": io.BytesIO(body)}
    return s3


def test_txt_extraction():
    content = "Hello, world!"
    s3 = _mock_s3(content.encode("utf-8"))
    result = extract_text(s3, "bucket", "documents/file.txt")
    assert result == content


def test_txt_extension_case_insensitive():
    content = "uppercase ext"
    s3 = _mock_s3(content.encode("utf-8"))
    result = extract_text(s3, "bucket", "documents/file.TXT")
    assert result == content


def test_pdf_extraction_returns_string():
    pytest.importorskip("pypdf")
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)

    buf = io.BytesIO()
    writer.write(buf)
    buf.seek(0)

    s3 = _mock_s3(buf.read())
    result = extract_text(s3, "bucket", "documents/file.pdf")
    assert isinstance(result, str)


def test_s3_called_with_correct_args():
    s3 = _mock_s3(b"data")
    extract_text(s3, "my-bucket", "documents/note.txt")
    s3.get_object.assert_called_once_with(Bucket="my-bucket", Key="documents/note.txt")
