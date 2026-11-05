"""Review workflow tests use fake I/O and never contact GitHub or a model."""
from pathlib import Path

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

import review_workflow as rw


def test_review_path_validation():
    for invalid in ("../x", "/etc/passwd", "src/../x", "a//b", "a b"):
        with pytest.raises(ValueError):
            rw.validate_path(invalid)
    rw.validate_path("src/app.py")


def test_diff_generation_and_persistent_approval(monkeypatch, tmp_path):
    monkeypatch.setattr(rw, "read_source", lambda repo, path: "value = 1\n")
    monkeypatch.setattr(rw, "generate_replacement",
                        lambda repo, path, instruction, old: "value = 2\n")
    db = tmp_path / "checkpoints.sqlite3"

    # Separate graph and connection so the test never touches the app's DB.
    import sqlite3
    with sqlite3.connect(db, check_same_thread=False) as conn:
        builder = StateGraph(rw.ReviewState)
        builder.add_node("draft", rw.draft_node)
        builder.add_node("review", rw.review_node)
        builder.add_node("finalize", rw.finalize_node)
        builder.add_edge(START, "draft")
        builder.add_edge("draft", "review")
        builder.add_edge("review", "finalize")
        builder.add_edge("finalize", END)
        graph = builder.compile(checkpointer=SqliteSaver(conn))
        config = {"configurable": {"thread_id": "test-task"}}
        graph.invoke({"repository": "owner/repo", "path": "src/app.py",
                      "instruction": "Change the value to 2"}, config=config)
        snapshot = graph.get_state(config)
        assert "review" in snapshot.next
        assert "-value = 1" in snapshot.values["diff"]
        assert "+value = 2" in snapshot.values["diff"]
        graph.invoke(Command(resume={"approved": True}), config=config)
        assert graph.get_state(config).values["status"] == "approved"
