## Summary
<!-- What does this PR do, in one or two sentences? -->

## Linked spec / issue
- Spec: `specs/<NNN-name>/spec.md`
- Closes #<!-- issue number -->

## Acceptance criteria
<!-- Copy each criterion from the spec and state how it is satisfied. -->
- [ ] criterion — satisfied by: <test name / change>
- [ ] criterion — satisfied by: <test name / change>

## Verification
- [ ] `ruff check .` clean
- [ ] `pytest tests/test_handler.py` passes locally
- [ ] CI green (unit + integration-localstack)

## Guardrails
- [ ] No secrets committed
- [ ] `uploads/` S3 event filter unchanged
- [ ] `handler.py` remains environment-agnostic
