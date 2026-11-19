"""Protobuf Editions binary store — the SQL replacement.

Instead of SQL tables, the harness persists its data as length-delimited
Protobuf records in three files:

- ``datasets.bin`` — :class:`Dataset` records.
- ``runs.bin`` — :class:`RunRecord` records (one per harness evaluation).
- ``events.bin`` — :class:`HookEvent` records (one per hook firing).

Each record is a varint length prefix followed by the message bytes (the
standard Protobuf delimited framing). Queries are implemented by reading
and filtering in Python — the same operations SQL would do (filter by
program, by dataset, by minimum pass rate; sort by time; fetch by id) —
without a database server, a query language, or a schema migration.
The schema lives in ``proto/harness.proto`` (edition 2024).
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Iterable

from . import harness_pb2


def _encode_varint(value: int) -> bytes:
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _decode_varint(data: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7


def _read_delimited(path: Path, msg_cls: type) -> list:
    if not path.exists():
        return []
    data = path.read_bytes()
    out = []
    pos = 0
    while pos < len(data):
        length, pos = _decode_varint(data, pos)
        chunk = data[pos : pos + length]
        pos += length
        msg = msg_cls()
        msg.ParseFromString(chunk)
        out.append(msg)
    return out


def _append_delimited(path: Path, msg) -> None:
    blob = msg.SerializeToString()
    with open(path, "ab") as f:
        f.write(_encode_varint(len(blob)))
        f.write(blob)


def _rewrite_delimited(path: Path, msgs: Iterable) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "wb") as f:
        for msg in msgs:
            blob = msg.SerializeToString()
            f.write(_encode_varint(len(blob)))
            f.write(blob)
    os.replace(tmp, path)


class ProtobufStore:
    """A directory of Protobuf binary files, queried like small tables."""

    def __init__(self, directory: str | Path) -> None:
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        # Owner-only permissions: this directory holds run data.
        try:
            os.chmod(self.dir, 0o700)
        except OSError:
            pass

    # -- datasets --------------------------------------------------------
    def save_dataset(
        self, name: str, examples: list[dict]
    ) -> harness_pb2.Dataset:
        """Store a dataset. ``examples`` are dicts with ``id``, ``inputs``
        and ``expected`` (both str->str maps). Replaces any dataset of the
        same name."""
        ds = harness_pb2.Dataset(name=name)
        for ex in examples:
            e = ds.examples.add(id=str(ex.get("id", "")))
            for k, v in (ex.get("inputs") or {}).items():
                e.inputs[k] = str(v)
            for k, v in (ex.get("expected") or {}).items():
                e.expected[k] = str(v)
        kept = [
            d
            for d in self._datasets()
            if d.name != name
        ]
        kept.append(ds)
        _rewrite_delimited(self.dir / "datasets.bin", kept)
        return ds

    def _datasets(self) -> list:
        return _read_delimited(self.dir / "datasets.bin", harness_pb2.Dataset)

    def load_dataset(self, name: str) -> harness_pb2.Dataset | None:
        for d in self._datasets():
            if d.name == name:
                return d
        return None

    def list_datasets(self) -> list[str]:
        return [d.name for d in self._datasets()]

    # -- runs ------------------------------------------------------------
    def save_run(self, record: harness_pb2.RunRecord) -> harness_pb2.RunRecord:
        if not record.run_id:
            record.run_id = uuid.uuid4().hex[:12]
        if not record.started_unix_ms:
            record.started_unix_ms = int(time.time() * 1000)
        _append_delimited(self.dir / "runs.bin", record)
        return record

    def load_runs(
        self,
        program: str | None = None,
        dataset: str | None = None,
        min_pass_rate: float | None = None,
    ) -> list:
        """Query runs, newest first. Filters are the SQL WHERE clause."""
        runs = _read_delimited(self.dir / "runs.bin", harness_pb2.RunRecord)
        if program is not None:
            runs = [r for r in runs if r.program_name == program]
        if dataset is not None:
            runs = [r for r in runs if r.dataset_name == dataset]
        if min_pass_rate is not None:
            runs = [r for r in runs if r.pass_rate >= min_pass_rate]
        runs.sort(key=lambda r: r.started_unix_ms, reverse=True)
        return runs

    def get_run(self, run_id: str) -> harness_pb2.RunRecord | None:
        for r in _read_delimited(self.dir / "runs.bin", harness_pb2.RunRecord):
            if r.run_id == run_id:
                return r
        return None

    # -- hook events -----------------------------------------------------
    def log_event(
        self,
        run_id: str,
        program_name: str,
        hook: str,
        kind: str,
        detail: str = "",
    ) -> harness_pb2.HookEvent:
        ev = harness_pb2.HookEvent(
            event_id=uuid.uuid4().hex[:12],
            run_id=run_id,
            program_name=program_name,
            hook=hook,
            kind=kind,
            timestamp_unix_ms=int(time.time() * 1000),
            detail=detail,
        )
        _append_delimited(self.dir / "events.bin", ev)
        return ev

    def load_events(
        self,
        program: str | None = None,
        kind: str | None = None,
        hook: str | None = None,
    ) -> list:
        events = _read_delimited(self.dir / "events.bin", harness_pb2.HookEvent)
        if program is not None:
            events = [e for e in events if e.program_name == program]
        if kind is not None:
            events = [e for e in events if e.kind == kind]
        if hook is not None:
            events = [e for e in events if e.hook == hook]
        events.sort(key=lambda e: e.timestamp_unix_ms)
        return events
