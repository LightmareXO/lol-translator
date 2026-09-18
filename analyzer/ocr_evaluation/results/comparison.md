# OCR比較の自動集計

主評価はUnicode NFC後も空白・句読点・改行を保持したCERです。
速度は画像読込を除くOCR推論と、画像読込・固定前処理を含む合計を分けています。

| エンジン | モデル | 前処理 | CER | 完全一致 | 推論中央値 (ms) | 推論p95 (ms) | 揺れ率 | Peak RSS (MiB) |
|---|---|---|---:|---:|---:|---:|---:|---:|
| easyocr | korean_g2 | contrast | 0.9649 | 0/24 | 99.7 | 110.5 | 0.7500 | 382.1 |
| easyocr | korean_g2 | grayscale | 0.8947 | 0/24 | 105.2 | 117.0 | 0.6875 | 378.4 |
| easyocr | korean_g2 | raw | 0.8947 | 0/24 | 106.7 | 115.5 | 0.6875 | 376.8 |
| easyocr | korean_g2 | scale2x | 0.9123 | 0/24 | 109.5 | 124.8 | 0.8750 | 387.4 |
| paddleocr | korean_PP-OCRv5_mobile_rec | contrast | 0.1729 | 3/24 | 18.1 | 25.0 | 0.3125 | 365.2 |
| paddleocr | korean_PP-OCRv5_mobile_rec | grayscale | 0.1729 | 0/24 | 21.3 | 38.4 | 0.4375 | 679.5 |
| paddleocr | korean_PP-OCRv5_mobile_rec | raw | 0.1905 | 3/24 | 17.4 | 38.3 | 0.2500 | 680.3 |
| paddleocr | korean_PP-OCRv5_mobile_rec | scale2x | 0.1880 | 2/24 | 15.3 | 23.1 | 0.3125 | 372.2 |
