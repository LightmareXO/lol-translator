# アプリ用韓日辞書の更新手順

アプリ用辞書は、Riot Data Dragonから生成する正式名称層と、人が出典を確認して保守する別名層に分かれています。
正式名称を再生成しても、`aliases/`と`terms/`にある略称、愛称、旧称、表記ゆれは変更されません。

別名は呼び方の種類ではなく、参照先のカテゴリで分割します。
チャンピオン、アイテム、ルーン、サモナースペルの別名は`aliases/`に置きます。
特定の正式名称へ紐づかないゲーム用語と、その別名は`terms/gameplay.json`に置きます。
出典は`sources.json`、読み込むファイルとSHA-256は`manifest.json`で管理します。

現在の配布対象は次のファイルです。

- `aliases/champions.json`
- `aliases/items.json`
- `aliases/runes.json`
- `aliases/summoner_spells.json`
- `terms/gameplay.json`
- `sources.json`
- `official-16.18.1.json`

`namu-survey.json`は、今回のNamu Wiki調査範囲と候補の判断を保存する調査台帳です。
`migration-baseline.json`は、分割前のID、件数、内容、代表的な検索結果を固定する検証資料です。
この二つは実行時辞書へ読み込みません。

`translation_evaluation/glossary.json`は過去の翻訳評価を再現するための凍結済みv1です。
アプリ用辞書の更新時に、このファイルを編集しないでください。

## 正式名称の再生成

Python 3.10以上とネットワーク接続を用意します。
取得キャッシュはリポジトリ外へ置きます。

```powershell
$patch = "16.18.1"
$cache = Join-Path $env:TEMP "lol-translator-ddragon-$patch"
python analyzer/app_dictionary/generate_official.py `
  --patch $patch `
  --retrieved-at "2026-09-19" `
  --cache $cache `
  --output "analyzer/app_dictionary/official-$patch.json"
```

生成処理は韓国語版と日本語版をIDで照合します。
チャンピオンスキルはチャンピオンIDとP/Q/W/E/Rのスロットで照合します。
片方のロケールにIDがない場合や、同じIDが重複した場合は失敗します。

## パッチ更新時の差分確認

新しいJSONを採用する前に、旧版との差分を出します。

```powershell
python analyzer/app_dictionary/validate_dictionary.py `
  --base analyzer/app_dictionary `
  --compare analyzer/app_dictionary/official-<旧パッチ>.json
```

`added_ids`、`removed_ids`、`renamed`を確認します。
削除IDや名称変更は、別名の参照先と過去動画の旧称に影響するため、人が判断します。

採用時は`manifest.json`のパッチ、ファイル名、SHA-256、期待件数を更新します。
`AppDictionary.load_default`とTauriのresource指定も、新しい正式名称ファイルへ合わせます。

## 別名の追加

別名は参照先に対応する`aliases/*.json`または`terms/gameplay.json`へ追加します。
正式名称JSONへ手書きで追加してはいけません。

一つの別名には、次の情報が必要です。

- 一意な別名ID
- 韓国語の表記
- 参照先ID
- 略称、愛称、旧称などの種別
- 確認状態
- 出典IDと、そのページで確認できる短い根拠
- 一般語との衝突がある場合の意味候補と判断条件
- 旧称の場合は由来と、動画のパッチが不明な場合の注意

検索だけで対応を推測した語は、`alias-survey.json`で`unconfirmed`として保留します。
保留語と却下語は、実行時辞書へ入れません。

## Namu Wiki由来の別名を追加する場合

収集前に、対象ページ、節、カテゴリを`namu-survey.json`へ追加して範囲を固定します。
今回の固定範囲は、チャンピオン8ページ、アイテム3ページ、ゲーム用語1ページです。
この範囲の完了は、Namu Wiki全体またはLoLの全別名を網羅したことを意味しません。

正式なURLは`namu.wiki`を記録します。
今回の収集環境では正式URLがHTTP 403を返したため、同じ題名の本文を`namu.moe`で確認しました。
この確認状態は`source_checked`であり、韓国語話者による`human_checked`ではありません。

候補には`added_active`、`duplicate_existing`、`held_unconfirmed`のいずれかを記録します。
本文から参照先を確認できない語は、推測で有効辞書へ入れません。
同じ表記が複数の対象を指す場合は、上書きせず`target_ids`へ候補を残します。
一般語と衝突する語は`ambiguity`へ意味候補と文脈条件を記録します。
旧称や過去の呼び方は`historical`へ確認できた時期と由来を記録します。

別名または出典を変更した後は、manifestの件数とハッシュを更新します。

```powershell
python analyzer/app_dictionary/update_manifest.py
```

## 検証

```powershell
python analyzer/app_dictionary/validate_dictionary.py `
  --output analyzer/app_dictionary/validation-report.json
python -m unittest discover -s analyzer -p "test_*.py"
```

検証処理は正式名称件数、ファイルをまたぐID重複、ロケール欠落、別名の出典、参照先、カテゴリ、調査結果との整合、全配布ファイルのSHA-256、旧v1のSHA-256を確認します。
`migration-baseline.json`との照合により、分割前から存在した出典、補助概念、別名、代表的な候補選択結果が維持されていることも確認します。
衝突一覧は、同じ正式名を持つ別IDを削除するための一覧ではありません。
ゲームモード違い、アイテム派生、スキル名と一般名の重複を確認するための一覧です。

## 実モデル比較

Ollamaと固定モデルを起動した状態で、旧v1と新辞書を同じプロンプトと生成設定で比較します。

```powershell
python analyzer/app_dictionary/compare_runtime_dictionary.py
```

比較データは、既に調整へ使った開発字幕と明示的な合成文だけです。
独立評価用データは使いません。
自動判定は期待用語の有無だけを確認するため、翻訳品質の人手評価を置き換えません。

Namu Wiki由来の追加前後だけを比較する場合は、追加直前のコミットと専用データを指定します。

```powershell
python analyzer/app_dictionary/compare_runtime_dictionary.py `
  --dataset analyzer/app_dictionary/namu-comparison-dataset.json `
  --output analyzer/app_dictionary/namu-comparison-results.json `
  --baseline-ref ee603d4
```

比較処理は指定したGitコミットの辞書を一時領域へ復元し、現在の辞書と同じモデル、プロンプト、生成設定で実行します。
`namu-comparison-review.json`はAIによる暫定確認であり、人手確認済みの評価ではありません。
新規別名を含む実字幕は現在の開発データで確認できなかったため、今回の追加語の比較例は合成文です。
独立評価用データに存在する語は、辞書調整の入力へ使用していません。
