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

解析時間は開始秒と終了秒で指定するか、動画全体を選択します。
3分の上限は実装していません。
初回は1〜3分の区間で起動条件と処理時間を確認してください。

FFmpegは指定区間のROI画像を200ms間隔で一時ディレクトリへ抽出します。
アプリは動画全体をメモリへ読み込みません。
PaddleOCRの結果はUnicode正規化後に空白だけを除き、まず隣接する完全一致字幕を統合します。
さらに、時間的に隣接する候補の韓国語部分が近い場合はOCR揺れとして統合します。
両方で認識できた数字やQ・W・E・R・D・Fが食い違う候補は統合せず、1.2秒未満しか確認できない一過性候補は表示対象外にします。
1回分の未認識は200msまで橋渡しします。
代表文には最も信頼度が高い候補を使い、統合対象となった異なるOCR候補と時刻はJSONへ残します。
両方で認識できた数字やスキル文字が異なる字幕と、200msを超える字幕なし区間は統合しません。

信頼度0.5未満のOCR結果は低情報ノイズとして翻訳へ渡しません。
韓国語を含まない場合は、さらに英数字2文字以上かつ信頼度0.6以上を必要とします。
これは`S`や`SZHT`のような入力から辞書説明を生成する幻覚と、200msごとの表示切替を抑えるためです。

OCRが空文字を返したフレームは「字幕なし」として現在の字幕区間を閉じます。
OCRの例外は「OCR失敗」として時刻とエラーを保存します。
認識専用OCRは字幕の有無を判定する検出モデルではないため、背景や固定UIを文字として認識する可能性は残ります。

2行字幕は手動分割を有効にし、ROI上端からの分割率を指定します。
上段、下段の順でOCR結果を保存します。

OCRはPaddleOCR 3.7.0、PaddlePaddle 3.4.0、`korean_PP-OCRv5_mobile_rec`、1.5倍の固定コントラスト補正、CPU 4スレッドを基準にしています。
翻訳は`qwen3:4b-instruct-2507-q4_K_M`、プロンプト版`ko-ja-v4-instruct-nonthinking`、辞書版`lol-ko-ja-v1`を使います。
設定は`think=false`、`temperature=0.7`、`top_p=0.8`、`top_k=20`、`min_p=0`、`seed=42`、`num_ctx=4096`、`num_predict=384`です。
現在のプロンプトは直前字幕を参照しないため、韓国語の修正時に後続字幕を`stale`にする必要はありません。

## 保存と再翻訳

保存JSONにはスキーマ版、動画のパスとSHA-256、解析区間、ROI、OCRと翻訳の設定、字幕時刻、字幕画像、OCR生出力、修正内容、処理エラーを含みます。
Tauriは一時ファイルをディスクへ確定してから、保存先を原子的に置き換えます。

韓国語の修正はOCR生出力を上書きせず、対象字幕の翻訳状態を`stale`にします。
「この字幕を再翻訳」と「翻訳だけ全件再実行」は、保存済みOCRからOllamaだけを実行します。
ユーザーが修正した日本語は保持し、自動翻訳だけを更新します。

## Issue #13の手動スモークテスト

独立評価用に確保した動画ではなく、観測済みの開発用動画で実施します。

1. アプリを起動し、「動画を選択」からローカル動画を開く。
2. 「字幕範囲を指定」で字幕を囲み、必要なら手動2行分割を有効にする。
3. 開始と終了を指定する。初回は60秒程度を使い、「解析を開始」を押す。
4. 工程と進捗が更新され、完了後に日本語字幕と字幕一覧が表示されることを確認する。
5. 一覧の字幕を押して該当時刻へ移動し、再生、一時停止、シーク、再生速度変更で字幕が追従することを確認する。
6. 字幕のない時刻では表示が消えることを確認する。
7. 韓国語を修正して対象字幕を再翻訳し、OCR生出力とユーザー修正の日本語が上書きされないことを確認する。
8. JSONを保存し、「保存済みJSONを開く」から再読込する。動画、解析範囲、字幕、韓国語と日本語の修正が復元されることを確認する。
9. 別の解析を開始してキャンセルし、進捗がキャンセル表示になり、解析プロセスが残らないことを確認する。

Ollama停止、対象モデル不足、存在しないPython環境などの失敗は、画面のエラー文で不足条件を確認します。

## 実測と制約

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
