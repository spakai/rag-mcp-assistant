from typing import Generator


def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 100) -> Generator[dict, None, None]:
    if not text:
        return
    start = 0
    index = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk = text[start:end]
        yield {"chunk_index": index, "text": chunk, "char_count": len(chunk)}
        if end == len(text):
            break
        index += 1
        start += chunk_size - overlap
