"""Cooperative offline lock ownership; no stale lock recovery or CLI wiring."""

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from phase2_contracts import FileStore, LockConflictError, ResponseContractError


@dataclass(frozen=True)
class LockMetadata:
    lock_version: str
    run_id: str
    started_at: str
    target_suffix: str


def _validate(metadata):
    if not isinstance(metadata, LockMetadata) or metadata.lock_version != '1.0':
        raise ResponseContractError('Invalid lock metadata')
    if not isinstance(metadata.run_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', metadata.run_id):
        raise ResponseContractError('Invalid lock identifier')
    for value, pattern, fmt in (
        (metadata.started_at, r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', '%Y-%m-%dT%H:%M:%SZ'),
        (metadata.target_suffix, r'\d{4}-\d{2}-\d{2}_\d{4}', '%Y-%m-%d_%H%M'),
    ):
        try:
            if not isinstance(value, str) or not re.fullmatch(pattern, value, flags=re.ASCII):
                raise ValueError()
            datetime.strptime(value, fmt)
        except ValueError:
            raise ResponseContractError('Invalid lock date') from None


class LocalFileStore:
    def create_exclusive(self, path, payload):
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o600)
        identity = os.fstat(descriptor)
        try:
            try:
                handle = os.fdopen(descriptor, 'wb')
            except BaseException:
                os.close(descriptor)
                raise
            with handle:
                if handle.write(payload) != len(payload):
                    raise OSError('Short lock write')
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            # Only remove our created inode, never an observed replacement.
            try:
                current = os.stat(path, follow_symlinks=False)
                if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                    os.unlink(path)
            except OSError:
                pass  # Residual lock intentionally fails closed on next acquire.
            raise

    def read_bytes(self, path):
        with open(path, 'rb') as handle:
            payload = handle.read(4097)
        if len(payload) > 4096:
            raise OSError('Oversized lock')
        return payload

    def remove(self, path):
        os.unlink(path)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate lock key')
        result[key] = value
    return result


@dataclass(frozen=True)
class Phase2Lock:
    path: Path
    run_id: str
    _store: FileStore = field(repr=False, compare=False)
    _metadata: LockMetadata = field(repr=False)

    def release(self):
        try:
            payload = self._store.read_bytes(self.path)
            decoded = json.loads(payload.decode('utf-8'), object_pairs_hook=_unique_object)
            metadata = LockMetadata(**decoded)
            _validate(metadata)
            if metadata != self._metadata:
                return False
            self._store.remove(self.path)
            return True
        except (OSError, KeyError, TypeError, ValueError, ResponseContractError):
            return False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.release()


def acquire_lock(directory, suffix, metadata, *, store=None):
    _validate(metadata)
    if suffix != metadata.target_suffix:
        raise ResponseContractError('Lock suffix mismatch')
    store = LocalFileStore() if store is None else store
    path = Path(directory) / f'.external_analysis_{suffix}.lock'
    payload = (json.dumps(metadata.__dict__, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
    try:
        store.create_exclusive(path, payload)
    except FileExistsError:
        raise LockConflictError('Lock already exists') from None
    except OSError:
        raise ResponseContractError('Lock storage failed') from None
    return Phase2Lock(path, metadata.run_id, store, metadata)
