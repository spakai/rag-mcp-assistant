#!/usr/bin/env python3
"""Idempotent DDL: creates pgvector extension and tables in Aurora via the Data API.

Usage:
    python scripts/init_db.py

Required env vars (read from Terraform outputs or set manually):
    AURORA_CLUSTER_ARN
    AURORA_SECRET_ARN
    AURORA_DATABASE   (default: rag)
    AWS_DEFAULT_REGION (or AWS_REGION)
"""
import os

import boto3

CLUSTER_ARN = os.environ["AURORA_CLUSTER_ARN"]
SECRET_ARN = os.environ["AURORA_SECRET_ARN"]
DATABASE = os.environ.get("AURORA_DATABASE", "rag")

STATEMENTS = [
    "CREATE EXTENSION IF NOT EXISTS vector",
    """CREATE TABLE IF NOT EXISTS documents (
    document_id  TEXT        PRIMARY KEY,
    source_key   TEXT        NOT NULL UNIQUE,
    chunk_count  INTEGER     NOT NULL,
    status       TEXT        NOT NULL DEFAULT 'embedded',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
)""",
    """CREATE TABLE IF NOT EXISTS chunks (
    document_id  TEXT        NOT NULL,
    chunk_index  INTEGER     NOT NULL,
    text         TEXT        NOT NULL,
    char_count   INTEGER     NOT NULL,
    source_key   TEXT        NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL,
    embedding    vector(1024),
    PRIMARY KEY (document_id, chunk_index)
)""",
    "CREATE INDEX IF NOT EXISTS chunks_source_key_idx ON chunks (source_key)",
    "CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw_idx ON chunks USING hnsw (embedding vector_cosine_ops)",
]


def main() -> None:
    client = boto3.client("rds-data")
    for sql in STATEMENTS:
        client.execute_statement(
            resourceArn=CLUSTER_ARN,
            secretArn=SECRET_ARN,
            database=DATABASE,
            sql=sql,
        )
        print(f"  OK: {sql[:60]}...")
    print("Aurora schema initialised.")


if __name__ == "__main__":
    main()
