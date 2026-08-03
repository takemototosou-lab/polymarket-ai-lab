# 外部分析 Phase 2 検索・安全取得基盤 設計仕様

## 1. 目的と最重要安全原則

本仕様は、外部分析工程Phase 2の検索候補取得、安全なURL本文取得、SSRF防御、
retry、lock、JSONL logの契約を固定する。検索候補URLは未信頼入力であり、検索結果を
そのまま取得してはならない。URL policy、DNS全結果、実接続先IP、redirect各hop、
response契約を順に検証した場合だけ本文候補として扱う。

本仕様の承認だけでは外部通信を許可しない。次を全Phase 2の安全原則とする。

- 最初の実装はfake/mock provider、fake resolver、fake transportだけを使う
- APIキーが環境に存在しても読み取らず、存在確認もしない
- Brave Search APIとURL本文取得の実通信には、それぞれ利用者の別途明示承認が必要
- 課金、無料枠、trial、creditの存在を前提にしない
- 契約、プラン、請求、支払い方法、自動継続課金を変更しない
- 市場数、query数、request数、取得数、retry数、予算を自動的に増やさない
- Phase 2ではOpenAIを呼ばず、AI推論、Structured Outputs、completed/error生成を行わない
- `analysis_result_*.json` と既存data成果物を変更しない
- 明示承認、段階設定、件数上限、予算上限のいずれかが不足すればfail closedとする

APIキーの存在、provider名の設定、無料枠の表示、過去の承認を、現在の実通信承認と
みなさない。

## 2. 正本・責務境界・優先順位

外部分析全体の段階とPhase 1契約は
[`2026-07-31-external-ai-analysis-pipeline-design.md`](./2026-07-31-external-ai-analysis-pipeline-design.md)、
分析結果JSONは
[`2026-07-31-ai-analysis-completion-contract-design.md`](./2026-07-31-ai-analysis-completion-contract-design.md)
を正本とする。本仕様はPhase 2の検索、取得、安全性、運用基盤の詳細正本である。

既存外部AI設計のPhase 2概略と本仕様が矛盾する場合は本仕様の狭い契約を優先する。
特に本仕様はHTTPSだけ、port 443だけ、HTML/XHTMLだけ、圧縮なしを許可する。
推測で旧許可範囲と結合せず、矛盾が解消できなければ実装を停止する。

対象:

- URL parser・policy、IP判定、DNS pinning、redirect検証
- HTML/XHTML responseの上限制御とdecode前検証
- immutableな検索候補と取得結果の内部契約
- fake SearchProvider、fake DNS resolver、fake HTTP transportの境界
- retry、lock、JSONL log、CLI分類、課金・実通信ゲート

対象外:

- OpenAI provider、AI推論、Structured Outputs、正式source評価
- HTML本文抽出アルゴリズム、completed/error構築、analysis result更新
- 売買、wallet、注文、scheduler、GUI、browser、認証付き取得
- Brave providerと実HTTP transportの実装、APIキーの実読込

## 3. Phase 2A・2B・2Cの分割

### 3.1 Phase 2A: 完全オフライン基盤

本仕様承認後に別PRで実装計画を作成できる範囲は次だけとする。

- URL parser・policy validator、IP range validator
- fake DNS resolver、fake redirect chain、fake HTTP transport
- fake SearchProvider、`SourceCandidate`、取得response内部構造
- retry policy、lock、JSONL serializer
- clock、sleep、filesystem、transportを注入したmock/fake test

外部通信とAPIキー読込は禁止する。通常経路からsocket、system resolver、HTTP client、
Brave SDKへ到達できてはならない。Phase 1 dry-runの動作を変更しない。

### 3.2 Phase 2B: 無課金の手動URL取得試験

今回実装しない。別設計・別PRと利用者の実行直前明示承認を必要とする。対象は利用者が
その場で指定した公開HTTPS URL 1件だけとし、Brave、APIキー、analysis result更新、
本文保存、本文stdout出力を禁止する。safe fetcherのDNS、peer IP、redirect、MIME、
byte、timeout検査を小さい上限で検証する。

承認は指定URLと1回の試験に限定し、将来実行や別URLへ持ち越さない。
preflightでhost、最大redirect、byte、timeout、最大HTTP request数を表示した後、対話CLIで
`FETCH 1 <hostname>`の完全一致確認を要求する。非対話実行、CLI flagだけ、環境変数だけでは
承認成立とせず拒否する。

### 3.3 Phase 2C: Brave候補検索試験

今回実装しない。別設計・別PR承認後、実行直前にBraveの公式料金、無料枠、規約、
request課金条件を確認し、利用者が1市場、query数、retry込み最大request数、最大予算を
明示承認した場合だけ開始候補とする。検索結果は未検証候補の発見にだけ使い、本文取得は
別のsafe fetcherを通す。OpenAIとanalysis result更新を禁止する。
preflightで市場ID、query数、retry込み最大request数、公式単価、予約最大費用を表示し、
対話CLIで`BRAVE 1 <max_requests> <max_cost_usd>`の完全一致確認を要求する。確認文字列は
logや環境変数へ永続保存せず、当該processだけに有効とする。

## 4. URL受理・正規化契約

入力URLは長さ2,048 UTF-8 bytes以下の単一Unicode文字列とする。受理条件はすべてAND:

- absolute URL
- schemeはASCII大小文字正規化後に厳密な`https`
- hostname必須、ASCIIまたはUTS #46非transitional IDNAでASCII化可能
- pathとqueryは許可し、fragmentは送信前に除去
- port省略時443、明示時も443だけ
- URL parserがauthority、hostname、port、path、queryを一意に返す

拒否:

- `http`、`file`、`ftp`、`data`、`javascript`、`mailto`、`ws`、`wss`
- scheme-relative、relative、userinfo、username、password、空hostname
- port 443以外、不正percent escape、NUL、C0/C1 control、CR、LF、tab
- backslashをauthority区切りと解釈し得る表記
- hostname末尾dot、連続dot、空label、label 63 bytes超、host 253 bytes超
- 全角・互換文字等を正規化するとauthorityの意味が変わる曖昧表記
- parser間でhost、port、pathの解釈が異なるmixed/ambiguous encoding
- 入力またはIDNA・fragment除去後のASCII URLが2,048 bytes超

IP literalは構文だけで拒否せず5節のglobal-unicast検証へ渡す。保存用のcanonical URL
とは別に、接続policy用URLを持つ。検証前にpercent decode、path collapse、query sort等で
意味を変えない。relative `Location` の解決だけはRFC準拠parserで行い、解決結果を新規
入力として最初から検証する。

## 5. SSRF・DNS・接続先検証

接続先に許可するのはIPv4・IPv6のglobal unicastだけとする。標準ライブラリの
`is_global`だけに依存せず、少なくとも次を明示拒否する。

- loopback、private、link-local、multicast、unspecified、reserved
- documentation、benchmark、carrier-grade NAT、broadcast相当
- IPv6 unique local、IPv4-mapped IPv6でmapped先が拒否対象のもの
- metadata serviceとして知られるlink-local・host
- OSまたは言語の判定でglobalでないもの

DNS契約:

- A/AAAAとCNAME/aliasの最終結果を両familyとも検査する
- 解決IPは重複排除後最大16件、CNAME chainは最大8段、空結果は拒否
- DNS total timeoutは5秒、絶対上限10秒
- 1件でも拒否IP、構文不正IP、上限超過を含めばURL全体を拒否
- 許可IPだけを選び直して接続しない

DNS rebinding対策:

1. resolverは検証済みIP集合をimmutableな接続計画として返す。
2. transportへ元hostname、port 443、検証済みIPを渡し、OSの通常再解決を禁止する。
3. TLS SNI、Host header、証明書検証は元のASCII hostnameを使う。
4. 接続後に実peer IPを取得し、global判定を再実行する。
5. peer IPが検証済み集合に完全一致しなければresponse body読込前に切断・拒否する。

peer IPを取得・照合できないtransportを実通信に使用しない。proxyはこの一致を壊すため
system proxyとproxy環境変数を使わない。

## 6. Redirect契約

- 受理statusは301、302、303、307、308だけ
- redirectは初回response後に最大3回、4回目を拒否
- `Location`欠落・複数の矛盾するLocation・不正値を拒否
- relative Locationは現在URLをbaseに解決してから4・5節を再適用
- 各hopでURL、scheme、port、DNS全結果、peer IPを再検証
- HTTP downgrade、userinfo追加、port変更、同一policy URL再訪、loopを拒否
- Cookie、Authorization、APIキーheader、Refererを送信・継承しない
- redirect response bodyは本文候補として読まず、上限付きで破棄または接続を閉じる

結果metadataは入力URL、各hopのstatusとpolicy URL、最終URLを固定順配列で保持できる。
queryはlogへ保存せず、redirect chainの永続ログにはhostとpathを含むURL全文を残さない。

## 7. HTTP request契約

methodはGETだけ。POST、PUT、PATCH、DELETE、HEADの自動事前実行、request body、Cookie、
authentication、client certificate、custom user header、system proxyを禁止する。

固定header:

```text
Accept: text/html, application/xhtml+xml
Accept-Encoding: identity
User-Agent: polymarket-ai-lab-safe-fetch/1.0 (+https://github.com/takemototosou-lab/polymarket-ai-lab)
Connection: close
```

HostとTLS SNI以外をredirect先へ動的追加しない。`Content-Encoding`は未指定または
`identity`だけを受理し、gzip、deflate、br、複数encoding、unknown encodingを拒否する。
これによりPhase 2初期版は圧縮bombを展開しない。

Phase 2B/2Cでpublisher本文を取得する前には、既存外部AI正本のrobots契約も適用する。
robots取得自体を同じURL・SSRF・redirect・response上限で検証し、HTTP request数と全体
timeoutへ算入する。robotsの実通信もPhase 2B/2Cの明示承認範囲外では行わない。

## 8. Response hard max・status契約

設定値と絶対上限を次に固定する。設定は絶対上限以下へ狭められるが、clampせず、
上限超過値を処理開始前に拒否する。

| 設定・項目 | 既定値 | 絶対上限 |
| --- | ---: | ---: |
| `POLYMARKET_FETCH_TIMEOUT_SECONDS` | 15秒 | 60秒 |
| connect timeout | 5秒 | 15秒 |
| read inactivity timeout | 10秒 | 30秒 |
| DNS timeout | 5秒 | 10秒 |
| `POLYMARKET_MAX_RESPONSE_BYTES` | 2,097,152 | 4,194,304 |
| `POLYMARKET_MAX_SOURCE_CHARS` | 12,000 | 20,000 |
| `POLYMARKET_MAX_TOTAL_SOURCE_CHARS` | 60,000 | 80,000 |
| `POLYMARKET_MAX_FETCHES_PER_MARKET` | 8 | 12 |
| redirect回数 | 3 | 3 |
| response header総量 | 32,768 bytes | 65,536 bytes |
| header数 | 64 | 100 |
| 1 header name+value | 4,096 bytes | 8,192 bytes |

`POLYMARKET_FETCH_TIMEOUT_SECONDS`はDNSを除く1 URL/hopのconnectからbody完了までの
total deadlineであり、connect/read個別上限を含む。市場全体は既存
`POLYMARKET_RUN_TIMEOUT_SECONDS`の既定1,200秒・絶対上限3,600秒にも従う。
fetch件数は新しい候補URLの取得開始数であり、redirect hopとretryを件数増枠には使わない。
一方、実HTTP request数は初回、robots、redirect各hop、retryをすべて個別に数え、実行前の
request hard maxと予算予約へ算入する。

本文候補として受理するstatusは200だけ。redirectは6節、429・502・503・504は13節の
retry候補、その他1xx/2xx/3xx/4xx/5xxは本文不採用とする。204等bodyなしも不採用。

- Content-Lengthが非負10進整数でない、複数値が不一致、またはbyte上限超過なら読込前拒否
- Content-Length欠落・chunkedもstream累計を監視し、上限+1 byteを読んだ時点で停止
- header総量、数、1 header長をbody読込前に検査
- transfer framingの曖昧性、複数Content-Length、Content-Lengthとchunked併用を拒否
- body、抽出本文、response生headerをdisk、stdout、JSONL logへ保存しない

## 9. MIME・文字コード契約

受理MIMEはparameter除去・ASCII小文字化後の`text/html`と
`application/xhtml+xml`だけとする。header `Content-Type`欠落、unknown、PDF、JSON、
XML単体、text/plain、image、video、audio、zip、gzip、binary、executable、
`application/octet-stream`を拒否する。`X-Content-Type-Options: nosniff`の有無で緩和しない。

decode前にNUL比率とbinary octetを検査し、NULを1 byteでも含むbodyを拒否する。
charsetはContent-Typeの単一有効指定を優先し、未指定時はUTF-8、UTF-8 BOM付きbodyは
BOMを署名として除去してUTF-8 strict decodeする。複数・unknown charset、宣言とBOMの
矛盾、decode error、surrogate、NUL、許可しないC0/C1 controlを拒否する。LF、CR、tabは
HTML入力として許可するが、後続抽出時に固定規則で扱う。

decode後は1 source 20,000文字の絶対上限を超えた時点で不採用とし、Phase 2Aでは
勝手に切り詰めない。将来の抽出結果は既定12,000・市場合計60,000、絶対20,000・80,000
へ制限する。HTML本文抽出方式は別設計とし、本仕様では検証済みdecoded HTMLをmemory
内の取得結果へ渡すところまでとする。

## 10. immutable内部契約

### 10.1 `SourceCandidate`

検索provider出力は次の固定順immutable構造とする。

```text
source_id
query_kind
rank
url
title
snippet
publisher_hint
published_at_hint
```

- `source_id`: 実行内候補ID。正式source IDではなく、空白なし1～64文字
- `query_kind`: 固定列挙`official`、`status`、`support`、`counter`
- `rank`: 1始まり整数、query内1～10
- `url`: 1～2,048 UTF-8 bytes。safe fetch前の`unvalidated`値
- `title`: 前後空白除去後1～500文字
- `snippet`: 0～1,000文字。候補表示専用で根拠・AI入力に使用しない
- `publisher_hint`: nullまたは1～200文字
- `published_at_hint`: nullまたはproviderが返した1～100文字の未検証値

文字列のNUL、surrogate、CR/LFを含むすべてのC0/C1 controlを拒否する。API生response、APIキー、
provider固有blobを保持しない。title/snippet/query本文をJSONL logへ保存しない。
`source_id`はquery順・rank・候補順から決定的に生成する。

### 10.2 `SearchRequest` と `SearchProvider`

`SearchRequest`はquery kind、query本文、最大結果数、request ordinalだけを持つimmutable
構造とする。query本文はprocess memoryだけに保持し、logにはkindとSHA-256 digestだけを
残せる。digestは本文の復元や公開を許可するものではない。

```python
class SearchProvider(Protocol):
    def search(self, request: SearchRequest) -> list[SourceCandidate]:
        ...
```

Phase 2Aで許可するのは、時刻・乱数に依存せず固定候補を返すfake providerだけ。
network、APIキー、課金がなく、query順・rank・上限を検証できる。Braveの生JSONを
模倣せず、provider adapterと共通契約を分離する。Brave providerは設計上の将来境界に
留め、Phase 2Aでclass、SDK、HTTP call、APIキー環境変数読込を実装しない。

### 10.3 `ValidatedFetchResult`

本文抽出前の取得成功結果は次の固定順immutable構造とする。

```text
requested_url
final_url
redirect_chain
resolved_ips_by_hop
peer_ip_by_hop
status_code
content_type
charset
response_bytes
decoded_chars
retrieved_at
decoded_html
```

`decoded_html`はprocess memoryだけに保持し、log、stdout、結果JSONへ出さない。
IP metadataは接続検証用であり正式sourceへそのまま保存しない。失敗は部分resultを返さず、
固定error categoryと非秘匿metadataだけを返す。Phase 2Aのfake clockを除き、
`retrieved_at`は実transportがbody取得を完了したUTC時刻とする。

## 11. Brave Search安全ゲート

Brave実通信は次をすべてANDで満たす場合だけ候補となる。

1. Phase 2C実装の別PRが承認・main統合済み
2. 利用者が当該実行を明示承認し、3.3の対話確認が完全一致
3. `POLYMARKET_SEARCH_PROVIDER=brave`
4. dry-run falseを厳密に明示
5. 利用者がAPIキーを明示設定
6. 最大市場数を1に明示設定
7. 市場あたり最大query数を明示設定
8. retry込み最大request数を実行前表示
9. 正の有限な最大予算を明示設定
10. 最新の公式料金、無料枠、規約、課金単位を実行直前に再確認
11. log、lock、retry、request hard max、予算hard maxが有効
12. OpenAIとanalysis result更新が到達不能

1つでも不足、料金計算不能、予算超過可能性、APIキー存在だけ、過去承認だけの場合は
APIキーを読む前に通信を拒否する。CLI flagや環境変数だけで利用者承認を捏造しない。
契約、料金プラン、支払い設定をコードから変更しない。

## 12. Request・費用上限

Phase 2Cの初回は1市場だけ。既定4 query、絶対6 query、各既定5・絶対10候補とする。
Brave request上限は次で事前計算する。

```text
最大request数 = 市場数 × query数 × (1 + 最大retry回数)
```

検索0件も1 requestへ算入する。retryをrequest・予算へ必ず含め、実回数が予約上限へ
達した時点で新規callを拒否する。単価不明、通貨換算が必要だが未設定、最大予算未設定、
予約費用が予算を超える場合は通信前に終了コード8で拒否する。無料枠・creditを予約費用から
差し引かない。URL fetchにもprovider課金が将来発生する場合は同じ方式で別上限を設ける。

## 13. Retry契約

retry候補はconnect timeout、read timeout、一時的DNS failure、HTTP 429、502、503、504
だけ。URL policy、SSRF、peer IP不一致、MIME、size/header上限、decode、400、401、403、
404、APIキー不正、設定不正、予算拒否、lock競合、contract不正をretryしない。

- 既定は初回+retry 1回、設定の絶対上限は初回+retry 2回
- exponential backoffは`min(30, base_seconds * 2^retry_index)`
- jitterはproductionでfull jitter `[0, delay]`、Phase 2Aでは固定乱数とfake sleepを注入
- `Retry-After`は有効な0～30秒だけを採用し、超過値を30秒へclampせずretryを中止
- run total timeout、request上限、予算上限を超えるretryは開始しない
- retryは市場・query・fetch上限を増やさず、request/costへ算入する
- 同一request内容とrequest ordinalを維持し、自動無限retryを禁止する

## 14. Lock契約

Phase 2A/2Cの同じdata directory・同suffix処理は
`.external_analysis_<suffix>.lock`を同一directoryへ排他的新規作成して二重実行を防ぐ。
Phase 1 dry-runにはlockを追加しない。

lockはUTF-8 BOMなし・LF・末尾LFの固定JSONで、`lock_version`、`run_id`、
`started_at`、`target_suffix`の4キーだけを持つ。APIキー、query、URL、PID、username、
hostnameを保存しない。PID/hostnameはcontainer・Windows間で生存確認が曖昧でprivacyも
増えるため採用しない。

- atomic createは`O_CREAT | O_EXCL`相当。存在時は終了コード5
- 正常・既知例外時は、自分のrun IDと一致するlockだけfinallyで削除
- start時刻超過だけでstaleとみなさず、自動削除しない
- 利用者がprocess不存在、一時成果物、log、result不変を確認して明示削除する
- Windowsで同一directoryの排他作成・close・削除をmock filesystemと実filesystemで検証
- crash後のlock残存はfail closedとし、別実行が勝手に回復しない

## 15. JSONL log契約

logは正式data成果物と分けた`data/logs/`に、1実行1ファイルで保存する。Phase 2Aでは
fake directoryだけを使い、Phase 1 dry-runへ追加しない。UTF-8 BOMなし、LF、末尾LF、
1行1object、duplicate keyなし、1行8,192 bytes、1ファイル4 MiBを絶対上限とする。

固定共通キー順:

```text
schema_version, run_id, sequence, timestamp, phase, event_type, market_id,
query_kind, provider, attempt, status, error_code, http_status,
response_byte_count, selected_count, fetched_count, duration_ms,
request_limit, cost_limit_usd, request_count, retry_count
```

`schema_version`は`"1.0"`、run IDとevent typeはstring、sequence・attempt・count・
duration・HTTP statusは非負integerまたはnull、`cost_limit_usd`は固定Decimal表現のJSON
numberまたはnull、status/error code等は固定列挙stringまたはnullとする。event typeは
`run_started`、`request_reserved`、`search_finished`、`fetch_finished`、
`retry_scheduled`、`run_finished`、`log_error`だけを初期許可値とする。

eventに該当しない値はnullとし、未知キーを禁止する。timestampはUTC固定形式、sequenceは
0始まり。market/query順を維持し、同じ確定eventデータから同じ1行bytesを生成する。

保存禁止:

- APIキー、Authorization、Cookie、secret、環境変数全体
- URL/query/title/snippet/publisher hintの全文
- response body、decoded HTML、Brave生response、AI prompt/response
- 例外原文、stack trace、個人情報、絶対path、username、hostname

単一process lock下で1 event全bytesを1回writeし、短いwriteを失敗扱いにする。flush方針を
event種類ごとに固定し、`run_started`、外部call前予約、外部call後結果、`run_finished`は
flush+fsyncする。log作成・追記・fsync失敗は監査不能として新規外部callを停止する。
外部API成功後にlogだけ失敗した場合も再callせず、analysis resultを更新せず、終了コード7で
停止する。成功済み課金を隠さずstderrへ件数だけを通知し、本文やsecretを出さない。

## 16. CLI・エラー分類

Phase 1の終了コード0～3を変更しない。Phase 2用は次に固定する。

```text
4: URL policy、DNS/IP、peer IP、redirectを含むURL安全性拒否
5: lock競合
6: retry後も継続する一時的DNS・network・HTTP dependency失敗
7: response framing、header、size、decode、log等の取得契約不正
8: request数・費用・予算安全ゲート拒否
9: search provider認証失敗
10: MIMEまたはContent-Encoding拒否
```

設定不正は1、段階未到達・明示承認不足は3を維持する。各内部errorは上記へ1対1変換し、
通常利用時にtracebackを表示しない。複数候補の1件だけが4/7/10の場合は候補不採用として
継続できるが、単一URL試験または全候補不採用時のCLI最終分類は実際に停止させた固定codeを
使う。Phase 2はanalysis resultの市場単位errorを生成しない。

## 17. Phase 2Aテスト計画

すべてfake/mockで実行し、socket、system DNS、HTTP、Brave、APIキー環境変数の参照を
監視して0回であることを確認する。

### 17.1 URL parser・SSRF

- HTTPS、IDNA、IPv4/IPv6 global literalの正常境界
- HTTP、userinfo、relative、port、control/NUL、不正percent、backslash、末尾dot拒否
- 127.0.0.1、0.0.0.0、RFC1918、169.254.169.254、CGNAT、multicast、documentation
- IPv6 loopback/link-local/ULA、IPv4-mapped IPv6、global IP
- 複数DNS結果の1件だけprivate、空・17件、CNAME超過、timeout
- 検証済み集合外peer IP、peer IP取得不能、通常再解決未使用

### 17.2 Redirect・request・response

- 正常1hop・3hop、4hop、HTTP downgrade、private redirect、loop、Location欠落、port変更
- GETと固定headerだけ、proxy/cookie/auth/body/HEADが0回
- Content-Length、stream、chunked、header総量・数・1件長の上限と上限+1
- connect/read/total timeout、200以外、gzip/deflate/br拒否
- HTML/XHTML、MIME欠落・PDF/JSON/text/binary、encoding不正、BOM矛盾、NUL

### 17.3 Provider・retry・lock・log

- fake候補、query/rank順、時刻・乱数非依存、title/snippet上限
- retry許可・禁止、0/1/2回、fake clock、Retry-After、request/予算算入
- lock取得、競合、手動stale、自己lockだけcleanup、crash、Windows互換
- log固定キー・LF・size、secret/body/query非出力、短いwrite、fsync・追記失敗
- API成功後log失敗で再call・結果更新なし

### 17.4 課金安全性・回帰

- APIキーが環境に存在しても読まず通信しない
- provider設定、dry-run false、承認flagの一部だけでは通信しない
- 予算未設定・request計算不能・上限超過を通信前拒否
- fake resolver/transport/provider以外が0回
- Phase 1の4設定、stdout、data不変、終了コード0～3が不変
- Python実装時も既存138テストを回帰実行する

## 18. 決定性・再現性・保存境界

Phase 2Aは同じfake入力、clock、resolver、transport、providerから同じ候補順、retry判断、
取得metadata、JSONL bytesを生成する。固定キー・配列順、UTF-8 BOMなし、LF、末尾LFを
保証する。random jitterは注入値で固定する。

実DNS、ページ、redirect、Brave順位、network timeout、料金は変化するためPhase 2B/Cの
完全再現を保証しない。再現に必要なrun ID、時刻、件数、provider、retry、status、byte数、
上限だけをlogへ残し、検索結果全文・本文・query・生responseは保存しない。この境界を
完全監査と誤認しない。

## 19. 実装開始ゲート

### 19.1 Phase 2A

- 本仕様がレビュー承認されmainへ統合済み
- Phase 1の138テストと安全性が維持される実装計画が承認済み
- fake/mock以外へ到達不能、APIキー未読込、analysis result不変をテストで証明する計画

満たした場合だけPhase 2Aのテスト先行実装を開始できる。

### 19.2 Phase 2B

- safe fetcher実装PRが承認・統合済み
- SSRF、DNS pinning、peer IP、redirect、上限、MIMEのmock test成功
- 利用者が1 URL、1回、byte/timeout上限を実行直前に明示承認

### 19.3 Phase 2C

- Brave adapter実装PRと最新規約・料金レビューが承認・統合済み
- 11・12・13・14・15節の全安全ゲートが有効
- 利用者が1市場、query、retry込みrequest、最大予算を実行直前に明示承認

どのゲートも下位段階の承認を上位段階へ持ち越さない。

## 20. 自己レビュー・完了条件

- Phase 2A/2B/2C、外部通信、APIキー、課金承認の境界が一意
- HTTPS/443、global unicast、全DNS結果、peer IP、各redirect hopをfail closedで検証
- HTML/XHTML、identity、byte/header/time/文字数hard maxが具体的
- SourceCandidate、SearchProvider、取得結果がimmutableでprovider固有値を漏らさない
- retryがrequest・予算を増枠せず、lockとlogがsecret・本文を保持しない
- Phase 1の0～3、dry-run、data/result不変を変更しない
- OpenAI、AI推論、正式結果、売買を設計範囲へ持ち込まない
- Phase 2A実装計画のmock/fakeテスト観点が具体的
- 未確定の仮置き語や、解釈が分かれる曖昧な要件を残さない

本設計PRではPython、test、依存、実データ、GitHub Actionsを変更せず、APIキーを読まず、
外部API・URLへ通信せず、課金・契約・plan・支払い設定を変更しない。レビュー承認後も
Phase 2Aの実装計画作成へ進めるだけで、Phase 2B/2Cの実通信は別承認まで禁止する。
