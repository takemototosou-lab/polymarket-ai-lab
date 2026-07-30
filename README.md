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

## 取得条件

- Gamma API上で有効
- 未終了で注文受付中
- 締切日が取得時点より後
- 累計出来高10,000ドル以上
- 流動性5,000ドル以上
- outcomeがYes/Noの二択
- Yes/No両方のCLOB token IDとmidpointを取得可能
- スポーツタグやスポーツ固有項目を持たない

条件を満たす市場を累計出来高順に最大100件保存します。公開中の市場や板の状態によっては100件未満になる場合があり、その場合は警告を表示します。

## CSV列

```text
取得日時,市場ID,市場,YES価格,NO価格,出来高,流動性,締切日,URL
```

- 文字コード: UTF-8 BOM
- 取得日時: 日本時間（UTC+09:00）のISO 8601
- YES価格・NO価格: CLOB板の最良買値と最良売値から計算されたmidpoint
- 出来高・流動性: Gamma APIの値

CSVは取得時点のスナップショットです。過去の検証では、後から現在価格で置き換えず、生成されたファイルをそのまま使用してください。

## エラー処理

- HTTP 429、5xx、タイムアウト、一時的な接続失敗を最大3回再試行します。
- CLOB価格の一括取得に失敗した場合は、バッチを分割して取得可能な市場の処理を続けます。
- 欠損値や不正値を含む個別市場は除外し、他市場の処理を続けます。
- スポーツタグ一覧や最初の市場ページを取得できない場合は、安全にスポーツを除外できないためCSVを作成せず終了します。

## テスト

```powershell
python -m unittest discover -s tests -v
```

単体テストは外部APIへ接続しません。収集機の再試行、絞り込み、価格対応、重複除外、CSVエンコーディングに加え、候補選別機の境界値、取得日時基準、URL正規化、分散選択、0件出力、入力不正、バイト単位の決定性を確認します。

## 使用API

- [Polymarket API概要](https://docs.polymarket.com/api-reference/introduction)
- [Gamma API market keyset pagination](https://docs.polymarket.com/api-reference/markets/list-markets-keyset-pagination)
- [Gamma API sports metadata](https://docs.polymarket.com/api-reference/sports/get-sports-metadata-information)
- [CLOB API midpoint batch endpoint](https://docs.polymarket.com/api-reference/market-data/get-midpoint-prices-request-body)
- [APIレート制限](https://docs.polymarket.com/api-reference/rate-limits)

公開読み取りAPIだけを使用します。API利用料はなく、認証も不要です。実際の売買・入出金に伴う費用は本プロジェクトの対象外です。

## 対象外

- AIによる確率予測
- 自動売買
- 定期実行
- データベース
- GUI
