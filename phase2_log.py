"""Bounded, allowlisted offline audit events; caller owns the cooperative lock.

All events flush/fsync. Failed writers are terminal and must gate further work.
No free-form diagnostic fields are accepted. Identifiers must be caller-generated
opaque tokens, never copied from external payloads.
"""

import json
import os
import re
from dataclasses import dataclass, fields
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType

from phase2_contracts import ResponseContractError

MAX_LINE_BYTES = 8192
MAX_FILE_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class LogEvent:
    schema_version: str
    run_id: str
    sequence: int
    timestamp: str
    phase: str
    event_type: str
    market_id: str | None
    query_kind: str | None
    provider: str | None
    attempt: int | None
    status: str
    error_code: str | None
    http_status: int | None
    response_byte_count: int | None
    selected_count: int | None
    fetched_count: int | None
    duration_ms: int | None
    request_limit: int | None
    cost_limit_usd: Decimal | None
    request_count: int | None
    retry_count: int | None


LOG_KEYS = tuple(field.name for field in fields(LogEvent))
ERROR_CODES = frozenset(('url_safety', 'lock_conflict', 'dependency', 'response_contract', 'budget_limit', 'provider_auth', 'mime_rejected'))
# Each entry: allowed statuses, required non-null fields, optional fields.
EVENT_POLICY = MappingProxyType({
    'run_started': (frozenset(('started',)), frozenset(('provider','request_limit','cost_limit_usd','request_count','retry_count')), frozenset()),
    'request_reserved': (frozenset(('started',)), frozenset(('provider','attempt','request_limit','request_count','retry_count')), frozenset(('market_id','query_kind','cost_limit_usd'))),
    'search_finished': (frozenset(('succeeded','failed')), frozenset(('provider','attempt','request_count','retry_count')), frozenset(('market_id','query_kind','selected_count','duration_ms','error_code','http_status'))),
    'fetch_finished': (frozenset(('succeeded','failed')), frozenset(('provider','attempt','request_count','retry_count')), frozenset(('market_id','query_kind','response_byte_count','fetched_count','duration_ms','error_code','http_status'))),
    'retry_scheduled': (frozenset(('retry_scheduled',)), frozenset(('provider','attempt','request_count','retry_count','error_code','duration_ms')), frozenset(('market_id','query_kind','http_status'))),
    'run_finished': (frozenset(('succeeded','failed')), frozenset(('request_count','retry_count')), frozenset(('provider','selected_count','fetched_count','duration_ms','error_code'))),
    'log_error': (frozenset(('failed',)), frozenset(('error_code',)), frozenset(('request_count','retry_count'))),
})
_COMMON = frozenset(LOG_KEYS[:6]) | {'status'}
_COUNTS = frozenset(('sequence','attempt','http_status','response_byte_count','selected_count','fetched_count','duration_ms','request_limit','request_count','retry_count'))


def _invalid():
    raise ResponseContractError('Invalid log event')


def _number(value):
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        _invalid()
    if not value:
        return '0'
    sign, digits, exponent = value.as_tuple()
    # Bound before fixed-point expansion, independent of Decimal context.
    if max(len(digits) + max(exponent, 0), -exponent + 2) > MAX_LINE_BYTES:
        _invalid()
    result = format(value, 'f')
    return result.rstrip('0').rstrip('.') if '.' in result else result


def serialize_event(event):
    if type(event) is not LogEvent:
        _invalid()
    values = {key: getattr(event, key) for key in LOG_KEYS}
    for key in ('schema_version','run_id','timestamp','phase','event_type','market_id','query_kind','provider','status','error_code'):
        if values[key] is not None and not isinstance(values[key], str):
            _invalid()
    if event.schema_version != '1.0' or event.phase != '2a':
        _invalid()
    for key in ('run_id', 'market_id'):
        value = values[key]
        if value is None and key == 'market_id':
            continue
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value):
            _invalid()
    try:
        if not isinstance(event.timestamp, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z', event.timestamp):
            _invalid()
        datetime.strptime(event.timestamp, '%Y-%m-%dT%H:%M:%SZ')
    except ValueError:
        _invalid()
    for key in _COUNTS:
        value = values[key]
        if value is not None and (type(value) is not int or not 0 <= value <= 2**63-1):
            _invalid()
    if event.sequence is None:
        _invalid()
    if event.provider not in (None, 'fake') or event.query_kind not in (None,'official','status','support','counter'):
        _invalid()
    if event.error_code is not None and event.error_code not in ERROR_CODES:
        _invalid()
    if not isinstance(event.event_type, str) or event.event_type not in EVENT_POLICY:
        _invalid()
    statuses, required, optional = EVENT_POLICY[event.event_type]
    if event.status not in statuses:
        _invalid()
    if any(values[key] is None for key in required):
        _invalid()
    if any(value is not None for key, value in values.items() if key not in _COMMON | required | optional):
        _invalid()
    if (event.status in ('failed', 'retry_scheduled')) != (event.error_code is not None):
        _invalid()
    if event.event_type == 'log_error' and event.error_code != 'response_contract':
        _invalid()
    if event.event_type == 'search_finished' and event.status == 'succeeded' and event.selected_count is None:
        _invalid()
    if event.event_type == 'fetch_finished' and event.status == 'succeeded' and (event.response_byte_count is None or event.http_status != 200 or event.fetched_count is None):
        _invalid()
    pieces = []
    for key, value in values.items():
        token = _number(value) if key == 'cost_limit_usd' and value is not None else json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        pieces.append(json.dumps(key) + ':' + token)
    payload = ('{' + ','.join(pieces) + '}\n').encode('utf-8')
    if len(payload) > MAX_LINE_BYTES:
        _invalid()
    return payload


class Phase2JsonlWriter:
    """Injected empty binary file, owned by caller; no paths opened here.

An append failure can leave a partial last audit line. It is never retried or
repaired automatically. The run stops; no new request may start.
"""

    def __init__(self, handle, *, max_file_bytes=MAX_FILE_BYTES):
        self.failed = False
        self._handle = handle
        self._sequence = 0
        self._size = 0
        self._run_id = None
        if type(max_file_bytes) is not int or not 1 <= max_file_bytes <= MAX_FILE_BYTES:
            _invalid()
        self._limit = max_file_bytes
        try:
            if handle.seek(0, os.SEEK_END) != 0:
                _invalid()
        except (OSError, ValueError):
            raise ResponseContractError('Log storage failed') from None

    @property
    def can_start_request(self):
        return not self.failed

    def ensure_ready(self):
        if self.failed:
            raise ResponseContractError('Log writer has failed')

    def append(self, event):
        self.ensure_ready()
        try:
            payload = serialize_event(event)
            if event.sequence != self._sequence or (self._run_id is not None and event.run_id != self._run_id):
                _invalid()
            if self._size + len(payload) > self._limit:
                _invalid()
            if self._handle.write(payload) != len(payload):
                raise OSError('Short log write')
            self._handle.flush()
            os.fsync(self._handle.fileno())
        except ResponseContractError:
            self.failed = True
            raise
        except (OSError, ValueError, TypeError, OverflowError):
            self.failed = True
            raise ResponseContractError('Log storage failed') from None
        self._size += len(payload)
        self._sequence += 1
        self._run_id = event.run_id
