# LoL Translator

A desktop app for working with Korean subtitles embedded in League of Legends videos.
Currently supports selecting and playing a local video, and choosing one subtitle
region. OCR and translation are not implemented yet.

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
```

Vitest and React Testing Library cover empty/loading/error states, cancellation,
replacement, retrying the same file, and preventing concurrent selection dialogs.
Native dialogs and actual media decoding require a desktop smoke test.

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
