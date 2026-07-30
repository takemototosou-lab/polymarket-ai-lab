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

単体テストは外部APIへ接続せず、通信応答を制御して再試行、絞り込み、価格対応、重複除外、CSVエンコーディングを確認します。

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
