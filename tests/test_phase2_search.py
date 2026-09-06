import unittest
from dataclasses import replace

from phase2_contracts import SearchRequest, SourceCandidate, QueryKind, ResponseContractError, DependencyError
from phase2_search import validate_search_request, validate_candidate, FakeSearchProvider, make_candidate


def request(**kw):
    return replace(SearchRequest(QueryKind.OFFICIAL, 'fixed query', 5, 0), **kw)


def candidate(**kw):
    return replace(SourceCandidate('fixture', QueryKind.OFFICIAL, 1, 'https://example.com/', 'title', '', None, None), **kw)


class SearchTests(unittest.TestCase):
    def test_request_boundaries(self):
        for kw in ({'query': ''}, {'query': ' x'}, {'query': 'x '}, {'query': 'x'*501}, {'query': 'x\n'}, {'query': '\ud800'}, {'query_kind': 'official'}, {'max_results': True}, {'max_results': 0}, {'max_results': 11}, {'request_ordinal': True}, {'request_ordinal': -1}):
            with self.subTest(kw=kw), self.assertRaises(ResponseContractError):
                validate_search_request(request(**kw))
        self.assertEqual('x'*500, validate_search_request(request(query='x'*500)).query)

    def test_candidate_limits_and_controls(self):
        for kw in ({'source_id':'a b'}, {'source_id':'x'*65}, {'rank':True}, {'rank':11}, {'url':'é'*1025}, {'title':' '*3}, {'title':'x'*501}, {'snippet':'x'*1001}, {'publisher_hint':''}, {'publisher_hint':'x'*201}, {'published_at_hint':'x'*101}, {'query_kind':'official'}):
            with self.subTest(kw=kw), self.assertRaises(ResponseContractError):
                validate_candidate(candidate(**kw))
        for field in ('source_id','url','title','snippet','publisher_hint','published_at_hint'):
            for control in ('\x00','\n','\x85','\ud800'):
                with self.subTest(field=field), self.assertRaises(ResponseContractError):
                    validate_candidate(candidate(**{field:'a'+control}))
        self.assertEqual('title', validate_candidate(candidate(title=' title ')).title)
        self.assertEqual('é'*1024, validate_candidate(candidate(url='é'*1024)).url)

    def test_factory_ids_are_deterministic(self):
        value = make_candidate(request(request_ordinal=2), candidate(rank=3), 1)
        self.assertEqual('Q2-R3-C1', value.source_id)
        self.assertEqual(value, make_candidate(request(request_ordinal=2), candidate(rank=3), 1))

    def test_provider_copies_sorts_and_limits(self):
        values = [candidate(rank=2, source_id='b'), candidate(source_id='a')]
        mapping = {request(max_results=1):values}
        provider = FakeSearchProvider(mapping)
        values.clear()
        mapping.clear()
        result = provider.search(request(max_results=1))
        self.assertEqual(['Q0-R1-C1'], [x.source_id for x in result])
        result.clear()
        self.assertEqual(1, len(provider.search(request(max_results=1))))

    def test_unknown_and_duplicate_candidates_rejected(self):
        with self.assertRaises(DependencyError):
            FakeSearchProvider({}).search(request())
        with self.assertRaises(ResponseContractError):
            FakeSearchProvider({request():[candidate(), candidate(rank=2)]})

    def test_kind_mismatch_rejected(self):
        with self.assertRaises(ResponseContractError):
            FakeSearchProvider({request():[candidate(query_kind=QueryKind.COUNTER)]})
