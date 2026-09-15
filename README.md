# LoL Translator

A desktop app for working with Korean subtitles embedded in League of Legends videos.
Currently supports selecting and playing a local video. Subtitle region selection,
OCR, and translation are not implemented yet.

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
playback, pause, seeking to 4m10s, and cancellation. Error states and retry
are covered by the component tests. Test videos are not part of this repository.
