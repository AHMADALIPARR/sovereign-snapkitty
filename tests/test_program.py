"""Tests: Predict, ChainOfThought, ReAct, and dotprompt rendering."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dspy_dotprompt import (
    ChainOfThought,
    DotpromptProgram,
    FakeLM,
    Predict,
    ReAct,
    Signature,
)
from dspy_dotprompt.hooks import HOOKS

QA_SRC = """---
model: test/model
input:
  schema:
    question: string
---
{{role "user"}}
Answer this question: {{question}}
"""


def _qa_program():
    return DotpromptProgram(QA_SRC, name="qa")


def test_program_renders_inputs():
    prog = _qa_program()
    msgs = prog.render({"question": "What is 2+2?"})
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"
    assert "What is 2+2?" in msgs[0]["content"]


def test_predict_single_output():
    lm = FakeLM(responses={"2+2": "Answer: 4"}, default="Answer: ?")
    pred = Predict(Signature("question -> answer"), _qa_program(), lm)(question="What is 2+2?")
    assert pred.answer == "4"


def test_predict_json_output():
    src = """---
model: test/model
input:
  schema:
    text: string
output:
  format: json
---
Extract: {{text}}
"""
    lm = FakeLM(responses={"Ada": '{"name": "Ada", "age": 36}'})
    pred = Predict(
        Signature("text -> name, age"), DotpromptProgram(src, name="extract"), lm
    )(text="Ada is 36.")
    assert pred.name == "Ada"
    assert pred.age == "36"


def test_chain_of_thought():
    lm = FakeLM(
        responses={"2+2": "Reasoning: 2 plus 2 is 4.\nAnswer: 4"},
        default="Reasoning: none.\nAnswer: ?",
    )
    pred = ChainOfThought(
        Signature("question -> answer"), _qa_program(), lm
    )(question="What is 2+2?")
    assert pred.reasoning.startswith("2 plus 2")
    assert pred.answer == "4"


def test_react_calls_tool():
    src = """---
model: test/model
input:
  schema:
    question: string
---
{{role "user"}}
Use tools to answer: {{question}}
"""
    calls = []

    def add(arg):
        calls.append(arg)
        a, b = arg.split(",")
        return str(int(a.strip()) + int(b.strip()))

    lm = FakeLM(
        responses={
            "2+2": "Thought: I need to add.\nAction: add(2, 2)",
            "Observation: 4": "Answer: 4",
        },
        default="Answer: ?",
    )
    pred = ReAct(
        Signature("question -> answer"),
        DotpromptProgram(src, name="react"),
        lm,
        tools={"add": add},
    )(question="What is 2+2?")
    assert pred.answer == "4"
    assert calls == ["2, 2"]
    assert "Observation: 4" in pred["trajectory"]
