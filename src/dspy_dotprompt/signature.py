"""DSPy-style signatures, hand-rolled.

A Signature names the inputs a program takes and the outputs it produces,
in DSPy's ``"input1, input2 -> output1, output2"`` notation. A field may
carry a short description after a colon, e.g. ``"question: the user's
question -> answer: a short answer"``. This is the contract a Predict or
ChainOfThought module is held to: it must be given exactly the input fields
and must return exactly the output fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field as _field


@dataclass(frozen=True)
class Field:
    """One named slot of a signature, with an optional description."""

    name: str
    description: str = ""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.name}: {self.description}" if self.description else self.name


def _parse_side(text: str) -> list[Field]:
    fields: list[Field] = []
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" in chunk:
            name, desc = chunk.split(":", 1)
            fields.append(Field(name=name.strip(), description=desc.strip()))
        else:
            fields.append(Field(name=chunk))
    return fields


class Signature:
    """A DSPy-style ``inputs -> outputs`` contract.

    Examples:
        Signature("question -> answer")
        Signature("question, context -> reasoning, answer")
        Signature("text: the passage to summarize -> summary: one paragraph")
    """

    def __init__(self, spec: str) -> None:
        if "->" not in spec:
            raise ValueError(
                f"signature needs '->', got {spec!r} (e.g. 'question -> answer')"
            )
        left, right = spec.split("->", 1)
        self.input_fields: list[Field] = _parse_side(left)
        self.output_fields: list[Field] = _parse_side(right)
        if not self.input_fields:
            raise ValueError(f"signature needs at least one input field: {spec!r}")
        if not self.output_fields:
            raise ValueError(f"signature needs at least one output field: {spec!r}")
        self.spec = spec

    @property
    def input_names(self) -> list[str]:
        return [f.name for f in self.input_fields]

    @property
    def output_names(self) -> list[str]:
        return [f.name for f in self.output_fields]

    def with_reasoning(self) -> "Signature":
        """Return a copy with a ``reasoning`` output field prepended.

        ChainOfThought uses this: the model reasons first, then answers.
        """
        fields = [Field("reasoning", "step-by-step reasoning")] + self.output_fields
        out = ", ".join(str(f) for f in fields)
        ins = ", ".join(str(f) for f in self.input_fields)
        return Signature(f"{ins} -> {out}")

    def validate_inputs(self, inputs: dict) -> None:
        missing = [n for n in self.input_names if n not in inputs]
        if missing:
            raise ValueError(f"missing input field(s): {', '.join(missing)}")
        extra = [k for k in inputs if k not in self.input_names]
        if extra:
            raise ValueError(f"unexpected input field(s): {', '.join(extra)}")

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"Signature({self.spec!r})"


@dataclass
class Prediction:
    """The outputs a module produced, plus the raw LM text it came from.

    Access fields by attribute (``pred.answer``) or as a dict
    (``pred["answer"]``). ``reasoning`` is present for ChainOfThought.
    """

    values: dict[str, str] = _field(default_factory=dict)
    raw_text: str = ""

    def __getattr__(self, name: str) -> str:
        try:
            return self.values[name]
        except KeyError:
            raise AttributeError(name) from None

    def __getitem__(self, name: str) -> str:
        return self.values[name]

    def __contains__(self, name: str) -> bool:
        return name in self.values

    def to_dict(self) -> dict[str, str]:
        return dict(self.values)
