# 市場説明・解決情報源 伝播設計仕様

## 1. 目的

本仕様は、Polymarket Gamma APIのmarket objectが返す`description`と
`resolutionSource`を、次の経路で欠落させずに伝播する契約を固定する。

```text
Gamma API
  -> markets_*.csv
  -> candidates_*.csv
  -> analysis_input_*.json
  -> analyze_market.py の入力検証
```

出力名はそれぞれ`市場説明`、`解決情報源`とする。市場タイトルだけではなく、
実際の解決条件を後続の外部検索・AI分析へ渡せることが目的である。

Polymarket公式仕様では、タイトルは質問を示す一方、resolution rulesが判定元、
判定可能時期、曖昧時の扱いを定義する。Gamma APIの市場schemaには
`description`と`resolutionSource`がある。

- [Polymarket Resolution](https://docs.polymarket.com/concepts/resolution)
- [Gamma API: List markets](https://docs.polymarket.com/api-reference/markets/list-markets)
- [Gamma API: List markets (keyset pagination)](https://docs.polymarket.com/api-reference/markets/list-markets-keyset-pagination)

本工程では外部検索、情報源本文取得、AI推論、確率予測、売買、認証、
`completed` / `error`生成を実装しない。承認済みの外部AI設計に対する前提工程である。

## 2. 現状確認

### 2.1 現行データ契約

実コードを正本として確認した現行契約は次のとおりである。

| 段階 | 現行契約 | 0件時 |
| --- | --- | --- |
| `fetch_markets.py` | 9列の市場CSV | 有効市場または有効価格が0件なら全体失敗し、CSVを作らない |
| `select_candidates.py` | 9列入力、12列出力 | 12列ヘッダーだけの候補CSV、終了コード0 |
| `prepare_analysis_input.py` | 12列入力、12キー出力 | 正確な`[]\n`、終了コード0 |
| `analyze_market.py` | 12キー入力、4キーの`pending`出力 | 正確な`[]\n`、終了コード0 |

現行の市場CSV列順は次である。

```text
取得日時,市場ID,市場,YES価格,NO価格,出来高,流動性,締切日,URL
```

現行の候補CSVは`INPUT_FIELDS`の9列を同じ順で維持し、その後ろへ3列を足す。

```text
取得日時,市場ID,市場,YES価格,NO価格,出来高,流動性,締切日,URL,
カテゴリ,締切までの日数,選定理由
```

現行の分析入力JSONキー順、および`analyze_market.py`の必須入力順は同じ12キーである。

```text
市場ID,市場,YES価格,NO価格,出来高,流動性,締切日,カテゴリ,
締切までの日数,URL,分析基準日時,選定理由
```

### 2.2 現行の決定性と保存

- 市場行は出来高降順で処理し、市場IDで重複排除する
- 候補は既存の完全ソートキーと3段階分散規則で選ぶ
- CSVはUTF-8 BOM付きで、Python標準`csv`モジュールを使う
- 候補CSVのレコード終端は明示的にCRLFである
- 分析入力・結果JSONはUTF-8 BOMなし、2スペース、LF、末尾LFである
- JSON数値は`Decimal`から指数表記なしの固定表現へ変換する
- JSONは同一ディレクトリの一時ファイルを`fsync`後、`os.replace`する
- JSON入力不正・保存失敗時は既存正式JSONを保持する

一方、市場CSVは排他的な直接新規書き込み、候補CSVは直接上書きであり、
JSONと同等の一時ファイル・`fsync`・原子的置換にはなっていない。本伝播の実装時には、
新しいmultiline列を含む不完全CSVを正式成果物として残さないため、CSV保存も後述の
原子的保存契約へ揃える。この既存差異は本設計で記録し、今回の設計コミットでは直さない。

### 2.3 現行fixtureと文書

4つのテストファイルは、それぞれ9列のGamma/市場行、12列の候補行、12キーの
分析入力行をhelperで生成している。ヘッダー・キー定数とfixtureは伝播実装時に
同じ変更単位で更新する必要がある。

`README.md`と`plan.md`も9列、12列、12キー、実データ検証値を明記している。
今回これらは変更せず、実装承認後にコード・テストと同時更新する。

## 3. 基本判断

### 3.1 `description`欠損市場を除外する段階

初期版は、**`fetch_markets.py`の市場正規化段階で無効市場として除外する**。

比較は次のとおりである。

| 方式 | 監査性 | 後続契約 | 判断 |
| --- | --- | --- | --- |
| 市場CSVへ空値を残し候補選別時に除外 | 欠損市場がCSVに残る | 正式11列内に分析不能行が混在し、全段階で分岐が必要 | 不採用 |
| 収集時に除外 | 欠損市場自体はCSVに残らない | 市場CSV以降は全行が有効な`市場説明`を持つ | 採用 |

既存収集機も、価格、締切、二択、スポーツ判定など個別市場の不正を除外して他市場を
継続する。この責務に合わせる。監査性を補うため、標準エラーへ本文を出さず、
`description`不正、`resolutionSource`不正、長さ超過の除外件数だけを集計表示する。
集計はCSV内容や選別順に影響させない。

### 3.2 値伝播だけに限定する

- `市場説明`をカテゴリ判定、テーマ判定、スコア、ソート、分散選択に使わない
- `解決情報源`が空でも候補から除外しない
- 要約、翻訳、HTML除去、Markdown除去、URL補完、言い換えをしない
- 収集時の固定正規化後は、CSVとJSONの各段階で再整形しない
- 市場ごと・取得ごとの値の変化は、その時点の新しいスナップショットとして保存する
- 過去ファイルとの差分マージや、過去値による補完はしない

## 4. Gamma API入力契約

### 4.1 共通文字列正規化

Gammaの値に対して許される内容変更は、次の順の固定処理だけである。

1. 値が契約上許容される型か判定する
2. 文字列中の`\r\n`を`\n`へ変換する
3. 残る単独`\r`を`\n`へ変換する
4. 文字列全体の前後空白を`str.strip()`で除去する
5. NUL（U+0000）とsurrogate code point（U+D800～U+DFFF）がないことを検証する
6. Unicodeコードポイント数を`len()`で検証する

Python文字列内のsurrogate code pointを、本契約における「不正Unicode」と定義する。
正常な非ASCII、絵文字、結合文字は受理し、Unicode正規化（NFC/NFKC等）はしない。
内部のタブ、LF、連続空白、カンマ、ダブルクォート、HTML、Markdownは維持する。

`str(value)`による型変換はしない。無効な個別市場は除外し、同じAPIページの他市場を
処理する。無効値の本文はログへ出さない。

### 4.2 `description` / `市場説明`

| Gamma値 | 処理 |
| --- | --- |
| string | 共通正規化後、1～262,144コードポイントなら受理 |
| キー欠落 | 市場を除外 |
| `null` | 市場を除外 |
| 空文字・空白だけ | 市場を除外 |
| 非文字列 | 市場を除外 |
| NUL・不正Unicode | 市場を除外 |
| 262,144コードポイント超 | 切り詰めず市場を除外 |

上限は単一定数`MAX_MARKET_DESCRIPTION_CHARS = 262_144`で管理する。これは承認済み
外部AI設計の1情報源20,000文字、全情報源80,000文字というhard capより十分大きく、
通常の解決条件を収集段階でAI用サイズへ切り詰めないための防御上限である。
AI入力時のtoken budget適用は外部AI工程の責務であり、ここでは本文を切り詰めない。

### 4.3 `resolutionSource` / `解決情報源`

| Gamma値 | 処理 |
| --- | --- |
| string | 共通正規化後、0～32,768コードポイントなら受理 |
| キー欠落 | 空文字へ正規化 |
| `null` | 空文字へ正規化 |
| 空文字・空白だけ | 空文字として受理 |
| 非文字列 | 市場を除外 |
| NUL・不正Unicode | 市場を除外 |
| 32,768コードポイント超 | 切り詰めず市場を除外 |

上限は`MAX_RESOLUTION_SOURCE_CHARS = 32_768`で一元管理する。URLであること、
HTTP(S)であること、到達可能であることを要求しない。文言や組織名の場合もそのまま
保持し、空値へ架空URLや推測値を補完しない。

## 5. markets CSV契約

### 5.1 正式11列

`CSV_FIELDS`を次の正確な順に変更する。

```text
取得日時
市場ID
市場
市場説明
解決情報源
YES価格
NO価格
出来高
流動性
締切日
URL
```

1行表記は次である。

```text
取得日時,市場ID,市場,市場説明,解決情報源,YES価格,NO価格,出来高,流動性,締切日,URL
```

既存9列の相対順は変更せず、`市場`直後に2列だけを挿入する。ファイル名、
取得日時、最大100件、出来高順、市場ID重複排除、価格対応は変更しない。

### 5.2 CSV形式

- UTF-8 BOMあり
- Python標準`csv.DictWriter`を使用
- `newline=""`で読み書きする
- レコード終端を`lineterminator="\r\n"`へ明示して固定する
- 値内部の改行は正規化済みLFのままCSV quotingで保持する
- カンマ、ダブルクォート、LFは`csv`モジュールのRFC 4180型quotingでround-tripする
- 手動のカンマ結合、`splitlines()`、1物理行=1市場という解析を禁止する
- Excel等の表示方法は契約外とする

ファイル自体のレコード終端はCRLF、quoted field内部の市場説明改行はLFである。
空の`解決情報源`は空fieldとして保存する。

### 5.3 0件と保存

候補0件の正常系とは異なり、収集対象または有効CLOB価格が0件なら現行どおり
終了コード1とし、正式な市場CSVを作らない。`description`不正によって全市場が
除外された場合も同じである。

市場CSVは全行を決定的にシリアライズしてから、同一ディレクトリの一時ファイルへ
UTF-8 BOM付きbytesを書き、`flush`、`fsync`、close後に正式名へ原子的に移す。
既存の同分衝突回避名を先に確定し、正式パスが既に存在する場合は上書きせず、
再度衝突回避名を導出する。書き込み・同期・確定失敗では一時ファイルを削除し、
不完全な正式CSVを残さない。

## 6. candidates CSV契約

### 6.1 正式14列

現行候補CSVは`取得日時`を持ち、`分析基準日時`は持たない。したがって、
分析入力JSON用に提示された14キー順を候補CSVへ流用しない。既存列の相対順を保ち、
`市場`直後に2列を挿入する正確な順は次である。

```text
取得日時
市場ID
市場
市場説明
解決情報源
YES価格
NO価格
出来高
流動性
締切日
URL
カテゴリ
締切までの日数
選定理由
```

1行表記は次である。

```text
取得日時,市場ID,市場,市場説明,解決情報源,YES価格,NO価格,出来高,流動性,締切日,URL,カテゴリ,締切までの日数,選定理由
```

`INPUT_FIELDS`は市場CSVと同じ11列、`OUTPUT_FIELDS`はその11列の後ろに現行3列を
追加する。`Candidate.source`から2値を一文字も変更せず書き出す。

### 6.2 入力検証と選別

- ヘッダーに11必須列がなければファイル全体を拒否する
- 行の`市場説明`はstring、正規化済みの非空値、上限内でなければその市場を除外する
- `解決情報源`はstring、空文字可、正規化済み、上限内でなければその市場を除外する
- NUL、surrogate、残存CR、前後空白がある2値は再正規化せず、その市場を除外する
- `解決情報源`が空であることを除外理由にしない
- 既存の価格0.10～0.90、締切7～90日を緩和しない
- カテゴリ判定対象は従来どおり市場名と正規化テーマURLだけとする
- 完全ソートキー、重複排除、3段階分散、最大10件を変更しない

新しい収集機の正式出力では全市場が有効な2値を持つ。行単位除外は、手編集、
破損、将来の生成差異があっても他の有効行を処理できる防御である。ヘッダー不足、
CSV構文・UTF-8不正、取得日時不統一など現行のファイル全体不正は従来どおり停止する。

### 6.3 0件、multiline、保存

適格市場0件は正常で、上記14列のヘッダーだけをUTF-8 BOM付き、CRLF終端で保存し、
終了コード0とする。そこから`prepare_analysis_input.py`を実行すると正確な`[]\n`を
生成する。

multiline fieldは標準`csv`と`newline=""`で読み書きし、正規化済みLFを保持する。
候補CSVも全bytesを一時ファイルへ書き、`flush`、`fsync`、close後に`os.replace`する。
入力検証・シリアライズ・保存失敗では既存正式候補CSVと元市場CSVを変更しない。

## 7. analysis_input JSON契約

### 7.1 候補CSV入力

`prepare_analysis_input.py`の`INPUT_FIELDS`を候補CSVと同じ14列へ変更する。
追加列は将来互換のため引き続き無視する。必須列欠落はファイル全体不正とする。

データ行では`市場説明`を非空string、`解決情報源`を空文字可stringとして検証する。
両値は正規化済みであること、NUL・surrogate・CRがなく上限内であることを要求する。
違反行を含む候補CSVは入力不正として全体を停止し、既存JSONを保持する。
CSVから読んだ値の再`strip`、改行変換、HTML処理は行わない。

### 7.2 正式14キーと型

`JSON_KEYS`を次の単一定数順へ変更する。

| 順 | キー | JSON型 | 条件 |
| ---: | --- | --- | --- |
| 1 | `市場ID` | string | 非空 |
| 2 | `市場` | string | 現行どおり伝播 |
| 3 | `市場説明` | string | 1～262,144コードポイント |
| 4 | `解決情報源` | string | 空文字可、最大32,768コードポイント |
| 5 | `YES価格` | number | 現行Decimal規則 |
| 6 | `NO価格` | number | 現行Decimal規則 |
| 7 | `出来高` | number | 現行Decimal規則 |
| 8 | `流動性` | number | 現行Decimal規則 |
| 9 | `締切日` | string | 現行どおり伝播 |
| 10 | `カテゴリ` | string | 現行どおり伝播 |
| 11 | `締切までの日数` | number | 現行Decimal規則 |
| 12 | `URL` | string | 現行どおり伝播 |
| 13 | `分析基準日時` | string | CSVの`取得日時`を表記も含め維持 |
| 14 | `選定理由` | string | 現行どおり伝播 |

固定キー順は次である。

```text
市場ID,市場,市場説明,解決情報源,YES価格,NO価格,出来高,流動性,締切日,
カテゴリ,締切までの日数,URL,分析基準日時,選定理由
```

候補CSVの行順と1対1対応を維持する。`市場説明`と`解決情報源`はCSVデコーダが
返した文字列をそのままJSON stringとしてescapeし、再整形しない。

### 7.3 シリアライズと保存

- トップレベルは常に配列
- UTF-8 BOMなし、`ensure_ascii=False`相当
- 2スペースインデント、LF、末尾LF
- `JSON_KEYS`の固定キー順、候補CSVの固定行順
- 5数値は現行の丸めなし、指数表記なし、末尾ゼロ除去、負のzeroを`0`とする規則
- 0件は正確な3bytes `[]\n`
- 入力検証と全bytes生成後だけ、現行の一時ファイル・`fsync`・`os.replace`を行う
- 書き込み・置換失敗時は既存JSONと元候補CSVを保持する

## 8. analyze_market.py入力契約

### 8.1 必須14キー

`INPUT_KEYS`を分析入力JSONと同じ14キー順へ変更する。`STRING_INPUT_KEYS`へ
`市場説明`と`解決情報源`を追加し、次を意味検証する。

- `市場説明`: string、前後空白除去後ではなく入力値自体が正規化済みで、非空、上限内
- `解決情報源`: string、空文字可、正規化済み、上限内
- 両方: NUL、surrogate、CRを拒否する

旧12キー入力は2キー欠落として外部通信前に明示拒否する。全階層の重複JSONキー、
非有限number、市場ID空欄・重複、分析基準日時の形式・完全一致、入力順維持は変更しない。

### 8.2 未知キーとバージョン境界

- 未知キーは将来互換のため引き続き許容する
- 入力内の`schema_version`は未知キーとして扱い、出力へ継承しない
- `build_pending_results`は入力市場順と1対1対応を維持する
- pending結果は既存4キー順を変更しない
- この伝播工程では`SCHEMA_VERSION = "1.0"`を維持する
- 空入力の結果は引き続き正確な`[]\n`

承認済み完了契約の`2.0`および外部AI実装は、本14キー伝播の実装・レビュー・
実データ検証が完了した後の別工程である。伝播実装と`2.0`移行を同じコミットにしない。

承認済み外部AI設計にある「2.0 pendingを再生成してから外部AIを開始する」という
最終ゲートとは矛盾しない。移行順は、(1) 本工程で14キー入力と1.0 pendingを検証、
(2) 別工程で完了契約に従い2.0 pendingへ移行、(3) 外部検索・AIを開始、である。

## 9. 互換性と移行

### 9.1 旧成果物の扱い

| 旧成果物 | 新コードの扱い |
| --- | --- |
| 9列`markets_*.csv` | 最新として選ばれた場合、`select_candidates.py`が2列不足で停止 |
| 12列`candidates_*.csv` | 最新として選ばれた場合、`prepare_analysis_input.py`が2列不足で停止 |
| 12キー`analysis_input_*.json` | `analyze_market.py`が2キー不足で停止 |
| 既存`analysis_result_*.json` | 書き換えず、その旧入力suffixに対応する過去成果物として保持 |

空文字の暗黙補完、Gamma再取得による穴埋め、旧ファイル自体のmigrationはしない。
新しい`fetch_markets.py`からmarkets、candidates、analysis input、1.0 pending結果を
順に再生成する。

### 9.2 最新選択と新旧混在

各段階は現行どおりファイル名昇順の最後の通常ファイルをまず選び、その1ファイルを
新契約で検証する。最新が旧形式・破損・不正でも、古い有効ファイルへ暗黙fallback
しない。停止して対象ファイル名と契約不足を報告する。

新旧混在を配列要素・CSV行ごとに許可しない。ヘッダーまたは全14必須キーを
ファイル契約として要求する。未知の追加列・追加キーは既存方針に従い許容するが、
`市場説明`と`解決情報源`の欠落を未知キー許容で補えない。

### 9.3 suffixと再生成

- 市場CSVの現行衝突回避名（分、秒、連番）は変更しない
- candidate名はCSV内の統一`取得日時`を分単位へ整形する現行契約を維持する
- analysis input/resultは候補ファイル名の日時suffixを1対1で維持する
- 新しい収集時刻を使うことで、通常は旧analysis resultと別suffixになる
- 同じ正式resultパスが既にある場合も、14キー入力の全検証成功後にだけ1.0 pendingを
  原子的に再生成する。失敗時は既存resultを保持する

旧結果を新14キー入力へ付け替えたり、旧`pending`をそのまま外部AI工程へ渡したりしない。

## 10. 決定性

### 10.1 各段階

- 同じGamma response、同じ`fetched_at`、同じCLOB価格から同じmarkets CSV bytes
- 同じmarkets CSVから同じcandidate順・同じcandidates CSV bytes
- 同じcandidates CSVから同じanalysis input JSON bytes
- 同じanalysis inputから同じ1.0 pending result JSON bytes
- 列・キー定数を単一の正本として固定する
- 市場行、候補ソート、配列順を変更しない
- 正規化はGamma入力境界で一度だけ行う
- 伝播段階では文字列を再正規化しない
- 実行日時、temp名、絶対path、環境依存情報を成果物へ追加しない

Gamma response自体やCLOB価格が変われば別スナップショットの値が変わることは正常で、
外部データの再現性とシリアライズの決定性を混同しない。

### 10.2 byte契約

| 成果物 | encoding | BOM | record/newline | 末尾 |
| --- | --- | --- | --- | --- |
| markets CSV | UTF-8 | あり | レコードCRLF、field内部LF | CSV writerのCRLF |
| candidates CSV | UTF-8 | あり | レコードCRLF、field内部LF | CRLF |
| analysis input JSON | UTF-8 | なし | LF、2スペース | LF |
| pending result JSON | UTF-8 | なし | LF、2スペース | LF |

CSV・JSONとも、全検証と全シリアライズ後に同一ディレクトリの一時ファイルへ書き、
`flush`、`fsync`、close、原子的確定の順とする。正式ファイルを直接truncateしない。
失敗時はtempを削除し、入力と既存正式出力を変更しない。

## 11. 将来実装のテスト観点

### 11.1 `tests/test_fetch_markets.py`

- 正常な`description`と`resolutionSource`を内部候補・CSVへ保持する
- `resolutionSource`の欠落、null、空文字、空白だけを空文字として受理する
- `description`の欠落、null、空、空白、非文字列を市場単位で除外する
- `resolutionSource`非文字列を市場単位で除外する
- CRLFと単独CRをLFへ正規化し、前後空白だけを除去する
- NUL、surrogate、各長さ上限超過を切り詰めず除外する
- 上限ちょうどを受理し、上限+1を拒否する
- HTML、Markdown、カンマ、ダブルクォート、複数行、Unicodeを保持する
- 正式11列順、UTF-8 BOM、レコードCRLF、field内部LFを検証する
- 同じ確定入力からbyte同一、保存失敗時に正式CSVを残さない
- 0市場時の終了コード1と非出力を維持する

### 11.2 `tests/test_select_candidates.py`

- 11列入力を受理し、旧9列を必須列不足で拒否する
- 正式14列順とヘッダーだけの0件出力を検証する
- 2値が入力CSVからbyte/文字列として完全一致する
- 空または不正な市場説明行を除外する
- 空の解決情報源を受理する
- multiline、カンマ、引用符、UnicodeをCSV round-tripする
- 市場説明をカテゴリ・テーマ・ソートに使わない
- 既存の価格・期限境界、分散、重複排除、完全ソート結果が不変である
- 同一入力2回のSHA-256一致、元市場CSVのSHA-256不変
- 書き込み・置換失敗時に既存候補CSVを保持する

### 11.3 `tests/test_prepare_analysis_input.py`

- 14列入力を受理し、旧12列を拒否する
- 14キーの型・固定順を検証する
- 市場説明の欠落、空白、NUL、surrogate、CR、上限超過を拒否する
- 解決情報源の欠落を拒否し、空文字を受理する
- multiline、Unicode、HTML/Markdownを一文字も変更せずJSONへ渡す
- 5数値の現行Decimal正規化を維持する
- 行順と1対1対応、未知追加列許容を維持する
- 0件を正確な`[]\n`にする
- 同一入力2回のJSON SHA-256一致、元候補CSVのSHA-256不変
- 原子的保存とwrite/fsync/replace失敗時の既存JSON保護を検証する

### 11.4 `tests/test_analyze_market.py`

- 正常な14キーを受理し、旧12キーを拒否する
- 市場説明欠落、非string、空、空白、NUL、surrogate、CR、上限超過を拒否する
- 解決情報源欠落・null・非stringを拒否し、空文字を受理する
- 未知キーと入力`schema_version`を許容するが出力へ継承しない
- 未知キー内を含む全階層の重複キーを拒否する
- 市場ID一意性、分析基準日時完全一致、入力順を維持する
- 1入力1pending、4キー順、`SCHEMA_VERSION = "1.0"`を維持する
- 空配列を正確な`[]\n`にする
- 入力JSON SHA-256不変、同一出力SHA-256一致、既存結果保護を検証する

### 11.5 回帰・実データ

- 現行81テストをfixture変更後もすべて成功させ、新規境界テストを追加する
- AI、検索、売買、ウォレット、認証、外部AI依存が追加されていないことを確認する
- 実Gamma/CLOBデータでmarkets -> candidates -> analysis input -> 1.0 pendingを通す
- 各段階の件数、列・キー順、BOM、改行、0件正常系を検証する
- 実行前後の各入力SHA-256が不変であることを確認する
- 同一確定入力を2回処理し、各出力SHA-256が一致することを確認する
- 市場説明を追加しただけでは、同じ適格市場集合に対する既存選別順が変わらないことを
  固定fixtureで確認する

## 12. 設計承認後の実装範囲

リポジトリに実在する次の10ファイルだけを予定変更対象とする。

```text
fetch_markets.py
select_candidates.py
prepare_analysis_input.py
analyze_market.py
tests/test_fetch_markets.py
tests/test_select_candidates.py
tests/test_prepare_analysis_input.py
tests/test_analyze_market.py
README.md
plan.md
```

依存ライブラリは追加せず、Python標準ライブラリと既存`requests`だけを使う。
既存設計書、既存data、`.gitignore`、`requirements.txt`、GitHub Actionsは変更しない。

実装は次の順で行う。

1. 4テストファイルへ失敗テストと新fixtureを追加する
2. `fetch_markets.py`で正規化、除外、11列、原子的CSV保存を実装する
3. `select_candidates.py`で11列入力、14列伝播、原子的CSV保存を実装する
4. `prepare_analysis_input.py`を14列・14キー化する
5. `analyze_market.py`の入力だけを14キー化し、1.0 pending出力を維持する
6. `README.md`と`plan.md`を実装結果に合わせて更新する
7. 全テストと全Python構文確認を行う
8. 実データを一式再生成し、件数・形式・multilineを確認する
9. 入力不変性と同一出力のSHA-256を確認する

## 13. 自己レビュー項目

- 公式仕様上、タイトルではなく解決ルールが重要である根拠を示した
- `description`欠損を収集段階の市場単位除外へ固定した
- `resolutionSource`欠損/nullだけを空文字にし、非文字列は除外へ固定した
- URL補完、要約、翻訳、HTML/Markdown除去を禁止した
- NUL、不正Unicode、CRLF、長さ、切り詰め禁止を固定した
- 現行候補CSVに`取得日時`がある事実を反映し、正しい14列順を固定した
- markets 11列、analysis input 14キーの正確な順と型を固定した
- 旧9列・12列・12キーを暗黙補完せず、最新不正時にfallbackしない
- CSV multilineを標準`csv`と`newline=""`でround-tripする
- 0市場の失敗と候補0件・空配列の正常系を分離した
- `analyze_market.py`のpending 4キーと`SCHEMA_VERSION = "1.0"`を変更しない
- 2.0移行、外部検索、AI実装を別工程にした
- 同一確定データのbyte決定性と外部データの変化を分離した
- CSVを含む原子的保存と既存成果物保護を実装課題として明記した
- 実装対象を実在10ファイルへ限定し、依存追加を禁止した

## 14. 今回の停止点

今回追加するのは本設計書だけである。Python、テスト、README、`plan.md`、依存関係、
既存設計書、dataは変更しない。本設計をcommit・pushした後も`main`へ統合せず、
実装前レビューで停止する。
