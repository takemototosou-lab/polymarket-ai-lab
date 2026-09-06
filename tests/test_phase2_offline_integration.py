"""Test-only composition; deliberately no production orchestrator or CLI."""
import ast
import hashlib
import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from phase2_contracts import DnsResolution, QueryKind, ResponseContractError, SearchRequest, SourceCandidate, UrlSafetyError
from phase2_fetch import FakeHttpResponse, FakeHttpTransport, FetchLimits, fetch_validated_html
from phase2_lock import acquire_lock
from phase2_log import Phase2JsonlWriter
from phase2_network_policy import FakeDnsResolver, build_connection_plan
from phase2_retry import FakeClock, FakeConnectTimeout, RequestCounter, RetryPolicy, run_with_retry
from phase2_search import FakeSearchProvider
from phase2_url_policy import parse_policy_url
from tests.test_phase2_lock import metadata
from tests.test_phase2_log import event

ROOT = Path(__file__).resolve().parents[1]


class ObservedMapping(dict):
    def __getitem__(self, key): raise AssertionError('Environment read')
    def get(self, *args): raise AssertionError('Environment read')
    def __contains__(self, key): raise AssertionError('Environment presence check')
    def __iter__(self): raise AssertionError('Environment enumeration')
    def keys(self): raise AssertionError('Environment enumeration')
    def items(self): raise AssertionError('Environment enumeration')
    def copy(self): raise AssertionError('Environment copy')


def hashes(directory):
    return {str(p.relative_to(directory)):hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.rglob('*') if p.is_file()}


def fixtures(peer='8.8.8.8'):
    request = SearchRequest(QueryKind.OFFICIAL, 'fixed query', 1, 0)
    candidate = SourceCandidate('fixture', QueryKind.OFFICIAL, 1, 'https://a.example/', 'title', 'snippet', None, None)
    provider = FakeSearchProvider({request:[candidate]})
    resolver = FakeDnsResolver({h:DnsResolution(h, ('8.8.8.8',), ()) for h in ('a.example','b.example')})
    transport = FakeHttpTransport({
        'https://a.example/':[FakeHttpResponse(302,(('Location','https://b.example/'),),(),peer)],
        'https://b.example/':[FakeHttpResponse(200,(('Content-Type','text/html; charset=utf-8'),),(b'<html>fixture</html>',),'8.8.8.8')],
    })
    return request, provider, resolver, transport


def completed_event(sequence, **kw):
    return event(sequence=sequence, event_type='fetch_finished', status='succeeded', attempt=1,
                 request_limit=None, cost_limit_usd=None, request_count=1, response_byte_count=20,
                 fetched_count=1, http_status=200, **kw)


def offline_run(directory):
    request, provider, resolver, transport = fixtures()
    with acquire_lock(directory, metadata().target_suffix, metadata()):
        with open(directory / 'audit.jsonl', 'x+b') as handle:
            writer = Phase2JsonlWriter(handle)
            writer.append(event())
            writer.ensure_ready()
            writer.append(event(sequence=1,event_type='request_reserved',status='reserved',attempt=1,request_count=1))
            candidates = provider.search(request)
            writer.append(event(sequence=2,event_type='search_finished',status='succeeded',attempt=1,request_limit=None,cost_limit_usd=None,request_count=1,selected_count=1))
            writer.ensure_ready()
            writer.append(event(sequence=3,event_type='request_reserved',status='reserved',attempt=1,request_count=2))
            url = parse_policy_url(candidates[0].url)
            plan = build_connection_plan(url, resolver.resolve(url.hostname))
            result = fetch_validated_html(plan, resolver=resolver, transport=transport, limits=FetchLimits(), now=lambda:'2026-08-03T00:00:00Z')
            writer.append(replace(completed_event(4), request_count=2, response_byte_count=result.response_bytes))
            writer.append(event(sequence=5,event_type='run_finished',status='succeeded',request_limit=None,cost_limit_usd=None,request_count=2,fetched_count=1,selected_count=1))
    return (directory / 'audit.jsonl').read_bytes(), candidates, result


class OfflineIntegrationTests(unittest.TestCase):
    def test_runtime_offline_deterministic_and_data_unchanged(self):
        before_repo = hashes(ROOT / 'data')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / 'data'
            data.mkdir()
            (data / 'analysis_result_fixture.json').write_bytes(b'[]\n')
            before = hashes(data)
            first, second = root / 'first', root / 'second'
            first.mkdir(); second.mkdir()
            with ExitStack() as stack:
                sentinels = [stack.enter_context(patch(name, side_effect=AssertionError('Real network called'))) for name in (
                    'requests.sessions.Session.request',
                    'socket.socket','socket.getaddrinfo','urllib.request.urlopen',
                    'http.client.HTTPConnection.request','http.client.HTTPSConnection.connect')]
                stack.enter_context(patch.object(os, 'environ', ObservedMapping({'OPENAI_API_KEY':'fixture','BRAVE_API_KEY':'fixture'})))
                a = offline_run(first)
                b = offline_run(second)
                self.assertEqual(a, b)
                self.assertEqual('<html>fixture</html>', a[2].decoded_html)
                for sentinel in sentinels: sentinel.assert_not_called()
            self.assertEqual(before, hashes(data))
            self.assertFalse(list(root.rglob('*.lock')))
        self.assertEqual(before_repo, hashes(ROOT / 'data'))

    def test_rejected_peer_has_audit_attempt_without_transport_call(self):
        request, provider, resolver, transport = fixtures(peer='1.1.1.1')
        with tempfile.TemporaryFile() as handle:
            writer = Phase2JsonlWriter(handle)
            writer.append(event())
            writer.append(event(sequence=1,event_type='request_reserved',status='reserved',attempt=1,request_count=1))
            url = parse_policy_url(provider.search(request)[0].url)
            with self.assertRaises(UrlSafetyError):
                try:
                    fetch_validated_html(build_connection_plan(url,resolver.resolve(url.hostname)),resolver=resolver,transport=transport,limits=FetchLimits(),now=lambda:'2026-08-03T00:00:00Z')
                except UrlSafetyError:
                    writer.append(event(sequence=2,event_type='fetch_finished',status='failed',error_code='url_safety',attempt=1,request_limit=None,cost_limit_usd=None,request_count=1))
                    raise
            self.assertEqual((), transport.calls)
            handle.seek(0)
            logs = [json.loads(line) for line in handle]
            self.assertEqual(['run_started','request_reserved','fetch_finished'], [x['event_type'] for x in logs])
            self.assertEqual('url_safety', logs[-1]['error_code'])

    def test_success_then_log_failure_never_retries_operation(self):
        calls = []
        clock = FakeClock()
        with tempfile.TemporaryFile() as handle:
            writer = Phase2JsonlWriter(handle)
            writer.append(event())
            def operation():
                writer.ensure_ready()
                calls.append('fake success')
                writer.append(completed_event(1))
            with patch('phase2_log.os.fsync', side_effect=OSError('fixture')):
                with self.assertRaises(ResponseContractError):
                    run_with_retry(operation, RetryPolicy(), clock, lambda upper:Decimal(0), RequestCounter(3))
            self.assertEqual(['fake success'], calls)
            self.assertEqual((), clock.sleeps)
            with self.assertRaises(ResponseContractError): operation()
            self.assertEqual(['fake success'], calls)

    def test_retry_audit_is_deterministic(self):
        def run():
            clock, counter = FakeClock(), RequestCounter(3)
            with tempfile.TemporaryFile() as handle:
                writer = Phase2JsonlWriter(handle)
                writer.append(event())
                sequence = 1
                def emit(**kw):
                    nonlocal sequence
                    writer.append(event(sequence=sequence, **kw))
                    sequence += 1
                def operation():
                    writer.ensure_ready()
                    attempt = counter.used
                    emit(event_type='request_reserved',status='reserved',attempt=attempt,request_count=attempt,retry_count=attempt-1)
                    if attempt == 1:
                        emit(event_type='fetch_finished',status='failed',error_code='dependency',attempt=attempt,request_count=attempt,retry_count=0,request_limit=None,cost_limit_usd=None)
                        emit(event_type='retry_scheduled',status='scheduled',error_code='dependency',attempt=attempt,request_count=attempt,retry_count=1,duration_ms=500,request_limit=None,cost_limit_usd=None)
                        raise FakeConnectTimeout('fixture')
                    return 'ok'
                self.assertEqual('ok', run_with_retry(operation,RetryPolicy(),clock,lambda upper:Decimal('0.5'),counter))
                handle.seek(0)
                return handle.read(), clock.sleeps, counter.used
        self.assertEqual(run(), run())

    def test_initial_log_failure_prevents_first_call(self):
        calls = []
        with tempfile.TemporaryFile() as handle:
            writer = Phase2JsonlWriter(handle)
            with patch('phase2_log.os.fsync', side_effect=OSError('fixture')):
                with self.assertRaises(ResponseContractError): writer.append(event())
            def operation():
                writer.ensure_ready()
                calls.append('unexpected')
            with self.assertRaises(ResponseContractError): operation()
        self.assertEqual([], calls)

    def test_phase2_imports_and_environment_references(self):
        forbidden = ('socket','urllib.request','http.client','requests','httpx','aiohttp','openai','brave','subprocess','importlib')
        for path in ROOT.glob('phase2_*.py'):
            source = path.read_text(encoding='utf-8')
            tree = ast.parse(source)
            for node in ast.walk(tree):
                names = [n.name for n in node.names] if isinstance(node,ast.Import) else [node.module or ''] if isinstance(node,ast.ImportFrom) else []
                self.assertFalse(any(n == f or n.startswith(f+'.') for n in names for f in forbidden), path.name)
                if isinstance(node,ast.Attribute): self.assertNotIn(node.attr, ('environ','getenv'), path.name)
                if isinstance(node,ast.Name): self.assertNotIn(node.id, ('__import__','eval','exec'), path.name)
            for forbidden_text in ('OPENAI_API_KEY','BRAVE_API_KEY'):
                self.assertNotIn(forbidden_text,source)
        for filename in ('external_analysis.py','run_external_analysis.py'):
            source = (ROOT / filename).read_text(encoding='utf-8')
            self.assertNotIn('phase2_', source)
