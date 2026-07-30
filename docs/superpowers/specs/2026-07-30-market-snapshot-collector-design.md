# Polymarket市場スナップショット収集機 設計書

## 目的

認証不要のPolymarket公開APIから、現在取引可能な非スポーツのYes/No市場を約100件取得し、取得時点の価格と市場情報を時刻付きCSVへ保存する。AI予測、売買、口座接続、秘密鍵の取り扱いは対象外とする。

## 成果物

リポジトリ直下に以下を配置する。

```text
polymarket-ai-lab/
├─ fetch_markets.py
├─ data/
│  └─ .gitkeep
├─ tests/
│  └─ test_fetch_markets.py
├─ requirements.txt
├─ README.md
├─ plan.md
└─ docs/
   └─ superpowers/
      └─ specs/
         └─ 2026-07-30-market-snapshot-collector-design.md
```

`tests/`と`docs/`は、要求された実行ファイル群を保ったまま、品質確認と設計根拠を残すために追加する。

## 使用する公共仕様

- Gamma API: `https://gamma-api.polymarket.com`
- CLOB API: `https://clob.polymarket.com`
- 市場一覧はGamma APIのkeyset paginationを使う。
- スポーツ識別情報はGamma APIの`/sports`を使う。
- 価格はCLOB APIの`POST /midpoints`を使う。
- いずれも公開読み取りAPIのみを使用し、認証情報は使用しない。

旧offset paginationは今後非推奨となるため、新規実装では`after_cursor`と`next_cursor`を用いるkeyset paginationを優先する。

## 取得条件

市場は次の条件をすべて満たすものに限定する。

- `active`が真
- `closed`が偽
- `acceptingOrders`が真
- `endDate`が存在し、取得時点より後
- 累計出来高が10,000ドル以上
- 流動性が5,000ドル以上
- outcomeが`Yes`と`No`の二択
- Yes/NoそれぞれのCLOB token IDが存在する
- Yes/NoそれぞれのCLOB midpointが取得でき、0以上1以下
- スポーツ市場ではない

スポーツ市場は、`/sports`から得たタグIDと市場・イベントのタグを照合し、加えて`gameId`、`gameStartTime`、`sportsMarketType`などのスポーツ固有項目が存在する場合も除外する。タグ情報の欠損時にもスポーツ固有項目を補助判定として使う。

条件を満たす候補を累計出来高の降順に並べ、上位100件を保存する。API側の状況により100件未満しか得られない場合は、得られた件数を保存し、終了時に不足を明示する。

## データフロー

1. 日本時間の取得日時を確定する。
2. Gamma API `/sports`からスポーツタグIDを取得する。
3. Gamma APIの市場keyset endpointを、出来高・流動性・終了日の下限条件付きでページングする。
4. 各市場をローカル条件で検証し、スポーツと不完全なレコードを除外する。
5. 候補市場のYes/No token IDをCLOB API `POST /midpoints`へ小分けして送る。
6. 両価格が有効な市場だけを累計出来高順に確定する。
7. 最大100件をUTF-8 BOM付きCSVへ保存する。
8. 保存先と取得件数、除外・警告件数を標準出力へ表示する。

同じ市場IDは1回だけ保存する。URLは`https://polymarket.com/event/{event-slug}`を優先し、イベントslugがなければ`https://polymarket.com/event/{market-slug}`を使う。

## CSV仕様

ファイル名:

```text
data/markets_YYYY-MM-DD_HHMM.csv
```

同じ分に複数回実行して既存ファイルがある場合は、既存スナップショットを上書きしないよう、末尾に秒または連番を付ける。

列順:

```text
取得日時,市場ID,市場,YES価格,NO価格,出来高,流動性,締切日,URL
```

- 文字コード: UTF-8 BOM
- 取得日時: ISO 8601、日本時間のオフセット付き
- 市場ID: Gamma APIの市場`id`
- 市場: Gamma APIの`question`
- YES価格・NO価格: CLOB midpointの小数値
- 出来高・流動性: Gamma APIの数値
- 締切日: APIの値をISO 8601として保存

## エラー処理

- HTTP 429、5xx、タイムアウト、一時的な接続失敗は指数的に待機して最大3回試行する。
- スポーツタグ一覧の取得に失敗した場合、スポーツ除外の確実性を満たせないためCSVを作らず異常終了する。
- 市場一覧の最初のページが取得できない場合はCSVを作らず異常終了する。
- 途中ページの取得失敗は警告を出し、それまでの候補で処理を継続する。
- 個別市場の欠損、JSON文字列の不正、数値変換失敗はその市場だけを除外する。
- CLOBの一括価格取得失敗はバッチを分割して再試行し、最終的に取得できないtokenを含む市場だけを除外する。
- 0件の場合は空CSVを正常成果物として扱わず、原因を確認できるメッセージと非0終了コードを返す。

## テスト方針

外部APIそのものではなく、本実装が保証する境界と出力を検証する。

- JSON文字列になっているoutcomeとtoken IDを正しく解析できる。
- 条件を満たす市場だけを残せる。
- スポーツタグとスポーツ固有項目の市場を除外できる。
- 欠損値・不正値を含む市場だけを除外し、処理を継続できる。
- CLOB midpointをYes/No tokenへ正しく対応付ける。
- 市場IDの重複を除外し、出来高順で100件に制限できる。
- CSV列順、UTF-8 BOM、日本語・非ASCII文字、上書き防止を検証する。
- HTTP再試行は待機関数と通信関数を注入し、実時間待機なしで検証する。

単体テストはPython標準ライブラリの`unittest`を使用する。実APIを使う確認は単体テストと分離し、完成時に`fetch_markets.py`を実行してCSVを検査する。

## 依存関係

HTTP通信には`requests`のみを使用する。テスト、CSV、JSON、日時、パス処理にはPython標準ライブラリを使用し、不要なライブラリは追加しない。

## 完了条件

- コマンド1回でCSVが生成される。
- 取得件数が100件、または不足理由が明示される。
- 全価格が0以上1以下である。
- 全行に締切日と市場IDがある。
- 市場IDの重複がない。
- UTF-8 BOMがあり、CSVをUTF-8として再読込でき、市場名が保持される。
- 通信エラーと欠損値で全体が不必要に停止しない。
- READMEにセットアップ、実行方法、条件、注意事項が記載される。
- plan.mdに実装結果と検証結果が反映される。
- GitHub上に公開リポジトリ`polymarket-ai-lab`が作成され、検証済みの内容が保存される。

## 対象外

- AIによる確率予測
- 自動売買
- 認証API
- ウォレット、入金、秘密鍵
- 定期実行
- データベース
- GUI
