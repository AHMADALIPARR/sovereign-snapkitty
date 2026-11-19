"""Harness: discover .prompt programs, run them over datasets, score them.

The harness is the runner. It finds ``.prompt`` files in a directory,
builds a DSPy module (Predict / ChainOfThought / ReAct) for each one,
runs it over a dataset, scores every example with a metric, and records
the whole run to the Protobuf store (no SQL). Hook events are logged to
the same store. Secrets (LM API keys) come from the air-gapped LevelDB
key store, never from the environment or plaintext.

A metric is ``metric(example, prediction) -> float`` in [0, 1] (or a
bool, which is coerced). :func:`exact_match` compares the first expected
output field to the prediction's matching field.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Callable

from . import harness_pb2
from .hooks import HOOKS, HookRegistry
from .keystore import LevelDBKeyStore
from .lm import BaseLM
from .program import ChainOfThought, DotpromptProgram, Predict, ReAct
from .signature import Prediction, Signature
from .store import ProtobufStore

Metric = Callable[[dict, Prediction], float | bool]


def exact_match(example: dict, prediction: Prediction) -> float:
    """1.0 when the first expected output equals the prediction's field."""
    expected = example.get("expected") or {}
    if not expected:
        return 0.0
    name = next(iter(expected))
    got = prediction.values.get(name, "")
    return 1.0 if str(got).strip() == str(expected[name]).strip() else 0.0


class Harness:
    """Run dotprompt programs over datasets and record the results."""

    def __init__(
        self,
        prompts_dir: str | Path,
        lm: BaseLM,
        store: ProtobufStore | None = None,
        keystore: LevelDBKeyStore | None = None,
        registry: HookRegistry | None = None,
    ) -> None:
        self.prompts_dir = Path(prompts_dir)
        self.lm = lm
        self.store = store
        self.keystore = keystore
        self.registry = registry or HOOKS

    # -- discovery -------------------------------------------------------
    def discover(self) -> list[str]:
        """Names of the ``.prompt`` files in the prompts directory."""
        if not self.prompts_dir.exists():
            return []
        return sorted(p.stem for p in self.prompts_dir.glob("*.prompt"))

    def program_path(self, name: str) -> Path:
        return self.prompts_dir / f"{name}.prompt"

    def load_program(self, name: str) -> DotpromptProgram:
        path = self.program_path(name)
        if not path.exists():
            raise FileNotFoundError(f"no .prompt file for {name!r} in {self.prompts_dir}")
        return DotpromptProgram(path, name=name)

    def secret(self, name: str) -> str | None:
        """Read a secret from the air-gapped key store (None when no store)."""
        if self.keystore is None:
            return None
        return self.keystore.get(name)

    # -- running ---------------------------------------------------------
    def _build_module(
        self,
        signature: Signature | str,
        program: DotpromptProgram,
        module: str,
        tools: dict[str, Any] | None = None,
    ):
        if module == "predict":
            return Predict(signature, program, self.lm, self.registry)
        if module == "cot":
            return ChainOfThought(signature, program, self.lm, self.registry)
        if module == "react":
            return ReAct(
                signature, program, self.lm, tools or {}, registry=self.registry
            )
        raise ValueError(f"unknown module {module!r}; use predict, cot, or react")

    def run(
        self,
        program_name: str,
        dataset: str | list[dict],
        metric: Metric,
        signature: Signature | str,
        module: str = "predict",
        tools: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run one program over a dataset; returns a summary dict.

        ``dataset`` is a dataset name (loaded from the store) or a list of
        example dicts (each with ``id``, ``inputs``, ``expected``).
        The run is recorded to the Protobuf store when one is configured.
        """
        program = self.load_program(program_name)
        mod = self._build_module(signature, program, module, tools)

        if isinstance(dataset, str):
            ds_name = dataset
            if self.store is None:
                raise ValueError("dataset by name needs a store")
            ds = self.store.load_dataset(dataset)
            if ds is None:
                raise ValueError(f"dataset {dataset!r} not in the store")
            examples = [
                {
                    "id": e.id,
                    "inputs": dict(e.inputs),
                    "expected": dict(e.expected),
                }
                for e in ds.examples
            ]
        else:
            ds_name = "inline"
            examples = list(dataset)

        run_id = uuid.uuid4().hex[:12]
        record = harness_pb2.RunRecord(
            run_id=run_id,
            program_name=program_name,
            dataset_name=ds_name,
            started_unix_ms=int(time.time() * 1000),
        )
        failures: list[str] = []
        latencies: list[float] = []
        for ex in examples:
            t0 = time.perf_counter()
            result = harness_pb2.RunResult(example_id=str(ex.get("id", "")))
            for k, v in (ex.get("inputs") or {}).items():
                result.inputs[k] = str(v)
            try:
                pred = mod(**(ex.get("inputs") or {}))
                score = float(metric(ex, pred))
                result.score = score
                result.passed = score >= 1.0
                for k, v in pred.values.items():
                    result.prediction[k] = str(v)
            except Exception as exc:  # noqa: BLE001 - recorded, not raised
                result.score = 0.0
                result.passed = False
                result.error = f"{type(exc).__name__}: {exc}"
                failures.append(result.example_id)
            result.latency_ms = (time.perf_counter() - t0) * 1000.0
            latencies.append(result.latency_ms)
            record.results.append(result)

        record.total = len(record.results)
        record.pass_count = sum(1 for r in record.results if r.passed)
        record.pass_rate = (
            record.pass_count / record.total if record.total else 0.0
        )
        record.mean_latency_ms = (
            sum(latencies) / len(latencies) if latencies else 0.0
        )
        if self.store is not None:
            self.store.save_run(record)
            self.store.log_event(
                run_id, program_name, "harness", "after_prompt",
                detail=f"run {run_id}: {record.pass_count}/{record.total} passed",
            )
        return {
            "run_id": run_id,
            "program": program_name,
            "dataset": ds_name,
            "total": record.total,
            "passed": record.pass_count,
            "pass_rate": record.pass_rate,
            "mean_latency_ms": record.mean_latency_ms,
            "failures": failures,
        }

    def evaluate_all(
        self,
        dataset: str | list[dict],
        metric: Metric,
        signature: Signature | str,
        module: str = "predict",
    ) -> list[dict[str, Any]]:
        """Run every discovered program over the dataset."""
        return [
            self.run(name, dataset, metric, signature, module)
            for name in self.discover()
        ]
