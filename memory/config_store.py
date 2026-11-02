"""Atomic JSON updates shared by every credentials/configuration writer.

The lock file contains one constant byte, never configuration data. It stays in
place so concurrent processes always lock the same file. Temporary files are
created alongside the destination with private POSIX permissions and removed
when an update fails; no credentials backup is created.
"""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
import errno
import json
import os
from pathlib import Path
import tempfile
import threading
import time


LOCK_TIMEOUT_SECONDS = 5.0
REPLACE_RETRY_SECONDS = 1.0
_THREAD_LOCKS: dict[str, threading.RLock] = {}
_REGISTRY_LOCK = threading.Lock()


class ConfigUpdateError(OSError):
    """A recoverable configuration error with no file contents or secrets."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(f"Não foi possível atualizar a configuração ({code}).")


def _path_lock(path: Path) -> threading.RLock:
    key = os.path.normcase(str(path.resolve()))
    with _REGISTRY_LOCK:
        return _THREAD_LOCKS.setdefault(key, threading.RLock())


def _native_lock(descriptor: int, *, unlock: bool = False) -> None:
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK if unlock else msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(descriptor, fcntl.LOCK_UN if unlock else fcntl.LOCK_EX | fcntl.LOCK_NB)


@contextmanager
def _configuration_lock(path: Path):
    """Serialize writers in this process and other cooperating processes."""
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    lock = _path_lock(path)
    if not lock.acquire(timeout=LOCK_TIMEOUT_SECONDS):
        raise ConfigUpdateError("ocupada")
    descriptor = None
    acquired = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_name(f".{path.name}.lock")
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        while True:
            try:
                _native_lock(descriptor)
                acquired = True
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                if time.monotonic() >= deadline:
                    raise ConfigUpdateError("ocupada") from None
                time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
        # Windows permits locking beyond EOF. Initialize only while holding the
        # byte lock, so two first-time writers cannot write each other's lock.
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
        yield
    finally:
        try:
            if acquired:
                _native_lock(descriptor, unlock=True)
        finally:
            try:
                if descriptor is not None:
                    os.close(descriptor)
            finally:
                lock.release()


def read_json_object(path: Path) -> dict:
    """Read a complete object, or raise; invalid existing files stay intact."""
    try:
        with path.open("r", encoding="utf-8") as stream:
            data = json.load(stream)
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError("A configuração deve ser um objeto JSON.")
    return data


def _merge(current: dict, fields: dict) -> None:
    for key, value in fields.items():
        if isinstance(value, dict):
            previous = current.get(key)
            merged = dict(previous) if isinstance(previous, dict) else {}
            _merge(merged, value)
            current[key] = merged
        else:
            current[key] = value


def _replace_atomic(source: Path, destination: Path) -> None:
    """Tolerate brief Windows sharing denials without relaxing permissions.

    Readers or filesystem filters can briefly hold a destination without delete
    sharing. The old file stays intact until replacement succeeds. Permanent
    denials remain errors after this short deadline.
    """
    deadline = time.monotonic() + REPLACE_RETRY_SECONDS
    while True:
        try:
            os.replace(source, destination)
            return
        except PermissionError as exc:
            if (os.name != "nt" or getattr(exc, "winerror", None) not in (None, 5, 32, 33)
                    or time.monotonic() >= deadline):
                raise
            time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))


def update_json_object(path: Path, fields: Mapping) -> None:
    """Merge and replace one JSON object as a single bounded transaction.

    Every participant must use this API. Readers observe the old or the new
    complete file. Invalid input, unreadable existing data and I/O failures
    raise ``ConfigUpdateError`` instead of silently discarding configuration.
    """
    if not isinstance(fields, Mapping) or any(not isinstance(key, str) for key in fields):
        raise ConfigUpdateError("campos_invalidos")
    try:
        # Snapshot caller-owned values before acquiring the file lock.
        patch = json.loads(json.dumps(dict(fields), ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise ConfigUpdateError("campos_invalidos") from None
    temporary = None
    try:
        destination = Path(path).resolve()
        with _configuration_lock(destination):
            try:
                data = read_json_object(destination)
            except (OSError, ValueError, UnicodeError):
                raise ConfigUpdateError("arquivo_ilegivel") from None
            _merge(data, patch)
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=destination.parent,
                    prefix=f".{destination.name}.", suffix=".tmp", delete=False,
                ) as stream:
                    temporary = Path(stream.name)
                    json.dump(data, stream, ensure_ascii=False, indent=4, allow_nan=False)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                _replace_atomic(temporary, destination)
                temporary = None
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
    except ConfigUpdateError:
        raise
    except (OSError, ValueError, UnicodeError, RecursionError):
        raise ConfigUpdateError("falha_de_gravacao") from None
