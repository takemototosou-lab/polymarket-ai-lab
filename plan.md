# polymarket-ai-lab 計画

## 現在の段階

AI予測へ進む前に、市場価格を再現可能な時刻付きスナップショットとして保存し、固定条件で分析候補を最大10件へ絞り込み、決定的なJSON入力と初期状態 `pending` の分析結果契約へ変換する。

## 完了

- [x] 公開Gamma APIから市場をkeyset paginationで取得
- [x] 公開CLOB APIからYes/No midpointを一括取得
- [x] 現在取引中・将来締切・Yes/No二択へ限定
- [x] 累計出来高10,000ドル以上・流動性5,000ドル以上へ限定
- [x] スポーツタグとスポーツ固有項目でスポーツを除外
- [x] 最大100件を累計出来高順に選択
- [x] UTF-8 BOM付き時刻別CSVを上書きなしで保存
- [x] 通信再試行と個別欠損の継続処理
- [x] Gamma APIの`description`と`resolutionSource`を固定規則で正規化
- [x] 非空の市場説明を必須化し、解決情報源の欠落・nullを空文字化
- [x] markets CSVを固定11列へ拡張し、multilineを標準CSVで保持
- [x] markets / candidates CSVを一時ファイルから原子的に保存
- [x] markets CSVを排他的ハードリンクで公開し、同時実行時は衝突回避名を再選択
- [x] markets / candidates CSVの短い書き込みを検出し、既存正式出力を保護
- [x] 単体テスト
- [x] 最新市場CSVから最大10件を選ぶ候補選別機
- [x] YES価格0.10～0.90、締切7～90日の厳格条件
- [x] 元CSV各行の取得日時を基準にした締切日数計算
- [x] カテゴリ・テーマの3段階分散選択
- [x] URL正規化と不正URLの市場IDフォールバック
- [x] カテゴリキーワードと優先順位の一元管理
- [x] 完全ソートキーと市場ID重複排除
- [x] 候補0件のヘッダーのみ出力・正常終了
- [x] 11列markets入力から市場説明・解決情報源を変更せず14列候補へ伝播
- [x] 旧9列markets入力を暗黙補完せず拒否
- [x] 同一入力のバイト単位で決定的な再出力
- [x] 取得日時不統一と入力不正時の非出力・非上書き
- [x] 最新候補CSVを固定契約のJSONへ変換する分析入力準備機
- [x] 必須14列・取得日時完全一致・タイムゾーン付きISO 8601の入力検証
- [x] 14キー分析入力へ市場説明・解決情報源を変更せず伝播
- [x] 旧12列候補入力を暗黙補完せず拒否
- [x] 5数値項目の有限Decimal解析と固定JSON number表現
- [x] 入力行順維持と`JSON_KEYS`による固定キー順
- [x] UTF-8 BOMなし・LF・2スペース・末尾LFの固定出力
- [x] 候補0件の正確な`[]\n`出力・正常終了
- [x] 一時ファイルと原子的置換による既存JSON保護
- [x] 最新分析入力JSONから固定契約の分析結果JSONを生成する契約固定機
- [x] 必須14キー・型・市場ID一意性・分析基準日時完全一致の入力検証
- [x] 市場説明の非空検証と解決情報源の空文字受理
- [x] 旧12キー分析入力を暗黙補完せず拒否
- [x] 未知キーを許容しつつ、全オブジェクト階層の重複JSONキーを拒否
- [x] `+09:00`と`Z`を受理するタイムゾーン付きISO 8601検証
- [x] 入力順と1対1対応を維持した全市場`pending`出力
- [x] `SCHEMA_VERSION = "2.0"`と`RESULT_KEYS`によるバージョン・4キー順の一元管理
- [x] 全件1.0 pendingの検証と2.0 pendingへの原的一括移行
- [x] 全件2.0 pendingの検証・決定的再生成
- [x] version混在・未知version・不正結果・`completed`・`error`の安全な拒否
- [x] 入力の`schema_version`を継承しない責務境界
- [x] UTF-8 BOMなし・LF・2スペース・末尾LFの固定出力
- [x] 分析結果0件の正確な`[]\n`出力・正常終了
- [x] 原子的置換と書き込み・置換失敗時の既存結果JSON保護

## 実API検証

- 取得日時: 2026-07-30T22:00:31.247975+09:00
- 取得件数: 100件
- YES価格範囲: 0.0005～0.495
- NO価格範囲: 0.505～0.9995
- 価格範囲外: 0件
- 締切日欠損数: 0件
- 市場ID欠損数: 0件
- 市場ID重複数: 0件
- UTF-8 BOM: `EF BB BF`を確認
- UTF-8再読込: 厳密UTF-8復号と全100行のCSV再読込に成功
- 文字化け兆候: 0件
- 非ASCII市場名: 1件を正しく再読込

生成CSVは実行ごとのスナップショットであり、`.gitignore`対象のためGitへは登録しない。

## 候補選別機の実データ検証

- 入力CSV: `data/markets_2026-07-30_2204.csv`
- 入力件数: 100件
- 候補件数: 0件（価格・期限の厳格条件を緩和せず正常終了）
- 出力CSV: `data/candidates_2026-07-30_2204.csv`
- 出力形式: 12列のヘッダーのみ（metadata伝播前の旧契約時）
- UTF-8 BOM: `EF BB BF`を確認
- 元CSV SHA-256: 実行前後とも`5BAA10A4657424C7F81DB9D9ADDE753B61FD12B32F69BAD50113535CC38A179A`
- 候補CSV SHA-256: 2回の実行とも`DED0092267A2E3187E9AB52DC5D6F3ED1AD38A6ED86549A7ECFD8602F066FC8A`
- 単体テスト: 既存17件を含む全36件に成功

候補0件は異常ではない。自動的な条件緩和は行わず、後続処理が扱えるヘッダー付きCSVを残す。

## 分析入力変換機の実データ検証

- 入力CSV: `data/candidates_2026-07-30_2204.csv`
- 入力件数: 0件
- 出力JSON: `data/analysis_input_2026-07-30_2204.json`
- 終了コード: 2回とも0
- 出力内容: 正確な3バイト `5B 5D 0A`（`[]\n`）
- UTF-8 BOM: なし
- 入力CSV SHA-256: 実行前後とも`DED0092267A2E3187E9AB52DC5D6F3ED1AD38A6ED86549A7ECFD8602F066FC8A`
- 出力JSON SHA-256: 2回とも`37517E5F3DC66819F61F5A7BB8ACE1921282415F10551D2DEFA5C3EB0985B570`
- 単体テスト: 既存36件を含む全53件に成功

候補0件でも入力契約を満たす空配列JSONを正常に残す。条件緩和、AI API呼び出し、ニュース検索、確率予測は行わない。

## 分析結果契約機の実データ検証

- 入力JSON: `data/analysis_input_2026-07-30_2204.json`
- 入力件数: 0件
- 出力JSON: `data/analysis_result_2026-07-30_2204.json`
- 終了コード: 2回とも0
- 出力内容: 正確な3バイト `5B 5D 0A`（`[]\n`）
- UTF-8 BOM: なし
- 入力JSON SHA-256: 実行前後とも`37517E5F3DC66819F61F5A7BB8ACE1921282415F10551D2DEFA5C3EB0985B570`
- 出力JSON SHA-256: 2回とも`37517E5F3DC66819F61F5A7BB8ACE1921282415F10551D2DEFA5C3EB0985B570`
- 単体テスト: 既存53件を含む全81件に成功

分析対象0件でもトップレベル配列を維持し、空配列JSONを正常に残す。空配列内にはバージョン情報を保持できないため、契約バージョンはコード定数と設計書で管理する。AI、Web検索、ニュース検索、確率予測、売買、認証、外部通信は行わない。

## 市場説明・解決情報源伝播の実データ検証

- 入力API: 公開Gamma keyset API / 公開CLOB API
- 市場CSV: `data/markets_2026-07-31_2356.csv`
- 取得件数: 100件
- 市場CSV列数: 11列、固定順一致
- UTF-8 BOM: あり
- 市場説明空欄: 0件
- 解決情報源空欄: 100件（契約どおり有効）
- 市場説明内LF: 100件を標準CSVで再読込成功
- metadata内CR / NUL: 0件
- 市場説明最大長: 1,530 Unicodeコードポイント
- 候補CSV: `data/candidates_2026-07-31_2356.csv`
- 候補件数: 0件（厳格条件を緩和せず正常終了）
- 候補CSV列数: 14列のヘッダーのみ
- 分析入力 / pending結果: どちらも正確な`5B 5D 0A`（`[]\n`）
- 市場CSV SHA-256: 実行前後とも`27DE7D2841C1AC07E0446102677D1207FC160C2505F7E7D143161083FDD49DB2`
- 候補CSV SHA-256: 2回とも`243B1734AF198E0144CFF9B00BC7D158CE3D484E2CC6D1D0F593DFDF6DD66D37`
- 分析入力 / 結果 SHA-256: 2回とも`37517E5F3DC66819F61F5A7BB8ACE1921282415F10551D2DEFA5C3EB0985B570`
- 各後続処理で入力ファイルSHA-256不変を確認
- 単体テスト: 全101件に成功

候補0件のため実データJSONは空配列だが、非空metadataの14列・14キー伝播、
multiline、旧形式拒否、伝播工程時点のpending 1.0維持は単体テストfixtureで確認した。

## pending schema 2.0移行の実データ検証

- 検証場所: 一時data directory（既存data成果物は変更なし）
- 公開Gamma / CLOB APIから市場100件を取得
- markets CSV: 11列、candidates CSV: 14列
- 候補件数: 0件（厳格条件を緩和せず正常終了）
- analysis input / 2.0 pending結果: どちらも正確な`5B 5D 0A`（`[]\n`）
- 結果SHA-256: 2回とも`37517E5F3DC66819F61F5A7BB8ACE1921282415F10551D2DEFA5C3EB0985B570`
- markets / candidates / analysis inputのSHA-256: 実行前後で不変
- 隔離した1件fixture: 全件1.0 pendingから2.0 pendingへ一括移行
- fixture再実行: 2.0結果のSHA-256一致、分析入力SHA-256不変
- 単体テスト: 全109件に成功

候補0件は正常系であり、条件緩和は行わない。2.0 pendingは4キーのままで、外部検索、AI推論、`completed`、`error`、売買は実装していない。

## 外部AI Phase 1 foundation

- [x] `run_external_analysis.py` dry-run CLIを追加
- [x] frozenな設定dataclassとmapping注入を追加
- [x] provider `fake`、dry-run `true`、対象1市場、reasoning effort `low`を既定化
- [x] 市場数1～10、hard max 10、非canonical表記拒否
- [x] 最新14キー入力と同suffixの全件2.0 pending結果を厳格照合
- [x] 入力順先頭N件を選択し、15項目のfrozen `AnalysisRequest`へ変換
- [x] provider protocolとネットワークを使わない固定fake providerを追加
- [x] basename・設定・件数・市場IDだけの決定的dry-run stdoutを追加
- [x] 終了コード0（正常）、1（設定不正）、2（契約不正）、3（段階未到達）を固定
- [x] data全通常ファイルのSHA-256不変、一時ファイル・log・lockなしを検証
- [x] APIキーを読み取らず、存在確認もしないことを監視mappingで検証
- [x] socket・HTTP入口を呼ばず外部通信しないことを検証
- [x] 既存109件を含む全138テスト成功

Phase 1はOpenAI、Brave Search、その他の有料APIを呼ばず、trial、free credit、無料枠も利用しない。課金・契約・プラン・請求・支払い方法・自動継続課金を変更せず、料金発生経路を持たない。`POLYMARKET_AI_DRY_RUN=false`と`POLYMARKET_AI_PROVIDER=openai`は終了コード3で拒否する。completed/error、analysis result更新、売買も未実装である。

Phase 2・3で有料APIを実装する場合は別設計レビューと利用者の明示承認を必須とする。APIキーの存在だけでは実行しない。dry-run既定値を`true`に維持し、provider、対象市場数、最大request数、最大token数、最大予算を課金前に表示する。最大予算未設定または超過可能性がある場合は拒否し、市場数、retry数、token上限、予算を自動増加させない。無料枠を前提とせず、契約・プラン・請求設定・支払い方法・自動継続課金を変更しない。

## 公共仕様との差異

Gamma APIのkeyset仕様ページでは並び順の例が`volume_num`だが、2026-07-30時点の実APIはこの値をHTTP 422で拒否し、JSONフィールド名の`volumeNum`を受理した。実測結果を確認し、利用者承認のうえ`volumeNum`を採用した。

## 次の段階

- [ ] 収集を定期実行して履歴を蓄積
- [x] 候補CSVをAI分析用の固定JSON形式へ変換
- [x] 分析入力JSONから`pending`の分析結果契約を生成
- [x] `completed`・`error`とAI分析結果項目の次期契約を設計
- [x] 外部検索・AI分析パイプラインを設計
- [x] 市場説明・解決情報源を11列→14列→14キーへ伝播
- [x] `SCHEMA_VERSION = "2.0"`へpending結果を移行
- [x] 外部AI Phase 1 foundation（通信なしdry-run）
- [ ] Phase 2A/2B/2Cのsafe URL fetcher・SSRF・Brave設計レビュー（Draft作成、承認待ち）
- [ ] 固定JSONの最大10市場だけをAIで分析
- [ ] AI推定確率と市場価格の差を記録
- [ ] 決着後に精度と収益性を評価

AI分析対象は、収集機が安定した後に10件、30件、100件の順で増やす。
