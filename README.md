# MinatoAgent

A small, inspectable GitHub repository assistant built for AI Agent full-stack development practice.

## Features (MVP)

- FastAPI backend with a bounded tool-calling loop (max iterations and timeout)
- Read-only GitHub tools: `list_repository_files`, `read_repository_file`
- OpenAI-compatible Chat Completions API and configurable model
- Structured SSE events for steps, tool calls, text deltas and completion
- Single-page web UI with live trace
- Unit tests using a fake model, without spending API credits

**Scope:** This version only reads public GitHub repositories. It does not execute repository code, write files, create branches, or open PRs. Treat repository contents as untrusted.

## Quick start

Requirements: Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env and add your model credentials
uvicorn app:app --reload
```

Visit http://127.0.0.1:8000.

Environment:
- `OPENAI_API_KEY`: model API key
- `OPENAI_BASE_URL`: OpenAI-compatible endpoint (optional)
- `OPENAI_MODEL`: model name (default: gpt-4.1-mini)
- `GITHUB_TOKEN`: optional GitHub PAT to increase rate limits and access authorized repos (avoid granting write scopes)

Tests: `pytest -q`.

## Architecture

```text
Browser UI --POST /api/chat--> FastAPI --Chat Completions--> LLM
     ^                           |
     | SSE                       +-- bounded Agent Loop
     |                           |     +-- tool schema validation
     +---------------------------+     +-- GitHub read-only REST API
```

SSE events: `status`, `tool_call`, `tool_result`, `delta`, `done`, `error`. The `done` event is a final snapshot; clients **replace** text with it rather than appending it, preventing duplication.

## Roadmap

1. LangGraph state/checkpoints and durable task IDs
2. Git diff generation in a restricted sandbox and tests
3. Human approval before any GitHub write or PR
4. Persistent evaluation cases, tracing, observability, and deployment

## Security notes

The backend limits the number of tool calls, duration, repository file size, and returned output. Tool parameters are validated. Public demo endpoints are not authenticated: run locally only until authentication, rate limits and per-user quotas are added. Never expose secrets in the browser.

## Phase 2: checkpointed code-change review

Visit **/review** to propose a one-file modification and inspect a unified diff.

- `POST /api/proposals`: fetch one GitHub file (maximum 16 KB), ask the model for an updated version, calculate a unified diff, then interrupt the LangGraph workflow.
- `GET /api/proposals/{task_id}`: inspect the saved checkpoint and current review status.
- `POST /api/proposals/{task_id}/decision` with `{"approved": true}` or `false`: resume the paused graph exactly once.
- SQLite checkpoints are written to `minato_checkpoints.sqlite3` (configurable by `MINATO_CHECKPOINT_DB`) and survive server restarts when the DB file is preserved.

**Safety boundary:** An approved proposal remains a review record. No GitHub write, patch execution, or PR creation occurs. A later phase will require authenticated users, branch-based writes, head-SHA validation, and explicit PR confirmation. Limit the app to a local trusted environment: approval endpoints have no login yet.

The review model must return JSON with `updated_content`. Some OpenAI-compatible models may not obey the format; such failures are surfaced as errors.

## Phase 3: publish an approved proposal

Set `GITHUB_TOKEN` with repository Contents and Pull Requests write permissions, and set a **long random** `MINATO_ADMIN_TOKEN`. Keep both in server-side `.env` (never commit it). After generating and approving a proposal in `/review`, enter the admin token and click **Publish draft PR**.

`POST /api/proposals/{task_id}/publish` requires `Authorization: Bearer <MINATO_ADMIN_TOKEN>`. The endpoint verifies the approved checkpoint, checks the original GitHub file content against the proposal, creates a unique `minato/approved-...` branch, commits the approved full-file replacement, and creates a **draft** pull request. It never writes to the default branch or merges the PR.

A SQLite publication journal records each phase and reconciles branch content and existing PRs on retry. A failed request can be retried with the same task ID. The journal is single-process oriented, and the publish API is intended for **trusted local use only** until real user authentication, CSRF protection, per-user authorization, distributed locking, and rate limits are added. A GitHub write token should be scoped to the specific repository.

**Important:** This publishes code for human review; it does not run tests or prove the generated patch is correct. Verify the draft PR before merging.
