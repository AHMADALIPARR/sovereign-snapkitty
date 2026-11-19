"""DSPy-style program modules, rendered through dotprompt.

A program is a DSPy module whose prompt is a dotprompt ``.prompt`` file.
The dotprompt file carries the model, the input/output schemas, and the
Handlebars template; the module carries the signature (the contract) and
the hooks (the behavior around the call).

Modules:
- :class:`DotpromptProgram` — loads a ``.prompt`` source and renders it.
- :class:`Predict` — runs a signature through a program and an LM.
- :class:`ChainOfThought` — Predict, but the model reasons first.
- :class:`ReAct` — interleaves reasoning, tool calls, and observations.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from dotpromptz import DataArgument, Dotprompt

from .hooks import HOOKS, HookContext, HookRegistry
from .lm import BaseLM
from .signature import Prediction, Signature


def _render_sync(source: str, inputs: dict[str, Any]) -> list[dict]:
    """Render a dotprompt source to role/content messages (sync wrapper).

    dotpromptz's render is async; this runs it to completion and flattens
    the messages to plain ``{"role": ..., "content": ...}`` dicts.
    """
    dp = Dotprompt()

    async def _go() -> list[dict]:
        rendered = await dp.render(source, DataArgument(input=dict(inputs)))
        out: list[dict] = []
        for msg in rendered.messages:
            text = "".join(
                getattr(part, "text", "") for part in msg.content
            )
            # Role is a str Enum; use its value ("user") not str() ("Role.USER").
            role = getattr(msg.role, "value", msg.role)
            out.append({"role": str(role).lower(), "content": text})
        return out

    return asyncio.run(_go())


def _parse_output(text: str, signature: Signature) -> dict[str, str]:
    """Split raw LM text into the signature's output fields.

    Rules, in order:
    1. If the text is a JSON object and every output field is a key, use it.
    2. If a line starts with ``fieldname:`` (or ``fieldname =``), that line
       (and any continuation lines until the next field) is that field.
    3. If there is exactly one output field, the whole text is that field.
    4. Otherwise, the whole text goes to the first output field.
    """
    stripped = text.strip()
    # 1. JSON object.
    if stripped.startswith("{"):
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict) and all(
            name in obj for name in signature.output_names
        ):
            return {name: str(obj[name]) for name in signature.output_names}
    # 2. ``field: value`` lines (field names matched case-insensitively,
    # so a model that writes "Answer: 4" fills the "answer" field).
    by_lower = {name.lower(): name for name in signature.output_names}
    values: dict[str, list[str]] = {}
    current: str | None = None
    for line in stripped.splitlines():
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*(.*)$", line.strip())
        if m and m.group(1).lower() in by_lower:
            current = by_lower[m.group(1).lower()]
            values.setdefault(current, []).append(m.group(2).strip())
        elif current is not None and line.strip():
            values[current].append(line.strip())
    if values:
        out = {name: " ".join(values.get(name, [])).strip() for name in signature.output_names}
        if any(out.values()):
            return out
    # 3/4. Single field, or fall back to the first field.
    if len(signature.output_names) == 1:
        return {signature.output_names[0]: stripped}
    return {signature.output_names[0]: stripped, **{n: "" for n in signature.output_names[1:]}}


class DotpromptProgram:
    """A dotprompt ``.prompt`` file, loaded as a runnable program.

    Give it a source string or a path to a ``.prompt`` file. ``render``
    fills the template with inputs and returns the messages. ``metadata``
    exposes the frontmatter (model, input/output schemas, config).
    """

    def __init__(self, source: str | Path, name: str | None = None) -> None:
        if isinstance(source, Path) or (
            isinstance(source, str) and source.endswith(".prompt")
            and "\n" not in source and Path(source).exists()
        ):
            path = Path(source)
            self.source = path.read_text(encoding="utf-8")
            self.name = name or path.stem
        else:
            self.source = str(source)
            self.name = name or "inline"
        parsed = Dotprompt().parse(self.source)
        self.metadata = parsed.metadata
        self.model = getattr(self.metadata, "model", None)
        self.config = getattr(self.metadata, "config", None)

    def render(self, inputs: dict[str, Any]) -> list[dict]:
        return _render_sync(self.source, inputs)

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"DotpromptProgram({self.name!r}, model={self.model!r})"


class Predict:
    """Run a signature through a dotprompt program and an LM.

    ``Predict(signature, program, lm)`` — call it with the signature's
    input fields as kwargs and it returns a :class:`Prediction` with the
    signature's output fields. Before/after/error hooks from the registry
    run around the call.
    """

    def __init__(
        self,
        signature: Signature | str,
        program: DotpromptProgram,
        lm: BaseLM,
        registry: HookRegistry | None = None,
    ) -> None:
        self.signature = (
            signature if isinstance(signature, Signature) else Signature(signature)
        )
        self.program = program
        self.lm = lm
        self.registry = registry or HOOKS

    def __call__(self, **inputs: Any) -> Prediction:
        self.signature.validate_inputs(inputs)
        ctx = HookContext(
            program_name=self.program.name,
            inputs=dict(inputs),
            metadata={"model": self.program.model},
        )
        try:
            inputs = self.registry.run_before(ctx)
            messages = self.program.render(inputs)
            ctx.messages = messages
            raw = self.lm(messages)
            ctx.output = raw
            out = self.registry.run_after(ctx)
            if out is not None:
                raw = out
        except BaseException as exc:  # noqa: BLE001 - hooks decide the fallback
            ctx.error = exc
            fallback = self.registry.run_error(ctx)
            if fallback is not None:
                raw = fallback
            else:
                raise
        values = _parse_output(raw, self.signature)
        return Prediction(values=values, raw_text=raw)


class ChainOfThought(Predict):
    """Predict, but the model reasons before it answers.

    The signature gains a ``reasoning`` output field (prepended). The
    rendered prompt gets a "think step by step, then give the answer"
    instruction appended to the last user message. The reply is split into
    ``reasoning`` (the text before the answer field marker) and the answer
    fields. If the model does not use markers, the whole reply is the
    reasoning and the first output field is empty.
    """

    def __init__(
        self,
        signature: Signature | str,
        program: DotpromptProgram,
        lm: BaseLM,
        registry: HookRegistry | None = None,
    ) -> None:
        sig = signature if isinstance(signature, Signature) else Signature(signature)
        super().__init__(sig.with_reasoning(), program, lm, registry)
        self.base_signature = sig

    def __call__(self, **inputs: Any) -> Prediction:
        self.base_signature.validate_inputs(inputs)
        ctx = HookContext(
            program_name=self.program.name,
            inputs=dict(inputs),
            metadata={"model": self.program.model, "module": "ChainOfThought"},
        )
        try:
            inputs = self.registry.run_before(ctx)
            messages = self.program.render(inputs)
            # Ask for step-by-step reasoning before the answer.
            if messages and messages[-1]["role"] == "user":
                messages[-1] = {
                    "role": "user",
                    "content": messages[-1]["content"]
                    + "\nThink step by step, then give your final answer.",
                }
            ctx.messages = messages
            raw = self.lm(messages)
            ctx.output = raw
            out = self.registry.run_after(ctx)
            if out is not None:
                raw = out
        except BaseException as exc:  # noqa: BLE001 - hooks decide the fallback
            ctx.error = exc
            fallback = self.registry.run_error(ctx)
            if fallback is not None:
                raw = fallback
            else:
                raise
        values = _parse_output(raw, self.signature)
        # If the model gave no explicit reasoning marker, treat the text
        # before the first answer field as the reasoning.
        if not values.get("reasoning"):
            values["reasoning"] = raw.strip()
        return Prediction(values=values, raw_text=raw)


class ReAct:
    """Reason + Act: interleave LM reasoning with tool calls.

    Tools are callables registered by name: ``{"calculator": fn}``. The LM
    is asked (via the program's prompt) to emit ``Thought:`` and
    ``Action: tool_name(arg)`` lines. Each action is executed, the result
    is fed back as an ``Observation:``, and the loop repeats until the LM
    emits ``Answer:`` or ``max_steps`` is hit. Returns a Prediction with
    the base signature's outputs plus a ``trajectory`` of the steps taken.
    """

    def __init__(
        self,
        signature: Signature | str,
        program: DotpromptProgram,
        lm: BaseLM,
        tools: dict[str, Any],
        max_steps: int = 5,
        registry: HookRegistry | None = None,
    ) -> None:
        self.signature = (
            signature if isinstance(signature, Signature) else Signature(signature)
        )
        self.program = program
        self.lm = lm
        self.tools = dict(tools)
        self.max_steps = max_steps
        self.registry = registry or HOOKS

    def _run_tool(self, name: str, arg: str) -> str:
        fn = self.tools.get(name)
        if fn is None:
            return f"error: no tool named {name!r}"
        try:
            return str(fn(arg))
        except Exception as exc:  # noqa: BLE001 - tool errors become observations
            return f"error: {exc}"

    def __call__(self, **inputs: Any) -> Prediction:
        self.signature.validate_inputs(inputs)
        ctx = HookContext(
            program_name=self.program.name,
            inputs=dict(inputs),
            metadata={"model": self.program.model, "module": "ReAct"},
        )
        messages = self.program.render(inputs)
        ctx.messages = messages
        trajectory: list[str] = []
        answer_text = ""
        for _step in range(self.max_steps):
            raw = self.lm(messages)
            trajectory.append(raw)
            # Final answer?
            m = re.search(r"Answer\s*:\s*(.+)", raw, re.DOTALL)
            if m:
                answer_text = m.group(1).strip()
                break
            # Tool call?
            m = re.search(
                r"Action\s*:\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*(.*?)\s*\)",
                raw,
                re.DOTALL,
            )
            if not m:
                # No action and no answer: treat the raw text as the answer.
                answer_text = raw.strip()
                break
            tool_name, tool_arg = m.group(1), m.group(2)
            obs = self._run_tool(tool_name, tool_arg)
            trajectory.append(f"Observation: {obs}")
            messages = messages + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": f"Observation: {obs}"},
            ]
        ctx.output = answer_text
        self.registry.run_after(ctx)
        values = _parse_output(answer_text, self.signature)
        values["trajectory"] = "\n".join(trajectory)
        return Prediction(values=values, raw_text=answer_text)
