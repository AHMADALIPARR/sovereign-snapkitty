"""Tests: Protobuf Editions store (the SQL replacement)."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dspy_dotprompt import ProtobufStore
from dspy_dotprompt import harness_pb2


def test_dataset_round_trip():
    with tempfile.TemporaryDirectory() as d:
        store = ProtobufStore(d)
        store.save_dataset(
            "qa",
            [{"id": "e1", "inputs": {"question": "What is 2+2?"}, "expected": {"answer": "4"}}],
        )
        assert store.list_datasets() == ["qa"]
        ds = store.load_dataset("qa")
        assert ds is not None
        assert ds.examples[0].inputs["question"] == "What is 2+2?"
        assert ds.examples[0].expected["answer"] == "4"
        assert store.load_dataset("missing") is None


def test_run_query_filters():
    with tempfile.TemporaryDirectory() as d:
        store = ProtobufStore(d)
        for prog, rate in [("qa", 1.0), ("qa", 0.5), ("sum", 1.0)]:
            rec = harness_pb2.RunRecord(
                run_id=f"r-{prog}-{rate}",
                program_name=prog,
                dataset_name="qa",
                pass_count=int(rate * 2),
                total=2,
                pass_rate=rate,
            )
            store.save_run(rec)
        assert len(store.load_runs()) == 3
        assert len(store.load_runs(program="qa")) == 2
        assert len(store.load_runs(program="qa", min_pass_rate=1.0)) == 1
        assert len(store.load_runs(dataset="qa")) == 3
        assert store.get_run("r-qa-1.0") is not None
        assert store.get_run("nope") is None


def test_hook_events():
    with tempfile.TemporaryDirectory() as d:
        store = ProtobufStore(d)
        store.log_event("r1", "qa", "log_hook", "after_prompt", detail="ok")
        store.log_event("r1", "qa", "validate", "before_prompt", detail="ok")
        assert len(store.load_events()) == 2
        assert len(store.load_events(kind="after_prompt")) == 1
        assert len(store.load_events(program="qa", hook="log_hook")) == 1
        assert len(store.load_events(program="other")) == 0
