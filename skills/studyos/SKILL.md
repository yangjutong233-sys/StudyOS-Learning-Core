---
name: studyos
description: "Operate a local StudyOS learning archive using its CLI: initialize an empty archive, preserve genuine learning reports, prepare independent assessments, inspect reminders, and verify generated handoffs. Use only for StudyOS archive operations, not for teaching or unrelated databases."
---

# StudyOS archive operations / 档案运营

Read the repository-root AGENTS.md first. Use docs/AI_QUICKSTART.md for onboarding and docs/CLI.md for input contracts. This skill has no machine-specific root or learner profile. Resolve the selected archive explicitly and use its absolute path as ROOT.

先读根目录AGENTS.md；首次操作参照docs/AI_QUICKSTART.md，字段契约参照docs/CLI.md。本技能不内置个人档案路径或学习偏好。ROOT必须是已确认的数据根目录。

## Resume / 恢复

```sh
python -m studyos --root ROOT handoff ensure --target both --json
python -m studyos --root ROOT summary --json
python -m studyos --root ROOT doctor --json
python -m studyos --root ROOT handoff show --id SCOPE --target codex --json
```

Do not initialize an existing archive. For a user-authorized new archive, use tools/setup.py with a new empty root. Read-only sessions may inspect handoff freshness without regenerating files. Report identity, event sequence, task/knowledge status, pending reports, and due reminders before making changes.

## Receive / 接收

Read the original learner-delivered report. Prepare a UTF-8 JSON input with request_id, source_path, source_ref, reported_summary, reported_facts, and user_delivered:true. Include reported_date only when known. Associate verified knowledge_id/knowledge_ids, or formally archive as unassigned. Do not require the learner to provide internal IDs or JSON.

```sh
python -m studyos --root ROOT report receive --input INPUT.json --json
python -m studyos --root ROOT report show --id ARTIFACT_ID --json
```

Use report confirm-learning only for a previously pending or unassigned report. Preserve originals and append corrections. Formal reports move unassessed/learning points to ASSESSMENT_PENDING without granting mastery. Known learning dates create first reviews; unknown dates do not. Replays must not duplicate evidence.

## Assess / 考核

Save assessment create before asking independent questions. Record actual verbatim answers using attempt add, with confirmed occurred_at, assistance, form, dimensions, and a justified evaluation. Then assessment submit using the current revision. Unknown answer times cannot grant mastery. Guided classroom checks stay in the report, not independent attempts.

## Review / 复习

Use review due/history to inspect reminders. Keep unfinished reminders pending unless the learner chooses cancellation or deferral. review complete requires new qualifying independent assessment evidence for that round. review reopen restores cancelled reminders without inventing completion or mastery.

## Duration and backup / 时长与备份

Use session duration to append corrections for a completed session. Preserve the original and count only effective records. Unknown duration is not zero, and minutes cannot manufacture actual start/end times.

```sh
python -m studyos --root ROOT backup create --mode personal --json
python -m studyos --root ROOT backup verify --path BACKUP_DIRECTORY --json
```

Use independent new destinations for restore verification. Never roll back genuine learning records because a backup or derived handoff failed. HANDOFF_FAILED after a committed operation requires handoff ensure, not duplicate business writes.

## Errors / 错误

- NOT_INITIALIZED: verify ROOT; do not initialize an old archive accidentally.
- STALE_REVISION: re-read the entity and use its latest revision.
- IDEMPOTENCY_CONFLICT: do not reuse a request_id with different content.
- EVIDENCE_TIME_UNVERIFIED / INVALID: preserve the evidence, but do not grant mastery.
- MATERIAL_MISSING / CHANGED: report the exact missing/changed registration; do not search for substitutes.
- UNREGISTERED_CONFIG: stop writes and inspect the registered configuration history.
- RECOVERY_REQUIRED: inspect the interrupted transaction and use the documented recover command.
- DAILY_PLANNING_USER_ONLY: accept only the learner's concrete plan with source=user_provided and a real authorization_ref.

Never directly edit registered config, records, SQLite rows, or generated handoffs. Use temporary roots for all tests. Before publishing source, run tools/check_public_tree.py; exclude learner data and credentials.
