# External Analysis Phase 2A Offline Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 承認済みPhase 2正本に従い、外部通信へ到達不能なURL・DNS/IP・fake fetch・fake search・retry・lock・log基盤を、mock/fakeだけで実装する。

**Architecture:** Phase 1の`external_analysis.py`と`run_external_analysis.py`は変更せず、Phase 2Aを責務別の純粋モジュールへ分離する。parser、policy、fake resolver、fake transport、fake provider、clock、filesystem境界を注入し、production CLIから到達不能にする。実装は3 PRに分け、各PRを`main`へ統合して全回帰を確認した後だけ次PRを開始する。

**Tech Stack:** Python 3.13、標準ライブラリ`dataclasses`・`enum`・`ipaddress`・`urllib.parse`・`json`・`os`・`pathlib`・`typing`、既存`requests`依存が導入する`idna`のUTS #46 API、`unittest`。依存関係と`requirements.txt`は変更しない。

## Global Constraints

- 全TaskはTDD（失敗test、最小実装、対象test、全回帰）の順で進める。
- Phase 2BのURL実通信とPhase 2CのBrave実通信は本計画の対象外であり、それぞれ別設計・別承認まで到達不能とする。
- 詳細正本は`docs/superpowers/specs/2026-08-03-external-analysis-phase2-search-fetch-design.md`。矛盾時は同文書を優先し、推測実装を停止する。
- Phase 2Aはfake/mockだけ。socket接続、system DNS、URL実取得、robots実取得、Brave、OpenAI、課金を行わない。
- Phase 2Aモジュールは`socket`、`urllib.request`、`http.client`、`requests`、`aiohttp`、`httpx`、Brave SDK、OpenAI SDKをimportしない。
- `urllib.parse`は解析だけに使い、backslash、userinfo、authority、percent escape、末尾dotを独自に追加検証する。
- UTS #46 nontransitional IDNAは`idna.encode(host, uts46=True, transitional=False, std3_rules=True)`を使う。標準`encodings.idna`へfallbackしない。既存環境でこのAPIを利用できなければ依存追加せず設計レビューへ戻る。
- Phase 1のCLI、設定4件、終了コード0～3、stdout、data/result不変契約を変更しない。
- Phase 2終了コード4～10は純粋な例外変換関数だけを実装し、CLIへ接続しない。
- lockとlogはtestの`TemporaryDirectory`だけで検証し、repositoryの`data/`へ作らない。
- `analysis_result_*.json`、既存data、契約・料金プラン・請求・支払い設定を変更しない。
- 各taskは失敗テスト、最小実装、対象テスト、全回帰、明示commitの順とする。

---

## 1. PR分割判断

### 案A: Phase 2Aを1 PR

長所は全境界を一度に統合確認できること。短所はURL/SSRF、framing、retry、Windows lock、監査logの異なるriskが1差分に混在し、reviewと切戻しが重くなること。

### 案B: 3 PR分割（採用）

1. `phase2a-contracts-url-network`: 内部型、例外、URL、IP、fake DNS、connection plan
2. `phase2a-fake-fetch-retry`: fake transport、redirect、response、decode、retry
3. `phase2a-search-storage-integration`: fake SearchProvider、lock、JSONL log、offline integration、README、plan

案Bを採用する。各PRは単独でnetwork importを持たず、全test成功後に`main`へ統合する。後続PRは前PR統合後の最新`main`から作り、3 PRを並行開発しない。

## 2. 推奨ファイル構成

| ファイル | 単一責務 |
| --- | --- |
| `phase2_contracts.py` | enum、immutable dataclass、Protocol、内部例外、終了コード変換 |
| `phase2_url_policy.py` | URL構文、IDNA、HTTPS/443、fragment、redirect URL解析 |
| `phase2_network_policy.py` | global-unicast、fake DNS結果、connection plan、peer IP照合 |
| `phase2_fetch.py` | fake transport、redirect追跡、header/body/MIME/decode検証 |
| `phase2_retry.py` | retry分類、backoff、fake clock/sleep、request上限 |
| `phase2_search.py` | SearchRequest/SourceCandidate検証とfake SearchProvider |
| `phase2_lock.py` | 排他lockの作成・所有確認・削除 |
| `phase2_log.py` | 固定JSONL serialize、上限、短いwrite、flush/fsync |
| `tests/test_phase2_*.py` | 各責務の境界test |
| `tests/test_phase2_offline_integration.py` | 禁止import、未通信、APIキー未読込、data/result不変 |

既存`external_analysis.py`へPhase 2Aを追加せず、`run_external_analysis.py`も変更しない。

## 3. 共通interface

最初のPRで次の名前と型を固定し、後続PRは変更せず利用する。

```python
class QueryKind(str, Enum):
    OFFICIAL = "official"
    STATUS = "status"
    SUPPORT = "support"
    COUNTER = "counter"

class Phase2Error(Exception):
    exit_code: int

class UrlSafetyError(Phase2Error):       # 4
class LockConflictError(Phase2Error):    # 5
class DependencyError(Phase2Error):      # 6
class ResponseContractError(Phase2Error):# 7
class BudgetLimitError(Phase2Error):     # 8
class ProviderAuthError(Phase2Error):    # 9
class MimeRejectedError(Phase2Error):    # 10

def phase2_exit_code(error: Phase2Error) -> int:
    return error.exit_code

@dataclass(frozen=True)
class PolicyUrl:
    original: str
    request_url: str
    hostname: str
    port: int
    path_and_query: str

@dataclass(frozen=True)
class DnsResolution:
    hostname: str
    addresses: tuple[str, ...]
    cname_chain: tuple[str, ...]

@dataclass(frozen=True)
class ConnectionPlan:
    url: PolicyUrl
    verified_ips: tuple[str, ...]

@dataclass(frozen=True)
class RedirectHop:
    request_url: str
    status_code: int
    location: str
    peer_ip: str

@dataclass(frozen=True)
class SourceCandidate:
    source_id: str
    query_kind: QueryKind
    rank: int
    url: str
    title: str
    snippet: str
    publisher_hint: str | None
    published_at_hint: str | None

@dataclass(frozen=True)
class SearchRequest:
    query_kind: QueryKind
    query: str
    max_results: int
    request_ordinal: int

class SearchProvider(Protocol):
    def search(self, request: SearchRequest) -> list[SourceCandidate]:
        raise NotImplementedError

class FileStore(Protocol):
    def create_exclusive(self, path: Path, payload: bytes) -> None:
        raise NotImplementedError
    def read_bytes(self, path: Path) -> bytes:
        raise NotImplementedError
    def remove(self, path: Path) -> None:
        raise NotImplementedError

@dataclass(frozen=True)
class ValidatedFetchResult:
    requested_url: str
    final_url: str
    redirect_chain: tuple[RedirectHop, ...]
    resolved_ips_by_hop: tuple[tuple[str, ...], ...]
    peer_ip_by_hop: tuple[str, ...]
    status_code: int
    content_type: str
    charset: str
    response_bytes: int
    decoded_chars: int
    retrieved_at: str
    decoded_html: str
```

---

## 4. PR 1: 内部型・URL・IP・fake DNS

### Task 1: Phase 2内部型、例外、固定終了コード

**Files:**
- Create: `phase2_contracts.py`
- Create: `tests/test_phase2_contracts.py`

**Interfaces:**
- Produces: 3節のenum、dataclass、Protocol、例外、`phase2_exit_code()`
- Consumes: 標準`dataclasses`、`enum`、`typing.Protocol`

- [ ] **Step 1: 型と終了コードの失敗testを書く**

```python
class ContractTests(unittest.TestCase):
    def test_all_contract_values_are_frozen_and_ordered(self):
        candidate = SourceCandidate("C1", QueryKind.OFFICIAL, 1,
            "https://example.com/", "title", "", None, None)
        self.assertEqual(
            ("source_id", "query_kind", "rank", "url", "title", "snippet",
             "publisher_hint", "published_at_hint"),
            tuple(candidate.__dataclass_fields__),
        )
        with self.assertRaises(FrozenInstanceError):
            candidate.rank = 2

    def test_maps_phase2_errors_to_codes_four_through_ten(self):
        cases = ((UrlSafetyError(), 4), (LockConflictError(), 5),
                 (DependencyError(), 6), (ResponseContractError(), 7),
                 (BudgetLimitError(), 8), (ProviderAuthError(), 9),
                 (MimeRejectedError(), 10))
        for error, code in cases:
            self.assertEqual(code, phase2_exit_code(error))
```

- [ ] **Step 2: 失敗を確認する**

Run: `python -m unittest tests.test_phase2_contracts -v`
Expected: `ModuleNotFoundError: No module named 'phase2_contracts'`

- [ ] **Step 3: 3節のinterfaceを最小実装する**

例外classの`exit_code`はclass定数とし、`phase2_exit_code()`は未知例外を受け取らない型にする。dataclassはすべて`frozen=True`、可変list/dictをfieldへ持たせない。

- [ ] **Step 4: 対象testと既存回帰を実行する**

Run: `python -m unittest tests.test_phase2_contracts -v`
Expected: PASS

Run: `python -m unittest discover -s tests`
Expected: 既存138件を含め全件PASS

- [ ] **Step 5: commitする**

```powershell
git add phase2_contracts.py tests/test_phase2_contracts.py
git commit -m "Add Phase 2A immutable contracts"
```

### Task 2: URL policyとUTS #46 IDNA

**Files:**
- Create: `phase2_url_policy.py`
- Create: `tests/test_phase2_url_policy.py`

**Interfaces:**
- Produces: `parse_policy_url(raw: str) -> PolicyUrl`、`parse_redirect_url(current: PolicyUrl, location: str) -> PolicyUrl`
- Consumes: `PolicyUrl`、`UrlSafetyError`、`urllib.parse.urlsplit/urljoin`、`idna.encode`

- [ ] **Step 1: URL境界のtable-driven失敗testを書く**

```python
class UrlPolicyTests(unittest.TestCase):
    def test_accepts_https_idna_and_removes_fragment(self):
        result = parse_policy_url("https://例え.テスト/a?q=1#frag")
        self.assertEqual("xn--r8jz45g.xn--zckzah", result.hostname)
        self.assertEqual("https://xn--r8jz45g.xn--zckzah/a?q=1", result.request_url)
        self.assertEqual(443, result.port)

    def test_rejects_ambiguous_or_unsafe_urls(self):
        values = ("http://example.com/", "//example.com/", "/relative",
                  "https://u:p@example.com/", "https://example.com:444/",
                  "https://example.com./", "https://a..example/",
                  "https://example.com\\@private/", "https://example.com/%ZZ",
                  "https://example.com/\x00", "https://example.com/\r",
                  "https://example.com/\n", "https://example.com/\t")
        for value in values:
            with self.subTest(value=value), self.assertRaises(UrlSafetyError):
                parse_policy_url(value)

    def test_enforces_utf8_and_ascii_url_byte_limits(self):
        with self.assertRaises(UrlSafetyError):
            parse_policy_url("https://example.com/" + "a" * 2049)
```

追加test名を固定する: `test_accepts_public_ipv4_literal`、`test_accepts_bracketed_ipv6_literal_syntax`、`test_rejects_invalid_idna`、`test_rejects_empty_hostname_label`、`test_rejects_invalid_percent_escape`、`test_relative_redirect_is_reparsed_from_start`。

- [ ] **Step 2: 失敗を確認する**

Run: `python -m unittest tests.test_phase2_url_policy -v`
Expected: module import failure

- [ ] **Step 3: URL parserを最小実装する**

```python
def parse_policy_url(raw: str) -> PolicyUrl:
    reject_controls_backslash_bad_percent_and_byte_length(raw)
    parts = urllib.parse.urlsplit(raw, allow_fragments=True)
    require_https_absolute_no_userinfo_443(parts)
    ascii_host = idna.encode(parts.hostname, uts46=True,
        transitional=False, std3_rules=True).decode("ascii")
    reject_trailing_or_empty_labels(ascii_host)
    return build_policy_url_without_fragment(raw, parts, ascii_host)
```

`idna` importまたはUTS #46引数が利用不能なら標準codecへfallbackせずtestを失敗させ、設計レビューへ戻る。IPv6 bracket、userinfo、backslashを`urlsplit`前後の両方で検査する。

- [ ] **Step 4: 対象testと禁止import scanを実行する**

Run: `python -m unittest tests.test_phase2_url_policy -v`
Expected: PASS

Run: `rg -n "urllib\.request|http\.client|requests|socket" phase2_url_policy.py`
Expected: match 0件

- [ ] **Step 5: commitする**

```powershell
git add phase2_url_policy.py tests/test_phase2_url_policy.py
git commit -m "Add strict Phase 2A URL policy"
```

### Task 3: global-unicast、fake DNS、connection plan

**Files:**
- Create: `phase2_network_policy.py`
- Create: `tests/test_phase2_network_policy.py`

**Interfaces:**
- Produces: `is_global_unicast(value: str) -> bool`、`FakeDnsResolver.resolve(hostname: str) -> DnsResolution`、`build_connection_plan(url, resolution) -> ConnectionPlan`、`validate_peer_ip(plan, peer_ip) -> str`
- Consumes: `ipaddress.ip_address`、Phase 2 contracts

- [ ] **Step 1: IP/DNS/peer境界の失敗testを書く**

```python
class NetworkPolicyTests(unittest.TestCase):
    def test_accepts_only_public_addresses(self):
        self.assertTrue(is_global_unicast("8.8.8.8"))
        self.assertTrue(is_global_unicast("2606:4700:4700::1111"))
        for value in ("127.0.0.1", "10.0.0.1", "169.254.169.254",
                      "100.64.0.1", "192.0.2.1", "198.18.0.1", "224.0.0.1",
                      "0.0.0.0", "::1", "fe80::1", "fc00::1",
                      "::ffff:127.0.0.1"):
            with self.subTest(value=value):
                self.assertFalse(is_global_unicast(value))

    def test_rejects_mixed_dns_and_unverified_peer(self):
        resolution = DnsResolution("example.com", ("8.8.8.8", "10.0.0.1"), ())
        with self.assertRaises(UrlSafetyError):
            build_connection_plan(policy_url(), resolution)
        plan = ConnectionPlan(policy_url(), ("8.8.8.8",))
        with self.assertRaises(UrlSafetyError):
            validate_peer_ip(plan, "1.1.1.1")
```

追加test: DNS空、17件、重複の決定的除去、CNAME 8件受理/9件拒否、hostname不一致、IPv4-mapped public/private、peer IP取得不能。

- [ ] **Step 2: 失敗を確認する**

Run: `python -m unittest tests.test_phase2_network_policy -v`
Expected: module import failure

- [ ] **Step 3: policyとfake resolverを最小実装する**

```python
class FakeDnsResolver:
    def __init__(self, records: Mapping[str, DnsResolution]):
        self._records = dict(records)
        self.requested: list[str] = []

    def resolve(self, hostname: str) -> DnsResolution:
        self.requested.append(hostname)
        if hostname not in self._records:
            raise DependencyError("fake DNS result is absent")
        return self._records[hostname]
```

`is_global_unicast()`は`is_global`に加え、CGNAT、documentation、benchmark、mapped IPv4を明示検査する。address順は入力順を保持して重複だけ除去する。

- [ ] **Step 4: PR 1全検証を実行する**

Run: `python -m unittest tests.test_phase2_contracts tests.test_phase2_url_policy tests.test_phase2_network_policy -v`
Expected: PASS

Run: `python -m unittest discover -s tests`
Expected: 全件PASS

Run: `python -m py_compile @(rg --files -g '*.py')`
Expected: exit 0

- [ ] **Step 5: commitし、PR 1レビューで停止する**

```powershell
git add phase2_network_policy.py tests/test_phase2_network_policy.py
git commit -m "Add offline DNS and IP policy"
```

PR 1が承認・main統合されるまでPR 2を開始しない。

---

## 5. PR 2: fake fetch・redirect・response・retry

### Task 4: fake transportとredirect chain

**Files:**
- Create: `phase2_fetch.py`
- Create: `tests/test_phase2_fetch.py`

**Interfaces:**
- Produces: `FakeHttpResponse`、`FakeHttpTransport.get()`、`follow_redirects(start, resolver, transport, limits) -> RawFetchTrace`
- Consumes: PR 1のURL/DNS/connection plan/peer検証

```python
@dataclass(frozen=True)
class FetchLimits:
    max_redirects: int = 3
    max_response_bytes: int = 2_097_152
    max_decoded_chars: int = 20_000
    max_header_bytes: int = 32_768
    max_header_count: int = 64
    max_single_header_bytes: int = 4_096

@dataclass(frozen=True)
class FakeHttpResponse:
    status_code: int
    headers: tuple[tuple[str, str], ...]
    body_chunks: tuple[bytes, ...]
    peer_ip: str | None

@dataclass(frozen=True)
class RawFetchTrace:
    requested_url: PolicyUrl
    final_url: PolicyUrl
    redirect_chain: tuple[RedirectHop, ...]
    resolutions: tuple[DnsResolution, ...]
    peer_ips: tuple[str, ...]
    final_response: FakeHttpResponse
```

`tests/test_phase2_fetch.py`は`limits(**overrides)`、`raw_response(**overrides)`、`responses_for_one_redirect()`を同test file内の固定helperとして定義し、未定義fixtureへ依存しない。

- [ ] **Step 1: redirectと固定requestの失敗testを書く**

```python
def test_revalidates_each_redirect_and_never_sends_sensitive_headers(self):
    transport = FakeHttpTransport(responses_for_one_redirect())
    trace = follow_redirects(parse_policy_url("https://a.example/"), resolver(),
                             transport, limits())
    self.assertEqual(1, len(trace.redirect_chain))
    self.assertEqual(("GET",), tuple(call.method for call in transport.calls))
    for call in transport.calls:
        self.assertNotIn("Cookie", call.headers)
        self.assertNotIn("Authorization", call.headers)
        self.assertNotIn("Referer", call.headers)
```

追加test: 1hop、3hop、4hop拒否、HTTP downgrade、port変更、private redirect、loop、Location欠落、relative Location、peer mismatch、peer取得不能、固定4 header、proxy/body/HEADなし。

- [ ] **Step 2: 失敗を確認する**

Run: `python -m unittest tests.test_phase2_fetch -v`
Expected: module import failure

- [ ] **Step 3: fake transportとmanual redirectを実装する**

Fake transportは事前登録responseだけを返し、未登録callで`AssertionError`にする。transport callはimmutable recordへ記録する。`follow_redirects()`は各hopでURL再parse、fake DNS resolve、connection plan、peer照合を行い、自動redirect機能を持たない。

- [ ] **Step 4: 対象testを実行する**

Run: `python -m unittest tests.test_phase2_fetch -v`
Expected: redirect test PASS

- [ ] **Step 5: commitする**

```powershell
git add phase2_fetch.py tests/test_phase2_fetch.py
git commit -m "Add fake fetch redirect pipeline"
```

### Task 5: response header・byte・MIME・strict decode

**Files:**
- Modify: `phase2_fetch.py`
- Modify: `tests/test_phase2_fetch.py`

**Interfaces:**
- Produces: `validate_response(trace, limits, retrieved_at) -> ValidatedFetchResult`
- Consumes: immutable header pairsとbody chunk tuple

- [ ] **Step 1: response契約の失敗testを追加する**

```python
def test_accepts_html_and_rejects_limit_plus_one(self):
    result = validate_response(raw_response(
        headers=(("Content-Type", "text/html; charset=utf-8"),),
        chunks=(b"<html>ok</html>",)), limits(max_bytes=64), FIXED_TIME)
    self.assertEqual("<html>ok</html>", result.decoded_html)
    with self.assertRaises(ResponseContractError):
        validate_response(raw_response(chunks=(b"a" * 65,)),
                          limits(max_bytes=64), FIXED_TIME)

def test_rejects_compression_and_non_html_mime(self):
    for encoding in ("gzip", "deflate", "br"):
        with self.assertRaises(MimeRejectedError):
            validate_response(raw_response(headers=(
                ("Content-Type", "text/html"),
                ("Content-Encoding", encoding))), limits(), FIXED_TIME)
```

追加test: 200 HTML/XHTML、Content-Length超過、stream/chunked上限+1、複数Content-Length、chunked併用、header数64/65、総量、1header長、PDF/JSON/text/plain、charset未指定UTF-8、UTF-8 BOM、BOM矛盾、decode error、NUL、decoded 20,000/20,001文字、204/400/500。

- [ ] **Step 2: 追加testの失敗を確認する**

Run: `python -m unittest tests.test_phase2_fetch -v`
Expected: `validate_response` missing/failing

- [ ] **Step 3: streaming validatorを実装する**

headerは元のpair列のまま重複・framingを検査し、dictへ変換して情報を失わない。bodyはchunkごとに累計し、上限+1で停止する。Content-Encodingは欠落/identity、MIMEはHTML/XHTMLだけ。decodeはBOM・charset整合後にstrict modeを使い、部分結果を返さない。

- [ ] **Step 4: 対象testと禁止importを確認する**

Run: `python -m unittest tests.test_phase2_fetch -v`
Expected: PASS

Run: `rg -n "socket|urllib\.request|http\.client|requests|aiohttp|httpx" phase2_fetch.py`
Expected: match 0件

- [ ] **Step 5: commitする**

```powershell
git add phase2_fetch.py tests/test_phase2_fetch.py
git commit -m "Validate offline fetch responses"
```

### Task 6: retry policyとfake clock/sleep/jitter

**Files:**
- Create: `phase2_retry.py`
- Create: `tests/test_phase2_retry.py`

**Interfaces:**
- Produces: `RetryPolicy`、`FakeClock`、`run_with_retry(operation, policy, clock, jitter, budget) -> T`
- Consumes: fixed failure kind、request counter

```python
@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 1
    base_seconds: Decimal = Decimal("1")

@dataclass
class RequestCounter:
    limit: int
    used: int = 0

class FakeClock:
    def __init__(self) -> None:
        self.sleeps: list[Decimal] = []
    def sleep(self, seconds: Decimal) -> None:
        self.sleeps.append(seconds)
```

`operation_from()`、`always_raise()`、`zero_jitter()`は`tests/test_phase2_retry.py`内の固定helperとして定義する。

- [ ] **Step 1: retry分類と上限の失敗testを書く**

```python
def test_retries_only_transient_failures_and_counts_requests(self):
    calls = iter((DependencyError("timeout"), "ok"))
    clock = FakeClock()
    counter = RequestCounter(limit=2)
    result = run_with_retry(operation_from(calls), RetryPolicy(max_retries=1),
                            clock, lambda upper: Decimal("0.5"), counter)
    self.assertEqual("ok", result)
    self.assertEqual(2, counter.used)
    self.assertEqual([Decimal("0.5")], clock.sleeps)

def test_never_retries_policy_mime_or_size_failures(self):
    for error in (UrlSafetyError(), MimeRejectedError(), ResponseContractError()):
        counter = RequestCounter(limit=3)
        with self.assertRaises(type(error)):
            run_with_retry(always_raise(error), policy(), FakeClock(), zero_jitter, counter)
        self.assertEqual(1, counter.used)
```

追加test: timeout、429、502/503/504、retry 0/1/2、3拒否、Retry-After 0/30受理・31で中止、request limit、同じordinal、無限retryなし。

- [ ] **Step 2: 失敗を確認する**

Run: `python -m unittest tests.test_phase2_retry -v`
Expected: module import failure

- [ ] **Step 3: retryを最小実装する**

```python
delay = min(Decimal("30"), base_seconds * (2 ** retry_index))
sleep_for = jitter(delay)
```

実sleep、`time.sleep`、乱数globalを直接使わず注入する。request counterをcall直前に予約し、上限到達時は`BudgetLimitError`で新規callを開始しない。

- [ ] **Step 4: PR 2全検証を実行する**

Run: `python -m unittest tests.test_phase2_fetch tests.test_phase2_retry -v`
Expected: PASS

Run: `python -m unittest discover -s tests`
Expected: 全件PASS

- [ ] **Step 5: commitし、PR 2レビューで停止する**

```powershell
git add phase2_retry.py tests/test_phase2_retry.py
git commit -m "Add bounded offline retry policy"
```

PR 2が承認・main統合されるまでPR 3を開始しない。

---

## 6. PR 3: fake search・lock・log・offline integration

### Task 7: fake SearchProvider

**Files:**
- Create: `phase2_search.py`
- Create: `tests/test_phase2_search.py`

**Interfaces:**
- Produces: `validate_search_request()`、`validate_candidate()`、`FakeSearchProvider.search()`
- Consumes: `SearchRequest`、`SourceCandidate`、`QueryKind`

`tests/test_phase2_search.py`は`request()`と`candidate(source_id, rank, **overrides)`を同file内で定義し、query本文、候補URL、title、snippetの既定値を固定する。

- [ ] **Step 1: fake providerの失敗testを書く**

```python
def test_fake_provider_returns_fixed_order_without_environment(self):
    provider = FakeSearchProvider({request(): (candidate("C1", 1), candidate("C2", 2))})
    result = provider.search(request())
    self.assertEqual(["C1", "C2"], [item.source_id for item in result])
    self.assertEqual([request()], provider.requests)

def test_rejects_candidate_limits_and_controls(self):
    for candidate_value in (candidate("C1", 11), candidate("C1", 1, title="x" * 501),
                            candidate("C1", 1, snippet="x" * 1001),
                            candidate("C1", 1, title="bad\nvalue")):
        with self.assertRaises(ResponseContractError):
            validate_candidate(candidate_value)
```

追加test: query kind順、rank順、重複source ID、URL 2,048 bytes、publisher/published hint、時刻・乱数非依存、APIキー名を読むMappingを渡すinterfaceが存在しない。

- [ ] **Step 2: 失敗確認、実装、再実行**

Run: `python -m unittest tests.test_phase2_search -v`
Expected before implementation: module import failure

実装後Expected: PASS

- [ ] **Step 3: commitする**

```powershell
git add phase2_search.py tests/test_phase2_search.py
git commit -m "Add deterministic fake search provider"
```

### Task 8: 排他lock

**Files:**
- Create: `phase2_lock.py`
- Create: `tests/test_phase2_lock.py`

**Interfaces:**
- Produces: `LocalFileStore`、`acquire_lock(store, directory, suffix, metadata) -> Phase2Lock`、`Phase2Lock.release()`
- Consumes: `FileStore`。`LocalFileStore`だけが`os.open`の`O_CREAT|O_EXCL|O_WRONLY`を使う

```python
@dataclass(frozen=True)
class LockMetadata:
    lock_version: str
    run_id: str
    started_at: str
    target_suffix: str

@dataclass(frozen=True)
class Phase2Lock:
    path: Path
    run_id: str
```

`Phase2Lock.release(self) -> bool`を実装し、`metadata(run_id)`はtest file内で固定UTC時刻とsuffixを返すhelperとして定義する。
unit testはin-memory `FakeFileStore`で競合・crash・ownershipを検証し、1件のWindows互換testだけ`TemporaryDirectory`と`LocalFileStore`を使う。

- [ ] **Step 1: ownershipとWindows filesystemの失敗testを書く**

```python
def test_lock_is_exclusive_and_only_owner_releases_it(self):
    with TemporaryDirectory() as directory:
        first = acquire_lock(Path(directory), "2026-08-03_1200", metadata("run-1"))
        with self.assertRaises(LockConflictError):
            acquire_lock(Path(directory), "2026-08-03_1200", metadata("run-2"))
        forged = Phase2Lock(first.path, "run-2")
        self.assertFalse(forged.release())
        self.assertTrue(first.path.exists())
        self.assertTrue(first.release())
```

追加test: 固定4キー順、UTF-8 BOMなし/LF/末尾LF、短いwrite、cleanup、crash残存、開始時刻が古くても自動削除なし、別suffix独立、実Windows temp directory。

- [ ] **Step 2: 失敗確認、排他作成、所有確認削除を実装する**

Run: `python -m unittest tests.test_phase2_lock -v`
Expected before implementation: module import failure

正式pathを先に通常openしない。排他作成後に全byte write・flush・fsync・closeし、失敗時は自分が作成した不完全lockだけをbest effort削除する。release時はJSONを厳格読込しrun ID一致時だけ削除する。

- [ ] **Step 3: commitする**

```powershell
git add phase2_lock.py tests/test_phase2_lock.py
git commit -m "Add fail-closed Phase 2A lock"
```

### Task 9: 固定JSONL serializer/writer

**Files:**
- Create: `phase2_log.py`
- Create: `tests/test_phase2_log.py`

**Interfaces:**
- Produces: `LogEvent`、`serialize_event(event) -> bytes`、`Phase2JsonlWriter.append(event)`
- Consumes: fixed 21-key order、`FileStore`またはbinary handle injection

`LogEvent`は正本15節の21キーを同じ順序で持つ`frozen=True` dataclassとし、string、非負integer、`Decimal | None`の型をfieldごとに固定する。`tests/test_phase2_log.py`は全21値を明示する`event(**overrides)` helperと、指定byte数だけ返す`ShortWriter`を同file内で定義する。

- [ ] **Step 1: JSONL契約とfail-closedの失敗testを書く**

```python
def test_serializes_fixed_utf8_lf_without_secrets(self):
    payload = serialize_event(event())
    self.assertTrue(payload.endswith(b"\n"))
    self.assertNotIn(b"\r", payload)
    self.assertLessEqual(len(payload), 8192)
    self.assertEqual(LOG_KEYS, tuple(json.loads(payload).keys()))

def test_short_write_and_fsync_failure_stop_future_calls(self):
    writer = Phase2JsonlWriter(ShortWriter(), max_file_bytes=4 * 1024 * 1024)
    with self.assertRaises(ResponseContractError):
        writer.append(event())
    self.assertTrue(writer.failed)
    with self.assertRaises(ResponseContractError):
        writer.append(event(sequence=1))
```

追加test: 1行8,192/8,193、file 4MiB境界、sequence、固定event type、Decimal number、NUL/surrogate、API key/Authorization/Cookie/body/URL/query/title/snippet/絶対path禁止、flush/fsync event、API成功後log失敗時にretry callback 0回。

- [ ] **Step 2: 失敗確認、serializer/writerを実装する**

Run: `python -m unittest tests.test_phase2_log -v`
Expected before implementation: module import failure

writerは1 eventを1回のbinary `write(payload)`で書き、戻り値を全byte長と比較する。失敗後はsticky failure状態とし、後続外部callを許可する判定をfalseにする。Phase 2Aではtemp directory以外へ接続しない。

- [ ] **Step 3: commitする**

```powershell
git add phase2_log.py tests/test_phase2_log.py
git commit -m "Add bounded Phase 2A JSONL logging"
```

### Task 10: 完全オフライン統合・回帰・文書

**Files:**
- Create: `tests/test_phase2_offline_integration.py`
- Modify: `README.md`
- Modify: `plan.md`

**Interfaces:**
- Consumes: Phase 2A全module
- Produces: offline安全性の実行証拠。production CLI入口は追加しない。

- [ ] **Step 1: static importとruntime非通信testを書く**

```python
def test_phase2_modules_have_no_forbidden_imports(self):
    forbidden = {"socket", "urllib.request", "http.client", "requests",
                 "aiohttp", "httpx", "openai", "brave"}
    for path in PHASE2_MODULE_PATHS:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = imported_names(tree)
        self.assertTrue(forbidden.isdisjoint(imports), (path, imports & forbidden))

def test_offline_pipeline_preserves_data_and_never_reads_api_keys(self):
    env = ObservedMapping({"OPENAI_API_KEY": "secret", "BRAVE_API_KEY": "secret"})
    fake_store = FakeFileStore()
    before = hashes(temp_data_dir)
    request = fixed_search_request()
    candidates = FakeSearchProvider(fixed_candidates()).search(request)
    trace = follow_redirects(parse_policy_url(candidates[0].url),
                             fake_resolver(), fake_transport(), limits())
    result = validate_response(trace, limits(), FIXED_TIME)
    writer = Phase2JsonlWriter(fake_store.open_log("run.jsonl"),
                               max_file_bytes=4 * 1024 * 1024)
    writer.append(fixed_log_event())
    self.assertEqual(before, hashes(temp_data_dir))
    self.assertEqual([], env.requested)
    self.assertEqual("<html>fixture</html>", result.decoded_html)
```

`FakeFileStore`はcreate/read/remove/open_logと保存byteをmemory内だけに記録するtest doubleとする。`ObservedMapping`、`hashes`、`fixed_search_request`、`fixed_candidates`、`fake_resolver`、`fake_transport`、`fixed_log_event`、`limits`、`FIXED_TIME`も統合test file内で固定実装する。追加test: `socket.getaddrinfo`/`socket.socket`/`urllib.request.urlopen`/`http.client.HTTPConnection.request`へpatchしたsentinelが0回、system resolverなし、fake provider/resolver/transport/clock/filesystemだけ、analysis result SHA-256不変、repository dataへlock/logなし、Phase 1 CLI output byte不変。

- [ ] **Step 2: production CLI非統合を確認する**

`run_external_analysis.py`と`external_analysis.py`のdiffが空であることをtestまたは`git diff main --`で確認する。Phase 2A専用CLI、subcommand、環境変数読込を追加しない。

- [ ] **Step 3: READMEとplanを最小更新する**

READMEへ「Phase 2Aは内部pure/fake基盤で、production CLI・外部通信・APIキー・課金・結果更新なし」と記載する。planはPhase 2A実装・offline testを完了扱いにするが、Phase 2B/2Cは未着手のまま維持する。

- [ ] **Step 4: 全検証を実行する**

```powershell
python -m unittest discover -s tests -v
$py = @(rg --files -g '*.py')
python -m py_compile $py
git diff --check
rg -n "^(import|from) (socket|urllib\.request|http\.client|requests|aiohttp|httpx|openai|brave)" phase2_*.py
git diff main -- external_analysis.py run_external_analysis.py analyze_market.py
```

Expected: 全test PASS、構文exit 0、diff check 0、禁止import 0件、Phase 1/analysis code diffなし。

- [ ] **Step 5: PR 3最終commitとレビュー**

```powershell
git add tests/test_phase2_offline_integration.py README.md plan.md
git commit -m "Verify Phase 2A offline foundation"
```

PR 3をpushしてDraft PRを作成し、mainへmergeせずcode reviewで停止する。

---

## 7. 課金・通信安全性の独立証明

実装完了の主張には、次の全証拠を同じPR headで取得する。

| 証明対象 | 証明方法 |
| --- | --- |
| 有料API class/Brave adapterなし | Phase 2 module名・class名・importのAST scan |
| APIキー未読込・存在確認なし | APIキーアクセス時に失敗する`ObservedMapping`のrequestedが空 |
| network importなし | Phase 2 module ASTで禁止module 0件 |
| network callなし | socket/DNS/HTTP入口sentinel 0回 |
| provider設定だけで通信不能 | production CLIがPhase 2 moduleをimportせず、Phase 1のfake/trueだけを維持 |
| dry-run falseでも到達不能 | Phase 1が従来どおり終了コード3、Phase 2A CLIなし |
| fake以外をCLI注入不能 | Phase 2Aをproduction CLIへ接続しない |
| 課金・契約変更なし | billing/subscription/payment関連import・HTTP adapter・設定書込0件 |
| analysis result不変 | temp fixtureと既存dataの実行前後SHA-256一致 |

Phase 2B/2Cの承認flag、APIキー名、Brave adapter、real resolver/transport stubを先取りして実装しない。

## 8. CLI、lock、logの接続時期

- Phase 2Aはproduction CLIから到達不能とする。`run_external_analysis.py`を変更しない。
- Phase 2の終了コード変換は純粋関数だけをtestし、CLI mappingはPhase 2B/2Cの別承認後。
- lock/logのcodeは実装するが、testの`TemporaryDirectory`と注入handleだけで呼ぶ。
- Phase 1 dry-runへlock/logを追加しない。repository `data/`へ生成しない。
- real URL、robots、Braveの入口はclassもstubも作らない。

## 9. 正本対応表

| Phase 2正本 | 実装task |
| --- | --- |
| 1～3 安全原則・段階分離 | Global Constraints、Task 10、課金安全性表 |
| 4 URL受理・IDNA | Task 2 |
| 5 SSRF・DNS・peer IP | Task 3 |
| 6 redirect | Task 4 |
| 7 GET・固定header・identity | Task 4・5 |
| 8 response hard max | Task 5 |
| 9 MIME・strict decode | Task 5 |
| 10 immutable contracts | Task 1・7 |
| 11～12 Brave・費用gate | 実装禁止。Task 10でadapter不存在を証明 |
| 13 retry | Task 6 |
| 14 lock | Task 8 |
| 15 JSONL log | Task 9 |
| 16 終了コード4～10 | Task 1。CLI未接続 |
| 17 test観点 | Task 2～10 |
| 18 決定性 | fake clock/jitterと固定順test |
| 19 開始gate | 3 PRの逐次review gate |
| 20 完了条件 | PR 3全検証 |

## 10. 実装禁止確認表

| 禁止対象 | 計画上の扱い |
| --- | --- |
| socket/system DNS/getaddrinfo | import・callともAST/runtime testで拒否 |
| urllib.request/http.client/requests/aiohttp/httpx | Phase 2A moduleでimport禁止 |
| Brave SDK/HTTP adapter | class・stub・APIキー名を作らない |
| URL/robots実取得 | fake responseだけ |
| OpenAI/Structured Outputs | 対象外、import禁止 |
| completed/error/result更新 | Phase 1/analysis code不変、SHA-256 test |
| 課金・契約・請求・支払い | adapter・設定変更codeなし |
| Phase 2B/2C | 承認を先取りしない |
| 売買/wallet/注文 | module・field・処理なし |

## 11. 各PRの完了ゲート

各PRで対象test、全test、全Python構文、`git diff --check`、禁止import scan、変更file範囲を確認する。PR headとorigin一致、worktree cleanを確認し、review承認・main統合後だけ次PRを開始する。3 PR完了後もPhase 2B/2Cへ進まず、別の明示承認を待つ。
