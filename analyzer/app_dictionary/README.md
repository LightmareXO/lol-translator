# アプリ用韓日辞書の更新手順

アプリ用辞書は、Riot Data Dragonから生成する正式名称層と、人が出典を確認して保守する別名層に分かれています。
正式名称を再生成しても、`aliases.json`の略称、愛称、旧称、表記ゆれは変更されません。

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

別名は`aliases.json`へ直接追加します。
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

## 検証

```powershell
python analyzer/app_dictionary/validate_dictionary.py `
  --output analyzer/app_dictionary/validation-report.json
python -m unittest discover -s analyzer -p "test_*.py"
```

検証処理は正式名称件数、ID重複、ロケール欠落、別名の出典、参照先、調査結果との整合、旧v1のSHA-256を確認します。
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
