import asyncio
import json
from types import SimpleNamespace

from app import ChatRequest, run_agent, validate_path, validate_repo, wire


def test_validation():
    validate_repo("Haotian14/MinatoAgent")
    validate_path("src/main.ts")
    for bad in ("../secrets", "/etc/passwd", "a/../../x"):
        try:
            validate_path(bad)
            assert False, f"accepted {bad}"
        except ValueError:
            pass


def test_wire_sse():
    event = wire("done", {"text": "ok"})
    assert event.startswith("event: done\ndata: ")
    assert json.loads(event.split("data: ")[1]) == {"text": "ok"}


def test_agent_no_tools():
    message = SimpleNamespace(content="The repository has a README.", tool_calls=None)
    answer = SimpleNamespace(choices=[SimpleNamespace(message=message)])

    class Completions:
        async def create(self, **kwargs):
            return answer

    model = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))

    async def collect():
        return [item async for item in run_agent(
            ChatRequest(repository="Haotian14/MinatoAgent", message="Summarize"), model)]

    events = asyncio.run(collect())
    assert [kind for kind, _ in events] == ["status", "delta", "done"]
    assert events[-1][1]["text"] == "The repository has a README."
