import io
import json
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from unittest.mock import patch

from phase2_contracts import ResponseContractError
from phase2_log import LOG_KEYS, MAX_FILE_BYTES, LogEvent, Phase2JsonlWriter, serialize_event


def event(**overrides):
    return replace(LogEvent('1.0','run-1',0,'2026-08-03T00:00:00Z','2a','run_started',None,None,'fake',None,'started',None,None,None,None,None,None,10,Decimal('0'),0,0), **overrides)


class MemoryHandle(io.BytesIO):
    def fileno(self): return 123


class ShortWriter(MemoryHandle):
    def write(self, payload): return super().write(payload[:1])


class LogTests(unittest.TestCase):
    def test_fixed_keys_and_bytes(self):
        payload = serialize_event(event())
        self.assertEqual(LOG_KEYS, tuple(json.loads(payload)))
        self.assertTrue(payload.endswith(b'\n'))
        self.assertNotIn(b'\r', payload)
        self.assertFalse(payload.startswith(b'\xef\xbb\xbf'))
        self.assertEqual(payload, serialize_event(event()))
        self.assertIn(b'"cost_limit_usd":0', payload)

    def test_fixed_decimal_numbers(self):
        for value, expected in (('42.00','42'), ('0.10','0.1'), ('-0.00','0'), ('1E+2','100')):
            self.assertIn(('"cost_limit_usd":'+expected+',').encode(), serialize_event(event(cost_limit_usd=Decimal(value))))
        for value in (Decimal('NaN'),Decimal('Infinity'),Decimal('-1'),0.1):
            with self.assertRaises(ResponseContractError): serialize_event(event(cost_limit_usd=value))

    def test_rejects_invalid_fields_and_event_policy(self):
        for kw in ({'event_type':'unknown'}, {'status':'secret'}, {'phase':'2b'}, {'provider':'openai'}, {'sequence':True}, {'request_count':-1}, {'timestamp':'2026-02-30T00:00:00Z'}, {'timestamp':'2026-08-03'}, {'market_id':'https://host/'}, {'error_code':'message'}, {'response_byte_count':2}, {'query_kind':'official'}, {'schema_version':'2.0'}):
            with self.subTest(kw=kw), self.assertRaises(ResponseContractError): serialize_event(event(**kw))
        for text in ('a\n','a\x85','a\ud800','C:\\secret','https://host','Authorization: secret','Cookie: x'):
            with self.assertRaises(ResponseContractError): serialize_event(event(run_id=text))
        with self.assertRaises(ResponseContractError): serialize_event([('run_id','a'),('run_id','b')])
        with self.assertRaises(TypeError): LogEvent(**dict(event().__dict__, body='secret'))

    def test_line_boundaries(self):
        base = len(serialize_event(event(cost_limit_usd=Decimal('1'))))
        self.assertEqual(8192, len(serialize_event(event(cost_limit_usd=Decimal('1'+'0'*(8192-base))))))
        with self.assertRaises(ResponseContractError): serialize_event(event(cost_limit_usd=Decimal('1'+'0'*(8193-base))))
        with self.assertRaises(ResponseContractError): serialize_event(event(cost_limit_usd=Decimal('1e999999999')))

    def test_short_write_is_sticky_without_flush(self):
        handle = ShortWriter()
        writer = Phase2JsonlWriter(handle)
        with patch.object(handle, 'flush', side_effect=AssertionError('must not flush')):
            with self.assertRaises(ResponseContractError): writer.append(event())
        self.assertTrue(writer.failed)
        self.assertFalse(writer.can_start_request)
        before = handle.getvalue()
        with self.assertRaises(ResponseContractError): writer.append(event(sequence=1))
        self.assertEqual(before, handle.getvalue())

    def test_flush_fsync_and_write_failures_sticky(self):
        for operation in ('write','flush','fsync'):
            handle = MemoryHandle()
            writer = Phase2JsonlWriter(handle)
            target = 'phase2_log.os.fsync' if operation == 'fsync' else None
            context = patch(target, side_effect=OSError('private details')) if target else patch.object(handle, operation, side_effect=OSError('private details'))
            with context, self.assertRaisesRegex(ResponseContractError, '^Log storage failed$'):
                writer.append(event())
            self.assertTrue(writer.failed)
            with self.assertRaises(ResponseContractError): writer.ensure_ready()

    def test_sequence_and_file_limit_before_write(self):
        size = len(serialize_event(event()))
        handle = MemoryHandle()
        writer = Phase2JsonlWriter(handle, max_file_bytes=size)
        with patch('phase2_log.os.fsync') as sync:
            writer.append(event())
            sync.assert_called_once_with(123)
            with self.assertRaises(ResponseContractError): writer.append(event(sequence=1))
        self.assertEqual(size, len(handle.getvalue()))
        for first in (event(sequence=1), event(status='invalid')):
            writer = Phase2JsonlWriter(MemoryHandle())
            with self.assertRaises(ResponseContractError): writer.append(first)
            self.assertTrue(writer.failed)
        with self.assertRaises(ResponseContractError): Phase2JsonlWriter(MemoryHandle(), max_file_bytes=MAX_FILE_BYTES+1)

    def test_real_file_fsync_and_run_identity(self):
        with tempfile.TemporaryFile() as handle:
            writer = Phase2JsonlWriter(handle)
            writer.append(event())
            with self.assertRaises(ResponseContractError): writer.append(event(sequence=1, run_id='different'))
            handle.seek(0)
            self.assertEqual(serialize_event(event()), handle.read())

    def test_nonempty_handle_rejected(self):
        with self.assertRaises(ResponseContractError): Phase2JsonlWriter(MemoryHandle(b'old'))

    def test_wrong_container_types_are_contract_errors(self):
        for key in ('status', 'error_code', 'query_kind', 'provider', 'event_type'):
            with self.subTest(key=key), self.assertRaises(ResponseContractError):
                serialize_event(event(**{key: []}))

    def test_exact_four_mib_boundary(self):
        handle = MemoryHandle()
        writer = Phase2JsonlWriter(handle)
        with patch('phase2_log.os.fsync'):
            for sequence in range(MAX_FILE_BYTES // 8192):
                base = len(serialize_event(event(sequence=sequence, cost_limit_usd=Decimal(1))))
                writer.append(event(sequence=sequence, cost_limit_usd=Decimal('1'+'0'*(8192-base))))
            self.assertEqual(MAX_FILE_BYTES, len(handle.getvalue()))
            with self.assertRaises(ResponseContractError):
                writer.append(event(sequence=MAX_FILE_BYTES // 8192))
        self.assertEqual(MAX_FILE_BYTES, len(handle.getvalue()))
