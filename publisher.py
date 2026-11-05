"""Publish an approved proposal to a new GitHub branch and open a PR.

Local single-process MVP: an admin token protects publish, SQLite journals every
step, and retries reconcile GitHub state after ambiguous network failures.
"""
import base64
import hmac
import os
import re
import sqlite3
import threading
from typing import Any

import httpx
from fastapi import HTTPException

from review_workflow import DB_PATH, task_snapshot, get_graph

_LOCK = threading.RLock()
_API = "https://api.github.com"
_BRANCH_RE = re.compile(r"^[a-f0-9-]{36}$")


def require_admin(authorization: str | None) -> None:
    expected = os.getenv("MINATO_ADMIN_TOKEN")
    if not expected or not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Admin authentication required")
    if not hmac.compare_digest(authorization[7:], expected):
        raise HTTPException(status_code=403, detail="Invalid admin token")


def _api(client: httpx.Client, method: str, url: str, **kwargs) -> Any:
    response = client.request(method, url, **kwargs)
    if response.status_code >= 400:
        detail = f"GitHub {method} failed ({response.status_code})"
        if response.status_code in (401, 403):
            detail += ": check GITHUB_TOKEN and repository permissions"
        raise HTTPException(status_code=409 if response.status_code in (409, 422) else 502,
                            detail=detail)
    return response.json()


def _read_file(client: httpx.Client, repository: str, path: str, ref: str) -> dict:
    return _api(client, "GET", f"/repos/{repository}/contents/{path}", params={"ref": ref})


def _file_text(payload: dict) -> str:
    if payload.get("type") != "file" or payload.get("encoding") != "base64":
        raise HTTPException(status_code=409, detail="GitHub file is not a supported base64 file")
    try:
        return base64.b64decode(payload["content"], validate=False).decode("utf-8")
    except (ValueError, UnicodeDecodeError, KeyError) as exc:
        raise HTTPException(status_code=409, detail="File content cannot be decoded") from exc


def _journal(conn: sqlite3.Connection, task_id: str, **values) -> None:
    columns = ", ".join(f"{key}=?" for key in values)
    conn.execute(f"UPDATE publications SET {columns} WHERE task_id=?",
                 (*values.values(), task_id))
    conn.commit()


def _existing_pr(client: httpx.Client, repository: str, branch: str) -> str | None:
    owner = repository.split("/")[0]
    prs = _api(client, "GET", f"/repos/{repository}/pulls",
               params={"state": "all", "head": f"{owner}:{branch}", "per_page": 100})
    for pr in prs:
        if pr.get("head", {}).get("ref") == branch and pr.get("base", {}).get("ref"):
            return pr["html_url"]
    return None


def publish_approved(task_id: str, authorization: str | None) -> dict:
    require_admin(authorization)
    if not _BRANCH_RE.fullmatch(task_id):
        raise HTTPException(status_code=422, detail="Invalid task ID")
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise HTTPException(status_code=503, detail="GITHUB_TOKEN is required for publishing")

    with _LOCK:
        task = task_snapshot(task_id)
        state = get_graph().get_state({"configurable": {"thread_id": task_id}}).values
        if task["status"] != "approved" or task["can_review"]:
            raise HTTPException(status_code=409, detail="Task is not approved")
        if not state.get("diff") or state["diff"] == "(No changes proposed)":
            raise HTTPException(status_code=409, detail="No change to publish")
        repository, path = state["repository"], state["path"]
        branch = f"minato/approved-{task_id[:12]}"
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(DB_PATH, timeout=20) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS publications (
                task_id TEXT PRIMARY KEY, repository TEXT NOT NULL, branch TEXT NOT NULL,
                base_sha TEXT, status TEXT NOT NULL, pr_url TEXT, error TEXT
            )""")
            conn.execute("""INSERT OR IGNORE INTO publications
                (task_id, repository, branch, status) VALUES (?, ?, ?, 'pending')""",
                (task_id, repository, branch))
            conn.commit()
            row = conn.execute(
                "SELECT repository, branch, base_sha, status, pr_url FROM publications WHERE task_id=?",
                (task_id,)).fetchone()
            if row[0] != repository or row[1] != branch:
                raise HTTPException(status_code=409, detail="Publication identity mismatch")
            if row[4]:
                return {"status": "published", "branch": branch, "pr_url": row[4]}

            headers = {"Authorization": f"Bearer {token}",
                       "Accept": "application/vnd.github+json",
                       "X-GitHub-Api-Version": "2022-11-28"}
            with httpx.Client(base_url=_API, headers=headers, timeout=20,
                              follow_redirects=False) as client:
                try:
                    repo_info = _api(client, "GET", f"/repos/{repository}")
                    base = repo_info["default_branch"]
                    if not re.fullmatch(r"[A-Za-z0-9._/-]+", base):
                        raise HTTPException(status_code=409, detail="Invalid default branch")
                    if not row[2]:
                        ref = _api(client, "GET",
                                   f"/repos/{repository}/git/ref/heads/{base}")
                        base_sha = ref["object"]["sha"]
                        source = _read_file(client, repository, path, base_sha)
                        if _file_text(source) != state["original"]:
                            raise HTTPException(status_code=409,
                                                detail="Base file changed since proposal; regenerate diff")
                        _journal(conn, task_id, base_sha=base_sha, status="validated")
                    else:
                        base_sha = row[2]

                    # A deterministic branch name makes retry after a network timeout safe.
                    ref_path = f"/repos/{repository}/git/ref/heads/{branch}"
                    existing = client.get(ref_path)
                    if existing.status_code == 404:
                        _api(client, "POST", f"/repos/{repository}/git/refs",
                             json={"ref": f"refs/heads/{branch}", "sha": base_sha})
                    elif existing.status_code != 200:
                        raise HTTPException(status_code=502, detail="Cannot inspect target branch")
                    _journal(conn, task_id, status="branch_ready")

                    branch_file = _read_file(client, repository, path, branch)
                    current = _file_text(branch_file)
                    if current == state["original"]:
                        _api(client, "PUT", f"/repos/{repository}/contents/{path}",
                             json={"message": f"feat: approved MinatoAgent change ({task_id[:8]})",
                                   "content": base64.b64encode(
                                       state["updated"].encode("utf-8")).decode("ascii"),
                                   "sha": branch_file["sha"], "branch": branch})
                    elif current != state["updated"]:
                        raise HTTPException(status_code=409,
                                            detail="Branch file differs from original and approved content")
                    _journal(conn, task_id, status="committed")

                    pr_url = _existing_pr(client, repository, branch)
                    if not pr_url:
                        created = _api(client, "POST", f"/repos/{repository}/pulls",
                                       json={"title": f"MinatoAgent: {state['instruction'][:70]}",
                                             "head": branch, "base": base,
                                             "body": (f"Approved MinatoAgent proposal {task_id}.\n\n"
                                                      f"File: {path}\n\n"
                                                      "Automated patch; review and run tests before merging."),
                                             "draft": True})
                        pr_url = created["html_url"]
                    _journal(conn, task_id, status="published", pr_url=pr_url, error=None)
                    return {"status": "published", "branch": branch, "pr_url": pr_url}
                except Exception as exc:
                    _journal(conn, task_id, status="failed",
                             error=f"{type(exc).__name__}: {str(exc)[:180]}")
                    raise
