# Phase 2B Real DNS/TLS Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Phase 2Bの後続HTTP取得処理が利用できる、OS設定recursive resolverを使う安全なDNS解決と、DNS pinningを維持するTLS接続基盤を完全オフラインテスト付きで実装する。

**Architecture:** `phase2_dns.py`がdnspythonのpublic APIを狭いbackend境界へ閉じ込め、A→AAAA順、CNAME最大8段、全IP fail-closedの`DnsResolution`を返す。`phase2_tls.py`は環境変数を参照しないSSLContextを明示生成し、数値IPへ接続したsocketを元hostnameのSNIと証明書検証でTLS化し、GET送信前にpeer IPを既存`ConnectionPlan`へ照合する。両モジュールは既存production CLIへ接続せず、通常テストではfake backendだけを使いreal DNS/socket/HTTPを0回に保つ。

**Tech Stack:** Python 3.10～3.13、標準`socket`/`ssl`、`dnspython>=2.8,<2.9`、`h11>=0.16,<0.17`、`unittest`

**Spec:** `docs/superpowers/specs/2026-09-06-external-analysis-phase2b-real-fetch-design.md`

## Global Constraints

- Phase 2B PR 1はDNS/TLS foundationだけを実装し、HTTP parser、redirect、CLI、lock/log統合、実通信smoke testを含めない。
- `requests`、urllib3、httpx、system/environment proxy、DoH、DDR、固定public resolverを使用しない。
- DNSはOS設定Do53 recursive resolverだけを使い、A、AAAAの順に問い合わせ、各response内の順序を維持する。
- CNAMEは最大8段、正規化後IPは最大16件、1件でも不正・unsafeなら全体を拒否する。
- TLSは数値IPへ直接接続し、SNI・hostname検証は元のASCII hostnameを使う。
- `ssl.create_default_context()`と`SSLKEYLOGFILE`参照、custom CA、client certificate、`verify=False`、TLS downgradeを禁止する。
- 通常テストとCIではreal DNS、socket、TLS network、HTTP通信を行わない。
- Brave、OpenAI、APIキー、課金、売買、analysis result、既存data成果物を変更しない。

---

### Task 1: Direct dependencies and boundary contracts

**Files:**
- Modify: `requirements.txt`
- Modify: `phase2_contracts.py`
- Test: `tests/test_phase2_contracts.py`

**Interfaces:**
- Produces: `DnsQueryBackend.query(hostname: str, rdtype: str, timeout_seconds: float) -> DnsQueryResult`
- Produces: `TlsByteStream.peer_ip() -> str`, `send_all(data: bytes) -> None`, `receive(max_bytes: int) -> bytes`, `close() -> None`
- Produces: `TlsConnector.connect(plan: ConnectionPlan, timeout_seconds: float) -> TlsByteStream`
- Produces: frozen `DnsQueryResult(hostname, canonical_hostname, addresses, cname_chain)`

- [ ] **Step 1: Write failing contract tests**

Add tests which import the three protocols and frozen dataclass, assert field order, tuple collection fields, runtime protocol availability, and immutability. The mutation caught is a missing/wrong interface before DNS/TLS implementations depend on it.

```python
def test_phase2b_boundary_contracts_are_fixed(self):
    self.assertEqual(
        ("hostname", "canonical_hostname", "addresses", "cname_chain"),
        tuple(field.name for field in fields(DnsQueryResult)),
    )
    self.assertTrue(getattr(DnsQueryBackend, "_is_runtime_protocol", False))
    self.assertTrue(getattr(TlsConnector, "_is_runtime_protocol", False))
    self.assertTrue(getattr(TlsByteStream, "_is_runtime_protocol", False))
```

- [ ] **Step 2: Run the contract test and verify RED**

Run: `python -m unittest tests.test_phase2_contracts.ContractTests.test_phase2b_boundary_contracts_are_fixed -v`

Expected: import failure because the Phase 2B contracts do not exist.

- [ ] **Step 3: Add the reviewed direct dependencies**

Append exactly:

```text
dnspython>=2.8,<2.9
h11>=0.16,<0.17
```

Install with `python -m pip install -r requirements.txt`. Do not add extras or another dependency.

- [ ] **Step 4: Implement the minimal contracts**

Use `@runtime_checkable` Protocols and frozen dataclasses. Protocol methods contain only signatures and docstrings; no network implementation belongs in `phase2_contracts.py`.

- [ ] **Step 5: Run contract and existing Phase 2 tests**

Run: `python -m unittest tests.test_phase2_contracts tests.test_phase2_network_policy -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add requirements.txt phase2_contracts.py tests/test_phase2_contracts.py
git commit -m "Add Phase 2B transport contracts"
```

### Task 2: Deterministic DNS orchestration with an injected backend

**Files:**
- Create: `phase2_dns.py`
- Create: `tests/test_phase2_dns.py`

**Interfaces:**
- Consumes: `DnsQueryBackend`, `DnsQueryResult`, `DnsResolution`
- Produces: `resolve_phase2b(hostname: str, backend: DnsQueryBackend, timeout_seconds: float) -> DnsResolution`
- Produces: `FakeDnsQueryBackend` only in tests

- [ ] **Step 1: Write failing orchestration tests**

Cover literal expectations for:

- query order exactly `[(hostname, "A"), (hostname, "AAAA")]`
- A records followed by AAAA records, first-occurrence duplicate removal
- A-empty/AAAA-present and inverse success
- both empty failure
- one-family temporary failure discarding the other family
- conflicting CNAME targets, CNAME loop, 8 accepted/9 rejected
- malformed or unsafe single IP rejecting the full result
- 16 accepted/17 rejected
- timeout must be positive finite and passed unchanged to the backend

Use public global addresses such as `8.8.8.8` and `2606:4700:4700::1111`; never invoke real DNS.

- [ ] **Step 2: Run the new test module and verify RED**

Run: `python -m unittest tests.test_phase2_dns -v`

Expected: import failure because `phase2_dns.py` does not exist.

- [ ] **Step 3: Implement minimal orchestration**

Normalize every hostname through the existing URL policy by parsing `https://<hostname>/`, maintain a visited CNAME set, call A then AAAA, concatenate in that order, and pass the final immutable result through existing `build_connection_plan()` safety rules before returning its normalized addresses as `DnsResolution`.

Temporary backend failures become `DependencyError`; malformed/conflicting/unsafe results become `UrlSafetyError`. Do not return partial family results.

- [ ] **Step 4: Run DNS tests and verify GREEN**

Run: `python -m unittest tests.test_phase2_dns tests.test_phase2_network_policy tests.test_phase2_url_policy -v`

Expected: PASS with no real network.

- [ ] **Step 5: Commit**

```powershell
git add phase2_dns.py tests/test_phase2_dns.py
git commit -m "Add deterministic Phase 2B DNS orchestration"
```

### Task 3: dnspython Do53 backend

**Files:**
- Modify: `phase2_dns.py`
- Modify: `tests/test_phase2_dns.py`

**Interfaces:**
- Produces: `DnspythonQueryBackend`
- Consumes only dnspython public APIs: `dns.resolver.Resolver`, `dns.nameserver.Do53Nameserver`, `resolve(..., search=False, lifetime=...)`

- [ ] **Step 1: Write failing backend tests with an injected fake Resolver**

Verify that construction rejects non-Do53 nameservers before any query, never calls `try_ddr()`, passes `search=False` and the exact lifetime, maps NXDOMAIN and malformed answers to `UrlSafetyError`, maps timeout/NoNameservers to `DependencyError`, preserves rrset order, and emits no DNS packet or exception text.

- [ ] **Step 2: Run backend tests and verify RED**

Run: `python -m unittest tests.test_phase2_dns.DnspythonBackendTests -v`

Expected: failure because `DnspythonQueryBackend` is missing.

- [ ] **Step 3: Implement the backend**

Create `Resolver(configure=True)` only when no resolver is injected. Verify every `resolver.nameservers` entry is a public `Do53Nameserver` instance (or the documented plain-address representation produced by the supported dnspython version); reject HTTPS/TLS nameserver objects. Parse only CNAME, A, and AAAA rdata from the public Answer/response interfaces, copying strings into `DnsQueryResult`.

- [ ] **Step 4: Run DNS tests and verify GREEN**

Run: `python -m unittest tests.test_phase2_dns -v`

Expected: PASS without external DNS.

- [ ] **Step 5: Commit**

```powershell
git add phase2_dns.py tests/test_phase2_dns.py
git commit -m "Add Do53 Phase 2B DNS backend"
```

### Task 4: Hardened TLS context factory

**Files:**
- Create: `phase2_tls.py`
- Create: `tests/test_phase2_tls.py`

**Interfaces:**
- Produces: `create_phase2b_ssl_context() -> ssl.SSLContext`
- Produces: `_validate_phase2b_ssl_context(context: ssl.SSLContext) -> None`

- [ ] **Step 1: Write failing context tests**

Assert `PROTOCOL_TLS_CLIENT`, `CERT_REQUIRED`, hostname checking, minimum TLS 1.2, `VERIFY_X509_STRICT | VERIFY_X509_PARTIAL_CHAIN`, `keylog_filename is None`, security level at least 2, and non-empty CA certificates. Patch `SSLKEYLOGFILE` in the test process and verify it is not read or copied.

- [ ] **Step 2: Run the context tests and verify RED**

Run: `python -m unittest tests.test_phase2_tls.TlsContextTests -v`

Expected: import failure because `phase2_tls.py` does not exist.

- [ ] **Step 3: Implement the explicit context factory**

Use exactly `ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)`, minimum TLS 1.2, explicit strict/partial-chain flags, and `load_default_certs(ssl.Purpose.SERVER_AUTH)`. Do not call `ssl.create_default_context`, alter environment variables, accept custom CA/client certificates, or expose weakening parameters.

- [ ] **Step 4: Add the isolated keylog regression test**

Run a child Python process with `SSLKEYLOGFILE` pointing to a temporary path; import and call only the production factory, serialize safe context properties to stdout, and assert the keylog path was neither created nor modified. No socket, DNS, or HTTP is permitted in the child.

- [ ] **Step 5: Run TLS context tests and verify GREEN**

Run: `python -m unittest tests.test_phase2_tls.TlsContextTests -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add phase2_tls.py tests/test_phase2_tls.py
git commit -m "Add hardened Phase 2B TLS context"
```

### Task 5: DNS-pinned socket/TLS connector

**Files:**
- Modify: `phase2_tls.py`
- Modify: `tests/test_phase2_tls.py`

**Interfaces:**
- Consumes: `ConnectionPlan`, `validate_peer_ip`, `TlsConnector`, `TlsByteStream`
- Produces: `PinnedTlsConnector(socket_factory=..., context_factory=...)`
- Produces: private owned stream implementing exact send/receive/peer/close methods

- [ ] **Step 1: Write failing connector tests with fake sockets and fake TLS context**

Verify:

- only the first four verified IPs are attempted, in fixed order
- numeric IPv4/IPv6 sockaddr construction never passes hostname to socket connect
- connect refusal/unreachable/timeout before TLS advances to the next IP
- SNI receives the original A-label hostname
- peer IP is checked before any application send
- private, unpinned, or unavailable peer closes the stream and raises `UrlSafetyError`
- certificate/hostname failures stop immediately without failover
- connect/TLS timeout maps to `DependencyError`
- `send_all` rejects short/zero progress and `receive` enforces positive bounded reads
- close is idempotent and ownership does not leak descriptors

- [ ] **Step 2: Run connector tests and verify RED**

Run: `python -m unittest tests.test_phase2_tls.PinnedTlsConnectorTests -v`

Expected: failure because `PinnedTlsConnector` is missing.

- [ ] **Step 3: Implement the minimal connector**

The production default socket factory uses `socket.socket(AF_INET/AF_INET6, SOCK_STREAM)` and connects directly to `(numeric_ip, 443)` or `(numeric_ipv6, 443, 0, 0)`. Apply the exact effective timeout before connect and TLS handshake. Call `context.wrap_socket(raw_socket, server_hostname=plan.url.hostname)`, obtain `getpeername()[0]`, validate it through `validate_peer_ip`, then return the owned TLS stream. Do not send HTTP here.

- [ ] **Step 4: Run connector and all Phase 2 tests**

Run: `python -m unittest tests.test_phase2_tls tests.test_phase2_contracts tests.test_phase2_network_policy tests.test_phase2_fetch tests.test_phase2_retry -v`

Expected: PASS with fake sockets only.

- [ ] **Step 5: Commit**

```powershell
git add phase2_tls.py tests/test_phase2_tls.py
git commit -m "Add DNS-pinned Phase 2B TLS connector"
```

### Task 6: Documentation and full verification

**Files:**
- Modify: `README.md`
- Modify: `plan.md`

**Interfaces:**
- Documents that Phase 2B PR 1 is a library foundation and is not reachable from production CLI.

- [ ] **Step 1: Update documentation**

Record the direct dependencies, DNS/TLS boundary, offline-only test policy, and remaining work: raw head/chunk/h11 parser, orchestration, Phase 2B CLI, lock/log, and separately authorized smoke test. Do not claim real network validation.

- [ ] **Step 2: Run full verification**

```powershell
python -m unittest discover -s tests -v
$files = @(git ls-files '*.py')
python -m py_compile @files
git diff --check
```

Expected: all tests PASS, all Python files compile, and `git diff --check` exits 0.

- [ ] **Step 3: Verify forbidden reachability and changes**

```powershell
rg -n "phase2_dns|phase2_tls" run_external_analysis.py external_analysis.py
git diff --name-only main...HEAD
```

Expected: no production CLI imports; changes limited to dependencies, contracts, two new foundation modules/tests, README, plan, and this implementation plan. No data file, schema, AI, Brave, wallet, or trading change.

- [ ] **Step 4: Commit documentation**

```powershell
git add README.md plan.md docs/superpowers/plans/2026-09-18-phase2b-real-dns-tls-foundation.md
git commit -m "Document Phase 2B DNS TLS foundation"
```

- [ ] **Step 5: Stop for code review**

Push only `agent/phase2b-real-dns-tls-foundation`. Do not merge to `main`, do not execute real DNS/socket/HTTP, and do not create or run the Phase 2B smoke test.
