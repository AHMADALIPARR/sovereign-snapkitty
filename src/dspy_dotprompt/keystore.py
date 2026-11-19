"""Air-gapped key storage on LevelDB.

Secrets (API keys, tokens) for the harness live in a local LevelDB
database, not in environment variables, not in plaintext files, and not
in any networked store. "Air-gapped" here is enforced by construction:

- The store is a directory on local disk. It makes **no network calls**
  (this module imports no networking code at all).
- The database directory is created with owner-only permissions (0700).
- Values are encrypted at rest with Fernet (AES-128-CBC + HMAC-SHA256).
  The Fernet key is derived from a passphrase with PBKDF2-HMAC-SHA256
  (600k iterations); the salt is stored unencrypted in the DB under a
  reserved key. Without the passphrase the values are opaque bytes.
- The operator keeps the machine offline; the store itself never reaches
  out, so there is nothing to exfiltrate through.

Implementation: a small ``ctypes`` binding to the LevelDB C API
(``leveldb/c.h``) over the shared library built from
https://github.com/google/leveldb. No third-party LevelDB Python binding
is used; the binding below is the whole interface.
"""

from __future__ import annotations

import base64
import ctypes
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

_SALT_KEY = b"__keystore_salt__"
_KDF_ITERATIONS = 600_000


def _find_lib(explicit: str | None = None) -> str:
    candidates = []
    if explicit:
        candidates.append(explicit)
    if os.environ.get("LEVELDB_LIB"):
        candidates.append(os.environ["LEVELDB_LIB"])
    home = Path.home()
    candidates += [
        str(home / "workspace" / ".tools" / "leveldb-build" / "libleveldb.so"),
        str(home / "workspace" / ".tools" / "leveldb-build" / "libleveldb.so.1"),
        "libleveldb.so",
    ]
    for path in candidates:
        if path == "libleveldb.so" or os.path.exists(path):
            return path
    raise FileNotFoundError(
        "libleveldb.so not found; set LEVELDB_LIB or build LevelDB "
        "(see ~/workspace/leveldb-ref)"
    )


def _load_lib(lib_path: str | None = None) -> ctypes.CDLL:
    lib = ctypes.CDLL(_find_lib(lib_path))
    c_void_p = ctypes.c_void_p
    c_char_p = ctypes.c_char_p
    c_size_t = ctypes.c_size_t
    c_ubyte = ctypes.c_ubyte

    lib.leveldb_options_create.restype = c_void_p
    lib.leveldb_options_create.argtypes = []
    lib.leveldb_options_destroy.restype = None
    lib.leveldb_options_destroy.argtypes = [c_void_p]
    lib.leveldb_options_set_create_if_missing.restype = None
    lib.leveldb_options_set_create_if_missing.argtypes = [c_void_p, c_ubyte]

    lib.leveldb_writeoptions_create.restype = c_void_p
    lib.leveldb_writeoptions_create.argtypes = []
    lib.leveldb_writeoptions_destroy.restype = None
    lib.leveldb_writeoptions_destroy.argtypes = [c_void_p]
    lib.leveldb_readoptions_create.restype = c_void_p
    lib.leveldb_readoptions_create.argtypes = []
    lib.leveldb_readoptions_destroy.restype = None
    lib.leveldb_readoptions_destroy.argtypes = [c_void_p]

    lib.leveldb_open.restype = c_void_p
    lib.leveldb_open.argtypes = [c_void_p, c_char_p, ctypes.POINTER(c_char_p)]
    lib.leveldb_close.restype = None
    lib.leveldb_close.argtypes = [c_void_p]

    lib.leveldb_put.restype = None
    lib.leveldb_put.argtypes = [
        c_void_p, c_void_p, c_char_p, c_size_t, c_char_p, c_size_t,
        ctypes.POINTER(c_char_p),
    ]
    lib.leveldb_get.restype = c_void_p
    lib.leveldb_get.argtypes = [
        c_void_p, c_void_p, c_char_p, c_size_t,
        ctypes.POINTER(c_size_t), ctypes.POINTER(c_char_p),
    ]
    lib.leveldb_delete.restype = None
    lib.leveldb_delete.argtypes = [
        c_void_p, c_void_p, c_char_p, c_size_t, ctypes.POINTER(c_char_p),
    ]
    lib.leveldb_free.restype = None
    lib.leveldb_free.argtypes = [c_void_p]

    lib.leveldb_create_iterator.restype = c_void_p
    lib.leveldb_create_iterator.argtypes = [c_void_p, c_void_p]
    lib.leveldb_iter_destroy.restype = None
    lib.leveldb_iter_destroy.argtypes = [c_void_p]
    lib.leveldb_iter_valid.restype = c_ubyte
    lib.leveldb_iter_valid.argtypes = [c_void_p]
    lib.leveldb_iter_seek_to_first.restype = None
    lib.leveldb_iter_seek_to_first.argtypes = [c_void_p]
    lib.leveldb_iter_next.restype = None
    lib.leveldb_iter_next.argtypes = [c_void_p]
    lib.leveldb_iter_key.restype = c_void_p
    lib.leveldb_iter_key.argtypes = [c_void_p, ctypes.POINTER(c_size_t)]
    return lib


class LevelDBKeyStore:
    """An air-gapped, encrypted key-value store backed by LevelDB.

    ``LevelDBKeyStore(path, passphrase)`` opens (or creates) the LevelDB
    database at ``path``. Use it as a context manager or call
    :meth:`close`. Keys are names (``"openai_api_key"``); values are
    strings, encrypted before they hit disk.
    """

    def __init__(
        self,
        path: str | Path,
        passphrase: str,
        lib_path: str | None = None,
    ) -> None:
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path, 0o700)
        except OSError:
            pass
        self._lib = _load_lib(lib_path)
        lib = self._lib

        opts = lib.leveldb_options_create()
        lib.leveldb_options_set_create_if_missing(opts, 1)
        err = ctypes.c_char_p(None)
        db = lib.leveldb_open(
            opts, str(self.path).encode("utf-8"), ctypes.byref(err)
        )
        lib.leveldb_options_destroy(opts)
        if not db:
            raise RuntimeError(
                f"leveldb open failed: {err.value.decode() if err.value else '?'}"
            )
        self._db = db
        self._wopts = lib.leveldb_writeoptions_create()
        self._ropts = lib.leveldb_readoptions_create()

        # Salt: stored unencrypted under a reserved key; generated once.
        salt = self._raw_get(_SALT_KEY)
        if salt is None:
            salt = os.urandom(16)
            self._raw_put(_SALT_KEY, salt)
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=_KDF_ITERATIONS,
        )
        key = base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))
        self._fernet = Fernet(key)

    # -- low-level (unencrypted) helpers ----------------------------------
    def _check(self, err: ctypes.c_char_p) -> None:
        if err.value:
            msg = err.value.decode("utf-8", "replace")
            self._lib.leveldb_free(ctypes.cast(err, ctypes.c_void_p))
            raise RuntimeError(f"leveldb error: {msg}")

    def _raw_put(self, key: bytes, value: bytes) -> None:
        err = ctypes.c_char_p(None)
        self._lib.leveldb_put(
            self._db, self._wopts, key, len(key), value, len(value),
            ctypes.byref(err),
        )
        self._check(err)

    def _raw_get(self, key: bytes) -> bytes | None:
        err = ctypes.c_char_p(None)
        vlen = ctypes.c_size_t(0)
        ptr = self._lib.leveldb_get(
            self._db, self._ropts, key, len(key), ctypes.byref(vlen),
            ctypes.byref(err),
        )
        self._check(err)
        if not ptr:
            return None
        try:
            return ctypes.string_at(ptr, vlen.value)
        finally:
            self._lib.leveldb_free(ptr)

    def _raw_delete(self, key: bytes) -> None:
        err = ctypes.c_char_p(None)
        self._lib.leveldb_delete(
            self._db, self._wopts, key, len(key), ctypes.byref(err)
        )
        self._check(err)

    # -- public (encrypted) API -------------------------------------------
    def put(self, name: str, value: str) -> None:
        """Store ``value`` under ``name``, encrypted at rest."""
        token = self._fernet.encrypt(value.encode("utf-8"))
        self._raw_put(name.encode("utf-8"), token)

    def get(self, name: str) -> str | None:
        """Return the value for ``name``, or None when absent.

        Raises ValueError when the stored bytes do not decrypt (wrong
        passphrase or tampered data).
        """
        token = self._raw_get(name.encode("utf-8"))
        if token is None:
            return None
        try:
            return self._fernet.decrypt(token).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError(
                f"cannot decrypt {name!r}: wrong passphrase or tampered data"
            ) from exc

    def delete(self, name: str) -> None:
        self._raw_delete(name.encode("utf-8"))

    def list_keys(self) -> list[str]:
        lib = self._lib
        it = lib.leveldb_create_iterator(self._db, self._ropts)
        keys: list[str] = []
        try:
            lib.leveldb_iter_seek_to_first(it)
            while lib.leveldb_iter_valid(it):
                klen = ctypes.c_size_t(0)
                ptr = lib.leveldb_iter_key(it, ctypes.byref(klen))
                raw = ctypes.string_at(ptr, klen.value)
                if raw != _SALT_KEY:
                    keys.append(raw.decode("utf-8"))
                lib.leveldb_iter_next(it)
        finally:
            lib.leveldb_iter_destroy(it)
        return sorted(keys)

    def close(self) -> None:
        lib = self._lib
        if getattr(self, "_db", None):
            lib.leveldb_close(self._db)
            self._db = None
        if getattr(self, "_wopts", None):
            lib.leveldb_writeoptions_destroy(self._wopts)
            self._wopts = None
        if getattr(self, "_ropts", None):
            lib.leveldb_readoptions_destroy(self._ropts)
            self._ropts = None

    def __enter__(self) -> "LevelDBKeyStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
