# AGENTS.md

Operating guide for AI coding agents working in this repository. Read this
fully before planning or editing. Humans: this doubles as the contributor guide.

## What this project is

A serverless image-processing pipeline, built as a learning project for the
AWS SAA-C03 and GitHub GH-600 exams. An image uploaded to S3 under `uploads/`
fires an S3 event that invokes a Lambda, which resizes the image, runs object
detection, and writes metadata to DynamoDB. One Terraform codebase deploys to
**LocalStack** (local + CI) and to **real AWS** (where object detection runs
for real).

```
Upload -> S3 (uploads/) -> Lambda --+-> S3 (resized/)
                                    +-> Rekognition (labels)
                                    +-> DynamoDB (metadata)
```

Fuller architecture and the diagram live in `README.md`. Treat the README as
the source of truth for architecture; keep it updated when the design changes.

## Repository layout

- `infra/` — Terraform: S3 bucket, DynamoDB table, IAM role, Lambda, and the
  S3 -> Lambda notification. The notification is filtered to the `uploads/`
  prefix on purpose (see Guardrails).
- `src/processor/handler.py` — the Lambda. Resize -> detect -> persist.
- `src/processor/requirements.txt` — Lambda runtime deps (Pillow only).
- `tests/test_handler.py` — unit tests. Real Pillow, mocked AWS clients. No
  network, no LocalStack.
- `tests/integration/test_localstack.py` — uploads an image to a live
  LocalStack stack and polls for the resized object + DynamoDB row.
- `scripts/build.sh` — packages the Lambda zip with Linux-built Pillow wheels.
- `scripts/deploy-local.sh` — builds then `tflocal apply` to LocalStack.
- `scripts/deploy-aws.sh` — builds then `terraform apply` to real AWS.
- `scripts/seed-local.sh` — uploads a sample image locally and prints results.
- `docker-compose.yml` — LocalStack.
- `.github/workflows/ci.yml` — CI: `unit` job, then `integration-localstack`.

## How to run things

Unit tests (fast, no infra — run these constantly while iterating):
```
pytest tests/test_handler.py
ruff check .
```

Local end-to-end against LocalStack (needs Docker + the auth token, see below):
```
export LOCALSTACK_AUTH_TOKEN=ls-...     # required since LocalStack 2026.03
docker compose up -d
bash scripts/deploy-local.sh
bash scripts/seed-local.sh
```

Real AWS (Rekognition fires for real here):
```
bash scripts/deploy-aws.sh
```

## Conventions

- Python 3.12. Keep `ruff check .` clean — CI fails on lint errors.
- The same `handler.py` must run unchanged on LocalStack and AWS. Differences
  in behaviour are controlled by environment variables, never by code branches
  that detect the environment.
- Terraform is the only way infrastructure changes. Do not create AWS/LocalStack
  resources imperatively in scripts or code; add them to `infra/`.
- Tests define "done." A feature is not complete until there is a test that
  asserts its acceptance criteria and that test passes in CI.

## Guardrails — do not violate

- **Never commit secrets.** `LOCALSTACK_AUTH_TOKEN` and any AWS credentials come
  from the environment or GitHub Actions secrets. They must never appear in
  committed files, including Terraform, tests, or this repo's history.
- **Never push directly to `main`.** Open a pull request and let CI gate it.
- **Keep the `uploads/` event filter.** The Lambda writes the resized image back
  to the same bucket under `resized/`. The S3 notification is filtered to the
  `uploads/` prefix so that write does not re-trigger the Lambda. Removing or
  broadening that filter creates an infinite, billable invocation loop.
- **Rekognition is env-gated.** It is unavailable on LocalStack's free tier, so
  `ENABLE_REKOGNITION=false` locally and the handler tolerates its absence. Do
  not make the pipeline hard-depend on Rekognition succeeding.
- **Pillow is a native dependency.** It must be packaged for the Lambda runtime
  (Amazon Linux), which is why `build.sh` pulls the `manylinux2014_x86_64`
  wheel. Do not replace that with a plain `pip install` of Pillow into the zip.
- **Build before apply.** Terraform references `build/processor.zip`; run
  `build.sh` (the deploy scripts do this) before any `apply`.

## Workflow for a new feature

1. Start from a GitHub Issue whose acceptance criteria are phrased as testable
   conditions (e.g. "a `thumbnails/` object is created, 150x150, and the
   DynamoDB record gains a `thumbnail_key` field").
2. Propose a short plan: which files change and why. Wait for human approval
   before large or destructive changes.
3. Implement test-first where practical: add/extend the unit test (and the
   integration test if the change is observable end-to-end) so "done" is defined
   before the code exists.
4. Make the code and Terraform changes. Keep `handler.py` environment-agnostic.
5. Run `pytest tests/test_handler.py` and `ruff check .` locally until green.
6. Open a pull request. CI runs unit tests then the LocalStack integration job.
   Green CI is the objective gate for merge.

## Repo-specific gotchas

- LocalStack requires `LOCALSTACK_AUTH_TOKEN` even for free-tier services since
  the 2026.03 release; without it the container exits immediately and nothing
  listens on port 4566. The token is a free Hobby-plan token, set locally as an
  env var and in CI as the `LOCALSTACK_AUTH_TOKEN` repo secret.
- On WSL/GitHub runners, prefer `127.0.0.1` over `localhost` for the LocalStack
  endpoint to avoid IPv6 (`::1`) resolution surprises.
- Lambda runs in its own container, so `docker-compose.yml` mounts the Docker
  socket. The host needs a running Docker daemon.
