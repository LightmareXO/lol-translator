# 音声と画面字幕の対応検証

韓国語音声をローカルで文字起こしし、既存の字幕IDと動画IDへ時刻付きで対応づける評価用コードです。

有料APIとクラウド音声認識は使いません。

共有の依存定義、既存OCR成果物、翻訳評価ファイルも変更しません。

実測結果と制約は [REPORT.md](REPORT.md)、行単位の引き継ぎデータは [results/correspondence.csv](results/correspondence.csv)、未加工のASR出力は [results/transcripts.raw.json](results/transcripts.raw.json) にあります。

## 固定した構成

- faster-whisper 1.2.1
- CTranslate2 4.8.2
- `large-v3-turbo`
- CPU `int8`、8スレッド、1 worker
- 韓国語固定、transcribe、beam size 5
- 単語時刻あり、VADなし、前文条件づけなし
- `initial_prompt=None`、hotwordなし、字幕とOCR出力の入力なし

この検証ではモデルと設定を一つだけ実行しました。

## 仮想環境

PowerShellでリポジトリのルートから実行します。

```powershell
python -m venv analyzer/audio_evaluation/.venv
analyzer/audio_evaluation/.venv/Scripts/python.exe -m pip install --upgrade pip
analyzer/audio_evaluation/.venv/Scripts/python.exe -m pip install `
  -r analyzer/audio_evaluation/requirements.txt
```

実測環境の全パッケージは `requirements-lock.txt` に保存しています。

## 音声抽出

動画はGitへ追加せず、絶対パスで指定します。

スクリプトは動画のSHA-256と長さを検証し、各表示区間の前後2秒を16kHz、モノラル、PCM WAVへ変換します。

```powershell
analyzer/audio_evaluation/.venv/Scripts/python.exe `
  -m analyzer.audio_evaluation.extract_audio `
  --manifest analyzer/audio_evaluation/manifest.json `
  --video "QK95uTvf7ks=<QK95uTvf7ks動画の絶対パス>" `
  --video "Ns8VQexyJes=<Ns8VQexyJes動画の絶対パス>" `
  --output-dir analyzer/audio_evaluation/work/audio_clips
```

抽出音声、動画の絶対パスを検証する作業情報、コンタクトシートは `work/` 以下に置き、Gitへ追加しません。

## 文字起こし

初回実行時は変換済みモデルを `models/` 以下へ取得します。

```powershell
analyzer/audio_evaluation/.venv/Scripts/python.exe `
  -m analyzer.audio_evaluation.transcribe `
  --manifest analyzer/audio_evaluation/manifest.json `
  --audio-dir analyzer/audio_evaluation/work/audio_clips `
  --model-dir analyzer/audio_evaluation/models `
  --output analyzer/audio_evaluation/results/transcripts.raw.json
```

各segmentと単語には、切り出し音声内の時刻を表す `clip_*_seconds` と、元動画の時刻を表す `source_*_seconds` を別々に保存します。

生出力を得た後の判断は `reviews.json` にだけ記録し、`transcripts.raw.json` を字幕へ合わせて修正しません。

## 対応表の生成

```powershell
python -m analyzer.audio_evaluation.build_correspondence `
  --manifest analyzer/audio_evaluation/manifest.json `
  --transcripts analyzer/audio_evaluation/results/transcripts.raw.json `
  --reviews analyzer/audio_evaluation/reviews.json `
  --output-dir analyzer/audio_evaluation/results
```

`correspondence.csv` はUTF-8 BOM付きです。

翻訳担当は `confirmation_status` を先に確認し、`unconfirmed_no_audio_input` の行を確定した発話として使わないでください。

## テスト

```powershell
python -m unittest analyzer.audio_evaluation.test_core -v
```

テストはmanifestの時刻関係、ID重複とパストラバーサル、クリップ時刻から元動画時刻への変換、t15の2行分離、未確認候補を確認済み件数へ入れない集計を検証します。
