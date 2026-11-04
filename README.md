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
