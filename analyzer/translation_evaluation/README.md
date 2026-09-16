# ローカル翻訳の比較実験

動画アプリへの組み込み前に、OCR誤りと翻訳モデルの誤りを切り分ける評価ツールです。
推論先はループバックのOllamaに限定し、クラウドモデルを拒否します。
モデルと画像はリポジトリに保存しません。

## 実行環境

集計とモックテストにはPython標準ライブラリを使用します。
実推論のRAM計測には`psutil`、GPU計測には`nvidia-smi`を使用します。
取得できない測定値は`null`です。
動画抽出にはFFmpegとOpenCV、OCRには既存のPaddleOCR環境が必要です。
OCRの環境構築手順は`../ocr_evaluation/README.md`を参照してください。

Ollamaをローカルで起動し、使用モデルを取得します。
通常のモデル取得にはネットワーク接続が必要ですが、翻訳は端末内で実行します。
必要なら公式のWindowsポータブル配布を使用できます。

```powershell
$env:OLLAMA_NO_CLOUD = '1'
$env:OLLAMA_HOST = '127.0.0.1:11434'
$env:OLLAMA_NUM_PARALLEL = '1'
$env:OLLAMA_MAX_LOADED_MODELS = '1'
ollama serve
```

別の端末で必要なモデルだけ取得します。

```powershell
ollama pull translategemma:4b
ollama pull qwen3:4b
python -m unittest discover -s analyzer -p 'test*.py'
python analyzer/translation_evaluation/evaluate.py --model translategemma:4b --output work/translation-runs
python analyzer/translation_evaluation/evaluate.py --model qwen3:4b --output work/translation-runs
python analyzer/translation_evaluation/collect.py --runs work/translation-runs --output work/translation-review
```

上の推論コマンドは逐次実行してください。
`prompts.json`は初期比較の記録用です。
この条件を推奨設定とは扱いません。
Qwen3の思考有効化やサンプリング変更は、`--prompts`で別ファイルを指定して別実験にします。

## 条件と保存形式

| 条件 | 入力本文 | 辞書 |
| --- | --- | --- |
| A | 目視転記した韓国語 | なし |
| B | OCRの生出力 | なし |
| C | 目視転記した韓国語 | あり |
| D | OCRの生出力 | あり |

既存8グループでは中央フレームを主評価、前後1フレームを補助評価に固定しています。
中央には4条件、隣接にはBとDのみを実行するため、1モデルあたり64リクエストになります。
字幕数の分母は8であり、24画像や64リクエストではありません。
正解韓国語も人による確認を自動的に意味しません。
初期データの既存`verified`表記を、新評価の人による確認済み状態へ引き継ぎません。

実験IDにはモデルdigest、Ollamaバージョン、テンプレート、プロンプト、辞書、生成設定、入力データのハッシュを含めます。
現行の実行器ではソースのハッシュも含めます。
別条件の履歴は渡しません。
保存した結果にはチェックサムがあり、入力やモデルが違う結果を再開時に流用しません。
成功済みのリクエストは再利用し、エラーは再実行します。
設定を変更した場合は出力先が同じでも別実験になります。

各実験は`running.lock`で二重実行を防ぎます。
強制終了でロックが残った場合は、その実験のプロセスが動いていないことを確認したうえで、該当ファイルだけを削除してください。
他の実験結果を削除する必要はありません。

## 採点

`blind_packet.json`にはモデル名と条件を載せません。
ただし訳文からモデルや条件を推測できる可能性があり、完全な盲検は保証しません。
別ファイルの`blind_mapping.json`が実測値との対応を保持します。
採点時は`scoring.json`を編集し、集計コマンドを再実行します。
再出力しても既存の採点は上書きせず、追加結果の未採点行だけを補います。

- **review_status**：`unreviewed`、`ai_provisional`、`human_confirmed`を区別します。
- **severity**：`none`、`minor`、`terminology`、`major`、`undetermined`から選びます。
- **error_types**：用語、欠落、捏造、形式、否定、数字、行為者、順序を別々に記録します。
- **evidence**：画像、原文、用語の出典、確認記録など、判断の根拠を記録します。
- **semantic_signature**：同じ意味の訳に同じ短い説明を記し、隣接フレーム間の意味変化を補助集計します。

形式違反は、意味の重大誤訳とは別に数えます。
訳が出ていない場合は形式違反を記録し、意味を判定不能とします。
AI採点は人による確認済みの分母に含めません。
2行のうち片方だけを採点したグループも、全体を確認済みとした分母に含めません。

## 追加動画

動画単位の分割は`PLAN.md`に記録しています。
開発用の28字幕は`talon_development.json`にあります。
字幕ごとの微調整ではなく、動画全体で共通の固定行帯を使います。
行数、色、役割は目視アノテーションによる補助情報であり、自動検出の実用品質を測る実験ではありません。

```powershell
python analyzer/translation_evaluation/video_dataset.py --video "動画のパス" --specification analyzer/translation_evaluation/talon_development.json --output work/talon-dataset --model-dir "既存Paddleモデルキャッシュ"
python analyzer/translation_evaluation/evaluate.py --model qwen3:4b --dataset work/talon-dataset/translation-dataset.json --prompts analyzer/translation_evaluation/prompts-sampling.json --output work/talon-runs
```

抽出先は未作成のディレクトリを指定します。
OCRのみの再実行では`--video`を省略できますが、既存のOCR結果を上書きすることはできません。
黄色の注釈と白色の発言は別々の翻訳入力にします。
2行全体の認識結果は診断用に保存し、発言へ連結しません。

## 測定上の制約

ロード後のウォームアップは評価対象外の挨拶で行い、結果を別ファイルに保存します。
アンロードしてもOSのファイルキャッシュは消えないため、初回コールドスタートの保証はありません。
実行中のプレフィックスキャッシュはOllamaに任せ、応答のキャッシュ関連フィールドも保存します。
逐次実行でも、同じseedで全環境の完全再現を保証するものではありません。

RAMはOllamaプロセス群のRSS合計、GPU使用量はGPU全体の値です。
後者には他アプリが含まれるため、モデル単体のVRAMと同一視しません。
`ps`応答のモデル配置も別に保存します。
短いリクエストのピークをサンプリングで取り逃す可能性があります。
初期8件の中央値とp95は小標本の記述値であり、一般的な性能や誤訳率に外挿しません。

## 公式資料

- [OllamaのWindows配布](https://docs.ollama.com/windows)
- [Ollamaの思考出力](https://docs.ollama.com/capabilities/thinking)
- [Qwen3の推奨生成設定](https://huggingface.co/Qwen/Qwen3-4B)
- [TranslateGemmaの入力形式](https://ollama.com/library/translategemma:4b)

辞書の各項目の出典は`glossary.json`に記録しています。
