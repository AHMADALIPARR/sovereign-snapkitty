"""End-to-end example: QA program through the harness, offline.

Uses FakeLM (deterministic, no key, no network), a pre-registered hook,
and the Protobuf store. Run it with:
    python3 examples/run_qa.py
or via zx:
    zx tasks.mjs run
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dspy_dotprompt import (
    FakeLM,
    Harness,
    ProtobufStore,
    Signature,
    exact_match,
    register_hook,
)


# A custom hook, pre-registered before any prompt runs.
@register_hook("before_prompt")
def uppercase_questions(ctx):
    """Normalise the question to a canonical form before rendering."""
    inputs = dict(ctx.inputs)
    if "question" in inputs:
        inputs["question"] = str(inputs["question"]).strip()
    return inputs


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    lm = FakeLM(
        responses={
            "What is 2+2?": "Answer: 4",
            "capital of France": "Answer: Paris",
        },
        default="Answer: done.",
    )
    store = ProtobufStore(root / ".store")
    harness = Harness(root / "prompts", lm, store=store)

    dataset = [
        {"id": "e1", "inputs": {"question": "What is 2+2?"}, "expected": {"answer": "4"}},
        {"id": "e2", "inputs": {"question": "What is the capital of France?"}, "expected": {"answer": "Paris"}},
    ]
    store.save_dataset("qa-demo", dataset)

    print("programs:", harness.discover())
    summary = harness.run(
        "qa", "qa-demo", exact_match, Signature("question -> answer")
    )
    print("run:", summary)
    print("stored runs for qa:", len(store.load_runs(program="qa")))
    print("stored events:", len(store.load_events(program="qa")))


if __name__ == "__main__":
    main()
