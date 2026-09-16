# LoL Translator

A desktop app for working with Korean subtitles embedded in League of Legends videos.
Currently supports selecting and playing a local video, and choosing one subtitle
region, and passing a JSON request to Python for input validation.
The app runtime does not perform OCR or translation yet. Issue #8's reproducible
OCR evaluation is complete under `analyzer/ocr_evaluation`; it selects PaddleOCR
PP-OCRv5 Korean recognition for the later runtime integration.

## Development

Install Bun and the [Tauri prerequisites](https://v2.tauri.app/start/prerequisites/)
(including Rust, Windows build tools, and WebView2 on Windows), then run:

```sh
bun install --frozen-lockfile
bun run tauri dev
```

Use the desktop app to test file selection. Running only `bun run dev` starts
the frontend in a browser without the native Tauri dialog and asset protocol.

## Checks

```sh
bun run check
bun run test
bun run build
bun run tauri build --no-bundle
python -m unittest discover -s analyzer -v
cargo test --manifest-path src-tauri/Cargo.toml --lib -- --include-ignored
```

Vitest and React Testing Library cover empty/loading/error states, cancellation,
replacement, retrying the same file, and preventing concurrent selection dialogs.
Native dialogs and actual media decoding require a desktop smoke test.

## Python request validation

Install Python 3.10 through 3.12 and create the project-only environment with
`python -m venv analyzer/.venv`. No third-party Python packages are needed yet.
Development builds use `analyzer/.venv/Scripts/python.exe` and never fall back to
the system `python` on PATH. Set `LOL_TRANSLATOR_PYTHON` to an existing Python
executable's absolute path only when an explicit development override is needed.
Packaged builds will use `analyzer-runtime/python.exe`; bundling that runtime is
tracked separately and must be completed before distributing the app.

After selecting a video and subtitle region, choose **Pythonへ渡して入力を検証**.
Tauri writes UTF-8 JSON under the user's `.lol-translator/requests` directory,
then runs `analyzer/main.py <request-path>` without a shell or a visible console.
On Windows, this is normally `%USERPROFILE%/.lol-translator/requests`. It is kept
outside `AppData` because Microsoft Store Python redirects that directory and
otherwise cannot see a request created by Tauri.
The success message displays the exact saved path. Requests have unique names,
are not automatically deleted, and include the video's local path; do not publish
them unintentionally. The video itself is never copied or uploaded.
If Python fails, an error is shown and the saved JSON remains available for diagnosis.

The JSON contract is:

```json
{
  "video_path": "C:/path/to/video.mp4",
  "subtitle_region": { "x": 0.1, "y": 0.7, "width": 0.8, "height": 0.2 }
}
```

Rust and Python check that the video path is absolute and points to a file,
and that the rectangle has positive area and fits within normalized image bounds.
Python also rejects malformed JSON and incorrect field types. This checks file
existence, not video decoding, OCR quality, or subtitle contents.
Run a saved request directly with `python analyzer/main.py <request-path>`;
success prints the validated JSON and exits with 0, while failure writes an error
to stderr and exits with 1. Quote paths that contain spaces.

Changing or clearing the region, or selecting a video again, resets the displayed
result. An already-started validation still finishes for its original input.
Rust tests include an opt-in real-Python round trip (`--include-ignored` above);
the default Rust test run does not require Python.

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
