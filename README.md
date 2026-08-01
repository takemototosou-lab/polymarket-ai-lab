# polymarket-ai-lab

Polymarketの公開APIから、現在取引中の非スポーツYes/No市場を取得し、時刻付きCSVへ保存する市場データ収集機です。

この段階ではAI予測や売買を行いません。口座、入金、APIキー、ウォレット、秘密鍵は不要です。

## 動作環境

- Python 3.10以上
- インターネット接続

## セットアップ

PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## 実行

```powershell
python fetch_markets.py
```

成功すると、次の形式で新しいCSVが作成されます。

```text
data/markets_YYYY-MM-DD_HHMM.csv
```

同じ分に再実行した場合も、既存ファイルは上書きしません。

## AI分析候補の選別

最新の市場CSVから、AI分析へ渡す候補を最大10件選ぶ場合は次を実行します。

```powershell
python select_candidates.py
```

出力先は、実行日時ではなく元CSVの取得日時に対応する次のファイルです。

```text
data/candidates_YYYY-MM-DD_HHMM.csv
```

元の市場CSVは変更しません。同じ入力を再実行すると同じ候補ファイルを上書きし、内容はバイト単位で一致します。

### 候補条件

- YES価格が0.10以上0.90以下（境界を含む）
- 元CSV各行の取得日時から締切までが7日以上90日以下（境界を含む）
- 出来高の高い順を基本とする
- 同一テーマは正規化したURLで判定する
- カテゴリとテーマへの偏りを3段階で抑える
- 最大10件

価格と期限の条件は自動的に緩和しません。該当市場が0件になることは正常です。その場合も、UTF-8 BOM付きのヘッダーだけの候補CSVを出力し、`候補0件` と表示して終了コード0で終了します。

元CSV内の取得日時が1文字でも異なる場合は入力不正とし、候補CSVを作成または上書きしません。

### カテゴリ優先順位

市場名とURLが複数カテゴリの固定キーワードに一致した場合は、次の優先順位で最初に一致したカテゴリを採用します。

| 優先順位 | カテゴリ | 固定キーワード |
| ---: | --- | --- |
| 1 | 政治 | election, president, presidential, nominee, nomination, congress, senate, governor, prime minister, parliament, vote |
| 2 | 国際情勢 | war, invasion, invade, ceasefire, iran, israel, ukraine, russia, china, taiwan, nato, greenland |
| 3 | 暗号資産 | bitcoin, btc, ethereum, eth, crypto, solana, token |
| 4 | 経済・金融 | federal reserve, fed, interest rate, inflation, recession, gdp, unemployment, stock, s&p, nasdaq, market cap |
| 5 | テクノロジー | artificial intelligence, openai, spacex, tesla, iphone, apple, google, microsoft, ai |
| 6 | エンタメ | album, movie, film, box office, gta, game, oscar, grammy, music |
| 7 | 科学・健康 | nasa, alien, vaccine, disease, covid, health, drug, medicine |
| 8 | その他 | 上記に一致しない市場 |

キーワード一覧と優先順位の正本は、`select_candidates.py` の `CATEGORY_RULES` 定数です。キーワードは英数字の単語境界で照合するため、例えば `ai` は `said` の一部には一致しません。

### テーマと分散規則

テーマ用URLはschemeとhostを小文字化し、queryとfragmentを除去し、path末尾のスラッシュを除去します。URLが欠損または不正な場合は市場IDをテーマ識別子として使います。

候補は次の順で補完します。

1. 同一テーマ1件、同一カテゴリ2件まで
2. カテゴリ上限だけを解除し、同一テーマ1件を維持
3. テーマ上限も解除し、残りを出来高順で補完

どの段階でも価格と期限の条件は緩和しません。同率時は市場ID、正規化テーマ、市場名、締切日、価格、流動性、元CSV行番号まで含む固定キーで順序を確定します。

## AI分析入力JSONの準備

最新の候補CSVを、後続のAI分析が読み込む固定形式のJSONへ変換する場合は次を実行します。

```powershell
python prepare_analysis_input.py
```

出力先は入力候補CSVと同じ日時に対応する次のファイルです。

```text
data/analysis_input_YYYY-MM-DD_HHMM.json
```

この処理は純粋なCSVからJSONへの変換です。AI APIの呼び出し、ニュース検索、確率予測、売買、認証処理は行いません。

### 入力契約

`data/candidates_*.csv` のうち、ファイル名昇順で最後のファイルを入力にします。選択されたファイル名は `candidates_YYYY-MM-DD_HHMM.csv` 形式の実在する日時でなければなりません。

次の14列が必須です。追加列は無視します。

```text
取得日時,市場ID,市場,市場説明,解決情報源,YES価格,NO価格,出来高,流動性,締切日,URL,カテゴリ,締切までの日数,選定理由
```

1件以上ある場合、全行の `取得日時` は文字列として完全一致し、タイムゾーン付きISO 8601である必要があります。市場IDの空値、必須値の欠損、数値の空文字列・非数値・NaN・Infinityも入力不正として扱います。

### JSON出力契約

配列内の市場順は入力CSV順を維持します。市場オブジェクトのキー順は `prepare_analysis_input.py` の `JSON_KEYS` 定数で次のように一元管理します。

```text
市場ID
市場
市場説明
解決情報源
YES価格
NO価格
出来高
流動性
締切日
カテゴリ
締切までの日数
URL
分析基準日時
選定理由
```

`市場説明` は非空文字列、`解決情報源` は空文字を許容する文字列です。CSVから読み込んだ改行やHTML・Markdownを再整形せず、そのままJSON stringへ渡します。`分析基準日時` は入力の `取得日時` を表記も含めてそのまま保持します。YES価格、NO価格、出来高、流動性、締切までの日数の5項目は `Decimal` で有限数として厳密に解析し、JSON numberとして出力します。

数値は丸めず、指数表記と不要な先頭・末尾ゼロを除去します。先頭の `+` と先頭ゼロ付き数値は受け入れ、例えば `+01.20` は `1.2`、`1E+3` は `1000`、`-0.00` は `0` になります。

JSONはUTF-8 BOMなし、2スペースインデント、LF改行、末尾LFで保存します。同じ入力からはバイト単位で同じ結果を生成します。候補0件は正常系で、正確な3バイト `[]\n` を出力し、`分析入力0件` と表示して終了コード0で終了します。

入力検証とJSON生成がすべて成功してから同一ディレクトリの一時ファイルへ保存し、最後に置換します。入力不正、書き込み失敗、置換失敗では既存JSONを変更しません。

## AI分析結果JSONの準備

最新の分析入力JSONから、後続のAI分析が更新する結果契約を作成する場合は次を実行します。

```powershell
python analyze_market.py
```

出力名は入力名の日時から決定します。

```text
入力: data/analysis_input_YYYY-MM-DD_HHMM.json
出力: data/analysis_result_YYYY-MM-DD_HHMM.json
```

初期版はAI分析機ではなく、全市場を `pending` として出力する契約固定機です。AI、Web検索、ニュース検索、確率予測、売買、認証、外部通信は行いません。

### 分析結果の入力契約

`data/analysis_input_*.json` のうち、ファイル名昇順で最後の通常ファイルを入力にします。トップレベルは配列で、各要素には分析入力JSONの次の14キーが必要です。

```text
市場ID,市場,市場説明,解決情報源,YES価格,NO価格,出来高,流動性,締切日,カテゴリ,締切までの日数,URL,分析基準日時,選定理由
```

市場ID、各文字列、5個の有限なJSON numberを型検証します。`市場説明` は正規化済みの非空文字列、`解決情報源` は空文字を許容する文字列として検証します。市場IDは空でなくファイル内で一意、全市場の `分析基準日時` は同じ文字列でなければなりません。`分析基準日時` はタイムゾーン付きISO 8601とし、`+09:00` と `Z` を受理して表記をそのまま保持します。タイムゾーンなし、日付だけ、不正な暦日・時刻は拒否します。

未知キーは将来互換のため許容しますが、出力へコピーしません。入力に `schema_version` があっても未知キーとして扱い、出力バージョンには使用しません。JSON内の重複キーは、未知キーの値に含まれる入れ子オブジェクトを含むすべての階層で拒否します。

### 分析結果の出力契約

入力順と1対1対応を維持し、各市場を次の固定キー順で出力します。

```text
schema_version
market_id
analysis_reference_time
status
```

`schema_version` は `analyze_market.py` の単一定数 `SCHEMA_VERSION = "2.0"` だけから設定し、同一ファイル内の全市場で統一します。`status` は初期版では常に `pending` です。`pending` は2.0でも上記4キーだけを持ち、`completed` と `error` の結果項目は追加しません。

同じ日時suffixの既存結果がなければ2.0 pendingを新規生成します。既存結果が全件1.0 pendingの場合は、固定4キーと型、件数、入力順、市場ID、分析基準日時をすべて検証してから、全件を一度に2.0 pendingへ原子的に置換します。全件2.0 pendingの場合も同じ検証後に決定的に再生成します。1.0／2.0の混在、未知version、キー不足・未知キー・型不正、入力との不一致、`completed` または `error` を含む結果は拒否し、既存結果を変更しません。

JSONはUTF-8 BOMなし、2スペースインデント、LF改行、末尾LFで保存します。キー順と入力順を固定し、同じ入力からバイト単位で同じ出力を生成します。

入力0件は正常系で、正確な3バイト `[]\n` を出力し、`分析結果0件` と表示して終了コード0で終了します。トップレベル配列を維持するため、空配列内には `schema_version` を保持できません。空配列の契約バージョンは、生成に使用したコードと設計書で管理します。

入力の読み込みと検証、結果生成、JSON生成がすべて成功してから同一ディレクトリの一時ファイルへ保存し、最後に原子的に置換します。入力不正、書き込み失敗、置換失敗では既存の分析結果JSONを変更しません。元の分析入力JSONも変更しません。

## 取得条件

- Gamma API上で有効
- 未終了で注文受付中
- 締切日が取得時点より後
- 累計出来高10,000ドル以上
- 流動性5,000ドル以上
- outcomeがYes/Noの二択
- Yes/No両方のCLOB token IDとmidpointを取得可能
- スポーツタグやスポーツ固有項目を持たない
- Gamma APIの非空な `description` を持つ

条件を満たす市場を累計出来高順に最大100件保存します。公開中の市場や板の状態によっては100件未満になる場合があり、その場合は警告を表示します。

## CSV列

```text
取得日時,市場ID,市場,市場説明,解決情報源,YES価格,NO価格,出来高,流動性,締切日,URL
```

- 文字コード: UTF-8 BOM
- 取得日時: 日本時間（UTC+09:00）のISO 8601
- YES価格・NO価格: CLOB板の最良買値と最良売値から計算されたmidpoint
- 出来高・流動性: Gamma APIの値
- 市場説明: Gamma APIの `description`。CRLF・CRだけをLFへ統一し、前後空白を除去
- 解決情報源: Gamma APIの `resolutionSource`。欠落・nullは空文字、URLであることは必須にしない

`市場説明`は最大262,144 Unicodeコードポイント、`解決情報源`は最大32,768コードポイントです。NUL、不正Unicode、非文字列、上限超過は市場単位で除外し、内容の切り詰め、要約、翻訳、HTML・Markdown除去は行いません。CSVの値内部にあるLF、カンマ、ダブルクォートは標準`csv`のquotingで保持します。

新しい収集機は11列markets CSVだけを生成します。旧9列markets CSV、旧12列candidates CSV、旧12キーanalysis inputは暗黙補完せず、各後続処理で入力不正として停止します。最新ファイルが旧形式でも古い有効ファイルへ自動フォールバックしません。新しい収集から成果物を一式再生成してください。

CSVは取得時点のスナップショットです。過去の検証では、後から現在価格で置き換えず、生成されたファイルをそのまま使用してください。

## エラー処理

- HTTP 429、5xx、タイムアウト、一時的な接続失敗を最大3回再試行します。
- CLOB価格の一括取得に失敗した場合は、バッチを分割して取得可能な市場の処理を続けます。
- 欠損値、不正値、無効な市場説明・解決情報源を含む個別市場は除外し、他市場の処理を続けます。
- スポーツタグ一覧や最初の市場ページを取得できない場合は、安全にスポーツを除外できないためCSVを作成せず終了します。

## テスト

```powershell
python -m unittest discover -s tests -v
```

単体テストは外部APIへ接続しません。収集機の再試行、絞り込み、価格対応、重複除外、11列CSV、metadata正規化、multiline、候補選別機の11列入力・14列出力、境界値、取得日時基準、URL正規化、分散選択、0件出力に加え、分析入力変換機の14列・14キー伝播、数値正規化と、分析結果契約機の14キー入力、全階層重複キー検出、ISO 8601受理形式、1.0から2.0への一括移行、混在・不正・状態境界の拒否、固定キー順、原子的保存、短い書き込みを含む既存出力保護、バイト単位の決定性を確認します。

## 使用API

- [Polymarket API概要](https://docs.polymarket.com/api-reference/introduction)
- [Gamma API market keyset pagination](https://docs.polymarket.com/api-reference/markets/list-markets-keyset-pagination)
- [Gamma API sports metadata](https://docs.polymarket.com/api-reference/sports/get-sports-metadata-information)
- [CLOB API midpoint batch endpoint](https://docs.polymarket.com/api-reference/market-data/get-midpoint-prices-request-body)
- [APIレート制限](https://docs.polymarket.com/api-reference/rate-limits)

公開読み取りAPIだけを使用します。API利用料はなく、認証も不要です。実際の売買・入出金に伴う費用は本プロジェクトの対象外です。

## 対象外

- AIによる確率予測
- ニュース検索
- 自動売買
- 定期実行
- データベース
- GUI
