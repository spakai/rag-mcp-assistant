# Agentic SDLC Summary

This document describes the structured software development workflow used in this project,
where AI agents handle implementation while humans maintain control over critical decisions.
It extends the pattern established in the prior project with lessons learned from the first
feature cycle.

## Core Structure

Two distinct roles:

- **Principal (human)**: Writes requirements, approves plans, reviews diffs, merges to main
- **Delegate (AI agent)**: Proposes plans, implements test-first, fixes failures, keeps CI green

The delegate never pushes to `main` directly. Every change flows through a PR gated by CI.

## The Development Loop

```
Issue → spec.md → plan.md (approved) → implementation → CI green → merge
```

1. **Requirements** — Human writes testable acceptance criteria in `specs/NNN-name/spec.md`
   and opens a GitHub Issue. Criteria are checkboxes; a feature is done only when all pass.

2. **Planning (Plan Mode)** — Agent explores the codebase, proposes approach in `plan.md`
   covering: files to create, data model, algorithm, infrastructure, cost impact, risks.
   No code is written until the plan is approved.

3. **Approval** — Human reads `plan.md` and either approves or redirects. This is the
   primary control point: the human is buying the _approach_, not just the outcome.

4. **Implementation** — Agent writes tests first, then code to satisfy them. Unit tests
   run with mocked AWS clients (no network). Infrastructure is Terraform only.

5. **Verification** — Two layers:
   - Agent runs `pytest tests/ -q` and `ruff check .` locally before committing
   - CI pipeline (GitHub Actions) re-verifies independently: unit job then
     integration-localstack job (real LocalStack, real Terraform apply)

6. **Merge** — Human clicks Merge on the PR after CI is green. Agent updates `spec.md`
   status and ticks acceptance criteria checkboxes before the final merge commit.

## Guardrails

Documented in `AGENTS.md` and enforced by convention:

| Guardrail | Reason |
|---|---|
| Never push to `main` directly | All changes gate on CI |
| Never commit secrets | Aurora/Bedrock credentials come from Secrets Manager/IAM at runtime |
| Keep `documents/` S3 filter | Prevents re-trigger loops on derived objects |
| Terraform only for infra | Scripts creating resources imperatively break reproducibility |
| One retrieval core, two transports | REST API and MCP server must not drift |
| `min_capacity = 0` on Aurora | Prevents idle cost from accumulating |

## Tooling

| Tool | Role |
|---|---|
| Claude Code (interactive) | Plan Mode → implementation → local verification |
| GitHub Actions | Independent CI: unit + LocalStack integration |
| LocalStack | Free-tier AWS emulation for S3, Lambda, DynamoDB, IAM |
| Terraform + tflocal | Reproducible infra, same config for local and real AWS |
| `gh` CLI | PR creation, CI log streaming, re-running failed jobs |

## Spec 001 — Lessons Learned

The first full feature cycle (document ingestion skeleton) produced several concrete lessons.

### 1. Test the LocalStack SERVICES list before pushing

**What happened:** CI failed immediately with:
```
Service 'iam' is not enabled. Please check your 'SERVICES' configuration variable.
```
The `docker-compose.yml` declared `SERVICES=s3,lambda,dynamodb` but Terraform also creates
IAM roles, which requires `iam` in the list.

**Fix:** `SERVICES=s3,lambda,dynamodb,iam`

**Lesson:** Run `bash scripts/deploy-local.sh` locally end-to-end before pushing. If any
Terraform resource depends on a service not in `SERVICES`, it fails at apply time, not at
plan time — so the plan looking clean is not enough.

---

### 2. boto3 clients outside LocalStack need explicit fake credentials

**What happened:** Integration tests failed with `NoCredentialsError` even though LocalStack
was running and Terraform applied cleanly.

**Root cause:** `tflocal` injects credentials for Terraform. But pytest runs as a separate
process with no `AWS_ACCESS_KEY_ID` set in the CI environment. boto3 has no credentials to
sign requests — even to LocalStack, which doesn't validate them.

**Fix:** Pass `aws_access_key_id="test", aws_secret_access_key="test"` explicitly to every
boto3 client in the integration test fixture.

**Lesson:** Any code that calls LocalStack from _outside_ a container (scripts, tests,
local tools) needs fake credentials explicitly set. `tflocal` handles it for Terraform;
nothing handles it for pytest automatically.

---

### 3. Don't both trigger and directly invoke the Lambda in the same test

**What happened:** Tests found 2 document records when expecting 1.

**Root cause:** `_invoke_ingest` was doing two things:
1. `s3.put_object` → S3 event notification fires → Lambda runs → record #1
2. Direct `lambda.invoke` with a synthetic event → Lambda runs again → record #2

The idempotency logic can't protect against this because both invocations race
concurrently before either can see the other's write.

**Fix:** Remove the direct Lambda invocation. Upload to S3 only, then poll DynamoDB
until the S3-triggered Lambda write appears (with a timeout).

**Lesson:** If the infrastructure wires a trigger (S3 → Lambda), the integration test
should exercise that trigger path, not bypass it. Direct invocation is useful for unit
testing the handler in isolation (use moto for that), not for end-to-end tests where the
trigger chain is the thing being verified.

---

### 4. Authenticate `gh` early — it unblocks autonomous CI monitoring

**What happened:** `gh pr create --fill` failed before auth, pushing the entire
debug-fix cycle into a manual paste-logs-here loop.

**Fix:** `gh auth login` (one-time, persists).

**Lesson:** With `gh` authenticated, the agent can run `gh run view --log-failed`
directly, diagnose CI failures, push fixes, and loop — without the human relaying logs.
The `/loop` skill enables fully autonomous CI babysitting:
> "Watch CI on this PR and fix any failures"

---

### 5. Verify locally before every push, not just before the first push

Each of the three CI failures above could have been caught with:
```bash
docker compose up -d
bash scripts/deploy-local.sh
pytest tests/integration/ -q
```

The temptation after a plan approval is to push immediately and "let CI verify it."
CI is the safety net, not the primary feedback loop. Local verification is faster
(seconds vs minutes) and keeps the commit history clean.

## What the Human Controls

| Decision | Why human-owned |
|---|---|
| Acceptance criteria | Defines what "done" means — the agent can't know this |
| Plan approval | Buys the approach before work is sunk into it |
| Secret values | `LOCALSTACK_AUTH_TOKEN`, AWS credentials — never in code |
| Merge to main | Final review of the diff; CI being green is necessary but not sufficient |
| Cost trade-offs | e.g. Aurora vs S3 Vectors — learning value vs zero idle cost |

## What the Agent Controls

| Task | Notes |
|---|---|
| Codebase exploration | `find`, `grep`, `Read` before writing anything |
| Plan authoring | Proposes, doesn't decide |
| Test-first implementation | Tests define done; code satisfies tests |
| Local verification loop | `ruff` + `pytest` before every commit |
| CI failure diagnosis | Reads logs, identifies root cause, pushes fix |
| Spec housekeeping | Updates status, ticks criteria checkboxes on merge |
