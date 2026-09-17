# Issue #13への引き継ぎ

## 保存時点

2026-09-17に評価作業を一区切りとし、新しいOCR方式、翻訳モデル、プロンプト、独立評価は開始しない。
実装、実測結果、辞書、AI暫定採点、部分的な人手評価は、PR #10とPR #12に保存した。

この文書はIssue #13の最小統合で再利用する構成と、再利用しない評価専用コードを区別する。
精度評価の完了や実用品質の保証を引き継ぎ条件にはしない。

## 再利用する構成

### OCR

- PaddleOCR 3.7.0
- PaddlePaddle 3.4.0のWindows CPU版
- 認識モデル`korean_PP-OCRv5_mobile_rec`
- 固定1.5倍コントラスト補正
- CPU 4スレッド、バッチサイズ1、ウォームアップ3回
- ユーザーが指定した字幕範囲を認識専用APIへ渡す

3枚、7枚、15枚の候補統合、時間中央値画像、色と輪郭の固定マスクは採用しない。
開発用9字幕では単一フレームを上回らず、`렙→럽`と単独字母の誤認も残ったためである。
PR #12の複数フレーム実装は診断の再現用に残し、Issue #13の実行経路へ組み込まない。

### 翻訳

- Ollamaの`qwen3:4b-instruct-2507-q4_K_M`
- 非思考専用のInstruct-2507
- プロンプト版`ko-ja-v4-instruct-nonthinking`
- `think=false`
- `temperature=0.7`
- `top_p=0.8`
- `top_k=20`
- `min_p=0`
- `seed=42`
- `num_ctx=4096`
- `num_predict=384`

このモデルはIssue #13で統合動作を確認するための基準であり、翻訳品質の採用確定ではない。
正しい韓国語でも重大な意味崩れが残り、AI暫定採点にも誤判定がある。

辞書は`glossary.json`の`lol-ko-ja-v1`を再利用する。
辞書項目は文脈に合う場合だけ参照し、一律置換へ使わない。
ユーザーの部分評価で不足が見つかった略称は、Issue #13で新規拡張せず、既知の制約として表示する。

## 再利用するコード

| 用途 | ファイル | Issue #13での扱い |
| --- | --- | --- |
| 画像前処理 | `analyzer/ocr_evaluation/preprocessing.py` | 固定コントラスト補正を再利用する |
| PaddleOCR呼び出し | `analyzer/ocr_evaluation/run_paddle.py` | モデル初期化と認識結果の取り出し方を再利用する |
| 動画フレームとOCR | `analyzer/translation_evaluation/video_dataset.py` | SHA-256、FFmpeg抽出、OCR記録の実装を参照する |
| Ollama呼び出し | `analyzer/translation_evaluation/evaluate.py` | `/api/show`によるモデル同一性確認と`/api/chat`呼び出しを分離して再利用する |
| 翻訳設定 | `analyzer/translation_evaluation/prompts-instruct.json` | 上記の固定設定を読み込む |
| LoL辞書 | `analyzer/translation_evaluation/glossary.json` | 出典と状態を保ったまま読み込む |
| 人手確認状態 | `analyzer/translation_evaluation/human_review_partial.json` | 未確認を埋めず、参考資料として保持する |

`evaluate.py`と`video_dataset.py`は評価用の一括処理であり、そのままアプリのバックエンドにはしない。
Issue #13ではOCR、翻訳、保存を小さな呼び出し単位へ分け、進捗とキャンセルをTauri側へ返す。

字幕変化検出、隣接字幕の統合、字幕タイムライン、キャンセル可能な解析ジョブ、修正履歴の保存は未実装である。
Issue #13は200ms程度の固定間隔抽出から始め、精度改善実験を追加しない。

## 部分的な人手評価

`human_review_partial.json`はユーザーがローカル確認画面から出力した60字幕分の評価である。
原文を`ok`とした字幕は21件で、そのうち20件はA、B、C、Dの4条件がすべて入力済みである。
1件は条件Aが空欄で、39件は原文確認と条件評価が空欄である。

ユーザーは韓国語とLoL文脈を照合できる一方、完全な正しさは保証できないとしている。
このファイルを既存の`human_confirmed`集計へ自動適用せず、部分的な人手自己評価として保存する。
AI暫定採点をこの評価で一括上書きしない。

保存ファイルのSHA-256は`618c1c0b55a8cf5dc8d82f01dd384419a85dc12e0ba5b8e8dca1c82b357cceb2`である。

## 開発環境の起動

### アプリ

```powershell
bun install
bun run tauri dev
```

### OCR用Python

```powershell
python -m venv analyzer/ocr_evaluation/.venv-paddle
analyzer/ocr_evaluation/.venv-paddle/Scripts/python.exe -m pip install --upgrade pip
analyzer/ocr_evaluation/.venv-paddle/Scripts/python.exe -m pip install `
  -r analyzer/ocr_evaluation/requirements-paddle.txt
```

初回は`korean_PP-OCRv5_mobile_rec`をモデル用ディレクトリへ取得する。
動画フレーム抽出にはFFmpegを使用する。

### Ollama

```powershell
ollama serve
ollama pull qwen3:4b-instruct-2507-q4_K_M
ollama show qwen3:4b-instruct-2507-q4_K_M
```

Ollamaは既定の`127.0.0.1:11434`で起動する。
Issue #13の実装は開始前に`/api/show`でモデル名、digest、内部版が期待値と一致するか確認する。
共有のOllamaサービスは解析キャンセル時に停止しない。

評価用の既存処理を再確認するときだけ、次のコマンドを使う。

```powershell
analyzer/ocr_evaluation/.venv-paddle/Scripts/python.exe `
  analyzer/translation_evaluation/video_dataset.py `
  --video "<開発用動画>" `
  --specification analyzer/translation_evaluation/talon_development.json `
  --output "<Git管理外の新規出力先>" `
  --model-dir "<Paddleモデル用ディレクトリ>"

python analyzer/translation_evaluation/evaluate.py `
  --model qwen3:4b-instruct-2507-q4_K_M `
  --dataset "<上で生成したtranslation-dataset.json>" `
  --prompts analyzer/translation_evaluation/prompts-instruct.json `
  --primary-only `
  --output "<Git管理外の新規出力先>"
```

これらは評価再現用であり、Issue #13の完了条件を満たすアプリ操作ではない。

## 完了済み

- OCRエンジンと固定前処理の比較
- Ollama実モデルによるA、B、C、D条件の翻訳比較
- モデル内部版、設定、digest、所要時間、資源情報の保存
- 出典と確認状態を持つLoL辞書
- AI暫定採点と人手確認状態を分離するデータ構造
- 開発用2動画と観測済み基準動画のOCRおよび翻訳結果
- 複数フレームOCR候補の限定検証と不採用判断
- 最終独立評価用動画`Di7pDd0YPw4`の未観測予約
- 部分的な人手評価の原本保存

## 未完了

- 60字幕すべての原文確認と人手採点
- AI暫定採点の人手による修正と正式な誤訳率
- `Di7pDd0YPw4`を使う最終独立評価
- OCRと翻訳の実用品質に関する採用判断
- 字幕変化検出と字幕タイムライン
- 解析の進捗、キャンセル、失敗復旧
- 字幕同期、修正、再翻訳、JSON保存と再読込
- Pythonランタイム、OCRモデル、Ollamaモデルの配布

未確認項目を解消した扱いにはしない。
Issue #13では精度評価を再開せず、現在の基準構成で短区間の統合動作を確認する。

## ブランチとPR

- PR #10：OCRとローカル翻訳の評価基盤、実測、辞書、部分的な人手評価
- PR #12：3枚、7枚、15枚と背景抑制の追加OCR検証
- Issue #13：上記から最小限を再利用するアプリ統合

PR #10とPR #12はDraftのまま保持し、評価タスクの完了やマージを意味しない。
Issue #13の実装PRでは`Refs #13`を使い、評価用コード一式を無条件に取り込まない。
