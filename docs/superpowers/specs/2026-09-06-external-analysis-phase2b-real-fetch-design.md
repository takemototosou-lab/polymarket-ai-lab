# 外部分析 Phase 2B 実URL安全取得 設計仕様

## 1. 目的と正本の優先順位

本仕様は、利用者がその場で手動指定した公開HTTPS URLを、1 runにつき1件だけ、
安全検証後にread-onlyで取得するPhase 2Bの詳細正本である。

Phase 2全体の共通契約は
`2026-08-03-external-analysis-phase2-search-fetch-design.md`を維持する。ただしPhase 2Bに
ついて両文書が矛盾する場合は、日付が新しく対象範囲も狭い本仕様を優先する。
Phase 2Aのfake実装、serializer、lock、テスト結果を変更する根拠にはしない。

本仕様の承認・main統合だけでは、依存追加、実URL取得、DNS・socket・HTTP通信を許可しない。
依存追加と各実装PRを別レビューし、全実装のmain統合後に利用者が実行直前のURLと上限を
確認して初めて、1回の手動smoke testを実行候補にできる。

## 2. スコープと禁止事項

Phase 2Bは次だけを対象とする。

- 利用者がCLI引数で指定するHTTPS URL 1件
- port 443
- OSに設定されたrecursive resolverを利用するDNS client
- Phase 2Aと同じURL・IP・peer IP安全検証
- DNS pinningされたGET
- redirect、response、MIME、charset、byte、timeout、retry検証
- process memory内だけの検証済みHTML/XHTML
- Phase 2B専用JSONL audit logとfixed-scope lock
- interactiveな実通信承認

次を禁止する。

- 検索、Brave、OpenAI、AI推論、確率予測、analysis result更新
- APIキー・credential・`.env`の読込または存在確認
- paid API、課金、契約・プラン・請求・支払い設定の変更
- 自動URL探索、HTML内リンク追跡、JavaScript、browser automation
- login、cookie jar、POST、form送信、request body、認証header
- raw response、HTML本文、URL一覧、cookie、証明書、DNS packetの永続保存
- wallet、注文、自動売買
- 既存production analysis pipelineへの接続

Phase 2BのAPI料金とAPIキー数は0である。通常のインターネット接続自体の費用は本契約の
API料金には含めない。

## 3. 1 run 1 URLとrobots.txt

`max_urls = 1`を固定し、設定で変更できない。複数URL入力は設定不正として通信前に拒否する。
redirectは同一取得操作内のhopであり、URL件数を増やさないが、各hopのHTTP requestは
request reservationへ算入する。

Phase 2Bではrobots.txtを取得しない。Phase 2Bはcrawlerではなく、利用者が明示した1 URLだけを
取得する実験であるため、robots.txtという第2 URLへの自動通信を禁止する。

この除外はPhase 2C以降の自動検索・crawlingにrobots確認が不要という決定ではない。
自動探索を導入する前に、robots URL、追加confirmation、request accounting、redirect、
取得失敗時の扱いを別設計で再検討する。

## 4. URL入力とinteractive confirmation

Phase 2B専用CLIは位置引数としてURLを正確に1件だけ受け取る。Phase 2AのURL policyを通過した
policy URLと次の上限を、通信前のconfirmation画面へ表示する。

```text
Phase 2B real fetch
URL: https://example.com/...
Requests max: N
Retries max: N
Redirects max: 3
Response max: 2097152 bytes
Total timeout: 15 seconds
External communication: YES
Paid API: NO
API key: NO
Body save: NO
Proceed? Type exactly: FETCH
```

標準入力から改行を除いた結果がASCII大文字4文字の`FETCH`と完全一致する場合だけ続行する。
`fetch`、`Fetch`、前後空白付き、空入力、`YES`、`Y`、EOF、非対話stdinを拒否する。
確認文字列をtrim、case fold、正規化してはならない。

confirmationは当該process・当該run・当該URLだけに有効であり、別URL、再実行、将来実行へ
持ち越さない。環境変数、config、保存済み承認、CLIの`--yes`等で省略できない。
不一致、EOF、非対話stdin、取消は実通信未承認として終了コード3とし、DNS・socket通信を
1 byteも開始しない。

## 5. 実行順とtimeout

通信前の順序を次に固定する。

1. 引数・URL・limit・runtime directoryを通信なしで検証
2. preflightを表示し、exact `FETCH`を確認
3. Phase 2B fixed-scope lockを取得
4. Phase 2B JSONL logを排他的に作成し、`run_started`をflush・fsync
5. monotonic clockでrun total deadlineを開始
6. 最初のDNSを開始
7. DNS、接続、TLS、GET、response検証、redirect、retryを実行
8. 最終eventを記録し、所有確認後にlockをrelease

run total timeoutは1 URL run全体で既定15秒、絶対上限60秒とする。開始点はinitial audit log成功後、
最初のDNS開始直前である。終了点は最終response検証完了またはfailure確定である。

run totalにはDNS、TCP connect、TLS handshake、request send、response read、redirect、retry、
`Retry-After` sleep、redirect先DNSを含む。confirmation待ち時間、lock取得前処理、initial log完了
までの時間は含めない。

attempt単位の上限は次に固定する。

| 処理 | 既定 | 絶対上限 |
| --- | ---: | ---: |
| DNS | 5秒 | 10秒 |
| TCP connectとTLS handshake | 5秒 | 15秒 |
| read inactivity | 10秒 | 30秒 |
| run total | 15秒 | 60秒 |

run total deadlineの残時間が個別timeoutより短い場合は残時間を実効上限とする。設定値が
絶対上限を超える場合はclampせず、通信開始前に設定不正として拒否する。deadlineのため
次の予約、接続、sleepを安全に開始できない場合は終了コード8とする。

## 6. DNS clientとpinning

Phase 2BのDNSは「system DNS」ではなく、次のように定義する。

> OSに設定されたrecursive resolverを利用するDNS client

Windows `getaddrinfo()`そのものには限定しない。DoH、hard-coded public resolver、8.8.8.8、
Cloudflare等の固定resolver、HTTP経由の名前解決を禁止する。実装候補は`dnspython`である。

resolverはURL policy通過済みのASCII hostnameを受け取り、次を満たすimmutableな
`DnsResolution`を返す。

- CNAME targetを各段でUTS #46 nontransitional・hostname構文検証する
- CNAME loop、複数の矛盾するtarget、8段超過を拒否する
- 最終hostnameのAを先に、AAAAを後に明示取得する
- A response内のrecord順とAAAA response内のrecord順をそれぞれ維持する
- A列の後ろへAAAA列を連結してから正規化・重複排除し、最初の出現位置を維持する
- 重複排除後のIPが16件を超える場合は切り詰めず拒否する
- A・AAAAの両方が正常応答で0件なら空結果として拒否する
- unsafe IPが1件でも含まれれば安全なIPだけを選び直さずURL全体を拒否する
- IPv4、IPv6、IPv4-mapped IPv6をPhase 2AのIP policyで検証する
- 一連のCNAME・A・AAAA処理を単一DNS deadlineへ含める

最終IP順を次の手順に固定する。

1. CNAME chainを確定する
2. 最終hostnameのAを問い合わせ、response内のrecord順を保持する
3. AAAAを問い合わせ、response内のrecord順を保持する
4. A列の後ろへAAAA列を連結する
5. IPを正規化し、最初の出現を残して重複排除する
6. 16件上限と全IPの安全性を検証する
7. 固定順を`DnsResolution.addresses`へ保存する
8. transportはその先頭から最大4 IPだけをconnect候補にする

例えばA responseが`203.0.113.10`、`203.0.113.11`、AAAA responseが`2001:db8::10`、
`2001:db8::11`の順なら、統合順もこのA 2件、AAAA 2件の順になる。これらの例示IPは
documentation rangeであるため、順序説明にだけ使用し、実通信ではunsafeとして全体を拒否する。

Aが正常応答で0件または当該record typeなしを明確に示し、AAAAに1件以上のIPがある場合は
AAAA列だけを候補にできる。AAAAが同様に0件またはrecord typeなしで、Aに1件以上ある場合は
A列だけを候補にできる。両方が0件またはrecord typeなしなら拒否する。いずれか一方でも
temporary DNS failureになった場合は、他方の部分結果だけを採用せず、DNS attempt全体を既存retry
policyに従わせる。retryではA・AAAAを両方再取得する。具体的なrcode・exception mappingは
依存・interfaceレビューで固定する。

malformed response、conflicting CNAME、CNAME loop、構文不正IP、unsafe IPはfail closedとし、
retry用の部分結果を返さない。A・AAAAのどちらかにunsafe IPが1件でもあれば、他方や同じresponseに
safe IPが存在してもURL全体を拒否する。

1回のHTTP attemptでは成功したresolutionを直ちにpinし、attempt途中で再解決しない。
redirect先は新しいpolicy URLとして必ず新規DNSを行う。HTTP retryでは元URLでも再解決し、
新しい全IP集合を改めて安全検証する。retry前の旧集合と新集合を混ぜない。

## 7. pinned transportとTLS

Phase 2B real transportは次の構成を正式候補とする。

```text
DNS client: dnspython
TCP: socket
TLS: ssl
HTTP/1.1 parser: h11
```

`requests`は既存収集処理のため維持するが、Phase 2B real transportには使用しない。
urllib3、requests、httpxの内部DNS、proxy、adapter、private socket取得へ依存しない。

transportはA response順、次にAAAA response順で固定されたverified IP集合と元のASCII hostnameを
受け取る。Happy Eyeballs、IPv6優先、OSアドレス選択policy、transport側の並べ替えはPhase 2B初版で
使用しない。将来必要になった場合は別設計とする。

1. socketを数値IPへ直接接続し、hostnameをOSで再解決しない
2. 1 logical request reservationにつき先頭から最大4 IPまで接続を試せる
3. TCP拒否・到達不能・connect timeoutだけはGET送信前に次IPへ進める
4. 後述のPhase 2B専用契約でTLS client contextを明示生成する
5. SNIと証明書hostname検証には元hostnameを使用する
6. Host headerにも元hostnameを使用する
7. TLS直後、GET送信前にpeer IPを取得してPhase 2A policyとpinned setで再検証する
8. 検証成功後だけ固定GETを送信する

system・environment proxy、hostname再解決、TLS downgrade、HTTP fallback、`verify=False`、
custom CA、client certificate、insecure modeを禁止する。TLS version、cipher、OS trust storeは
Python/OpenSSLの安全な標準既定へ従い、弱い値へ上書きしない。

Phase 2B production経路では、環境変数`SSLKEYLOGFILE`からTLS session keyの保存先を取り込む
`ssl.create_default_context()`を使用しない。環境変数の一時削除、変更、復元による回避も、
process global state、thread競合、復元失敗、他処理への副作用があるため禁止する。公開APIだけを使い、
次の手順でcontextを生成する。

```python
context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
context.minimum_version = ssl.TLSVersion.TLSv1_2
context.verify_flags |= (
    ssl.VERIFY_X509_STRICT
    | ssl.VERIFY_X509_PARTIAL_CHAIN
)
context.load_default_certs(ssl.Purpose.SERVER_AUTH)
```

通信開始前に`verify_mode == ssl.CERT_REQUIRED`、`check_hostname is True`、minimum TLS versionが
1.2以上、`keylog_filename is None`、security levelが2以上、system CAが読み込まれていることを
fail closedで検証する。Windowsでは`load_default_certs(ssl.Purpose.SERVER_AUTH)`によって`CA`と
`ROOT`のsystem storeを利用する。Python 3.10～3.12と3.13の既定差をなくすため、
`VERIFY_X509_STRICT`、`VERIFY_X509_PARTIAL_CHAIN`、minimum TLS 1.2を全対応versionで明示する。
暗号suite、TLS option、verify modeを弱める上書きを行わない。

`SSLKEYLOGFILE`を読まない。`keylog_filename`を設定せず、`load_cert_chain()`とproduction経路の
任意`load_verify_locations()`を呼ばない。custom CA、client certificate、weak cipher、TLS downgrade、
`verify=False`を許可する引数やfallbackを設けない。数値IPへ接続しても、DNS名URLでは検証済みの
original A-label hostnameを`server_hostname`としてSNIとhostname verificationへ渡し、IPへ置換しない。

certificate failure、hostname mismatch、peer mismatchはsecurity failureとして直ちに終了コード4
とし、同じreservation内の次IPやretryへ進まない。TCP/TLS connect timeoutは一時dependency
failureとしてfailoverまたはretry候補にできる。

## 8. HTTP request、redirect、response

methodはGETだけとし、request bodyを持たない。Host相当以外のheaderは次の4件へ固定する。

```text
Accept: text/html, application/xhtml+xml
Accept-Encoding: identity
User-Agent: polymarket-ai-lab-safe-fetch/1.0 (+https://github.com/takemototosou-lab/polymarket-ai-lab)
Connection: close
```

Authorization、Cookie、Referer、Origin、API key、bearer token、custom headerを禁止する。
redirectは301、302、303、307、308だけを受理し、最大3 hopとする。各hopでURL parse、HTTPS、
port、userinfo、DNS全結果、pinning、TLS、peer IPを再検証し、loopとHTTP downgradeを拒否する。
redirect auto-follow、cookie・auth・refererの持越しは禁止する。

Phase 2Aのresponse検証を実streamへ適用する。処理順は次へ固定する。

```text
raw response-head guard
→ h11
→ semantic validator
```

raw guardが成功するまでh11へ1 byteも渡さない。response headはstatus line先頭から最初の
`\r\n\r\n`までとし、同一recvにbody bytesが含まれる場合はその位置で分離する。body bytesを
header検証へ含めず、検証済みraw headも再構築せず受信したbyte列のままh11へ渡す。

### 8.1 Raw response-head guard

raw headの上限を次に固定する。

| 対象 | 既定 | 絶対上限 |
| --- | ---: | ---: |
| status line先頭から終端CRLF CRLFまでの総byte数 | 32 KiB | 64 KiB |
| raw header field数 | 64 | 100 |
| 1 fieldのraw name+value byte数 | 4 KiB | 8 KiB |

各上限はexact boundaryを許可し、`+1`で終了コード7、retry禁止とする。絶対上限を超える設定は
clampせず通信前に拒否する。head未完了時の1回のrecvは残りhead予算`+1`以下とする。
name+valueはcolonとCRLFを除くraw byte数、field countはraw field-line数として数える。

raw bytesをoctet列として解析し、status lineは先頭の1本だけ、line終端はCRLFだけとする。
bare LF、bare CR、NUL、CTL、obs-fold、不正なASCII tokenのfield-nameを拒否する。header名は
ASCII case-insensitiveで比較するが、raw field順と出現回数はpair列のまま保持し、dict化しない。

framing関連fieldはh11へ渡す前に次を検証する。

- `Content-Length`は1 fieldだけ許可し、2回以上は同値でも拒否する
- 単一`Content-Length` field内のcomma listを拒否し、値はASCII decimal digitsだけ許可する
- `Transfer-Encoding`は1 field・1 tokenの`chunked`だけをASCII case-insensitiveで許可する
- `Transfer-Encoding`のcomma list、parameter、duplicate field、未知codingを拒否する
- `Content-Encoding`のraw出現数を保持し、複数fieldを拒否する
- `Content-Length`と`Transfer-Encoding`の併存は即時に終了コード7とし、h11へ渡さない

h11 event生成後もstatus、MIME、charset、Content-Encoding、framing、response body rulesを
semantic validatorで再検証する。raw guardはh11の代替ではなく、h11が正規化・破棄する前の情報を
検証する純粋なHTTP parser層内部処理である。

### 8.2 Chunked framing guard

Phase 2B初版ではchunk extensionとtrailerをすべて禁止する。許可するchunk-size lineは
`1*HEXDIG CRLF`だけとし、semicolon、whitespace、sign、`0x` prefix、empty size、non-hexを拒否する。
chunk-size lineの絶対上限は終端CRLFを含む128 bytesとする。128 bytesは許可し、129 bytesは
終了コード7、retry禁止とする。宣言chunk sizeが残body予算を超える場合は、payload到着を待たず
同じ扱いで拒否する。

incremental framing guardは次の状態だけを持つ純粋処理とする。

```text
chunk-size
chunk-data
chunk-data-CRLF
zero-chunk
final-CRLF
complete
```

size lineをraw guardで検証してから、そのraw bytesをh11へ渡す。chunk payloadは再構築せずstreaming
し、zero chunk後はempty trailerを表す直後のCRLFだけを許可する。防御的な再検証として、h11の
`EndOfMessage.headers`もemptyでなければ終了コード7、retry禁止とする。

body上限はchunk framingを除いたh11 `Data.data`累計へ適用し、既定2 MiB、絶対4 MiBとする。
exact boundaryは許可し、`+1`でsocketを閉じ、partial resultを採用せず、bodyを保存せず、retryせず、
終了コード7とする。response headはheader hard limit、chunk-size lineは128-byte hard limit、
decoded payloadはbody hard limitで制限し、各recvは現在状態の残予算`+1`以下とする。run total deadlineを
最上位に維持し、新しい巨大なraw-wire予算は追加しない。

### 8.3 Semantic response contract

- 最終本文候補はstatus 200だけ
- Content-Encodingは欠落またはidentityだけ。gzip、deflate、brを拒否
- MIMEは`text/html`と`application/xhtml+xml`だけ
- charset未指定時はUTF-8、BOMと宣言の不一致、unknown・multiple charsetを拒否
- strict decodeし、NUL、surrogate、禁止C0/C1を拒否する
- decoded HTMLは20,000 Unicode code pointを上限とし、切り詰め・部分結果を禁止する

Phase 2Bはsource extractionを行わない。将来の抽出済みsourceに対する既定12,000文字上限は
別工程の契約であり、Phase 2B transportの20,000文字上限とは分離する。

## 9. retry、IP failover、request accounting

retry候補はtemporary DNS failure、connect/TLS timeout、read timeout、HTTP 429、502、503、504
だけとする。既定1 retry、絶対2 retriesとする。URL/IP/peer/TLS certificate安全性、MIME、
framing、size、decode、400、401、403、404、lock、contract、confirmationはretryしない。

`Retry-After`は正確な0～30秒だけを採用する。30秒超、負数、不正形式、deadline超過はclampせず
retryを開始しない。retryごとにDNSを再解決し、request reservationも新規消費する。

`request_count`は次の累計として定義する。

> socket接続前に消費するlogical HTTP GET request reservation

DNS成功後、最初のsocket接続前に1件を予約し、JSONLの`request_reserved`をflush・fsyncする。
全IPへのconnectが失敗してGETを送信できなくてもreservationは消費済みとする。同一reservation
内の最大4 IP connect failoverでは増加させない。GET送信開始時にも二重加算しない。
redirectとHTTP retryは別reservationとする。

最大HTTP request reservationは既定8、絶対12とする。これは初回とredirect最大3 hopの各論理
requestをretryする最悪ケースを包含する。利用者設定の自動増加、retryによる増枠、上限到達後の
socket接続を禁止する。TCP connect attempt数はrequest countと分離し、1 reservationにつき最大4、
絶対上限も4とする。

## 10. Phase 2B JSONL log policy

Phase 2A serializer、policy、許可値、既存byte列は変更しない。Phase 2Bは同じ21キー順を使う
別policyとして実装する。

固定値:

```text
schema_version = "1.0"
phase = "2b"
provider = null
query_kind = null
cost_limit_usd = 0
```

使用eventとstatus:

| event_type | status |
| --- | --- |
| run_started | started |
| request_reserved | started |
| fetch_finished | succeeded / failed |
| retry_scheduled | retry_scheduled |
| run_finished | succeeded / failed |
| log_error | failed |

statusは`started`、`succeeded`、`failed`、`retry_scheduled`の4値だけとする。Phase 2Aの
error code列挙を変更せず、Phase 2B policyだけに利用者中断用`cancelled`を追加する。
failedとretry_scheduledはPhase 2Bで許可されたerror code必須、startedとsucceededは禁止とする。

既存21キー以外を追加せず、初版ではURL digestも保存しない。URL、hostname、path、query、HTML、
response body、raw header、title、snippet、certificate、DNS raw response、例外原文、stack trace、
API key、credential、絶対filesystem pathを保存しない。

全eventは単一write、短いwrite検出、flush、fsyncを行う。初期log作成・`run_started`・
`request_reserved`に失敗した場合は次の通信を開始しない。writer失敗はstickyとし、再追記しない。
外部通信成功後にlogが失敗しても同じ通信を繰り返さず、本文やpartial resultを保存しない。

## 11. runtime directoryとfixed-scope lock

Phase 2B専用CLIは次の引数を必須とする。

```text
--runtime-dir <absolute-path>
```

既定値と環境変数fallbackを持たない。相対path、存在しないpath、通常directoryでないpath、
repository root、repository配下、`data/`配下を拒否する。既存directoryをstrictに実体解決し、
symlink・Windows junction等の解決後にrepository配下となる場合も拒否する。CLIはdirectoryを
自動作成しない。Phase 2BのlockとJSONL log以外を置かない。

環境変数`LOCALAPPDATA`を含むenvironment値をruntime directory決定に使用しない。利用者が
Windowsで推奨場所を使用する場合は、次のdirectoryを事前に作成して明示引数で渡す。

```text
%LOCALAPPDATA%\polymarket-ai-lab\runtime\phase2b\
```

これは利用者向けpath例であり、CLI自身は`LOCALAPPDATA`を展開・参照しない。

lock filenameとmetadataのscopeを次に固定する。

```text
.external_analysis_phase2b.lock
target_suffix = "phase2b"
```

metadataの固定4キーは`lock_version`、`run_id`、`started_at`、`target_suffix`とする。
Phase 2Aの日付suffix lockとvalidationは変更しない。Phase 2B用lock policyはfixed suffixを別に
検証する。同一runtime directoryではPhase 2B real fetchを常に1 runだけ許可する。

lockは排他的新規作成し、存在時は通信前に終了コード5とする。正常終了、既知失敗、Ctrl+Cでは
自分のrun IDとmetadataが一致するlockだけreleaseする。時刻だけでstale判定せず、自動削除・
自動回復しない。crash、破損、所有確認不能、cleanup失敗ではlockを残してfail closedとし、
利用者がprocess不在と内容を確認して手動削除する。

## 12. stdout、終了コード、Ctrl+C

confirmation画面以外のstdoutにはURL全文、hostname、path、query、HTML本文、header、証明書、
例外原文を表示しない。最終表示はsuccess/failure、HTTP status、response byte数、decoded char数、
redirect hop数、request count、retry count、`retrieved_at`だけに限定する。stderrも固定分類と
非秘匿countだけとし、通常失敗でtracebackを表示しない。

終了コード:

```text
1: CLI引数、runtime directory、limit等の設定不正
3: FETCH不一致、EOF、非対話stdin、confirmation取消
4: URL、DNS/IP、redirect、TLS certificate、hostname、peer IP安全性拒否
5: lock競合
6: retry後も継続する一時DNS・network・HTTP dependency失敗
7: response framing、header、size、decode、JSONL log契約不正
8: request reservationまたはrun total deadline安全ゲート拒否
9: Phase 2Bでは到達不能
10: MIMEまたはContent-Encoding拒否
130: Ctrl+C
```

confirmation中のCtrl+Cは通信なしで130を返す。lock取得後のCtrl+Cは、新規通信を停止し、
log writerが健全ならPhase 2B専用`cancelled`で`run_finished`を記録してから所有確認付きreleaseを
試みる。writerが失敗済みなら再writeしない。release確認に失敗してもlockを強制削除せず、
bodyやpartial resultを保存せず130を返す。

## 13. experimental CLIとテスト境界

将来のPhase 2B専用experimental CLI候補名は`run_phase2b_fetch.py`とする。このCLIだけがURL入力、
`--runtime-dir`、preflight、interactive confirmation、Phase 2B lock/log、終了コード変換を担当する。
既存`run_external_analysis.py`へ接続せず、Phase 3までproduction analysis pipelineへ接続しない。

通常unit/integration testはfake DNS、fake socket、fake TLS、fake HTTP byte stream、fake clock、
fake sleep、fake filesystemを注入し、real DNS・socket・HTTPが0回であることを検証する。
GitHub Actionsと通常test suiteでreal network testを禁止する。

raw response-head guardはsingle Content-Length、同値・異値duplicate Content-Length、CLとTEの併存、
duplicate TE、obs-fold、bare LF、bare CR、field count・single field・total headの超過とexact boundaryを
bytes fixtureで検証する。chunk guardはnormal chunked、extension、non-empty・empty trailer、
128-byte境界、malformed hex、body limit exact・`+1`をbytes fixtureで検証する。

TLS keylog testはprocess isolationを必須とする。subprocessだけに`SSLKEYLOGFILE=<temporary path>`を
設定してproduction SSLContext factoryを生成し、`keylog_filename is None`を確認する。その後、
test fixture CAと`ssl.MemoryBIO`・`wrap_bio()`によるclient/server handshakeをsocket、DNS、HTTPなしで
完了させ、keylog fileが新規作成されないこと、および既存fileなら内容が不変であることを確認する。
同じ契約をPython 3.10、3.11、3.12、3.13で検証対象とする。fixture CAの利用はtest process内だけに
限定し、productionのcustom CA入力経路を作る根拠にしない。

実通信smoke testは次をすべて満たした後の別作業とする。

1. Phase 2B全実装PRがレビュー承認されmainへ統合済み
2. 利用者がその場でHTTPS URLを1件指定
3. exact `FETCH`
4. request、retry、redirect、byte、timeout上限を事前表示
5. APIキー・paid APIなし
6. body・raw response保存なし

smoke testを自動化、CI化、定期実行してはならない。

## 14. 依存・実装開始ゲート

Phase 2B実装候補のdirect dependencyとversion rangeを次に固定する。

```text
dnspython>=2.8,<2.9
h11>=0.16,<0.17
```

本仕様のdependency/security/interface reviewでは、license、Python 3.10～3.13、Windows 11、
DNS・socket・TLS・h11 interface、fake backend注入、raw response-head、chunk extension・trailer、
`SSLKEYLOGFILE`非参照契約を確認し、実装前のremaining security/interface issuesは0件となった。
ただし、現在環境に存在する、または別packageのtransitive dependencyであることを理由に暗黙利用しない。
requirementsは本仕様更新時には変更せず、利用者が次段階で依存追加を明示承認した後だけ追加する。

レビュー済みの依存契約を次に固定する。

- `dnspython`はbase packageのみ、extrasなし、public APIだけを使用する
- `try_ddr()`を禁止し、通信前にDo53Nameserverだけであることを確認する
- DNSはA、AAAAの順に問い合わせ、各raw response順を維持し、retry時は両方再取得する
- `h11`はHTTP/1.1 Sans-I/O parser/serializerだけに使用し、TLS・socket責務を持たせない
- `h11` private APIを使用せず、raw response-head guard成功後だけbytesを投入する
- `DnsQueryBackend`、`TlsConnector`、`TlsByteStream`、`Clock`、`Sleeper`、`FileStore`の最小境界を維持する
- raw response-head guardとchunk framing guardはHTTP parser層内部の純粋処理とし、抽象を増やさない

依存・interfaceレビューの完了はrequirements変更やPython実装の承認ではない。次段階で利用者が
依存追加とPhase 2B PR 1実装を明示承認するまで開始しない。依存追加承認は実通信承認でもなく、
実装承認もsmoke test承認ではない。各ゲートを持ち越さず、Phase 2CとPhase 3は未着手のままとする。
