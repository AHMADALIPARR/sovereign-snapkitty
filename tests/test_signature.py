"""Tests: Signature parsing and Prediction."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dspy_dotprompt import Field, Prediction, Signature


def test_basic():
    s = Signature("question -> answer")
    assert s.input_names == ["question"]
    assert s.output_names == ["answer"]


def test_multi():
    s = Signature("question, context -> reasoning, answer")
    assert s.input_names == ["question", "context"]
    assert s.output_names == ["reasoning", "answer"]


def test_descriptions():
    s = Signature("text: the passage -> summary: one paragraph")
    assert s.input_fields[0] == Field("text", "the passage")
    assert s.output_fields[0] == Field("summary", "one paragraph")


def test_missing_arrow():
    try:
        Signature("question answer")
    except ValueError:
        return
    raise AssertionError("expected ValueError for a signature without '->'")


def test_validate_inputs():
    s = Signature("question -> answer")
    s.validate_inputs({"question": "hi"})  # no raise
    try:
        s.validate_inputs({})
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for missing input")
    try:
        s.validate_inputs({"question": "hi", "extra": 1})
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for unexpected input")


def test_with_reasoning():
    s = Signature("question -> answer")
    cot = s.with_reasoning()
    assert cot.output_names == ["reasoning", "answer"]
    assert cot.input_names == ["question"]


def test_prediction():
    p = Prediction(values={"answer": "4"}, raw_text="Answer: 4")
    assert p.answer == "4"
    assert p["answer"] == "4"
    assert "answer" in p
    assert p.to_dict() == {"answer": "4"}
