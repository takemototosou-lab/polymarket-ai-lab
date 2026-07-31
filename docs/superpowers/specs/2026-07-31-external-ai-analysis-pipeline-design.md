# 外部検索・AI分析パイプライン 設計仕様

## 1. 目的

本仕様は、`analysis_result_*.json` の `pending` 市場を、公開情報の検索、
本文取得、情報源検証、AI推論を経て `completed` または市場単位 `error`
へ遷移させる外部AI工程を固定する。

状態別JSON契約の正本は
[`2026-07-31-ai-analysis-completion-contract-design.md`](./2026-07-31-ai-analysis-completion-contract-design.md)
（以下「完了契約」）である。本仕様はそのキー、型、順序、値域、7エラー、
`SCHEMA_VERSION = "2.0"`、状態遷移、決定的シリアライズを変更しない。
矛盾時は完了契約を優先し、実装を停止する。

## 2. 責務境界

### 2.1 対象

- 最新の分析入力と対応結果を検証し、選択された `pending` を読む
- 決定的テンプレートから検索クエリを生成する
- 外部検索で本文取得候補を得る
- 公開HTTP(S) URLまたは公開APIから内容を安全に取得する
- 情報源を正規化、重複排除、分類、時点・独立性・関連性検証する
- 境界付きのAI入力を構築し、構造化推論を1市場ずつ実行する
- AI生出力を構文・意味検証し、コードで正式結果を構築する
- 失敗を市場単位7エラーまたはファイル全体失敗へ分類する
- 秘匿情報を除いた実行ログを残す
- 全市場の確定結果を一時ファイルから原子的に置換する

### 2.2 対象外

注文、数量・注文価格決定、ウォレット接続、入出金、秘密鍵、認証付き
Polymarket操作、自動売買、利益保証、投資助言の確定、GUI、定期実行、
スケジューラ、本番クラウド配備は行わない。分析結果は参考情報であり、
売買執行とは別工程・別権限とする。

## 3. 外部サービスの採用方針

### 3.1 検索方式

初期実装は **Brave Search API Web Searchを候補発見だけに使用し、リンク先を
本プロジェクトの安全なHTTP取得器で取得する方式**を採用する。

| 方式 | 長所 | 制約 | 初期判断 |
| --- | --- | --- | --- |
| モデル内蔵Web検索 | 1プロバイダで簡単 | 検索・推論が密結合し、本文検証と正確な上限管理が難しい | 不採用 |
| 独立検索APIだけ | URL候補を制御可能 | スニペットだけでは正式情報源にならない | 単独では不採用 |
| 独立検索API＋安全な本文取得 | 検索、取得、検証、AIを分離できる | SSRF、robots、本文抽出の実装が必要 | 採用 |
| 利用者URLだけ | 低費用で決定的 | 公式情報・反対材料を網羅できない | 将来の補助入力 |
| 複数検索API | 障害耐性・網羅性 | 費用、重複、規約、テスト量が増える | 初期対象外 |

Brave Search APIはAPIキーを要する。2026-07-31確認時点の公式料金はWeb検索
1,000リクエスト当たり5米ドル、毎月5米ドル分のクレジット、容量50 req/s
である。ただし料金・無料枠・上限は変更され得るため、実装時と運用開始前に
再確認する。[Brave Search API pricing](https://api-dashboard.search.brave.com/documentation/pricing)

API結果のURL、タイトル、説明、検索順位は候補メタデータであり、検索
スニペットを正式な `sources` やAI根拠にしない。検索結果は処理中だけ保持し、
AIへ渡さず、検索結果データベースを作らない。Braveの規約は検索結果の
永続的保存・再配布等を制限するため、実装時に利用目的と最新規約を再確認
する。[Brave Search API Terms](https://api-dashboard.search.brave.com/documentation/resources/terms-of-service)

検索プロバイダは `SearchProvider` 境界で差し替え可能にする。入力は正規化
済みクエリと上限、出力は候補のURL、タイトル、説明、順位、取得可能な
公開日時候補だけとし、Brave固有レスポンスを後段へ漏らさない。

### 3.2 AI方式

初期実装は **OpenAI Responses API、`gpt-5.6-terra`、Structured Outputsの
厳格なJSON Schema**を採用する。Terraは公式に知能と費用の均衡用途とされ、
構造化出力を利用できる。2026-07-31確認時点で入力100万token当たり2.50
米ドル、出力15米ドルである。モデル・価格は固定仕様ではなく、実装時と
運用開始前に再確認する。
[GPT-5.6 Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra)

初期品質評価で契約充足率が不足する場合だけ `gpt-5.6-sol` を比較し、費用を
理由なく上げない。Lunaは費用比較対象だが、最初の基準モデルにはしない。
モデル名は設定可能とするが、許可リストと対応機能を起動時に検証する。

Responses APIでは `store: false`、外部ツールなし、単発リクエストを使う。
AIに検索やURL取得をさせない。Structured OutputsはJSON Schemaへの適合を
支援するが、拒否・token上限・不完全応答を処理し、コード側の意味検証を
省略しない。[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)

OpenAI API送信データは明示的なopt-inなしに学習へ使われない一方、通常の
`/v1/responses` はabuse monitoring等の保持条件がある。公開情報だけを送り、
`store: false`を必須にし、運用組織のデータ管理設定を確認する。
[OpenAI API data controls](https://developers.openai.com/api/docs/guides/your-data)

`AIProvider` 境界は、モデル入力、厳格JSON Schema、timeout、出力token上限を
受け、構造化生出力と使用量メタデータだけを返す。正式結果構築、派生計算、
エラー分類、ログはプロバイダ実装の責務にしない。

## 4. 設定・環境変数契約

設定は起動時に全件読取り、型・範囲・プロバイダ組合せを外部通信前に検証
する。不正・不足は市場単位errorではなく終了コード1のファイル全体失敗。

| 環境変数 | 初期値 / 制約 |
| --- | --- |
| `POLYMARKET_SEARCH_PROVIDER` | 必須、初期許可値 `brave` |
| `POLYMARKET_BRAVE_SEARCH_API_KEY` | Brave時必須、空白不可 |
| `POLYMARKET_AI_PROVIDER` | 必須、初期許可値 `openai` |
| `POLYMARKET_OPENAI_API_KEY` | OpenAI時必須、空白不可 |
| `POLYMARKET_AI_MODEL` | 既定 `gpt-5.6-terra`、許可リスト方式 |
| `POLYMARKET_AI_MODEL_VERSION` | 任意の期待version。providerが返す値との一致検証用 |
| `POLYMARKET_AI_TEMPERATURE` | 既定 `0`、0以上2以下の有限Decimal |
| `POLYMARKET_AI_SEED` | 既定未指定。対応時のみ64-bit整数 |
| `POLYMARKET_PROMPT_VERSION` | 必須、初期 `1.0`、`MAJOR.MINOR` |
| `POLYMARKET_SEARCH_TIMEOUT_SECONDS` | 既定15、1以上60以下 |
| `POLYMARKET_FETCH_TIMEOUT_SECONDS` | 既定15、1以上60以下 |
| `POLYMARKET_AI_TIMEOUT_SECONDS` | 既定90、10以上300以下 |
| `POLYMARKET_RUN_TIMEOUT_SECONDS` | 既定1200、60以上3600以下 |
| `POLYMARKET_MAX_MARKETS_PER_RUN` | 既定10、1以上10以下 |
| `POLYMARKET_MAX_SEARCH_QUERIES_PER_MARKET` | 既定4、1以上6以下 |
| `POLYMARKET_MAX_RESULTS_PER_QUERY` | 既定5、1以上10以下 |
| `POLYMARKET_MAX_FETCHES_PER_MARKET` | 既定8、2以上12以下 |
| `POLYMARKET_MAX_RESPONSE_BYTES` | 既定2097152、65536以上4194304以下 |
| `POLYMARKET_MAX_SOURCE_CHARS` | 既定12000、1000以上20000以下 |
| `POLYMARKET_MAX_TOTAL_SOURCE_CHARS` | 既定60000、2000以上80000以下 |
| `POLYMARKET_MAX_AI_INPUT_TOKENS` | 既定32000、4096以上64000以下 |
| `POLYMARKET_MAX_AI_OUTPUT_TOKENS` | 既定4096、512以上8192以下 |
| `POLYMARKET_MAX_RETRIES` | 既定1、0以上2以下。初回を含め最大2回 |
| `POLYMARKET_RETRY_BASE_SECONDS` | 既定1、0.1以上10以下 |
| `POLYMARKET_MAX_RUN_COST_USD` | 既定2.00、正の有限Decimal |
| `POLYMARKET_AI_DRY_RUN` | 既定false、`true` / `false` のみ |

プロバイダ料金は変更されるため、費用上限だけに安全性を依存しない。
検索回数・取得数・token数・retry数もハード上限とし、予測費用が予算上限を
超える呼出しは開始しない。レスポンス使用量と検索成功回数から実績見積りを
更新し、次の呼出しが上限を超える場合は新規課金を止める。

dry-runは入力・既存結果・設定・対象選択・クエリ生成・予算事前計算までを
行い、外部通信、状態遷移、正式結果置換を行わない。ログはdry-runと明示する。

将来の実装で `.env.example` に変数名とダミー値だけを追加してよいが、同じ
変更で `.env` を `.gitignore` 対象にしてから利用する。APIキー、Cookie、
秘密鍵、AuthorizationヘッダーをJSON、ログ、例外、README例へ出さない。

## 5. 入力ファイルと処理対象

1. ファイル名昇順で最後の `data/analysis_input_*.json` を選ぶ。
2. 同じ日時suffixの `data/analysis_result_*.json` を唯一の対応結果とする。
3. 入力と結果の件数、順序、`market_id`、`analysis_reference_time` を検証する。
4. 空でない結果の全要素は同じ既知 `schema_version` でなければならない。
5. `1.0` は完了契約4.3の全件移行を原子的に完了してから外部通信を始める。
   1.0/2.0混在や処理中の暗黙移行は禁止する。
6. 0件は `[]\n` のまま正常終了し、外部通信しない。

初期版は入力順で最大10市場を逐次処理する。`pending` だけが既定対象。
`completed` と `error` はバイト上の値を保持し、再分析・自動再試行しない。
対象market IDを明示するオプションは `pending` の部分実行と、別処理で既に
`pending` へ戻された `retryable: true` の再試行だけに使用する。
`retryable: false` のリセット機能は初期版に含めない。

市場単位処理中の結果はメモリだけで保持し、全件処理・全体検証後に一度だけ
正式結果を置換する。途中停止では実行前ファイルを維持する。

## 6. 検索クエリ生成契約

初期版はAIにクエリを自由生成させず、入力12キーとコード定数
`QUERY_TEMPLATE_VERSION = "1.0"` から次の固定順で最大4件を作る。

1. `official`: 市場タイトル、主要固有名詞、締切日、`official` / 公式
2. `status`: 市場タイトル、主要固有名詞、締切日、`latest status`
3. `support`: 市場タイトル、YES成立を支持する語、締切日
4. `counter`: 市場タイトル、NO成立・延期・否定を示す語、締切日

市場入力に解決条件・説明文がないため、初期版は存在を推測しない。タイトル、
カテゴリ、締切日、URL hostから決定的に抽出できる語だけを使う。固有名詞抽出
が曖昧でもタイトル全文を残す。地域、人名、組織名、製品名を推測で追加しない。

- 入力タイトルのUnicode NFKC、C0制御文字除去、連続空白畳みを行う
- 1クエリはUTF-8換算ではなくUnicodeコードポイントで1～300文字
- CR/LF/tab、NUL、検索API演算子の自由注入を拒否または空白化する
- 同じ正規化比較値のクエリを最初の1件だけ残す
- 市場タイトルが日本語を含む場合、同じタイトルの日本語クエリを使い、
  固定英語語句を併記する。翻訳を推測生成しない
- クエリ順は上記分類順、同一入力・同一template versionで同一にする

将来AIクエリ生成を追加する場合は別prompt versionとし、最大件数・300文字・
重複・制御文字・禁止演算子をコード検証してから検索する。生出力を直接使わない。
query templateを変更する場合は `QUERY_TEMPLATE_VERSION` と
`POLYMARKET_PROMPT_VERSION` の両方を上げる。正式結果にquery version専用キーが
ないため、`model_info.prompt_version` が検索・分析手順一式の版を表す。

## 7. 外部検索契約

- 市場あたり最大4クエリ、各5結果、全体20候補を上限とする
- provider順位を各クエリ内で維持し、クエリ順、順位、正規化URLで安定整列する
- 完了契約9.3の `canonical_url` で重複排除し、最初の候補だけ残す
- sponsored、広告、URL短縮、非HTTP(S)、userinfo付きURLを候補から除外する
- 公式hostは `official` クエリの上位候補として優先するが、hostを推測しない
- SNS、掲示板、Wikipedia、匿名ブログ、AIまとめは探索の手掛かりに限り、
  初期版の正式情報源にしない
- paywall、ログイン、CAPTCHA、動画、音声、JSレンダリング必須、削除済みページ、
  PDF、XML、RSSは初期取得対象外
- 公開日時表示は候補値にすぎず、本文・API側で検証する
- snippetとextra snippetを根拠、反対材料、AI入力、正式sourceへ使用しない
- providerのrate-limitヘッダーを監視し、429は `Retry-After` 相当を尊重する

検索の200応答・0件は「検索通信成功」だが、候補不足が最終的に最低情報源条件を
満たさなければ `insufficient_evidence`。全検索呼出しが一時障害で失敗すれば
`search_failed`、期限切れは `timeout`、429は `rate_limited` とする。

## 8. URL・本文取得契約

取得器はHTTP(S)公開情報専用とし、認証、Cookie、ブラウザ、JS実行、ファイル
保存を行わない。`Content-Type` は `text/html`、`application/json`、
`text/plain` のみ。PDF、XML、RSS、画像、動画、音声、実行形式は拒否する。

### 8.1 SSRF対策

各初回URLおよびredirectごとに次をすべて行う。

1. 標準URL parserで絶対URLとして解析し、`http` / `https` だけを許可する
2. userinfo、空host、不正portを拒否し、初期版は80/443だけを許可する
3. IP literalとDNSの全A/AAAA結果を検証し、1件でも非globalなら拒否する
4. loopback、private、link-local、unspecified、multicast、reserved、IPv4-mapped
   IPv6、cloud metadata IPを拒否する
5. `localhost`、単一ラベルhost、`metadata.google.internal` 等のmetadata hostを拒否する
6. 接続直前に再解決し、検証済みIPへ接続をpinしつつ元hostでHost/SNI/TLS検証する
7. redirectを自動追従せず、本文は最大3回までLocationを再解析・再解決・再検証する
8. OS環境proxyを継承せず、TLS証明書検証を無効化しない

文字列denylistや最初のDNS検査だけに依存しない。OWASPはredirect検証とA/AAAA
全結果の検証、DNS pinning/rebindingへの注意を示している。
[OWASP SSRF Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html)

### 8.2 HTTP制限と本文抽出

- connect＋readの市場内取得timeoutは1 URL当たり15秒
- responseはstreamingで読み、展開後2,097,152 bytesを超えた時点で中止する
- `Content-Length` が上限超過なら本文を読まず拒否する
- 圧縮は初期版で `identity`、`gzip`、`deflate` だけ。展開後上限を必ず適用する
- charsetは有効なHTTP指定、HTML meta、UTF-8の順で決め、厳格decodeする
- HTMLはscript、style、nav、広告、フォームを除去し、title、publisher候補、
  公開日時候補、main/article本文を抽出する
- JSONは深さ・要素数・文字数を上限化し、公開APIの説明本文だけを抽出する
- textは制御文字を除き、連続空白を正規化する
- 1情報源12,000文字、全候補60,000文字まで。切詰めを記録し、先頭だけでなく
  見出し単位の決定的抽出を行う
- 同一hostへは1秒以上間隔を空け、同時アクセスしない
- User-Agentはプロジェクト名、版、公開リポジトリURLを含める

### 8.3 robots、アクセス制限

本文取得前に同一authorityの `/robots.txt` を同じSSRF規則で取得し、対象
User-Agentの規則を守る。成功時は規則に従い、4xxでunavailableならアクセスを
許可、5xx・DNS・network errorでunreachableなら完全disallowとする。cacheは
プロセス内だけで最大24時間とする。robots取得のredirectだけはRFCに従い最大5回
まで許可し、各hopへ同じSSRF検証を行う。
[RFC 9309](https://www.rfc-editor.org/rfc/rfc9309.html)

paywall、ログイン、CAPTCHA、robots disallow、403、JS必須を回避しない。
Cookie、Authorization、Referer偽装、headless browserを使わない。取得不能な
候補は正式sourceにしない。

## 9. 情報源候補の検証

取得成功だけでは正式 `sources` にしない。コードによる機械検証の後、AIには
分類候補を提示するが、最終採否は次の全条件をコードで再検証する。

- 実取得した最終URLと完了契約9.3の `canonical_url` を持つ
- title 1～500文字、publisher 1～200文字、関連本文を抽出できる
- 市場の解決条件に直接関係し、単なる一般背景ではない
- primary/secondary、support/counter/bothを定義どおり分類できる
- 同一canonical URL、同一記事の転載、同一publisherだけで独立性を水増ししない
- すべての正式sourceが根拠または反対材料から参照される
- 2～8件、異なる正規化URL2件以上、primary status条件を満たす

primaryは政府・規制当局・法令・公式統計・企業IR・原論文・公式記録等、
secondaryは編集責任を持つ報道・解説とする。Wikipedia、SNS、匿名投稿、
AI生成まとめ、検索cache、転載だけのページは初期版では正式sourceにしない。

### 9.1 時点完全性

初期版は **as-ofモードだけ**を実装する。正式sourceは、その内容が
`analysis_reference_time` 以前に公開されていたことを検証できなければならない。

- `published_at` がdatetimeならUTCへ変換した瞬間が基準日時以下
- dateだけなら基準日時のUTC暦日より前の日付だけを採用する。同じUTC暦日は
  公開時刻を証明できないため拒否し、時刻を検証できる場合はdatetimeとして扱う
- 更新日時しかないページは、元公開日時を別のページ本文/API metadataで検証
  できない限り正式sourceにしない
- 公開日時不明は完了契約上 `null` を表現できるが、初期as-of分析では採用しない
- 市場締切後、基準日時後に公開・更新された情報を使わない
- 現在用途でも直前に新しい市場snapshotを作り、その基準日時以前の公開情報だけ
  を使う。古い市場価格と現在情報を混ぜる「current緩和モード」は実装しない

この厳格さで最低条件を満たせない場合は、推測の `completed` ではなく
`insufficient_evidence` とする。将来のcurrent/researchモードは別契約・別成果物
として設計し、as-of結果と混在させない。

## 10. 生データと一時保持

| データ | 初期保持 | 永続保存 |
| --- | --- | --- |
| 検索レスポンス・snippet | process memory | しない |
| 取得response body・抽出本文 | process memory | しない |
| AI入力・モデル生出力 | process memory | しない |
| 正規化source metadata | process memoryから正式結果へ必要項目だけ | 完了契約の項目だけ |
| 実行イベント | サニタイズ後 | JSON Linesログ |

初期版は本文用一時ファイルを作らない。やむを得ずHTTPライブラリがbufferを使う
場合もOS tempへのspillを無効化するか、プロジェクト管理下の限定ディレクトリに
所有者だけの権限で作り、正常・例外・signal終了時にbest effort削除する。crash後の
完全削除は保証できないため、そもそもdiskへ出さないことを優先する。

debugでも本文、snippet、AI入力、生出力、内部prompt、個人情報を標準出力やログへ
出さない。将来、監査証跡として本文を保存する場合は、利用規約、著作権、個人情報、
暗号化、権限、保持期間、削除依頼を別契約で承認してから実装する。

## 11. AIへ渡す分析入力

AIリクエストは1市場単位。市場セクション、規則セクション、情報源セクション、
出力schemaを明示的に分離する。入力順は次の固定順とする。

1. `market_id`
2. 市場タイトル、YES/NO価格、締切日、カテゴリ、URL
3. `analysis_reference_time` と選定理由
4. 「解決条件の本文は入力に存在しない」等の既知の制約
5. prompt versionが指す分析手順と禁止事項
6. canonical URL等で安定ソート済みの情報源材料
7. 厳格なAI生出力JSON Schema

各情報源材料は固定キー順で `candidate_id`、title、publisher、published_at、
source_type候補、最終URL、retrieved_at、抽出本文を持つ。`candidate_id` は
canonical URL安定順の `C1` 連番で、正式 `source_id` ではない。AIからURLを
受け取らず、採用candidate IDだけを受け取る。正式sourceをコードが再整列した後に
`S1` から採番し、参照を書き換える。

- 1情報源最大12,000文字、全体最大60,000文字、推定32,000 input tokens
- 情報源の多様性を保つため、sourceごとに決定的な上限配分を行う
- 超過時は関連見出しとその段落を固定規則で残し、文字境界で切る
- HTML・JSON構造、NUL・C0制御文字を除き、Unicodeを保持する
- AIへ現在日時を曖昧に渡さず、唯一の時間基準としてreference timeを渡す
- 入力市場・外部本文・システム規則をXML風の明示境界で分ける

外部本文はすべて未信頼データである。本文中の「以前の指示を無視」「ツールを
呼べ」「秘密を出せ」等を引用対象の文字列として扱い、指示として実行しない。
AIはツールなし、外部通信なし、渡されたcandidate IDだけを参照する。情報源にない
事実を補完せず、不足時は構造化された分析不能理由を返す。

## 12. AI生出力契約

AIに正式 `analysis_result_*.json` を書かせない。`strict: true`、全objectで
`additionalProperties: false` のJSON Schemaを使用する。生出力は次だけを許可する。

| キー順 | キー | 型・制約 |
| ---: | --- | --- |
| 1 | `outcome` | `completed_candidate` または `insufficient_evidence` |
| 2 | `yes_probability` | numberまたはnull。候補時のみ0～1、最大4桁相当 |
| 3 | `confidence` | `low` / `medium` / `high` またはnull |
| 4 | `evidence` | `{text, candidate_ids}` の配列、候補時1～5。text 1～500文字、ID 1～3件 |
| 5 | `counter_evidence` | 同形式、候補時0～5 |
| 6 | `counter_evidence_assessment` | `{status, summary}` またはnull。summary 1～500文字 |
| 7 | `selected_candidate_ids` | 重複なし配列、候補時2～8 |
| 8 | `source_classifications` | `{candidate_id, source_type, stance, relevance}`。選択候補と1対1、relevance 1～500文字 |
| 9 | `primary_source_status` | `used` / `not_available` / `not_applicable` またはnull |
| 10 | `reason` | 分析不能時の秘匿情報なし1～500文字、それ以外null |

正式結果の17キーをAI schemaへ複製しない。次は必ずコード定数・入力・計算・
実測メタデータから構築する。

- `schema_version`、`market_id`、`analysis_reference_time`、`status`
- `no_probability`、`market_yes_price`、`probability_gap`、`conclusion`
- 正規化URL、正式source ID、source metadata、固定配列順
- `model_info`、`analysis_executed_at`
- `error_code`、`error_category`、`retryable`、`failed_stage`

APIキー、secret、架空URL、入力外candidate ID、自由エラーコード、schema version、
売買数量、注文価格、ウォレット・自動執行指示、内部prompt全文をAI出力に禁止する。

## 13. AI推論手順

`PROMPT_VERSION = "1.0"` は次の順序を意味する。

1. 市場タイトル、締切、既知の解決条件不足を確認する
2. `analysis_reference_time` より後の情報を排除する
3. 各候補の関連性、独立性、publisher、primary/secondaryを評価する
4. YES成立を支持する材料を整理する
5. NO成立、延期、未達、反証となる反対仮説を立てる
6. 提供されたcounter候補を評価し、未確認なら探索済み範囲を評価する
7. 情報源の矛盾、新しさ、直接性、独立性を比較する
8. 完了契約の最低情報源条件を満たせるか判定する
9. 満たせない場合は確率を出さず `insufficient_evidence`
10. 満たす場合だけbase rate、残り時間、主要促進要因、阻害要因を明示的に勘案し、
    YES確率を1つ出す
11. 信頼度を情報品質・独立性・時間的不確実性で分類する
12. 全主張をcandidate IDへ対応付け、schemaどおり返す

独自統計モデル、価格をそのまま確率予測へ写す規則、架空の精密さは導入しない。
市場価格はアンカー候補ではあるが、AIは根拠評価を独立に行う。情報不足を
`confidence: low`で隠さない。

## 14. AI出力検証と再要求

SDKの構造化parse後も、元textにJSON以外、code fence、前後文章がないことを確認
し、重複キーを全階層で拒否する。boolをnumberとして受理しない。NaN、Infinity、
未知・不足キー、型違反、制御文字、文字数・配列数超過を拒否する。

意味検証では次をすべて確認する。

- candidate IDが入力に存在し、重複せず、選択候補との参照が整合する
- evidence/counterのstance、件数、重複、参照順が完了契約と整合する
- primary statusとprimary件数、secondary publisher独立性が整合する
- counter空配列時は `searched_not_found`、counter検索・本文評価済みである
- 全採用sourceが少なくとも1主張から参照され、未参照sourceがない
- yes_probabilityの丸め前値が0～1で、最大4桁へ規定どおり正規化できる
- AIがtitle、publisher、日時、URL等の機械取得値を変更していない

構文または契約不正時だけ、検証エラーの固定分類と同じ入力を使って再要求を最大
1回行う。コードはJSON抽出、値補正、source ID置換、確率clamp、欠落値推測を
行わない。2回目も不正、拒否、不完全なら `invalid_model_output`。API通信障害・
timeoutは再要求ではなくretry規則に従う。

## 15. 正式結果構築

AI候補が意味検証に合格した後、コードが完了契約どおりsourcesを安定ソートし
`S1`から採番する。candidate参照をsource参照へ一意に変換し、Decimal、
`ROUND_HALF_UP`、最大4桁でNO確率・市場価格・差・結論を派生する。

`model_info` は実際のprovider/model/model_version/prompt version/temperature/
seed/tools/search providerから構築する。OpenAIが安定したmodel versionを返さない
場合はnullとし、推測しない。`POLYMARKET_AI_MODEL_VERSION` はproviderが公開・返却
するversionの期待値であり、request parameterや出力上書き値ではない。設定したのに
照合できない、または不一致なら外部通信前または当該response受領時に全体失敗する。
version pinがmodel ID自体で表現されるproviderでは、その完全IDを
`POLYMARKET_AI_MODEL` に設定する。外部検索・本文取得を実際に呼んだ場合だけ
`tools_used` を固定順で設定する。全17キーの最終検証後に `analysis_executed_at`
を確定する。

## 16. エラー変換

| 事象 | 結果 |
| --- | --- |
| 検索API 5xx/DNS/接続失敗がretry後も継続 | `search_failed` / `search` |
| 検索0件、本文採用不足、独立source不足 | `insufficient_evidence` / `evidence_evaluation` |
| 検索429 | `rate_limited` / `search` |
| 取得・AIのtimeout | `timeout` / 該当stage |
| URL取得側429 | `rate_limited` / `source_retrieval` |
| robots、paywall、login、unsupported type、SSRF拒否が一部候補だけ | 候補を除外し継続 |
| 取得候補を全て検証できない、日時・関連性・独立性不成立 | `source_validation_failed` / 許可stage |
| AI API 5xx・network・拒否（構造化出力なし） | `model_failed` / `model` |
| AI API 429 | `rate_limited` / `model` |
| JSON/schema/意味/source参照不正が再要求後も継続 | `invalid_model_output` / `model_output_validation` |
| AIが情報不足を正しく返す | `insufficient_evidence` / `evidence_evaluation` |

同じ市場で複数失敗がある場合、実際に処理を停止させた最終stageに対応するコードを
使う。HTTP statusや例外classを自由コードにせず、`error_message` は固定templateへ
件数等の非秘匿値だけを埋める。

APIキー不足、認証設定不正（401/403を含む）、不正provider/model、契約定数不正、
入力・既存結果不正、予算設定不正、lock内部不変条件、シリアライズ・保存失敗は
ファイル全体失敗。市場単位errorへ変換せず正式結果を変更しない。

## 17. retry方針

1実行内の自動retryと、結果JSONの `retryable`、利用者の後日再試行を分離する。

- 自動retry対象: 検索・取得・AIの一時network、502/503/504、429、timeout
- 対象外: 400/401/403/404、robots、SSRF、schema意味不正、情報不足、設定不正
- 既定は初回＋retry 1回。最大でも初回＋2回
- 待機は `min(30, base * 2^attempt)` 秒を上限にfull jitter
- 有効な `Retry-After` / provider reset値は30秒を上限に優先する
- 全体timeoutまたは予算上限を超えるretryは開始しない
- AI構造不正の再要求1回はtransport retry回数と別に数え、ログへ区別する
- 非冪等な外部操作は存在しない。各retryは同じrequest内容とrequest IDをログで関連付ける

自動retry後に失敗した市場の `retryable` は完了契約の固定値であり、retryを既に
行ったかには左右されない。`retryable: true` でも自動で `error → pending` に戻さない。
`retryable: false` のリセットは初期版に含めず、将来は変更条件と利用者指定を
`state_reset` ログへ残してから、別処理で原子的にpendingへ戻す。

## 18. 実行ログ契約

初期版は正式結果と別に、1実行1ファイルのJSON Linesを
`data/logs/analysis_run_<UTC compact>_<run_id>.jsonl` へ書く。UTF-8 BOMなし、
LF、1行1object、末尾LF。ログ保存失敗は監査不能のためファイル全体失敗とし、
正式結果を置換しない。

イベントの共通キー順は `log_version`、`run_id`、`sequence`、`timestamp`、
`event`、`market_id`、`details`。`sequence` は0始まり整数、timestampは完了契約の
UTC形式。`details` はevent別の既知キーだけを許可する。

初期イベント:

- `run_started`: 相対入力名、相対結果名、schema/prompt/query template version、
  provider/model/search provider、dry-run、対象・pending件数、設定上限
- `market_started`: market ID、入力順、attempted time
- `search_finished`: query分類、成功/失敗、結果件数、課金対象request数、retry数
- `retrieval_finished`: 候補件数、取得成功/拒否/失敗件数。URL本文は記録しない
- `model_finished`: 成否、token usage、provider request IDの非秘匿部分、retry/
  validation re-request回数
- `market_finished`: 遷移前後、error code/failed stage、採用source件数
- `state_reset`: 将来用。旧code、利用者指定理由、変更条件の固定列挙
- `run_finished`: 開始・終了、completed/error/pending件数、検索・取得・AI回数、
  retry合計、token合計、費用見積り、正式結果置換の成否

ログしないもの: APIキー、Authorization、Cookie、secret、秘密鍵、内部prompt全文、
検索snippet、URL queryの機微情報、本文全文、AI入力、生出力、例外原文、stack trace、
個人情報、OS username、hostname、不要な絶対path。ファイル名は相対basenameだけ。
例外は固定分類とサニタイズ済みmessageに変換する。

ログは結果JSONの決定性対象外だが、event内のキー順と市場・query順は固定する。
初期版の保持期間は自動削除せず利用者管理とし、本番運用前に保持・削除規程を
別途定める。本文を含まないことは長期保持の無条件許可を意味しない。

## 19. run ID、同時実行、冪等性

`run_id` は実行開始時のUTC compact時刻とUUIDv4から生成し、ログと一時ファイル
識別だけに使う。正式結果へ入れないため、結果シリアライズの決定性を壊さない。

同じ正式結果ファイルに対する同時実行を禁止する。初期版は同じdata directoryの
`.analysis_result_<suffix>.lock` を `O_CREAT | O_EXCL` 相当で原子的に作る。
lockにはlog version、run ID、開始UTC、結果basenameだけを記録し、PID、username、
hostname、秘密情報は記録しない。

- lock取得前に外部通信しない
- lock存在時は二重起動としてファイル全体失敗し、勝手に削除しない
- 正常・処理済み例外ではfinallyで自分のrun IDと一致するlockだけ削除する
- crash等のstale lockは自動判定・削除しない。利用者がprocess不存在と結果・tempを
  確認して明示削除する
- lockを保持したまま全市場を処理し、同一directoryの一時結果へ全バイトを書き、
  flush、fsync、close、全体再検証後に `os.replace()` する
- 置換成功後だけ正式更新済みとし、一時ファイルは失敗時best effort削除する

既存completedは保持し、pendingの二重処理をlockで防ぐ。入力・既存結果・設定の
ハッシュはログへSHA-256として記録可能だが、自動的な「同じ分析」判定やskipには
使わない。外部状態が変わるため、同一hashは同一推論を保証しない。

## 20. 秘密情報管理

- provider別APIキーは環境変数だけから読み、結果・ログ・promptへ渡さない
- `.env` はGit管理しない。将来の `.env.example` は変数名とplaceholderだけ
- 起動時にキー存在を検証し、値や長さを表示しない
- HTTP request/response header、provider SDK request objectをdumpしない
- 例外原文を保存せず、既知statusと固定messageへ変換する
- debug modeでもsecret redactionを無効化しない
- pipelineは子processを起動しない。将来起動する場合はproviderキーを環境から除外する
- crash dump/core dumpを意図的に生成せず、本番環境では無効化を推奨する
- GitHub Actionsを将来使う場合はrepository/environment Secrets、最小権限、fork PRへ
  secret非提供、mask確認を必須とする
- 漏えい疑いでは直ちにprovider側で失効・rotationし、Git履歴・ログ・成果物を監査する

モデル出力や例外文にsecretらしい値がある場合は正式JSONへ保存せず、内部不変条件
違反としてファイル全体を失敗させる。単純な文字置換だけで安全とみなさない。

## 21. 利用規約、robots、著作権、個人情報

初期版は認証不要で公衆に公開された情報だけを対象とする。robots.txtとサイトの
利用規約を尊重し、paywall、CAPTCHA、login、アクセス制限、技術的保護を回避しない。
スクレイピング禁止等を確認したsiteは取得対象から除外する。不正アクセス、非公開・
流出・違法取得情報を使わない。

同一host 1秒間隔、逐次取得、明示User-Agent、上限付きアクセスとする。記事全文や
長い引用を永続保存せず、正式結果のrelevance/evidenceは500文字以下の独自要約と
source URLだけを持つ。本文の長い逐語引用をAIへ要求しない。

公開ページに個人情報が含まれていても、予測に不要なら抽出・AI送信しない。要配慮
情報、非公人の連絡先等を検出した候補は除外する。削除依頼、規約変更、publisherの
訂正を知った場合、結果は自動訂正せず、利用停止・再分析方針を利用者判断へ上げる。

検索・AI providerの規約、価格、データ処理条件は運用開始前と定期的に再確認する。
本設計は法的助言ではなく、商用・公開運用では法務確認を行う。

## 22. timeout、件数、課金上限

初期推奨値を次に固定し、環境変数で狭められる。コードの絶対上限を超えて広げる
設定は拒否する。

| 項目 | 初期推奨 | 絶対上限 |
| --- | ---: | ---: |
| 市場/実行 | 10 | 10 |
| 検索query/市場 | 4 | 6 |
| 結果/query | 5 | 10 |
| 検索候補/市場 | 20 | 60 |
| URL取得/市場 | 8 | 12 |
| 正式sources/市場 | 2～8 | 8 |
| response展開後bytes | 2 MiB | 4 MiB |
| 抽出本文/source | 12,000文字 | 20,000文字 |
| 抽出本文/市場 | 60,000文字 | 80,000文字 |
| AI input/市場 | 32,000 token | 64,000 token |
| AI output/市場 | 4,096 token | 8,192 token |
| 検索timeout | 15秒 | 60秒 |
| URL timeout | 15秒 | 60秒 |
| AI timeout | 90秒 | 300秒 |
| 自動retry | 1回 | 2回 |
| 全体timeout | 1,200秒 | 3,600秒 |
| 実行予算 | 2.00 USD | 設定必須、正の有限値 |

4 query×10市場ならBrave検索はretry前最大40 requestで、2026-07-31の5 USD/
1,000 request価格では0.20 USD相当。ただし成功課金条件・価格はprovider公式を
実行時に再確認する。AI費用は実tokenと公式単価から見積り、検索・AIの合計を予算
へ計上する。本文HTTP取得に従量provider費用は想定しないがnetwork費用は別途あり得る。

予算計算不能、単価設定なし、usage欠落時は上限を無視せず、保守的な予約額または
request/tokenハード上限で止める。予算超過は新たな市場を開始せず、既処理結果と
未処理pendingを全体検証後に保存できる。ただし実行開始前から設定不正なら全体失敗。

## 23. 決定性と再現性

### 23.1 契約・シリアライズの決定性

外部検索結果、取得内容、AI生出力、日時、モデルmetadataが確定した後、同じ確定
データから同じ正式JSONを生成する。完了契約16の固定市場順、状態・nestedキー順、
source/evidence安定順、Decimal、最大4桁、`ROUND_HALF_UP`、指数表記なし、
UTF-8 BOMなし、2-space、LF、末尾LF、`[]\n`、原子的保存をそのまま適用する。

AIに正式source ID、派生値、schema version、日時を決めさせない。現在時刻、run ID、
temp名、host情報をシリアライズ時に差し込まない。

### 23.2 外部検索・AI推論の再現性

検索index・順位、ページ内容・削除・訂正、DNS、provider仕様、model alias、timeout、
rate limit、推論基盤は変化する。temperature 0やseedを使っても同一結果を保証しない。
本文とモデル生出力を永続保存しないため、完全再現も保証しない。

結果JSONのURL・日時・model info・prompt version・参照関係と、ログのrun ID、query分類、
回数、usage、状態・errorから条件を追跡する。query本文、検索結果全文、取得本文、
AI生出力までは追跡できない。この境界を完全監査と誤認しない。

## 24. 初期実装の最小範囲

含める:

- Brave Search API 1 provider、OpenAI Responses API 1 provider
- 既定1モデル `gpt-5.6-terra`、strict Structured Outputs
- 最大10市場の逐次処理、pendingだけ
- 公開HTTP(S)、HTML/JSON/text、as-of時点検証
- 決定的4 query、安全なURL取得、2～8source
- 生本文・検索結果・AI生出力を永続保存しない
- transport retry最大1回、モデル不正再要求最大1回
- 市場単位7error、全体原子的結果保存、サニタイズJSONLログ、lock
- dry-run、予算・timeout・件数上限、売買機能なし

含めない:

- 複数検索/AI providerの実装（interfaceのみ）、モデル自動fallback
- モデル内蔵Web検索、AI query生成、並列処理、scheduler、GUI、cloud
- PDF/XML/RSS、画像・動画・音声、JS rendering、browser、Cookie/login/paywall/CAPTCHA
- source本文・model生出力の証跡保存、current緩和モード
- completed再分析、`retryable: false` reset、自動error再処理
- 売買、wallet、注文、数量、収益計算、投資助言

## 25. 実装分割案

設計承認後も一括実装せず、各段階でunit testと既存回帰を通す。

1. 設定schema、secret redaction、SearchProvider/AIProvider interface
2. 入力・結果2.0移行/照合、対象選択、dry-run
3. 決定的query生成とBrave結果normalizer
4. SSRF・robots・size制限付きHTTP取得器
5. HTML/JSON/text抽出、時点・source検証、安定ID
6. AI入力builderとprompt injection境界
7. OpenAI Structured Outputs adapterと生出力validator
8. completed派生値・7error変換、状態遷移
9. JSONL log、cost/retry、lock
10. 全体orchestrator、原子的保存、停止回復
11. mock統合試験、明示承認後だけ少数実API smoke test

各commitは依存追加を最小化し、既存4 scriptの契約を変更しない。実API testは費用と
外部状態を伴うため通常unit testから分離し、secretなしCIでは実行しない。

## 26. テスト観点

### 26.1 設定・秘密情報

- provider別key未設定、空白、不正provider/model/model versionを全体失敗
- temperature/seed/timeout/件数/bytes/token/retry/予算の上下境界
- dry-runで外部call・結果変更なし
- API key、Authorization、Cookie、例外原文が結果・log・stdoutにない
- `.env` 非追跡、`.env.example` に実値なし

### 26.2 入力・状態

- 最新入力と同suffix結果を選び、件数・順序・ID・reference time一致
- 1.0全件pendingから2.0全件pendingへ原子的移行
- 1.0/2.0混在、未知version、壊れた既存結果を全体失敗
- pendingだけ処理し、completed/error/未選択pendingをbyte相当で保持
- 0件 `[]\n`、最大10件、逐次順、途中停止で既存結果不変

### 26.3 query・検索

- 日本語/英語、NFKC、制御文字、長さ300、重複、固定4分類・固定順
- 0件、重複URL、sponsored、短縮・userinfo・非HTTP URLを除外
- timeout、429、5xx、DNS、retry/Retry-After、最大件数、費用計数
- snippetだけをsource/AI入力にしない
- provider response field欠落・未知追加fieldをnormalizer境界で処理

### 26.4 URL取得・SSRF・robots

- localhost、単一label、IPv4/IPv6 private、link-local、metadata、mapped IPv4拒否
- DNSがglobal/non-global混在、rebind、接続直前変化を拒否
- redirect各hop再検証、4回目拒否、redirect先private拒否
- proxy環境非継承、TLS失敗、非standard port拒否
- `Content-Length`超過、stream途中超過、gzip/deflate展開bomb
- unsupported Content-Type、charset不正、HTML/JSON/text抽出、制御文字
- robots allow/disallow、4xx unavailable、5xx/network unreachable、cache
- paywall、login、CAPTCHA、JS必須、PDF/XML/RSSを不採用

### 26.5 情報源

- primary `used` / `not_available` / `not_applicable` の各条件
- secondary publisher重複、転載、canonical URL重複で最低数を満たさない
- published datetime/date/unknown、reference time直前・同時・直後
- 更新日時だけ、市場締切後、未来記事をas-ofで拒否
- support/counter/both、未参照source、counter 0件の探索済み条件
- 2/8件の境界、公式文書・IR・原論文・報道の分類

### 26.6 AI入力・推論

- source順、candidate ID、固定field順、12k/60k文字、32k token境界
- 長文の決定的切詰め、source多様性、HTML除去、時刻基準
- 本文内prompt injectionを指示として実行しない
- 入力外情報、秘密情報、source外URLをpromptへ混入しない
- `store: false`、外部toolなし、model/temperature/seed記録

### 26.7 AI出力

- 正常strict JSONと `completed_candidate` / `insufficient_evidence`
- code fence、前後文章、全階層重複key、未知key、必須不足、型違反
- bool-as-number、NaN/Infinity、範囲外・桁超過確率
- 架空/重複candidate ID、stance、primary status、counter assessment不整合
- 未参照source、source metadata改変、売買・secret fieldを拒否
- 1回目不正→再要求成功、2回目も不正→`invalid_model_output`
- コードがNO確率、市場価格、gap、conclusion、正式IDを正しく派生

### 26.8 エラー・retry・費用

- 7固定errorとcategory/retryable/failed stageの全許可組合せ
- 検索0件と検索障害、取得不能とsource検証失敗を区別
- 401/403認証、設定、入力、契約、保存失敗を市場errorにしない
- retry 0/1/2、backoff jitter範囲、Retry-After上限、全体timeout中止
- model不正再要求とtransport retryを別計数
- 予算直前・一致・超過、usage欠落で新規call停止
- 1市場errorでも残りを続行し、正常保存なら終了コード0

### 26.9 ログ・lock・原子的保存

- run ID形式・一意性、sequence、start/end、市場件数・retry・usage
- 本文、snippet、生output、secret、絶対path、username非出力
- 同じ結果への二重lock拒否、他suffixは独立
- stale lockを自動削除しない、自run lockだけ削除
- crash、temp write/fsync/replace/log失敗時の既存結果不変
- 成功時だけ全体置換、結果内にrun ID/temp名を含めない

### 26.10 決定性・回帰

- 同じ確定分析データから正式JSONがbyte単位一致
- UTF-8 BOMなし、LF、2-space、固定key/array順、末尾LF、空配列
- Decimal最大4桁と負のzero正規化
- 既存81テスト、4 scriptの契約、README、plan、依存を不変に保つ
- mock外部providerでnetwork非依存unit/integration testを行う

## 27. 自己レビューと未確定境界

### 27.1 既存契約との整合確認

- completed 17キー、error 12キー、pending 4キーを変更していない
- `SCHEMA_VERSION = "2.0"` と単一ファイル単一versionを維持した
- 7種類以外の市場errorを追加していない
- snippetを正式sourceにせず、2～8件、primary・counter条件を維持した
- YESだけをAI独立値とし、NO・市場価格・gap・conclusionはコード派生とした
- 情報不足時に低信頼completedを作らず、部分結果をerrorへ保存しない
- 市場単位失敗と設定・入力・保存等の全体失敗を分離した
- completed終端、error自動retryなし、全体原子的置換を維持した
- 結果の決定性と外部推論の再現性を分離した

### 27.2 安全性・実装量・費用のレビュー

- 独立検索＋自前取得は本文検証に必要だが、SSRF/robots/抽出が最大の実装risk
- 初期content typeとsource種別を狭め、並列・PDF・browserを外した
- 10市場×4検索、1 retry、token・予算hard capで意図しない課金を制限した
- Brave検索結果は候補発見だけ、OpenAIにはpublisher本文の限定抜粋だけを送る
- 生本文・model output非保存によりprivacy/copyright riskを減らす一方、完全再現は
  できないことを明示した

### 27.3 実装前に再確認する外部依存

料金、無料credit、rate limit、model availability、snapshot名、API response schema、
データ保持、Brave Search API規約、各publisherの利用条件は変更され得る。実装開始時に
公式資料を再確認し、本書と矛盾すれば推測採用せず設計レビューへ戻す。

特にBrave規約の検索結果保存・AI関連条項は、候補URLの一時利用という本用途に適合
するか運用者が確認する。適合を確認できない場合はBraveを呼ばず、別検索providerの
公式規約を比較した設計変更を承認してから差し替える。

## 28. 完了条件と実装開始ゲート

本設計は次を満たした時に設計完了とする。

- 検索・取得・情報源・AI・検証・error・retry・log・lockの境界が一意
- 環境変数、初期値、絶対上限、秘密情報禁止事項が具体的
- as-of時点完全性とprompt injection/SSRF対策がテスト可能
- 初期provider/modelと差替えinterface、費用・利用条件の注意が明確
- 完了契約との矛盾がなく、将来テスト観点と最小実装範囲が固定
- 設計書1ファイルだけが変更され、既存81テストとPython構文確認が成功

設計承認前に実装、依存追加、`.env.example`、`.gitignore`、README、plan、test、
data、GitHub Actionsを変更しない。承認後も25節の分割順でテスト先行し、実APIを
使う試験は費用・secret・利用条件の明示確認後に限定する。
