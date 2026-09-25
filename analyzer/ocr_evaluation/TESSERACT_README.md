# Issue #19の再実行手順

## 必要なもの

この比較はWindows x64のCPU環境を対象とする。
アプリ本体のOCR実行経路とは分離している。

次の依存関係が必要になる。

- PaddleOCR 3.7.0とPaddlePaddle 3.4.0を入れた既存の評価用Python環境
- OpenCV、NumPy、psutil
- Tesseract 5.4.0.20240606のWindows x64版
- 公式`tessdata_fast`と`tessdata_best`の`kor.traineddata`および`eng.traineddata`
- ffmpeg
- 開発用動画`QK95uTvf7ks`と`Ns8VQexyJes`

Python環境は既存のPaddleOCR手順で作成する。

```powershell
python -m venv analyzer/ocr_evaluation/.venv-paddle
analyzer/ocr_evaluation/.venv-paddle/Scripts/python.exe -m pip install --upgrade pip
analyzer/ocr_evaluation/.venv-paddle/Scripts/python.exe -m pip install `
  -r analyzer/ocr_evaluation/requirements-paddle-lock.txt
```

Tesseractの配布版と公式言語モデルの版およびSHA-256は`tesseract_toolchain.json`に固定している。
実行前に、ファイルが存在し、SHA-256が一致することを確認する。
不足時は評価コマンドが日本語のエラーを返し、未実測結果を作らない。

## Tesseractと公式言語モデル

Windows配布版は次の版を使う。

```powershell
winget install `
  --id UB-Mannheim.TesseractOCR `
  --exact `
  --version 5.4.0.20240606 `
  --accept-package-agreements `
  --accept-source-agreements
```

言語モデルはGit管理外の作業ディレクトリへ置く。
次の例では`<TOOLS>`を任意の絶対パスへ置き換える。

```powershell
New-Item -ItemType Directory -Force `
  -Path <TOOLS>/tessdata/fast,<TOOLS>/tessdata/best

curl.exe -L --fail `
  https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/87416418657359cb625c412a48b6e1d6d41c29bd/kor.traineddata `
  -o <TOOLS>/tessdata/fast/kor.traineddata
curl.exe -L --fail `
  https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/87416418657359cb625c412a48b6e1d6d41c29bd/eng.traineddata `
  -o <TOOLS>/tessdata/fast/eng.traineddata
curl.exe -L --fail `
  https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/e12c65a915945e4c28e237a9b52bc4a8f39a0cec/kor.traineddata `
  -o <TOOLS>/tessdata/best/kor.traineddata
curl.exe -L --fail `
  https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/e12c65a915945e4c28e237a9b52bc4a8f39a0cec/eng.traineddata `
  -o <TOOLS>/tessdata/best/eng.traineddata
```

`Get-FileHash -Algorithm SHA256`の結果を`tesseract_toolchain.json`と照合する。

## 分割の固定と画像準備

`tesseract_evaluation.json`はTesseract出力を見る前に固定した仕様である。
同じ字幕の隣接フレームを別分割へ移動しない。
既存の`verified`表記を人手確認済みへ読み替えない。

次の例では、作業ディレクトリを`<WORK>`、Pythonを`<PYTHON>`、動画を`<QK_VIDEO>`と`<NS8_VIDEO>`とする。
初期24画像は、既存手順で`<INITIAL_IMAGES>`へ抽出済みであることを前提にする。

```powershell
<PYTHON> -m analyzer.ocr_evaluation.tesseract_pipeline prepare `
  --spec analyzer/ocr_evaluation/tesseract_evaluation.json `
  --initial-images <INITIAL_IMAGES> `
  --video "Ns8VQexyJes=<NS8_VIDEO>" `
  --output <WORK>
```

この処理は動画SHA-256、追加4字幕の切り出し、全28画像のSHA-256を検証または記録する。
絶対パスを含まない`prepared.json`を作る。

## Adaptive設定の限定比較

Adaptive候補は3件だけを開発用8字幕で比較する。
この出力を見て候補を追加した場合、同じ結果を固定済み評価として扱えない。

```powershell
<PYTHON> -m analyzer.ocr_evaluation.tune_adaptive `
  --spec analyzer/ocr_evaluation/tesseract_evaluation.json `
  --prepared <WORK>/prepared.json `
  --output <WORK>/adaptive-tuning.json `
  --paddle-model-dir <PADDLE_MODEL_DIR> `
  --tesseract "C:/Program Files/Tesseract-OCR/tesseract.exe" `
  --fast-tessdata <TOOLS>/tessdata/fast `
  --best-tessdata <TOOLS>/tessdata/best `
  --threads 4
```

保存済み結果では`blockSize=31`、`C=11`、`THRESH_BINARY_INV`を選択した。

## 7前処理の生成

```powershell
<PYTHON> -m analyzer.ocr_evaluation.tesseract_pipeline materialize `
  --prepared <WORK>/prepared.json `
  --output <WORK>/inputs `
  --adaptive-block-size 31 `
  --adaptive-c 11
```

196枚の処理済み画像と`inputs.json`をGit管理外へ作る。
`inputs.json`は各画像のSHA-256、処理順、極性、非使用の拡大、平滑化、形態学処理を記録する。

## 3方式の逐次実行

3方式は並列実行しない。
各方式で最初の画像を1回測った後、3回ウォームアップし、28画像と7前処理を実行する。

```powershell
<PYTHON> -m analyzer.ocr_evaluation.tesseract_pipeline run `
  --engine paddle `
  --prepared <WORK>/prepared.json `
  --inputs <WORK>/inputs/inputs.json `
  --output <WORK>/paddle.json `
  --model-dir <PADDLE_MODEL_DIR> `
  --threads 4 `
  --warmup 3

<PYTHON> -m analyzer.ocr_evaluation.tesseract_pipeline run `
  --engine fast `
  --prepared <WORK>/prepared.json `
  --inputs <WORK>/inputs/inputs.json `
  --output <WORK>/tesseract-fast.json `
  --tesseract "C:/Program Files/Tesseract-OCR/tesseract.exe" `
  --tessdata <TOOLS>/tessdata/fast `
  --threads 4 `
  --warmup 3

<PYTHON> -m analyzer.ocr_evaluation.tesseract_pipeline run `
  --engine best `
  --prepared <WORK>/prepared.json `
  --inputs <WORK>/inputs/inputs.json `
  --output <WORK>/tesseract-best.json `
  --tesseract "C:/Program Files/Tesseract-OCR/tesseract.exe" `
  --tessdata <TOOLS>/tessdata/best `
  --threads 4 `
  --warmup 3
```

Tesseractは1行ごとにCLIプロセスを起動する。
記録時間にはプロセス起動とモデル読込を含む。
PaddleOCRは常駐モデルの呼び出し時間と初期化時間を分けて記録する。

## 集計

```powershell
<PYTHON> -m analyzer.ocr_evaluation.tesseract_pipeline aggregate `
  --results `
    <WORK>/paddle.json `
    <WORK>/tesseract-fast.json `
    <WORK>/tesseract-best.json `
  --output <WORK>/summary.json
```

主集計は代表12画像だけを使う。
隣接フレーム24画像の精度と変化率は別フィールドへ保存する。
失敗と空出力は除外しない。

## 背景抑制の限定比較

先にPR #12の固定済み7フレーム、0.5秒幅の出力を`<MULTIFRAME_WORK>`へ再現する。
その後、主比較で各エンジンのCERが最小だった前処理だけを適用する。

```powershell
<PYTHON> -m analyzer.ocr_evaluation.background_comparison `
  --source <MULTIFRAME_WORK> `
  --summary <WORK>/summary.json `
  --output <WORK>/background.json `
  --paddle-model-dir <PADDLE_MODEL_DIR> `
  --tesseract "C:/Program Files/Tesseract-OCR/tesseract.exe" `
  --fast-tessdata <TOOLS>/tessdata/fast `
  --best-tessdata <TOOLS>/tessdata/best `
  --adaptive-block-size 31 `
  --adaptive-c 11 `
  --threads 4
```

この比較は開発用9字幕の探索であり、独立評価ではない。
新しい背景除去方式を追加していない。

## テスト

```powershell
<PYTHON> -m unittest discover -s analyzer -v
```

テストはNFC、CER、空出力と失敗、CLI末尾改行、2行結合、7前処理、極性、固定分割、結果件数、ローカルパス混入を確認する。
単体テストとは別に、Tesseract fast、best、PaddleOCRを実画像で実行した結果をGitへ保存している。

## Tesseract.js 5.1.1のWeb相当参照条件

既存の`<WORK>/images`と`prepared.json`をそのまま再利用する。
Node.js用の依存はPython評価環境から分離している。

```powershell
Push-Location analyzer/ocr_evaluation/tesseract_js
npm ci --ignore-scripts
Pop-Location

node analyzer/ocr_evaluation/tesseract_js/run_reference.mjs `
  --prepared <WORK>/prepared.json `
  --images <WORK>/images `
  --output <JS_WORK>/reference.json `
  --languages kor `
  --lang-path analyzer/ocr_evaluation/tesseract_js/node_modules/@tesseract.js-data/kor/4.0.0_best_int `
  --gzip true `
  --psm 6 `
  --model-label tesseract_js_4.0.0_best_int `
  --condition-id tesseract_js_kor_psm6
```

Tesseract.jsとローカル`original`画像の画素一致、結果と正解文の対応、背景ノイズの指標、エラー分離を検査して集計する。
OpenCVとNumPyを含む既存のPaddleOCR評価用Pythonを使う。

```powershell
<PYTHON> -m analyzer.ocr_evaluation.tesseract_js_reference `
  --reference <JS_WORK>/reference.json `
  --local-summary <WORK>/summary.json `
  --local-details <WORK>/tesseract-details.json `
  --prepared <WORK>/prepared.json `
  --source-images <WORK>/images `
  --local-original-images <WORK>/inputs/original `
  --runner analyzer/ocr_evaluation/tesseract_js/run_reference.mjs `
  --package-lock analyzer/ocr_evaluation/tesseract_js/package-lock.json `
  --output <JS_WORK>/comparison.json
```

参照条件のCERが現在のローカル原画像条件の最良値を下回った場合だけ、言語、PSM、モデルの切り分けへ進む。
保存済み結果では改善しなかったため、切り分けは実行していない。
