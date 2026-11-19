"""Tests: Harness (discover, run, score, record)."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dspy_dotprompt import (
    FakeLM,
    Harness,
    ProtobufStore,
    Signature,
    exact_match,
)

ROOT = Path(__file__).resolve().parents[1]


def _harness(tmp):
    lm = FakeLM(
        responses={
            "What is 2+2?": "Answer: 4",
            "capital of France": "Answer: Paris",
        },
        default="Answer: ?",
    )
    return Harness(ROOT / "prompts", lm, store=ProtobufStore(tmp))


def test_discover():
    lm = FakeLM()
    h = Harness(ROOT / "prompts", lm)
    found = h.discover()
    assert "qa" in found
    assert "summarize" in found
    assert "extract_person" in found


def test_run_scores_and_records():
    with tempfile.TemporaryDirectory() as tmp:
        h = _harness(tmp)
        dataset = [
            {"id": "e1", "inputs": {"question": "What is 2+2?"}, "expected": {"answer": "4"}},
            {"id": "e2", "inputs": {"question": "What is the capital of France?"}, "expected": {"answer": "Paris"}},
            {"id": "e3", "inputs": {"question": "What is the capital of Mars?"}, "expected": {"answer": "Olympus Mons"}},
        ]
        summary = h.run("qa", dataset, exact_match, Signature("question -> answer"))
        assert summary["total"] == 3
        assert summary["passed"] == 2
        assert abs(summary["pass_rate"] - 2 / 3) < 1e-9
        # The run was recorded to the Protobuf store.
        store = ProtobufStore(tmp)
        runs = store.load_runs(program="qa")
        assert len(runs) == 1
        assert runs[0].pass_count == 2
        assert runs[0].total == 3
        assert len(store.load_events(program="qa")) >= 1


def test_run_with_named_dataset():
    with tempfile.TemporaryDirectory() as tmp:
        store = ProtobufStore(tmp)
        store.save_dataset(
            "qa-set",
            [{"id": "e1", "inputs": {"question": "What is 2+2?"}, "expected": {"answer": "4"}}],
        )
        h = Harness(ROOT / "prompts", FakeLM(responses={"2+2": "Answer: 4"}), store=store)
        summary = h.run("qa", "qa-set", exact_match, Signature("question -> answer"))
        assert summary["passed"] == 1
        assert summary["dataset"] == "qa-set"


def test_exact_match_metric():
    from dspy_dotprompt import Prediction

    ex = {"expected": {"answer": "4"}}
    assert exact_match(ex, Prediction(values={"answer": "4"})) == 1.0
    assert exact_match(ex, Prediction(values={"answer": "5"})) == 0.0
