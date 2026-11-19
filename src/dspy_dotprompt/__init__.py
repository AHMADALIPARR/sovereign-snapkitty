"""dspy-dotprompt: a custom DSPy-style DSL + harness on dotprompt.

The DSL is hand-rolled (Signature, Predict, ChainOfThought, ReAct) and
renders its prompts through Google's dotprompt ``.prompt`` format. Hooks
are pre-registered before any prompt runs. The harness records runs to a
Protobuf Editions binary store (no SQL) and runs shell tasks through zx
backticks (no bash scripts). Secrets live in an air-gapped LevelDB store.
"""

from .harness import Harness, exact_match
from .hooks import HOOKS, HOOK_LOG, HookContext, HookRegistry, register_hook
from .keystore import LevelDBKeyStore
from .lm import BaseLM, FakeLM, OllamaLM, OpenAILM
from .program import ChainOfThought, DotpromptProgram, Predict, ReAct
from .signature import Field, Prediction, Signature
from .store import ProtobufStore

__all__ = [
    "ChainOfThought",
    "DotpromptProgram",
    "Predict",
    "ReAct",
    "Signature",
    "Field",
    "Prediction",
    "BaseLM",
    "FakeLM",
    "OllamaLM",
    "OpenAILM",
    "HOOKS",
    "HOOK_LOG",
    "HookContext",
    "HookRegistry",
    "register_hook",
    "Harness",
    "exact_match",
    "ProtobufStore",
    "LevelDBKeyStore",
]
