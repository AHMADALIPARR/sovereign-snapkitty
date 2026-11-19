"""Tests: air-gapped LevelDB key store."""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dspy_dotprompt import LevelDBKeyStore


def test_put_get_delete_list():
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "keys"
        with LevelDBKeyStore(db, "test-passphrase") as ks:
            assert ks.get("missing") is None
            ks.put("openai_api_key", "sk-test-123")
            ks.put("other", "value")
            assert ks.get("openai_api_key") == "sk-test-123"
            assert ks.list_keys() == ["openai_api_key", "other"]
            ks.delete("other")
            assert ks.get("other") is None
            assert ks.list_keys() == ["openai_api_key"]
        # Reopen with the same passphrase: values survive.
        with LevelDBKeyStore(db, "test-passphrase") as ks:
            assert ks.get("openai_api_key") == "sk-test-123"


def test_wrong_passphrase_cannot_decrypt():
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "keys"
        with LevelDBKeyStore(db, "correct-passphrase") as ks:
            ks.put("secret", "top-secret-value")
        with LevelDBKeyStore(db, "wrong-passphrase") as ks:
            try:
                ks.get("secret")
            except ValueError:
                return
            raise AssertionError("expected ValueError for a wrong passphrase")


def test_values_encrypted_at_rest():
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "keys"
        with LevelDBKeyStore(db, "test-passphrase") as ks:
            ks.put("openai_api_key", "sk-test-123")
        # The plaintext must not appear anywhere in the DB files.
        blob = b"".join(p.read_bytes() for p in db.rglob("*") if p.is_file())
        assert b"sk-test-123" not in blob
        # And the directory is owner-only.
        assert oct(os.stat(db).st_mode & 0o777) == "0o700"
