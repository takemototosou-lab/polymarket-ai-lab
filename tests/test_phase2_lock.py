import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from phase2_contracts import LockConflictError, ResponseContractError
from phase2_lock import LockMetadata, LocalFileStore, acquire_lock


def metadata(run_id='run-1'):
    return LockMetadata('1.0', run_id, '2026-08-03T00:00:00Z', '2026-08-03_0000')


class FakeStore:
    def __init__(self):
        self.files = {}

    def create_exclusive(self, path, payload):
        if path in self.files:
            raise FileExistsError()
        self.files[path] = payload

    def read_bytes(self, path):
        return self.files[path]

    def remove(self, path):
        del self.files[path]


class LockTests(unittest.TestCase):
    def test_exclusive_and_owned_release(self):
        store = FakeStore()
        lock = acquire_lock(Path('fixture'), metadata().target_suffix, metadata(), store=store)
        payload = store.files[lock.path]
        self.assertTrue(payload.endswith(b'\n'))
        self.assertEqual(list(json.loads(payload)), ['lock_version','run_id','started_at','target_suffix'])
        with self.assertRaises(LockConflictError):
            acquire_lock(Path('fixture'), metadata().target_suffix, metadata('run-2'), store=store)
        self.assertEqual(payload, store.files[lock.path])
        self.assertTrue(lock.release())
        self.assertFalse(lock.release())

    def test_foreign_corrupt_duplicate_and_stale_not_deleted(self):
        for payload in (b'{bad', b'{"run_id":"run-1","run_id":"run-1"}', json.dumps(metadata('foreign').__dict__).encode()):
            store = FakeStore()
            lock = acquire_lock(Path('fixture'), metadata().target_suffix, metadata(), store=store)
            store.files[lock.path] = payload
            self.assertFalse(lock.release())
            self.assertEqual(payload, store.files[lock.path])
            with self.assertRaises(LockConflictError):
                acquire_lock(Path('fixture'), metadata().target_suffix, metadata(), store=store)

    def test_invalid_metadata_does_not_create(self):
        for kw in ({'run_id':'../x'}, {'lock_version':'2.0'}, {'started_at':'2026-08-03'}, {'target_suffix':'2026-02-30_0000'}):
            store = FakeStore()
            with self.assertRaises(ResponseContractError):
                acquire_lock(Path('fixture'), metadata().target_suffix, replace(metadata(), **kw), store=store)
            self.assertEqual({}, store.files)

    def test_real_store_context_and_exception_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RuntimeError):
                with acquire_lock(Path(directory), metadata().target_suffix, metadata()) as lock:
                    self.assertTrue(lock.path.is_file())
                    raise RuntimeError('fixture')
            self.assertEqual([], list(Path(directory).iterdir()))

    def test_short_write_and_fsync_failure_cleanup(self):
        import phase2_lock
        real_fdopen = phase2_lock.os.fdopen
        class ShortHandle:
            def __init__(self, handle): self.handle = handle
            def __enter__(self): return self
            def __exit__(self, *args): self.handle.close()
            def write(self, payload): return self.handle.write(payload[:1])
            def flush(self): raise AssertionError('flush after short write')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'lock'
            with patch('phase2_lock.os.fdopen', side_effect=lambda *a:ShortHandle(real_fdopen(*a))):
                with self.assertRaises(OSError): LocalFileStore().create_exclusive(path, b'full payload')
            self.assertFalse(path.exists())
            with patch('phase2_lock.os.fsync', side_effect=OSError('fixture')):
                with self.assertRaises(OSError): LocalFileStore().create_exclusive(path, b'full payload')
            self.assertFalse(path.exists())

    def test_real_existing_file_is_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalFileStore()
            path = Path(directory) / 'lock'
            store.create_exclusive(path, b'original')
            with self.assertRaises(FileExistsError): store.create_exclusive(path, b'replacement')
            self.assertEqual(b'original', path.read_bytes())

    def test_changed_public_owner_cannot_release(self):
        store = FakeStore()
        owner = acquire_lock(Path('fixture'), metadata().target_suffix, metadata(), store=store)
        self.assertFalse(replace(owner, run_id='run-2').release())
        self.assertIn(owner.path, store.files)
        self.assertTrue(owner.release())

    def test_fstat_failure_closes_descriptor_and_fails_closed(self):
        import phase2_lock
        real_close = phase2_lock.os.close
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'lock'
            with patch('phase2_lock.os.fstat', side_effect=OSError('fixture')), patch('phase2_lock.os.close', wraps=real_close) as close:
                with self.assertRaises(OSError): LocalFileStore().create_exclusive(path, b'payload')
                close.assert_called_once()
            # Without known inode ownership, leave the lock for manual recovery.
            self.assertTrue(path.exists())
