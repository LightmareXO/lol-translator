# OCR比較検証ツール

Issue #8で、LoL韓国語字幕に対するPaddleOCRとEasyOCRをWindows x64のCPU環境で比較するためのツールです。
アプリ本体のPython環境にはOCR依存を追加せず、エンジンごとに隔離した仮想環境を使います。

単一フレームの実測結果と採用判断は [REPORT.md](REPORT.md)、画像単位の結果は [results/details.csv](results/details.csv) にあります。
複数フレームの限定検証は [MULTIFRAME_REPORT.md](MULTIFRAME_REPORT.md) に分けています。
Tesseract fast/bestとの7前処理比較は [TESSERACT_REPORT.md](TESSERACT_REPORT.md)、再実行手順は [TESSERACT_README.md](TESSERACT_README.md) に分けています。
Tesseract.js 5.1.1のWeb相当参照条件は [TESSERACT_JS_REPORT.md](TESSERACT_JS_REPORT.md) に記録しています。

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

## 5. 複数フレームの限定実験

追加実験は`multiframe_development.json`で固定した開発用2動画・9行だけを使います。
最終評価用として予約した動画は、この仕様とコマンドでは受け付けません。
各字幕の安定表示区間から、同じ時間幅の3・7・15枚と、7枚固定の0.1秒・0.5秒幅を別条件として抽出します。

```powershell
analyzer/ocr_evaluation/.venv-paddle/Scripts/python.exe `
  analyzer/ocr_evaluation/multiframe.py extract `
  --specification analyzer/ocr_evaluation/multiframe_development.json `
  --video "QK95uTvf7ks=<QK開発動画の絶対パス>" `
  --video "Ns8VQexyJes=<Talon開発動画の絶対パス>" `
  --output "<Git管理外の新規出力ディレクトリ>"

analyzer/ocr_evaluation/.venv-paddle/Scripts/python.exe `
  analyzer/ocr_evaluation/multiframe.py recognise `
  --input "<上と同じ出力ディレクトリ>" `
  --model-dir analyzer/ocr_evaluation/models/paddle
```

`results.json`は各フレームのOCR、最高信頼度候補、文字列medoid、合意状態、処理時間を保存します。
最高信頼度候補とmedoidの総処理時間には、候補に使った全フレームのOCR時間を含めます。
合成方式の総処理時間には、画像合成と合成画像1枚のOCR時間を含めます。
画像合成は時間中央値と、白・黄色および輪郭の時間的一貫性を使う固定マスクの2候補だけです。
固定コントラスト補正後の各フレームは`preprocessed/`、合成画像は`composites/`へ保存します。
元画像、前処理後、背景抑制後を並べ、静止UIの残留、細線欠損、切り替わり混入を目視確認できます。
元画像・合成画像・絶対パスはGitへ追加しません。

正解韓国語はAI目視転記の暫定値です。
この実験のCERを人による確認済みの最終精度として扱いません。

## 6. テスト

```powershell
python -m unittest discover -s analyzer -v
```

manifest、正解文の確認状態、パストラバーサル、座標変換、NFC、編集距離、CER、失敗の扱い、パーセンタイル、画像と結果の対応を検証します。
この単体テストとは別に、REPORT.mdの数値は実画像24枚を両OCRで実行した結果です。
