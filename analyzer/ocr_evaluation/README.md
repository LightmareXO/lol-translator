# OCR比較検証ツール

Issue #8で、LoL韓国語字幕に対するPaddleOCRとEasyOCRをWindows x64のCPU環境で比較するためのツールです。
アプリ本体のPython環境にはOCR依存を追加せず、エンジンごとに隔離した仮想環境を使います。

実測結果と採用判断は [REPORT.md](REPORT.md)、画像単位の結果は [results/details.csv](results/details.csv) にあります。

## 固定した構成

- PaddleOCR 3.7.0 / PaddlePaddle 3.4.0 / `korean_PP-OCRv5_mobile_rec`
- EasyOCR 1.7.2 / PyTorch 2.14.0 CPU / `korean_g2`
- CPU 4スレッド、バッチサイズ1、ウォームアップ3回
- 認識専用。入力した字幕範囲全体を1行として読む
- 前処理は無加工、2倍拡大、グレースケール、固定コントラスト補正の4条件

直接依存は `requirements-*.txt`、実測環境の全バージョンは `requirements-*-lock.txt` に保存しています。

## 1. 仮想環境

PowerShellでリポジトリのルートから実行します。仮想環境名は `.gitignore` の対象です。

```powershell
python -m venv analyzer/ocr_evaluation/.venv-paddle
analyzer/ocr_evaluation/.venv-paddle/Scripts/python.exe -m pip install --upgrade pip
analyzer/ocr_evaluation/.venv-paddle/Scripts/python.exe -m pip install `
  -r analyzer/ocr_evaluation/requirements-paddle.txt

python -m venv analyzer/ocr_evaluation/.venv-easyocr
analyzer/ocr_evaluation/.venv-easyocr/Scripts/python.exe -m pip install --upgrade pip
analyzer/ocr_evaluation/.venv-easyocr/Scripts/python.exe -m pip install `
  -r analyzer/ocr_evaluation/requirements-easyocr.txt
```

WindowsではEasyOCRより先にCPU版PyTorchを入れることが公式に案内されています。
この要件ファイルはPyTorchのCPU wheel indexを追加済みです。

## 2. 実画像の再抽出

動画の絶対パスだけをローカルで指定します。
スクリプトはSHA-256と1920×1080の解像度を検証し、manifestの正規化座標をピクセルへ変換します。

```powershell
python analyzer/ocr_evaluation/extract_frames.py `
  --manifest analyzer/ocr_evaluation/manifest.json `
  --video "<テスト動画の絶対パス>" `
  --output-dir analyzer/ocr_evaluation/work/images
```

出力画像と、絶対パスを含む `extraction.json` は `work/` 以下に置き、Gitへ追加しません。

## 3. 各方式の実行

例として無加工条件を実行します。
初回だけ公式モデルが各 `models/` ディレクトリへ取得されます。

```powershell
analyzer/ocr_evaluation/.venv-paddle/Scripts/python.exe `
  analyzer/ocr_evaluation/run_paddle.py `
  --manifest analyzer/ocr_evaluation/manifest.json `
  --images-dir analyzer/ocr_evaluation/work/images `
  --output analyzer/ocr_evaluation/work/results/paddle-raw.json `
  --model-dir analyzer/ocr_evaluation/models/paddle `
  --preprocessing raw --threads 4 --warmup 3

analyzer/ocr_evaluation/.venv-easyocr/Scripts/python.exe `
  analyzer/ocr_evaluation/run_easyocr.py `
  --manifest analyzer/ocr_evaluation/manifest.json `
  --images-dir analyzer/ocr_evaluation/work/images `
  --output analyzer/ocr_evaluation/work/results/easyocr-raw.json `
  --model-dir analyzer/ocr_evaluation/models/easyocr `
  --preprocessing raw --threads 4 --warmup 3
```

`--preprocessing` を `scale2x`、`grayscale`、`contrast` に変えて、複数OCRを並列実行せず順番に測ります。
各結果には生のOCR出力、NFC正規化後の出力、信頼度、画像読込・前処理時間、推論時間、エラー、起動時間、50ms間隔で測ったピークRSS、依存一覧、実測容量が入ります。

## 4. 集計

8個の結果JSONを `--results` の後へ渡します。

```powershell
python analyzer/ocr_evaluation/aggregate.py `
  --manifest analyzer/ocr_evaluation/manifest.json `
  --results `
    analyzer/ocr_evaluation/work/results/paddle-raw.json `
    analyzer/ocr_evaluation/work/results/paddle-scale2x.json `
    analyzer/ocr_evaluation/work/results/paddle-grayscale.json `
    analyzer/ocr_evaluation/work/results/paddle-contrast.json `
    analyzer/ocr_evaluation/work/results/easyocr-raw.json `
    analyzer/ocr_evaluation/work/results/easyocr-scale2x.json `
    analyzer/ocr_evaluation/work/results/easyocr-grayscale.json `
    analyzer/ocr_evaluation/work/results/easyocr-contrast.json `
  --output-dir analyzer/ocr_evaluation/work/aggregate
```

結果JSONのmanifestハッシュ、画像順、正解文、時刻、タグが一致しない場合は集計を拒否します。
空文字と推論エラーは精度計算から除外せず、空の予測としてCERへ含めます。

## 5. テスト

```powershell
python -m unittest discover -s analyzer -v
```

manifest、正解文の確認状態、パストラバーサル、座標変換、NFC、編集距離、CER、失敗の扱い、パーセンタイル、画像と結果の対応を検証します。
この単体テストとは別に、REPORT.mdの数値は実画像24枚を両OCRで実行した結果です。
