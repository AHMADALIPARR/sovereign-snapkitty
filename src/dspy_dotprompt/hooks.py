"""Pre-registered prompt hooks.

A hook is a plain function that runs around a prompt execution. Hooks are
registered *before* any prompt runs — at import time or at setup — so by the
time a program executes, the full hook chain is already in place. That is
what "pre-registered" means here: registration happens up front, execution
just reads the registry.

Hook points:
- ``before_prompt`` — runs after inputs are validated, before the prompt
  is rendered. Receives the context; may return a dict of replacement
  inputs, or ``None`` to leave them alone. Raising aborts the run.
- ``after_prompt`` — runs after the LM returns text, before the output is
  parsed into fields. Receives the context with ``output`` set; may return
  replacement output text, or ``None``.
- ``on_error`` — runs when a prompt execution raises. Receives the context
  with ``error`` set; may return replacement output text (a fallback) or
  ``None`` to let the error propagate.

Register with the decorator::

    from dspy_dotprompt import register_hook

    @register_hook("before_prompt")
    def my_hook(ctx):
        ...

or directly::

    HOOKS.register("after_prompt", my_hook)

The global :data:`HOOKS` registry is the default. A program or harness can
be given its own registry instead. Built-in hooks are registered on import:
``validate_inputs`` (before), ``strip_output`` and ``log`` (after).
"""

from __future__ import annotations

from dataclasses import dataclass, field as _field
from typing import Any, Callable

HookFn = Callable[["HookContext"], Any]

EVENTS = ("before_prompt", "after_prompt", "on_error")


@dataclass
class HookContext:
    """Everything a hook may need, and everything it may change.

    ``inputs`` and ``output`` are mutable through the hook's return value:
    a ``before_prompt`` hook returns replacement inputs (a dict) or None;
    an ``after_prompt`` hook returns replacement output text (a str) or
    None; an ``on_error`` hook returns replacement output text (a str) or
    None. ``messages`` is the rendered prompt (list of role/content dicts).
    """

    program_name: str
    inputs: dict[str, Any] = _field(default_factory=dict)
    messages: list[dict] = _field(default_factory=list)
    output: str | None = None
    error: BaseException | None = None
    metadata: dict[str, Any] = _field(default_factory=dict)


class HookRegistry:
    """An ordered registry of hooks, keyed by event name."""

    def __init__(self) -> None:
        self._hooks: dict[str, list[HookFn]] = {e: [] for e in EVENTS}

    def register(self, event: str, fn: HookFn) -> HookFn:
        if event not in EVENTS:
            raise ValueError(f"unknown hook event {event!r}; use one of {EVENTS}")
        self._hooks[event].append(fn)
        return fn

    def decorator(self, event: str) -> Callable[[HookFn], HookFn]:
        def wrap(fn: HookFn) -> HookFn:
            return self.register(event, fn)

        return wrap

    def hooks_for(self, event: str) -> list[HookFn]:
        return list(self._hooks.get(event, []))

    def run_before(self, ctx: HookContext) -> dict[str, Any]:
        """Run before_prompt hooks; returns the (possibly replaced) inputs."""
        inputs = dict(ctx.inputs)
        for fn in self._hooks["before_prompt"]:
            out = fn(ctx)
            if isinstance(out, dict):
                inputs = dict(out)
                ctx.inputs = inputs
        return inputs

    def run_after(self, ctx: HookContext) -> str | None:
        """Run after_prompt hooks; returns the (possibly replaced) output."""
        output = ctx.output
        for fn in self._hooks["after_prompt"]:
            out = fn(ctx)
            if isinstance(out, str):
                output = out
                ctx.output = output
        return output

    def run_error(self, ctx: HookContext) -> str | None:
        """Run on_error hooks; returns a fallback output, or None."""
        for fn in self._hooks["on_error"]:
            out = fn(ctx)
            if isinstance(out, str):
                return out
        return None

    def clear(self, event: str | None = None) -> None:
        if event is None:
            for e in EVENTS:
                self._hooks[e] = []
        else:
            self._hooks[event] = []


# The global registry. Hooks registered here are pre-registered for every
# program and harness that uses the default registry.
HOOKS = HookRegistry()


def register_hook(event: str) -> Callable[[HookFn], HookFn]:
    """Decorator: pre-register ``fn`` as a hook for ``event`` on the global
    registry. Registration happens at import time, before any prompt runs."""
    return HOOKS.decorator(event)


# --- Built-in hooks (registered on import) ---------------------------------

# A plain list the log hook appends to, so tests and the harness can inspect
# hook activity without parsing stdout.
HOOK_LOG: list[dict[str, Any]] = []


@register_hook("before_prompt")
def validate_inputs_hook(ctx: HookContext) -> None:
    """Block the run when a required input is missing or blank."""
    for key, value in ctx.inputs.items():
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError(f"hook: input {key!r} is empty")


@register_hook("after_prompt")
def strip_output_hook(ctx: HookContext) -> str | None:
    """Trim surrounding whitespace from the LM output."""
    if ctx.output is not None:
        return ctx.output.strip()
    return None


@register_hook("after_prompt")
def log_hook(ctx: HookContext) -> None:
    """Record the prompt run (program, inputs, output) for audit."""
    HOOK_LOG.append(
        {
            "program": ctx.program_name,
            "inputs": dict(ctx.inputs),
            "output": ctx.output,
        }
    )
