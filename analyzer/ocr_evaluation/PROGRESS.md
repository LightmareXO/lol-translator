# Issue #8 進捗メモ

## 現在地

- Issue #3 のJSON契約と正規化座標を再利用している。
- 元動画はYouTube ID `QK95uTvf7ks`、SHA-256はmanifestに記録した。
- 1920x1080映像上の字幕帯を `x=38, y=832, width=1844, height=130` とした。
- 8種類の字幕について、60fpsの隣接3フレームずつ、計24枚を選定した。
- 正解文は中央フレームと24枚のコンタクトシートを目視して確認した。
- 動画内の字幕はすべて1行だった。2行字幕は別動画を用いた追加検証とし、今回の結果を2行字幕へ一般化しない。

## ローカルデータの再抽出

動画、抽出画像、モデル、仮想環境、結果の一時ファイルはGitへ含めない。

```powershell
python analyzer/ocr_evaluation/extract_frames.py `
  --manifest analyzer/ocr_evaluation/manifest.json `
  --video "<テスト動画の絶対パス>" `
  --output-dir analyzer/ocr_evaluation/work/images
```

抽出時に動画のSHA-256、解像度、manifest、出力の上書きを検証する。

## 次に行うこと

1. PaddleOCRとEasyOCRを分離した仮想環境で、実画像1枚のCPU推論を確認する。
2. 4種類の固定前処理で24枚を逐次実行する。
3. 精度・速度・安定性・ピークメモリ・ディスク使用量を集計する。
4. 実測値、採用判断、Issue #7への引き継ぎを日本語レポートへまとめる。
