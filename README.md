# LoL Translator

League of Legends動画に埋め込まれた韓国語字幕を、ローカル環境でOCRして日本語へ翻訳するデスクトップアプリです。

ユーザーは動画、字幕範囲、解析時間を指定できます。
アプリは再生前にPaddleOCRとOllamaを実行し、翻訳済み字幕を動画時刻に同期させます。
OCR原文、修正後の韓国語、自動翻訳、ユーザー修正訳はJSONへ保存し、再読込できます。

## Development

### アプリの必要条件

Bunと[Tauriの必要条件](https://v2.tauri.app/start/prerequisites/)を用意します。
WindowsではRust、Windowsビルドツール、WebView2が必要です。

Pythonは3.10から3.12を使い、PaddleOCRの評価で固定した依存関係を導入します。

```powershell
python -m venv analyzer/.venv
analyzer/.venv/Scripts/python.exe -m pip install --upgrade pip
analyzer/.venv/Scripts/python.exe -m pip install `
  -r analyzer/ocr_evaluation/requirements-paddle.txt
```

PaddleOCRモデル用のディレクトリを用意し、`korean_PP-OCRv5_mobile_rec`を一度取得します。
既存のモデルディレクトリを`LOL_TRANSLATOR_PADDLE_MODEL_DIR`に指定します。

Ollamaを起動し、固定済みのQwen3 4B Instructを用意します。

```powershell
ollama serve
ollama pull qwen3:4b-instruct-2507-q4_K_M
ollama show qwen3:4b-instruct-2507-q4_K_M
```

FFmpegとFFprobeは`PATH`から起動できる状態にします。
必要な場合は`LOL_TRANSLATOR_FFMPEG`と`LOL_TRANSLATOR_FFPROBE`に実行ファイルの絶対パスを指定できます。

### 開発起動

```powershell
$env:LOL_TRANSLATOR_PYTHON = (Resolve-Path "analyzer/.venv/Scripts/python.exe")
$env:LOL_TRANSLATOR_PADDLE_MODEL_DIR = "<PaddleOCRモデルディレクトリの絶対パス>"
bun install --frozen-lockfile
bun run tauri dev
```

`bun run dev`だけを実行した場合、フロントエンドは開きますが、Tauriのネイティブダイアログと解析コマンドは動作しません。

## Checks

```sh
bun run check
bun run test
bun run build
bun run tauri build --no-bundle
python -m unittest discover -s analyzer -v
cargo test --manifest-path src-tauri/Cargo.toml
cargo clippy --manifest-path src-tauri/Cargo.toml --all-targets -- -D warnings
```

VitestとReact Testing Libraryは解析リクエスト、時刻判定、字幕の消失、韓国語と日本語の修正、保存コマンド、再読込を検証します。
Pythonテストは区間統合、空白正規化、数字と文字差の保持、UTF-8 JSON、修正と再翻訳の状態遷移を検証します。

## 解析の動作

解析時間は1本の「解析区間」バーにある開始・終了のつまみで指定し、秒数欄で微調整できます。
黄色のマーカーは現在の再生位置を示し、再生やシークをしても青い解析区間は変わりません。
「動画全体を解析」を選ぶとバーは全区間を示して操作できなくなり、解除すると直前の部分区間へ戻ります。
3分の上限は実装していません。
初回は1〜3分の区間で起動条件と処理時間を確認してください。

Issue #15以降は、FFmpegからROI画像とPTSを200ms間隔で逐次取得します。
アプリは動画全体をメモリへ読み込みません。
白色と黄色の文字候補マスクを行ごとに追跡し、2サンプル続いた出現、変更、消失を確定します。
確認時刻ではなく最初の候補時刻を境界として保存します。
最小表示時間は初期値0.6秒で、0を指定すると時間による除外を無効にできます。
200ms間隔の3サンプルで詳細変化を確定するため、0.6秒を現在の実用上の下限としています。

区間確定後に鮮明さ、文字候補の完全性、画像の安定性から上位3フレームを選びます。
最初のOCRが空文字または低情報の場合だけ次候補へ進み、1区間のOCRを最大3回に制限します。
OCR試行は一つの字幕レコードへ保存し、試行ごとに字幕を増やしません。
採用原文の翻訳は再生前に1回だけ実行します。

信頼度0.5未満のOCR結果は低情報ノイズとして翻訳へ渡しません。
韓国語を含まない場合は、さらに英数字2文字以上かつ信頼度0.6以上を必要とします。
これは`S`や`SZHT`のような入力から辞書説明を生成する幻覚と、200msごとの表示切替を抑えるためです。

OCRが空文字を返したフレームは「字幕なし」として現在の字幕区間を閉じます。
OCRの例外は「OCR失敗」として時刻とエラーを保存します。
認識専用OCRは字幕の有無を判定する検出モデルではないため、背景や固定UIを文字として認識する可能性は残ります。

2行字幕は手動分割を有効にし、ROI上端からの分割率を指定します。
上段と下段を別の行IDとして追跡し、同時表示の関係をJSONへ保存します。
再生時は両方の行を読み順に表示します。

OCRはPaddleOCR 3.7.0、PaddlePaddle 3.4.0、`korean_PP-OCRv5_mobile_rec`、グレースケール後のOtsu二値化、CPU 4スレッドを基準にしています。
Otsu二値化は、開発用12字幕の比較で固定コントラスト補正よりCERが低かったため既定値にしています。
この比較の正解文はAI暫定転記であり、人手確認済みの独立評価ではありません。
翻訳は`qwen3:4b-instruct-2507-q4_K_M`、プロンプト版`ko-ja-v4-instruct-nonthinking`、辞書版`lol-ko-ja-v1`を使います。
設定は`think=false`、`temperature=0.7`、`top_p=0.8`、`top_k=20`、`min_p=0`、`seed=42`、`num_ctx=4096`、`num_predict=384`です。
現在のプロンプトは直前字幕を参照しないため、韓国語の修正時に後続字幕を`stale`にする必要はありません。

## 保存と再翻訳

保存JSONの現行版はスキーマv2です。
動画のパスとSHA-256、解析区間、ROI、画像検出設定、字幕時刻、行ID、字幕画像、OCR試行、OCR生出力、修正内容、処理エラーを含みます。
スキーマv1は読込時にv2へ移行し、既存境界、OCR生出力、ユーザー修正訳を変更しません。
Tauriは一時ファイルをディスクへ確定してから、保存先を原子的に置き換えます。

韓国語の修正はOCR生出力を上書きせず、対象字幕の翻訳状態を`stale`にします。
「この字幕を再翻訳」と「翻訳だけ全件再実行」は、保存済みOCRからOllamaだけを実行します。
ユーザーが修正した日本語は保持し、自動翻訳だけを更新します。

## Issue #13の手動スモークテスト

独立評価用に確保した動画ではなく、観測済みの開発用動画で実施します。

1. アプリを起動し、「動画を選択」からローカル動画を開く。
2. 「字幕範囲を指定」で字幕を囲み、必要なら手動2行分割を有効にする。
3. 「解析区間」バーの開始・終了つまみ、または秒数欄で区間を指定する。初回は60秒程度を使い、「解析を開始」を押す。
4. 工程と進捗が更新され、完了後に日本語字幕と字幕一覧が表示されることを確認する。
5. 動画直下に韓国語原文と日本語訳が表示されることを確認する。一覧の字幕を押して該当時刻へ移動し、再生、一時停止、シーク、再生速度変更で字幕が追従することを確認する。
6. 字幕のない時刻では表示が消えることを確認する。
7. 韓国語を修正して対象字幕を再翻訳し、OCR生出力とユーザー修正の日本語が上書きされないことを確認する。
8. JSONを保存し、「保存済みJSONを開く」から再読込する。動画、解析範囲、字幕、韓国語と日本語の修正が復元されることを確認する。
9. 別の解析を開始してキャンセルし、進捗がキャンセル表示になり、解析プロセスが残らないことを確認する。

Ollama停止、対象モデル不足、存在しないPython環境などの失敗は、画面のエラー文で不足条件を確認します。

字幕パネルは動画の外側にあるため、WebViewの動画コントロールからネイティブ全画面表示へ切り替えると表示されません。アプリ画面内での通常再生を基準にしています。

## 実測と制約

Issue #15の画像方式を既定方式として採用しました。
ユーザーが実アプリで字幕の表示安定性を確認し、旧方式より使用感がよいと判断したためです。
この採用は、境界精度が旧方式を上回ったことを意味しません。
QKの120〜180秒では主要字幕5件をすべて拾いましたが、字幕でない候補を10件作成しました。
Ns8の600〜660秒では、主要字幕の再現率が旧方式75.0%に対して58.3%となり、異なる字幕の誤統合も増えました。
一方、OCR呼出は約88〜89%減り、同じ字幕の翻訳が細かく切り替わる問題も体感上改善しました。
既知の見逃しと誤統合は、採用後の品質課題として残します。
再現条件と37件の画像確認結果は`analyzer/detection_evaluation/REPORT.md`に記録しています。

以下はIssue #13で確認した旧方式の実測値です。

Windowsの開発PCで、観測済み開発用動画の0〜60秒を実モデルで処理しました。
300フレームから、低信頼候補の除外と隣接OCR揺れの統合後に34字幕区間を生成し、OCRと翻訳の処理エラーは0件でした。
保存JSONは字幕画像を含むため、この条件で約7.2MBでした。
統合前に見られた、1字幕中の細かな訳の切り替わりと、無関係なフラッシュの辞書説明は再現しませんでした。

手動2行分割は、別の開発用動画の480〜600秒を分割位置0.38で実モデル処理しました。
600フレームから49字幕区間を生成し、OCRと翻訳の処理エラーは0件でした。
11区間には統合前のOCR候補を保存し、上段と下段の順序も保持しています。
保存JSONは約17.4MBでした。認識文には行端のノイズが残っています。

翻訳時のLoL辞書は、OCR原文に実際に現れる語だけをモデルへ渡します。
無関係な辞書説明をOCRノイズから生成する問題を抑えますが、高信頼のOCR誤認や翻訳誤りそのものをなくすものではありません。

15分程度の動画ファイルの選択と再生に上限はありませんが、15分全体のOCRと翻訳は未検証です。
固定200ms抽出は短い字幕を見逃す場合があり、区間境界に最大200ms程度の誤差が生じます。
保守的な統合条件を外れるOCR揺れは、同じ字幕でも複数区間へ分かれます。
これらはIssue #13の操作統合を阻げる不具合とは分けて扱います。

現在のビルドにPythonランタイム、PaddleOCRモデル、Ollama、Qwen3モデルは同梱していません。
インストーラー配布はIssue #13の対象外です。

## Subtitle region selection

Choose **字幕範囲を指定** to pause playback and draw one rectangle over the image.
Drag in any direction; drawing again replaces the previous rectangle.
Choose **範囲指定を終了** to restore playback controls, or **範囲をクリア** to remove
the rectangle. A cancelled or zero-area drag keeps the previous selection.
With the selection surface focused, Enter/Space selects the whole image and
Escape cancels the drag (or exits selection mode if no drag is active).

Geometry in `src/subtitleRegion.ts` matches centered `object-fit: contain`,
using the video's intrinsic dimensions and measured element size. The selectable
surface excludes display letterboxing; it does not detect black pixels encoded
in the video itself. Normalized coordinates survive window resizing.
`VideoPlayer.onRegionChange` receives `{ x, y, width, height }` in the range
0..1, or `null` when cleared. Only one region is kept, in memory for the current
video. Selecting another file (including reloading the same file) resets it.

Tests cover both black-bar orientations, matching aspect ratios, forward/reverse
drags, normalization, boundaries, reselection, cancellation, pointer capture,
resizing, and preserving playback controls.

## Local video playback

- Choose one file using the OS file picker. Use the video controls to play, pause, and seek.
- Cancelling selection keeps the existing video and playback position.
- The picker lists MP4, WebM, M4V, MOV, MKV, and AVI. An extension alone does not
  guarantee playback: codec support depends on the system WebView.
- MP4 with H.264 video and AAC audio is the tested baseline; WebM support also
  depends on the codecs and WebView.
- The dialog grants runtime asset access to selected files. No directory-wide
  access is configured. Videos are read locally without uploading or copying them.
- Selected paths are held in memory and are not restored on restart.

### Desktop smoke test

1. Open an H.264/AAC MP4, including a filename containing Korean text and spaces.
2. Play and pause; verify the picture changes during playback and stops when paused.
3. Seek to a later scene; verify both the time and picture change.
4. Open the picker again and cancel; verify the previous video and time remain.
5. Choose a different video, then a broken video file; verify replacement and the error message.

On Windows, a 1920×1080 H.264/AAC MP4 (8m19s) was verified for loading,
playback, pause, seeking to 4m10s, and cancellation. An intentionally invalid MP4
was also verified to display the playback error. Component tests cover retry,
errors after loading, and ignoring events from a replaced video.
Test videos are not part of this repository.
