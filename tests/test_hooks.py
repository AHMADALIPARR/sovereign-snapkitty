"""Tests: pre-registered prompt hooks."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dspy_dotprompt import (
    DotpromptProgram,
    FakeLM,
    HookRegistry,
    Predict,
    Signature,
)
from dspy_dotprompt.hooks import HOOK_LOG

QA_SRC = """---
model: test/model
input:
  schema:
    question: string
---
{{role "user"}}
Answer: {{question}}
"""


def test_before_hook_replaces_inputs():
    registry = HookRegistry()
    seen = {}

    @registry.decorator("before_prompt")
    def rewrite(ctx):
        seen.update(ctx.inputs)
        return {"question": "What is 2+2?"}

    lm = FakeLM(responses={"2+2": "Answer: 4"})
    pred = Predict(
        Signature("question -> answer"),
        DotpromptProgram(QA_SRC, name="qa"),
        lm,
        registry,
    )(question="something else")
    assert pred.answer == "4"
    assert seen["question"] == "something else"


def test_after_hook_replaces_output():
    registry = HookRegistry()

    @registry.decorator("after_prompt")
    def shout(ctx):
        return ctx.output.upper()

    lm = FakeLM(responses={"2+2": "Answer: 4"})
    pred = Predict(
        Signature("question -> answer"),
        DotpromptProgram(QA_SRC, name="qa"),
        lm,
        registry,
    )(question="What is 2+2?")
    assert pred.answer == "4"
    assert pred.raw_text == "ANSWER: 4"


def test_on_error_hook_fallback():
    registry = HookRegistry()

    class BoomLM:
        def __call__(self, messages):
            raise RuntimeError("model down")

    @registry.decorator("on_error")
    def fallback(ctx):
        assert ctx.error is not None
        return "Answer: fallback"

    pred = Predict(
        Signature("question -> answer"),
        DotpromptProgram(QA_SRC, name="qa"),
        BoomLM(),  # type: ignore[arg-type]
        registry,
    )(question="What is 2+2?")
    assert pred.answer == "fallback"


def test_on_error_hook_no_fallback_reraises():
    registry = HookRegistry()

    class BoomLM:
        def __call__(self, messages):
            raise RuntimeError("model down")

    try:
        Predict(
            Signature("question -> answer"),
            DotpromptProgram(QA_SRC, name="qa"),
            BoomLM(),  # type: ignore[arg-type]
            registry,
        )(question="What is 2+2?")
    except RuntimeError:
        return
    raise AssertionError("expected the LM error to propagate")


def test_builtin_log_hook_records_runs():
    HOOK_LOG.clear()
    lm = FakeLM(responses={"2+2": "Answer: 4"})
    Predict(
        Signature("question -> answer"),
        DotpromptProgram(QA_SRC, name="qa"),
        lm,
    )(question="What is 2+2?")
    assert len(HOOK_LOG) == 1
    assert HOOK_LOG[0]["program"] == "qa"
    assert HOOK_LOG[0]["output"] == "Answer: 4"
