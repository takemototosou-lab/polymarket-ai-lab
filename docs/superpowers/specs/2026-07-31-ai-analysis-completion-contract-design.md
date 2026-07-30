# AI分析完了・エラー結果契約 設計仕様

## 1. 目的

`analysis_result_*.json` の市場単位状態を、初期状態 `pending` から
`completed` または `error` へ安全に遷移させるためのJSON契約を固定する。

本仕様は次を定義する。

- `pending`、`completed`、`error` の状態別キー、型、順序、不変条件
- 市場単位の状態遷移と、ファイル全体の処理失敗の境界
- 予測確率、市場価格との差、根拠、反対材料、情報源、モデル情報
- 契約・シリアライズの決定性
- 外部検索・AI推論の再現性と、その非保証範囲
- 既存 `1.0` から新契約への移行方針
- 再実行、再試行、部分成功、原子的保存

本仕様の段階では実装コード、テストコード、README、`plan.md`、
依存関係を変更しない。外部検索方式、AIベンダー、具体的なモデル、
プロンプト本文、API呼び出し方式は別設計とする。

## 2. 現状と責務境界

現在の `SCHEMA_VERSION = "1.0"` は、各市場について次の4キーだけを
出力し、状態は `pending` だけを生成する。

```json
{
  "schema_version": "1.0",
  "market_id": "12345",
  "analysis_reference_time": "2026-07-30T22:04:49.568055+09:00",
  "status": "pending"
}
```

トップレベルは配列で、入力市場順と1対1対応を維持する。0件は正確な
3バイト `[]\n` である。この基本構造と0件契約は新契約でも維持する。

本仕様が扱うのは分析結果の記録であり、次を行わない。

- 売買、注文、注文候補の自動執行
- ウォレット接続、入出金、Polymarket認証
- APIキー、秘密鍵、認証トークンの結果JSONへの保存
- AI分析結果から売買数量や注文価格を決定すること
- 外部検索・AI推論の具体実装を確定すること

分析結果は助言・参考情報であり、自動執行の指示ではない。AI推論と
売買判断は別責務とする。

## 3. 採用方針と不採用案

### 3.1 採用: 状態別の厳格な単一配列契約

トップレベル配列と共通4キーを維持し、`status` に応じて許可する
追加キーを固定する。各状態の必須キーはすべて必須とし、任意キーや
未知キーは許可しない。

この方式は、後続処理が `status` ごとの完全性を機械的に検証でき、
部分的なAI出力を `completed` と誤認しにくい。

### 3.2 不採用: `1.1` で既存契約へ追加

`completed` と `error` は状態名として予約済みだが、`1.0` の消費側は
4キーの `pending` だけを前提にできる。状態別の多数の必須キー、
未知キー禁止、遷移規則の追加は、単なる後方互換な追加ではない。
このため `1.1` ではなく `2.0` とする。

### 3.3 不採用: 分析のたびに別結果ファイルを作る

初期実装では、同じ市場スナップショットに対応する
`analysis_result_YYYY-MM-DD_HHMM.json` を状態の正本として維持する。
履歴ファイルを増やす方式は監査には有利だが、命名、保持期間、
参照関係が未設計のため本仕様へ含めない。

`completed` の再分析履歴が必要になった場合は、既存結果を上書きせず、
実行ID付き成果物を別契約として設計する。

### 3.4 不採用: 情報源本文とモデル生出力を結果JSONへ格納

本文は容量、著作権、更新、個人情報の問題があり、モデル生出力には
内部プロンプトや秘密情報が混入し得る。結果JSONには検証済みの要約と
情報源メタデータだけを保存する。

取得本文、検索結果、生のモデル応答を保存する場合は、アクセス制御、
削除方針、秘匿情報除去、保持期間を含む別の証跡契約を先に設計する。

## 4. バージョン管理

### 4.1 新バージョン

新契約は次の単一定数を正本とする。

```python
SCHEMA_VERSION = "2.0"
```

`schema_version` が分析結果契約バージョンを表すため、
`analysis_contract_version` という重複キーは追加しない。モデル情報の
`prompt_version` は分析手順・プロンプト契約の版を表し、
`schema_version` とは別責務である。

### 4.2 単一ファイル・単一バージョン

空でない1ファイル内の全要素は、状態に関係なく
`schema_version == "2.0"` でなければならない。`1.0` と `2.0`、
または複数の将来バージョンを同一ファイル内へ混在させない。

1つの実装バージョンが生成する `SCHEMA_VERSION` は1つだけとする。
入力値、既存結果、モデル出力でバージョンを上書きしない。

未知のバージョンを受け取った後続処理は、推測で読み替えず明示的に
拒否する。

### 4.3 既存 `1.0` の扱い

`1.0` の空でない結果ファイルを要素単位で更新して `2.0` と混在させる
ことは禁止する。

既存 `1.0` は、次のいずれかを明示的な移行処理として行う。

1. 不変の `analysis_input_*.json` から全市場の `2.0` `pending` を再生成
2. `1.0` の全要素が正規の4キー `pending` であり、対応する分析入力と
   市場ID・順序・分析基準日時が一致することを検証した後、全要素を
   一括して `2.0` `pending` へ変換

移行結果は全件を構築・検証してから原子的に置換する。`1.0` では
`completed` と `error` を生成していないため、失われる分析結果はない。
移行前のファイルを暗黙に部分更新しない。

空配列 `[]\n` はファイル内にバージョンを持てない。0件時は引き続き
正確な `[]\n` とし、生成コードと本設計書で契約バージョンを管理する。

## 5. 共通構造

トップレベルは常にJSON arrayとする。入力市場順を維持し、入力1市場
につき結果1件を維持する。市場の追加、削除、並び替え、集約を行わない。

全状態に共通する先頭4キーは次の固定順とする。

| 順序 | キー | JSON型 | 制約 |
| ---: | --- | --- | --- |
| 1 | `schema_version` | string | 常にコード定数 `"2.0"` |
| 2 | `market_id` | string | 対応する入力の市場IDと完全一致 |
| 3 | `analysis_reference_time` | string | 対応する入力表記をそのまま維持 |
| 4 | `status` | string | `pending`、`completed`、`error` のいずれか |

出力JSONは、状態別に定義したキーだけを許可する。未知キー、別状態の
キー、同じキーの重複をすべて拒否する。ネストしたオブジェクトも
同じく未知キーと重複キーを拒否する。

文字列はUTF-8として有効で、指定された前後空白除去後の長さをUnicode
コードポイント数で数える。改行、tab、その他のC0制御文字を保存値へ
含めない。

## 6. 状態別契約

### 6.1 `pending`

`pending` は分析がまだ一度も完了していない、または明示的な再試行の
準備が完了した状態である。許可キーは共通4キーだけとする。

`completed` または `error` のキーを1つでも持つ `pending` は不正とする。

### 6.2 `completed`

`completed` は、確率、根拠、反対材料、情報源、モデル情報の全検証に
成功した市場だけに使用する。キー順は次のとおりとし、全キーを必須、
これ以外を禁止する。

| 順序 | キー | JSON型 | 制約 |
| ---: | --- | --- | --- |
| 1-4 | 共通4キー | - | `status` は `completed` |
| 5 | `yes_probability` | number | 0以上1以下、正規化規則に従う |
| 6 | `no_probability` | number | `1 - yes_probability` |
| 7 | `market_yes_price` | number | 0以上1以下、分析入力のYES価格から生成 |
| 8 | `probability_gap` | number | `yes_probability - market_yes_price` |
| 9 | `conclusion` | string | 固定列挙 |
| 10 | `confidence` | string | `low`、`medium`、`high` |
| 11 | `evidence` | array | 根拠項目、1件以上5件以下 |
| 12 | `counter_evidence` | array | 反対材料、1件以上5件以下 |
| 13 | `sources` | array | 情報源、2件以上8件以下 |
| 14 | `model_info` | object | 固定モデル情報契約 |
| 15 | `analysis_executed_at` | string | UTCの固定日時表記 |

`schema_version` が分析契約バージョンを表し、`model_info.prompt_version`
がプロンプトまたは分析手順のバージョンを表す。

### 6.3 `error`

`error` は市場単位の分析を試行したが、契約で定義した理由により
`completed` を構築できなかった状態である。キー順は次のとおりとし、
全キーを必須、これ以外を禁止する。

| 順序 | キー | JSON型 | 制約 |
| ---: | --- | --- | --- |
| 1-4 | 共通4キー | - | `status` は `error` |
| 5 | `error_code` | string | 固定列挙 |
| 6 | `error_category` | string | コードに対応する固定分類 |
| 7 | `error_message` | string | 人間向けの秘匿情報除去済み説明 |
| 8 | `retryable` | boolean | コードに対応する固定値 |
| 9 | `failed_stage` | string | 固定列挙と許可組合せ |
| 10 | `analysis_attempted_at` | string | UTCの固定日時表記 |
| 11 | `model_info` | object | 使用予定を含む固定モデル情報契約 |
| 12 | `external_search_used` | boolean | 外部検索を1回以上呼び出したか |

`error` には確率、市場価格差、結論、信頼度、根拠、反対材料、情報源を
保存しない。部分結果を残さず、誤って分析済みとして利用されることを
防ぐ。

元の例外文字列、スタックトレース、HTTPヘッダー、リクエスト本文、
モデル生出力は保存しない。`error_message` は秘密情報、認証情報、
ローカルパス、内部プロンプトを除去した1文字以上500文字以下の説明と
する。

## 7. 確率・市場価格・差・結論

### 7.1 JSON型と正規化

`yes_probability`、`no_probability`、`market_yes_price`、
`probability_gap` はJSON numberとし、文字列を許可しない。

計算は2進浮動小数点を使わず `Decimal` で行う。保存値は小数点以下
最大4桁へ `ROUND_HALF_UP` で丸め、次の規則で直列化する。

- 指数表記を使用しない
- 不要な先頭ゼロと小数末尾ゼロを除去する
- 整数値は小数点を付けない
- `-0`、`-0.0`、`-0.0000` は `0`
- `NaN`、`Infinity`、`-Infinity` を拒否する

モデルから受け取る独立値はYES確率だけとする。正規化後の値を `Y`、
分析入力のYES価格を同じ規則で正規化した値を `M` とし、次の順序で
派生値を生成する。

```text
yes_probability = Y
no_probability = 1 - Y
market_yes_price = M
probability_gap = Y - M
```

各演算結果にも最大4桁の正規化規則を適用する。
`yes_probability + no_probability` はDecimalとして正確に `1` で
なければならない。確率と市場価格は0以上1以下、差は-1以上1以下とする。
モデルのYES確率と分析入力の市場価格は、丸め前のDecimalについても
0以上1以下でなければならない。丸めによって値域外入力を救済しない。

### 7.2 市場との差

`probability_gap` はモデル推定と市場価格の符号付き差であり、次の単位
で解釈する。

```text
0.14 = YES予測確率が市場YES価格より14ポイント高い
```

利益、期待収益、手数料控除後の優位性を保証しないため、キー名に
`edge` を使用しない。これは確率差であり、売買判断ではない。

### 7.3 結論

確率差の分類しきい値を `0.05` に固定する。

| `conclusion` | 条件 |
| --- | --- |
| `yes_above_market` | `probability_gap >= 0.05` |
| `no_above_market` | `probability_gap <= -0.05` |
| `no_material_difference` | `-0.05 < probability_gap < 0.05` |

この分類は市場との乖離を表すだけであり、自動売買、注文推奨、収益保証
を意味しない。

### 7.4 情報不足

根拠、反対材料、情報源の最低条件を満たせない場合、推測で確率を保存
してはならない。`confidence: "low"` の `completed` ではなく、
`error_code: "insufficient_evidence"` の `error` とする。

`low` は最低条件を満たしたうえで不確実性が高い結果にだけ使用する。

`confidence` の意味は次に固定する。具体的な判定手順は
`model_info.prompt_version` が指す分析手順で管理する。

- `low`: 最低証拠条件は満たすが、重要な不確実性が残る
- `medium`: 複数情報源が概ね整合し、主要な反対材料を評価済み
- `high`: 複数の強い情報源が整合し、重大な未解決反対材料がない

## 8. 根拠・反対材料契約

`evidence` と `counter_evidence` はどちらも配列とする。単一文字列を
許可しない。

- `evidence`: 1件以上5件以下
- `counter_evidence`: 1件以上5件以下
- 両方とも空配列を禁止
- 根拠だけ、または反対材料だけの `completed` を禁止

各要素は次の固定キー順とする。

| 順序 | キー | JSON型 | 制約 |
| ---: | --- | --- | --- |
| 1 | `text` | string | 前後空白除去後1文字以上500文字以下 |
| 2 | `source_ids` | array of string | 1件以上3件以下、重複なし |

`source_ids` は同じ結果要素の `sources[].source_id` だけを参照する。
存在しないIDを禁止し、情報源配列の順序と同じ順で並べる。

同じ配列内では、`text` をUnicode NFKC正規化し、連続空白を1文字へ
畳み、casefoldした比較値が同一の項目を重複として拒否する。保存する
本文は前後空白だけを除去し、勝手に言い換えない。

`evidence` と `counter_evidence` の間でも同じ比較値を重複として拒否
する。同一文を賛否両方へ登録しない。

配列順は、`source_ids` の列を情報源順の数値列として比較し、その後に
正規化済み `text` を比較する安定ソートで固定する。

## 9. 情報源契約

### 9.1 件数と情報源オブジェクト

`sources` は2件以上8件以下とし、正規化URLが異なる情報源を最低2件
要求する。少なくとも1件は `source_type: "primary"` とする。

各要素は次の固定キー順とする。

| 順序 | キー | JSON型 | 制約 |
| ---: | --- | --- | --- |
| 1 | `source_id` | string | `S1` から始まる連番 |
| 2 | `url` | string | 実際に取得した絶対HTTP(S) URL |
| 3 | `canonical_url` | string | URL正規化規則による識別子 |
| 4 | `title` | string | 1文字以上500文字以下 |
| 5 | `publisher` | string | 1文字以上200文字以下 |
| 6 | `published_at` | string または null | 公開日時・日付。不明時はnull |
| 7 | `published_at_precision` | string | `datetime`、`date`、`unknown` |
| 8 | `retrieved_at` | string | UTCの固定日時表記 |
| 9 | `source_type` | string | `primary` または `secondary` |
| 10 | `stance` | string | `support`、`counter`、`both` |
| 11 | `relevance` | string | 市場判断との関係、1文字以上500文字以下 |

`published_at_precision` と `published_at` は次の組合せだけを許可する。

- `datetime`: UTCの固定日時表記
- `date`: `YYYY-MM-DD` の実在日
- `unknown`: `null`

`retrieved_at` は実際に内容を取得した時刻とし、検索結果へ表示された
時刻だけを使用しない。

`source_type` は、発行主体自身の公式発表、法令、政府統計、原論文、
公式記録を `primary`、報道、解説、まとめを `secondary` とする。

`evidence[].source_ids` はstanceが `support` または `both` の情報源だけ、
`counter_evidence[].source_ids` は `counter` または `both` の情報源だけ
を参照できる。全情報源は少なくとも1件の根拠または反対材料から参照
されなければならない。件数条件を満たすためだけの未使用情報源を禁止
する。

### 9.2 URL正規化と重複

`canonical_url` は次の順序で生成する。

1. 絶対URLとして解析し、schemeとhostを小文字化
2. schemeは `http` または `https` だけを許可
3. userinfoを含むURLを拒否
4. `http:80` と `https:443` の既定ポートを除去
5. fragmentを除去
6. 空pathを `/` とし、root以外の末尾スラッシュを除去
7. queryキーをASCII大小文字を区別せず評価し、`utm_*`、`gclid`、
   `fbclid` の追跡queryを除去
8. 残るqueryをデコード済みのキー、値、元の出現順で安定ソート

同じ `canonical_url` の情報源を複数登録しない。リダイレクト後に同じ
URLへ到達した場合も重複とする。重複排除後に最低件数を満たさない場合
は `insufficient_evidence` とする。

情報源配列は `canonical_url`、公開日時比較値、`title` の順で安定
ソートする。公開日時比較値は `null` を空文字列、それ以外を保存文字列
とする。その後 `S1`、`S2` の連番を割り当て、根拠・反対材料の参照IDも
割当後のIDへ統一する。

### 9.3 検索スニペットと本文

検索結果スニペットだけを情報源として認めない。リンク先本文、公開API、
公式文書など、内容を実際に取得して妥当性を確認したものだけを
`sources` へ登録する。

情報源本文や検索結果の生データは結果JSONへ保存しない。このため、
結果JSONだけによる外部情報の完全再現は保証しない。証跡保存が必要な
場合は、取得本文の扱いと著作権・保持期間を含む別契約を先に設計する。

## 10. モデル情報契約

`model_info` は `completed` と `error` の両方で必須のobjectとする。
検索前に失敗しても、使用する予定だった分析構成を記録する。

固定キー順は次のとおりとする。

| 順序 | キー | JSON型 | 制約 |
| ---: | --- | --- | --- |
| 1 | `provider` | string | 1文字以上100文字以下 |
| 2 | `model` | string | 1文字以上200文字以下 |
| 3 | `model_version` | string または null | 提供元が公開しない場合はnull |
| 4 | `prompt_version` | string | `MAJOR.MINOR` 形式 |
| 5 | `temperature` | number または null | 使用値。非対応・不明はnull |
| 6 | `seed` | integer または null | 使用値。非対応・不明はnull |
| 7 | `tools_used` | array of string | 実際に呼び出した固定列挙 |
| 8 | `search_provider` | string または null | 外部検索を使わない場合はnull |

`temperature` は有限な0以上2以下のJSON numberとし、確率と同じ固定
数値表現を使用する。`seed` は符号付き64ビット整数の範囲とする。

`tools_used` の許可値と順序は次のとおりとする。

```text
web_search
source_retrieval
```

重複を禁止し、使用した項目を上記順で並べる。
`web_search` が含まれる場合だけ `search_provider` を非nullとし、
含まれない場合はnullとする。

`error.external_search_used` は、同じ要素の `model_info.tools_used` に
`web_search` が含まれる場合だけtrueとし、それ以外はfalseとする。

次を `model_info` または他の結果キーへ保存しない。

- APIキー、認証トークン、Cookie、秘密鍵
- 内部プロンプト全文、非公開システム指示
- ローカル絶対パス、ユーザー名、ホスト名
- 不要なOS、CPU、メモリ、プロセス情報
- モデル生出力

## 11. 日時表記

入力由来の `analysis_reference_time` は、既存契約どおり入力文字列を
そのまま維持する。

新たに生成する次の日時はUTC、マイクロ秒6桁、末尾 `Z` の固定形式と
する。

```text
YYYY-MM-DDTHH:MM:SS.ffffffZ
```

対象:

- `analysis_executed_at`
- `analysis_attempted_at`
- `sources[].retrieved_at`
- `sources[].published_at` のprecisionが `datetime` の場合

タイムゾーンなし、不正な暦日・時刻、異なる精度、`+00:00` 表記を
正式出力では許可しない。時刻は確定済み分析データの一部であり、
シリアライズ時に現在時刻へ差し替えない。

`analysis_attempted_at` は、その市場について最初の外部検索、情報源取得、
またはモデル呼び出しを開始する直前の時刻とする。
`analysis_executed_at` は、completedの全値を構築して市場単位検証に
成功した時刻とする。`retrieved_at` はリンク先本文または公開APIの
応答取得に成功した時刻とする。

## 12. エラーコード契約

エラーコードは次の7種類だけとする。自由文コードを許可しない。

| `error_code` | `error_category` | `retryable` | 許可する `failed_stage` |
| --- | --- | --- | --- |
| `search_failed` | `external_dependency` | true | `search` |
| `insufficient_evidence` | `evidence` | false | `evidence_evaluation` |
| `model_failed` | `model` | true | `model` |
| `invalid_model_output` | `model_output` | true | `model_output_validation` |
| `timeout` | `external_dependency` | true | `search`, `source_retrieval`, `model` |
| `rate_limited` | `external_dependency` | true | `search`, `source_retrieval`, `model` |
| `source_validation_failed` | `evidence` | false | `source_retrieval`, `source_validation` |

この列挙に該当しない設定不正、入力不正、結果ファイル不正、内部不変条件
違反、シリアライズ失敗、保存失敗は市場単位の `error` に変換せず、
ファイル全体の処理失敗とする。

## 13. 状態遷移

### 13.1 許可する遷移

| 現在 | 次 | 条件 |
| --- | --- | --- |
| `pending` | `completed` | 全completed契約を満たす |
| `pending` | `error` | 市場単位の固定エラーに分類できる |
| `error` | `pending` | 利用者が対象市場の再試行を明示した場合だけ |

`pending → pending`、`completed → completed`、`error → error` は、
未処理要素を内容変更せず次の全体スナップショットへ引き継ぐ場合だけ
許可する。これは新しい分析や状態遷移ではない。

### 13.2 禁止する遷移

- `completed → pending`
- `completed → error`
- 内容を変更する `completed → completed`
- 明示的な再試行なしの `error → pending`
- 直接の `error → completed`
- 直接の内容変更を伴う `error → error`
- `pending` 以外から状態固有キーを引き継ぐこと

`completed` は同一スナップショットに対する終端状態とする。再分析は
既存結果を上書きせず、実行ID付き別成果物の契約を設計してから行う。

`error` の再試行は、まず選択した要素を共通4キーだけの `pending` へ
戻し、ファイル全体を原子的に保存する。その後の別処理で
`pending → completed` または `pending → error` とする。これにより
直接遷移を禁止し、失敗項目が残った状態と新しい試行を混同しない。

### 13.3 遷移時のキー

- `pending → completed`: completedの15キーだけを新規構築
- `pending → error`: errorの12キーだけを新規構築
- `error → pending`: error固有8キーをすべて除去
- 状態を維持する要素: キー、値、配列順を変更しない

状態遷移後に禁止キーが残った場合はファイル全体の検証失敗とし、
正式結果を置換しない。

## 14. 市場単位失敗とファイル全体失敗

### 14.1 市場単位の失敗

1市場の検索、証拠評価、モデル呼び出し、モデル出力検証が固定エラーへ
分類できる場合、その市場だけを `error` とし、他市場の処理を続ける。

市場単位の `error` を含んでも、全結果要素が契約を満たし、ファイルを
正常保存できた場合のプロセス終了コードは0とする。標準出力には
`completed`、`error`、未処理 `pending` の件数を表示する。

### 14.2 ファイル全体の失敗

次はファイル全体の処理失敗とし、終了コード1で終了する。

- 分析入力または既存結果JSONが不正
- 入力と結果の件数、順序、市場ID、分析基準日時が不一致
- ファイル内の `schema_version` が不統一または未知
- 状態別必須キー、禁止キー、型、値域、参照整合性が不正
- 未知のエラーコードや不正な状態遷移
- 設定、認証、契約定数が処理開始前から不足または不正
- 内部不変条件違反
- JSONシリアライズ、書き込み、同期、置換の失敗

ファイル全体の失敗では、新しい市場単位 `error` を捏造せず、既存の
正式結果ファイルを変更しない。

## 15. 再実行・部分成功・停止

### 15.1 既存状態の既定動作

- `pending`: 分析対象
- `completed`: 内容をそのまま保持し、既定では再分析しない
- `error`: 内容をそのまま保持し、既定では自動再試行しない

再試行可能な `error` であっても、無限再試行や意図しない外部料金を
避けるため利用者の明示選択を必要とする。

### 15.2 部分成功

全市場を順番に処理し、市場単位の成功と失敗をそれぞれ
`completed`、`error` としてメモリ上に構築する。既存 `completed`、
既定で再試行しない `error`、今回選択しない `pending` は保持する。

全要素の処理と検証が完了するまで正式結果を更新しない。1市場の失敗で
ファイル全体を失敗させず、固定エラーへ分類して残りを継続する。

### 15.3 実行途中の停止

プロセス停止、未処理例外、電源断などにより正式ファイルの置換前に
終了した場合、実行前の正式結果を維持する。一時ファイルは
ベストエフォートで削除する。

`os.replace()` 成功後は、新しい全体スナップショットを正式結果とする。
市場単位で正式ファイルを逐次上書きしない。

## 16. 決定性

決定性とは、外部検索とAI推論が終わり、すべての分析値、日時、情報源、
モデル情報が確定した後、その同じ確定データから同じ正式JSONバイト列を
生成できる性質と定義する。

次を固定する。

- トップレベル市場順は分析入力順
- 状態別キー順は本仕様の表どおり
- ネストしたオブジェクトのキー順も本仕様どおり
- `sources` は正規化URL等の固定キーでソート後にIDを割当
- 根拠・反対材料は固定ソート
- `source_ids` と `tools_used` は定義済み順序
- 数値はDecimal、最大4桁、`ROUND_HALF_UP`、指数表記なし
- JSON stringは `ensure_ascii=False` 相当で正しくエスケープ
- UTF-8、BOMなし
- 2スペースインデント
- コロン後1スペース
- LF改行
- ファイル末尾LF
- 0件は正確な `[]\n`
- 完成バイト列を同一ディレクトリの一時ファイルへバイナリ保存
- `flush()`、`fsync()`、close後に `os.replace()`
- 保存失敗時は既存正式ファイルを保持

現在時刻、ランダム値、一時ファイル名、ホスト情報をシリアライズ時に
追加しない。同じ確定済み分析データからは同じバイト列を生成する。

## 17. 再現性

再現性とは、同じ市場入力から検索とAI推論を再実行した場合に、同じ
分析データが得られる可能性と、その条件を追跡できる性質と定義する。
決定性とは別要件である。

次の理由により完全再現を保証しない。

- 検索順位、検索インデックス、公開ページ内容が時点で変わる
- 情報源が更新、削除、訂正される
- モデル提供元が同じモデル名の内部実装を更新する
- temperatureが0でも推論基盤が非決定的な場合がある
- seedを指定できないモデルや、seedだけでは再現できないモデルがある
- 外部ツールの取得結果とタイムアウトが変わる

追跡のため、結果JSONへ次を保存する。

- 分析基準日時と分析実行・試行日時
- 情報源URL、正規化URL、タイトル、発行元、公開日時、取得日時、種別
- 情報源と根拠・反対材料の参照関係
- provider、model、model_version
- prompt_version、temperature、seed
- 使用ツール、検索プロバイダ

結果JSONには情報源本文、検索結果全文、モデル生出力、内部プロンプト
全文を保存しない。したがって結果JSON単体での完全再現は保証しない。
完全な監査証跡が必要になった時点で、秘匿情報除去と保持方針を含む
別の証跡保存契約を設計する。

## 18. JSON例

### 18.1 `completed`

```json
{
  "schema_version": "2.0",
  "market_id": "12345",
  "analysis_reference_time": "2026-07-30T22:04:49.568055+09:00",
  "status": "completed",
  "yes_probability": 0.62,
  "no_probability": 0.38,
  "market_yes_price": 0.48,
  "probability_gap": 0.14,
  "conclusion": "yes_above_market",
  "confidence": "medium",
  "evidence": [
    {
      "text": "公式発表は期限内の達成可能性を支持している",
      "source_ids": [
        "S1"
      ]
    }
  ],
  "counter_evidence": [
    {
      "text": "第三者報道は実行上の遅延リスクを指摘している",
      "source_ids": [
        "S2"
      ]
    }
  ],
  "sources": [
    {
      "source_id": "S1",
      "url": "https://example.gov/release/123",
      "canonical_url": "https://example.gov/release/123",
      "title": "Official release",
      "publisher": "Example Agency",
      "published_at": "2026-07-30T01:00:00.000000Z",
      "published_at_precision": "datetime",
      "retrieved_at": "2026-07-31T02:00:00.000000Z",
      "source_type": "primary",
      "stance": "support",
      "relevance": "市場条件に直接関係する公式発表"
    },
    {
      "source_id": "S2",
      "url": "https://news.example/article",
      "canonical_url": "https://news.example/article",
      "title": "Execution risks remain",
      "publisher": "Example News",
      "published_at": "2026-07-30",
      "published_at_precision": "date",
      "retrieved_at": "2026-07-31T02:01:00.000000Z",
      "source_type": "secondary",
      "stance": "counter",
      "relevance": "公式計画の遅延要因を検討した報道"
    }
  ],
  "model_info": {
    "provider": "example-provider",
    "model": "example-model",
    "model_version": null,
    "prompt_version": "1.0",
    "temperature": 0,
    "seed": null,
    "tools_used": [
      "web_search",
      "source_retrieval"
    ],
    "search_provider": "example-search"
  },
  "analysis_executed_at": "2026-07-31T02:05:00.000000Z"
}
```

### 18.2 `error`

```json
{
  "schema_version": "2.0",
  "market_id": "67890",
  "analysis_reference_time": "2026-07-30T22:04:49.568055+09:00",
  "status": "error",
  "error_code": "insufficient_evidence",
  "error_category": "evidence",
  "error_message": "独立した検証済み情報源を2件確保できませんでした",
  "retryable": false,
  "failed_stage": "evidence_evaluation",
  "analysis_attempted_at": "2026-07-31T02:05:00.000000Z",
  "model_info": {
    "provider": "example-provider",
    "model": "example-model",
    "model_version": null,
    "prompt_version": "1.0",
    "temperature": 0,
    "seed": null,
    "tools_used": [
      "web_search",
      "source_retrieval"
    ],
    "search_provider": "example-search"
  },
  "external_search_used": true
}
```

## 19. テスト観点

### 19.1 バージョンと共通構造

- `SCHEMA_VERSION == "2.0"` を確認
- 1ファイル内の全要素が同じバージョンであること
- `1.0` と `2.0` の混在、未知バージョンを拒否
- 空配列が正確な `b"[]\n"` であること
- 入力市場順、件数、市場ID、分析基準日時の1対1対応
- 状態別の必須キー不足、未知キー、別状態キー、重複キーを拒否

### 19.2 状態遷移

- `pending → completed` と `pending → error` を受理
- 明示的な `error → pending` を受理し、error固有キーを除去
- `completed → pending`、`completed → error`、直接の
  `error → completed` を拒否
- 状態維持要素が内容変更されないこと
- `completed` と `error` を既定で再処理しないこと

### 19.3 completed契約

- 確率・市場価格の0、1と小数第4位境界を受理
- 値域外、文字列、真偽値、非有限数を拒否
- `ROUND_HALF_UP`、末尾ゼロ、指数、負のゼロの正規化
- YESとNOの合計が正確に1
- NO確率と価格差がYES確率から正しく派生
- 差が `0.05`、`-0.05` の結論境界
- 根拠、反対材料、情報源の最小・最大件数
- 根拠だけ、反対材料だけのcompletedを拒否
- 正規化後に重複する根拠・反対材料を拒否
- source ID参照、順序、重複を検証
- `insufficient_evidence` では確率を保存しないこと

### 19.4 情報源

- HTTP(S) URLだけを受理
- scheme・host、既定port、fragment、path、queryの正規化
- 追跡query除去と残存queryの安定ソート
- 同一canonical URLの重複を拒否
- primaryが最低1件、全体が2件以上
- スニペットだけの情報源を拒否
- 公開日時のdatetime、date、unknownと値の組合せ
- 情報源ソート後の `S1` 連番と参照ID再割当

### 19.5 モデル情報とエラー

- model_infoの必須キー、型、順序
- temperature、seedの境界とnull
- tools_usedの列挙、重複、固定順
- web_searchとsearch_providerの整合
- 7種類のエラーコードだけを受理
- コード、分類、retryable、失敗段階の組合せ
- errorでcompleted固有キーと部分結果を拒否
- 生例外、スタックトレース、秘密情報を結果へ含めない

### 19.6 ファイル処理

- 市場単位errorでも他市場を継続し、正常保存時は終了コード0
- ファイル全体エラーでは終了コード1、既存結果不変
- 部分成功を全件構築後に一括保存
- 書き込み、fsync、置換失敗時に既存正式ファイルを保持
- 中断時に正式ファイルを部分更新しない
- 同じ確定済み分析データからバイト単位で同一出力
- UTF-8 BOMなし、LF、2スペース、固定キー順、固定配列順、末尾LF
- 実行時刻をシリアライズ段階で再生成しない

### 19.7 既存機能の回帰

- 既存81テストが引き続き成功
- `fetch_markets.py`、`select_candidates.py`、
  `prepare_analysis_input.py` の出力契約が不変
- `1.0` の既存pending生成動作は、`2.0`実装を開始するコミットまで不変
- README、`plan.md`、依存関係を設計段階で変更しない

## 20. 実装前に別途設計する事項

本仕様の承認後も、直ちに外部AI接続を実装しない。最低限、次を別設計で
固定してから実装計画を作成する。

- 検索プロバイダと検索クエリ生成
- 情報源本文の取得、抽出、検証、robots・利用条件の扱い
- 一次情報判定
- モデルへの入力形式とプロンプト本文
- モデル出力の構造化取得と再試行回数
- timeout、rate limit、認証・設定エラーの境界
- 外部料金の上限と呼び出し件数
- 証跡ファイルを保存する場合の秘匿情報除去と保持期間
- completed再分析用の実行ID付き履歴契約

## 21. 完了条件

- 3状態の必須キー、禁止キー、型、順序が一意に定義されている
- 許可・禁止する状態遷移が一意に定義されている
- 市場単位errorとファイル全体失敗が分離されている
- 確率、丸め、合計、価格差、結論境界が機械的に検証できる
- 根拠、反対材料、情報源、モデル情報、エラー列挙が固定されている
- 情報不足を低信頼completedではなくerrorとする境界が明確である
- 決定性と再現性が別要件として定義されている
- `2.0`への更新理由と既存`1.0`移行方針が明確である
- 再実行、再試行、部分成功、中断、原子的保存が矛盾しない
- 売買、認証、外部検索・AI具体実装が本仕様の範囲外である
