import base64
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

import publisher


class FakeGitHub:
    def __init__(self):
        self.branch_exists = False
        self.content = "before\n"
        self.puts = 0
        self.prs = 0

    def handler(self, req):
        url = req.url.path
        if url == "/repos/owner/repo" and req.method == "GET":
            return httpx.Response(200, json={"default_branch": "main"})
        if url.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": "base123"}})
        if url.endswith("/git/ref/heads/minato/approved-12345678-123"):
            return httpx.Response(200 if self.branch_exists else 404, json={})
        if url.endswith("/git/refs") and req.method == "POST":
            self.branch_exists = True
            return httpx.Response(201, json={"ref": "branch"})
        if url.endswith("/contents/src/app.py") and req.method == "GET":
            ref = req.url.params.get("ref")
            value = "before\n" if ref == "base123" else self.content
            return httpx.Response(200, json={
                "type": "file", "encoding": "base64", "sha": "blob",
                "content": base64.b64encode(value.encode()).decode()})
        if url.endswith("/contents/src/app.py") and req.method == "PUT":
            self.puts += 1
            self.content = "after\n"
            return httpx.Response(200, json={"commit": {"sha": "new"}})
        if url.endswith("/pulls") and req.method == "GET":
            return httpx.Response(200, json=[])
        if url.endswith("/pulls") and req.method == "POST":
            self.prs += 1
            return httpx.Response(201, json={"html_url": "https://github.com/owner/repo/pull/12"})
        raise AssertionError(f"Unexpected request {req.method} {url}")


def test_authentication(monkeypatch):
    monkeypatch.setenv("MINATO_ADMIN_TOKEN", "long-secret")
    with pytest.raises(HTTPException) as denied:
        publisher.require_admin("Bearer wrong")
    assert denied.value.status_code == 403
    publisher.require_admin("Bearer long-secret")


def test_publish_only_once(monkeypatch, tmp_path):
    task_id = "12345678-1234-1234-1234-123456789abc"
    fake = FakeGitHub()
    transport = httpx.MockTransport(fake.handler)
    actual_client = httpx.Client
    monkeypatch.setattr(publisher.httpx, "Client",
                        lambda **kwargs: actual_client(transport=transport, **kwargs))
    monkeypatch.setattr(publisher, "DB_PATH", tmp_path / "checkpoints.sqlite3")
    monkeypatch.setenv("MINATO_ADMIN_TOKEN", "secret")
    monkeypatch.setenv("GITHUB_TOKEN", "fake-github-token")
    monkeypatch.setattr(publisher, "task_snapshot",
                        lambda task: {"status": "approved", "can_review": False})
    state = {"repository": "owner/repo", "path": "src/app.py",
             "instruction": "Change before to after", "original": "before\n",
             "updated": "after\n", "diff": "-before\n+after"}
    monkeypatch.setattr(publisher, "get_graph",
                        lambda: SimpleNamespace(get_state=lambda config: SimpleNamespace(values=state)))

    result = publisher.publish_approved(task_id, "Bearer secret")
    assert result["status"] == "published"
    assert result["pr_url"].endswith("/12")
    again = publisher.publish_approved(task_id, "Bearer secret")
    assert again == result
    assert fake.puts == 1
    assert fake.prs == 1


def test_publish_rejects_unapproved(monkeypatch):
    monkeypatch.setenv("MINATO_ADMIN_TOKEN", "secret")
    monkeypatch.setenv("GITHUB_TOKEN", "fake-github-token")
    monkeypatch.setattr(publisher, "task_snapshot",
                        lambda task: {"status": "rejected", "can_review": False})
    monkeypatch.setattr(publisher, "get_graph",
                        lambda: SimpleNamespace(get_state=lambda config: SimpleNamespace(values={})))
    with pytest.raises(HTTPException) as denied:
        publisher.publish_approved("12345678-1234-1234-1234-123456789abc", "Bearer secret")
    assert denied.value.status_code == 409
