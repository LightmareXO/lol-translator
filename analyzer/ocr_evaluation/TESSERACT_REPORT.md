# Tesseract韓国語字幕OCR比較

## 判定

Tesseract fastとbestは、どちらもアプリ本体へ採用しない。
代表12字幕の最良空白除外CERは、PaddleOCRが0.0617、Tesseract fastが0.5185、Tesseract bestが0.5370だった。
Tesseractの完全一致は、7前処理の全条件で0/12だった。

固定マスクはTesseract内のCERを下げたが、完全一致は0/9のままであり、PaddleOCRの背景抑制なし条件へ届かなかった。
したがって、Tesseractを主方式または補助方式へ切り替える根拠は得られなかった。
アプリ本体のOCR方式は変更していない。

この判定は開発データに対する比較結果である。
人による正解韓国語の確認は0件であり、未使用の独立評価データもないため、実用品質を確認した結果ではない。

## データと確認状態

初期データは`QK95uTvf7ks`の24画像であり、同じ8字幕の前後1フレームと中央フレームからなる。
24画像を24種類の字幕とは数えていない。
精度の主集計には、事前に固定した中央フレーム8画像だけを使った。
24画像全体は隣接フレーム間の出力変化を調べる補助集計へ使った。

追加データは`Ns8VQexyJes`の4字幕である。
内訳は1行字幕3件と、黄文字の注釈および白文字の発話を含む2行字幕1件である。
主集計は2動画、12字幕、12代表画像となる。
字幕なし画像は含まれないため、字幕なし区間の誤検出率は未測定であり、CERへ混ぜていない。

既存manifestの`verified`は、過去のAI目視転記で使われた表記である。
ユーザーによる韓国語正解文の確認記録とは一致しないため、今回の12字幕はすべて`ai_visual_transcription_provisional`として集計した。
人手確認済み字幕は0件である。

両動画は過去のOCRまたは翻訳評価で内容を確認している。
このため、どちらも開発データとして固定し、独立評価済みとは扱わない。
未使用かつ人手確認済みの独立評価データ不足は未解消である。

画像ID、動画ID、時刻、ROI、行帯、画像SHA-256、正解韓国語、確認状態、開発利用状態は`tesseract-dataset.json`に保存した。
画像、動画、モデル、実行ファイル、仮想環境はGitへ含めていない。

## 実行環境

Windows x64のCPU上で、3方式を同時実行せず順番に測定した。

- **PaddleOCR**：PaddleOCR 3.7.0、PaddlePaddle 3.4.0、`korean_PP-OCRv5_mobile_rec`、CPU 4スレッド
- **Tesseract**：UB Mannheim配布版5.4.0.20240606、`--oem 1`、`-l kor+eng`、`OMP_THREAD_LIMIT=4`
- **fast**：公式`tessdata_fast`のコミット`87416418657359cb625c412a48b6e1d6d41c29bd`
- **best**：公式`tessdata_best`のコミット`e12c65a915945e4c28e237a9b52bc4a8f39a0cec`

Tesseractの配布元、実行ファイル、依存DLL、言語モデルの取得元、容量、SHA-256は`tesseract_toolchain.json`に固定した。
fastおよびbestはLSTM方式だけをサポートするため、公式文書に従って`--oem 1`を指定した。
Tesseract内部の二値化パラメータは上書きせず、5.4.0の`thresholding_method=0`（Otsu）を含む既定値を`--print-parameters`で記録した。
外部のAdaptiveとTesseract内部のOtsuを同じ処理とは扱っていない。

## 前処理

3方式へ同じ寸法の処理済み画像を渡した。
拡大、平滑化、形態学処理、辞書、文字ホワイトリスト、LLM補正は使っていない。

| ID | 処理 |
| --- | --- |
| `original` | OpenCVで復号したカラーROI。外部二値化なし |
| `contrast` | 中間値127.5を固定した1.5倍線形コントラスト補正 |
| `grayscale` | 8ビットグレースケール |
| `otsu` | グレースケール後に大津の二値化 |
| `adaptive` | グレースケール後にGaussian adaptive threshold |
| `contrast_otsu` | コントラスト補正、グレースケール、大津の二値化 |
| `contrast_adaptive` | コントラスト補正、グレースケール、Gaussian adaptive threshold |

コントラスト補正はヒストグラム平坦化ではない。
大津の二値化とAdaptiveは`THRESH_BINARY_INV`を使い、明るい字幕画素を暗くする規則へ固定した。
しかしAdaptive画像の背景全体が明るくなるとは限らず、字幕の白い輪郭と背景輪郭が残る例を確認した。
反転指定そのものを文字抽出の成功とは扱っていない。

28画像では、`otsu`と`contrast_otsu`のPNGハッシュが一致した例は0件だった。
ただし、両条件のOCR結果が近いことはあり、コントラスト補正前後で大津の出力が近くなることを失敗条件にはしていない。

## Adaptive設定の固定

Tesseractの本評価出力を見る前に、開発用8字幕で3候補だけを比較した。
3エンジンの空白除外CERの単純平均を選択基準とし、同率時は`blockSize`と`C`が小さい候補を選ぶ規則を先に固定した。

| blockSize | C | PaddleOCR | fast | best | 3方式平均 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 21 | 7 | 0.2222 | 1.2381 | 1.3968 | 0.9524 |
| 31 | 7 | 0.2222 | 1.1667 | 1.2143 | 0.8677 |
| 31 | 11 | 0.2222 | 1.1032 | 1.1349 | 0.8201 |

主比較には`blockSize=31`、`C=11`を固定した。
同じ設定を`adaptive`と`contrast_adaptive`へ適用し、画像ごとの再選択はしていない。

## 主比較

主指標はUnicode NFC後のコードポイント単位corpus CERである。
空白、句読点、英字の大小、数字、内部改行を保持した。
補助指標としてUnicode空白を除いたCERを計算した。
CLI末尾のCRとLFだけを一律に除き、内部改行は保持した。
失敗と空出力は空予測として採点し、別件数にも記録した。
TesseractのconfidenceとPaddleOCRの認識スコアは同じ確率として比較できないため、方式間の判断指標に使っていない。

1行字幕はTesseractの`--psm 7`で認識した。
2行字幕は固定した行帯を上から下へ分割し、各行を`--psm 7`で認識して改行で結合した。
TesseractのROI全体`--psm 6`は別の診断条件として保存した。

| エンジン | 前処理 | CER | 空白除外CER | 完全一致 | 推論中央値 (ms) | p95 (ms) | 隣接フレーム変化率 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| PaddleOCR | Original | 0.2217 | 0.1049 | 1/12 | 19.6 | 86.2 | 0.2500 |
| PaddleOCR | Contrast | 0.1970 | 0.0679 | 1/12 | 29.4 | 60.4 | 0.3125 |
| PaddleOCR | Grayscale | 0.2020 | 0.1049 | 0/12 | 20.4 | 37.4 | 0.4375 |
| PaddleOCR | Otsu | **0.1232** | **0.0617** | **3/12** | 20.1 | 38.6 | 0.2500 |
| PaddleOCR | Adaptive | 0.3744 | 0.2654 | 0/12 | 20.5 | 39.2 | 0.5625 |
| PaddleOCR | Contrast→Otsu | 0.1379 | 0.0802 | 2/12 | 21.2 | 38.1 | 0.2500 |
| PaddleOCR | Contrast→Adaptive | 0.4187 | 0.3086 | 0/12 | 20.3 | 38.3 | 0.2500 |
| Tesseract fast | Original | 1.5567 | 0.7099 | 0/12 | 179.7 | 266.0 | 0.7500 |
| Tesseract fast | Contrast | 1.2611 | 0.6358 | 0/12 | 165.9 | 251.6 | 0.5000 |
| Tesseract fast | Grayscale | **1.1232** | 0.5556 | 0/12 | 166.3 | 252.6 | 0.8125 |
| Tesseract fast | Otsu | 1.1379 | **0.5185** | 0/12 | 137.3 | 215.4 | 0.7500 |
| Tesseract fast | Adaptive | 1.4335 | 1.2778 | 0/12 | 129.3 | 209.6 | 0.4375 |
| Tesseract fast | Contrast→Otsu | 1.3103 | 0.5926 | 0/12 | 137.4 | 207.2 | 0.9375 |
| Tesseract fast | Contrast→Adaptive | 1.3103 | 1.2037 | 0/12 | 154.1 | 268.1 | 0.8750 |
| Tesseract best | Original | 1.6700 | 0.8519 | 0/12 | 255.6 | 375.3 | 0.6875 |
| Tesseract best | Contrast | 1.3202 | 0.7160 | 0/12 | 235.2 | 368.4 | 0.4375 |
| Tesseract best | Grayscale | **1.1478** | 0.5617 | 0/12 | 235.7 | 366.7 | 0.6250 |
| Tesseract best | Otsu | 1.1724 | **0.5370** | 0/12 | 192.6 | 302.1 | 0.8125 |
| Tesseract best | Adaptive | 1.5074 | 1.3765 | 0/12 | 176.0 | 293.3 | 0.4375 |
| Tesseract best | Contrast→Otsu | 1.1724 | 0.5494 | 0/12 | 200.7 | 295.9 | 0.8125 |
| Tesseract best | Contrast→Adaptive | 1.4236 | 1.3025 | 0/12 | 229.6 | 404.2 | 0.8750 |

前処理の固定候補は主指標のCERで選んだ。
PaddleOCRはOtsu、fastとbestはGrayscaleとなる。
空白除外CERだけならTesseractのOtsuがわずかに低いが、CER、完全一致、出力内容を合わせてもPaddleOCRとの差は縮まらない。

隣接フレーム変化率は、初期8字幕の16隣接ペアに対して出力が変わった割合である。
代表画像のCERへ前後フレームを重複加算していない。
固定候補の変化率はPaddleOCR 0.2500、fast 0.8125、best 0.6250だった。

## 動画と行数による差

PaddleOCRのOtsuは、`QK95uTvf7ks`でCER 0.1128、`Ns8VQexyJes`で0.1429だった。
fastのGrayscaleは同じ順で1.0977と1.1714、bestのGrayscaleは1.1504と1.1429だった。

2行字幕は1件だけなので、行数差の一般化には使えない。
固定行分割時の2行字幕CERはPaddleOCR 0.1429、fast 2.0952、best 2.1905だった。
ROI全体`--psm 6`のCERはfast 1.3810、best 1.4286だったが、どちらも先頭行を欠落させた。
CERが固定行分割より低くても、2行を保持できないため採用候補にはならない。

## 出力画像と誤認の観察

Otsuは字幕の塗りを黒、周辺の多くを白に変換し、PaddleOCRでは7条件中の最良値になった。
ただし、配信者の顔、ミニマップ、明るい背景の輪郭も残った。

Adaptiveは字幕の輪郭と背景の細線を同時に残す例があり、PaddleOCRとTesseractの両方で悪化した。
2行字幕では、黄文字と白文字の輪郭は残ったが、背景の人物とUIも大きく残った。

PaddleOCRのOtsuでも、`1렙→1럽`、`줌→즘`、単独字母の脱落または展開が残った。
Tesseractは同じ誤認に加え、背景UIから英字、記号、余分な改行を出力し、短い`달려잇`でも前後にノイズを加えた。
fastとbestの`g01_f02`は空出力だった。

これらは、Tesseractが今回の広い字幕ROIとゲーム背景に適合しなかった事実を示す。
特定フォントを原因とする対照実験はしていないため、誤認を字体由来とは断定しない。

## 背景ノイズ低減

PR #12の時間中央値と白色または黄色および輪郭の固定マスクを再利用した。
対象は開発用9字幕、7フレーム、0.5秒幅であり、独立評価ではない。
新しい背景除去方式や安定区間検出は追加していない。

各エンジンには主比較の固定候補を適用した。
背景抑制なしは安定区間の中央フレーム、時間中央値と固定マスクは同じ7フレームから作った既存出力である。

| エンジン | 前処理 | 背景抑制 | CER | 空白除外CER | 完全一致 | 推論中央値 (ms) |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| PaddleOCR | Otsu | なし | 0.1338 | 0.0635 | 1/9 | 23.9 |
| PaddleOCR | Otsu | 時間中央値 | 0.1401 | 0.0714 | 1/9 | 22.9 |
| PaddleOCR | Otsu | 固定マスク | 0.1592 | 0.0794 | 1/9 | 24.0 |
| Tesseract fast | Grayscale | なし | 1.0127 | 0.5317 | 0/9 | 172.1 |
| Tesseract fast | Grayscale | 時間中央値 | 0.9363 | 0.5079 | 0/9 | 165.8 |
| Tesseract fast | Grayscale | 固定マスク | 0.8217 | 0.4683 | 0/9 | 157.1 |
| Tesseract best | Grayscale | なし | 1.0637 | 0.5794 | 0/9 | 232.3 |
| Tesseract best | Grayscale | 時間中央値 | 1.0191 | 0.5714 | 0/9 | 231.2 |
| Tesseract best | Grayscale | 固定マスク | 0.8344 | 0.4524 | 0/9 | 232.7 |

固定マスクはTesseract内では改善したが、完全一致を生まなかった。
PaddleOCRでは時間中央値と固定マスクの両方が悪化した。
画像でも、固定マスクがゲーム背景の大部分を黒くする一方、字幕輪郭、ミニマップ、静止UI、背景の細線を残すことを確認した。

この結果は開発データ内の探索であり、独立評価へ進める条件を満たさない。
背景抑制は不採用とする。

## 時間、メモリ、容量

PaddleOCRの推論時間は常駐モデルへの呼び出しであり、プロセス起動とモデル初期化を含まない。
Tesseractの推論時間は1行ごとのCLIサブプロセス起動とモデル読込を含む。
両者を純粋なモデル推論時間として同一視できないが、現在の呼び出し設計における画像入力から結果取得までのコストとして記録した。

| 方式 | 初回または初期化 | ウォームアップ後の代表中央値 | メモリ | 展開容量 |
| --- | ---: | ---: | ---: | ---: |
| PaddleOCR | 初期化5407.0ms、初回93.2ms | Otsu 20.1ms | 初期化後RSS 408.9MiB | モデル13.03MiB、評価用Python環境794.73MiB |
| Tesseract fast | 初回188.4ms | Grayscale 166.3ms | 子プロセス最大48.7MiB | 配布本体237.88MiB、kor+eng 5.52MiB |
| Tesseract best | 初回269.9ms | Grayscale 235.7ms | 子プロセス最大70.6MiB | 配布本体237.88MiB、kor+eng 26.63MiB |

Tesseract配布本体の容量には同梱DLLと既定のデータが含まれる。
fastとbestの言語データ容量は、今回別途固定した`kor+eng`だけの合計である。
PaddleOCRのPython環境容量にはpip、メタデータ、開発用依存を含み、配布向けに削減していない。

## 再実行物

- `tesseract_evaluation.json`：Tesseract出力を見る前に固定した分割、候補、背景抑制条件
- `tesseract_toolchain.json`：実行ファイル、DLL、モデルの版、取得元、SHA-256
- `tesseract_pipeline.py`：データ準備、7前処理、3方式の実行、採点、集計
- `tune_adaptive.py`：Adaptive候補の限定比較と固定
- `background_comparison.py`：PR #12出力の限定再利用
- `results/tesseract-dataset.json`：28画像の対応とSHA-256
- `results/tesseract-adaptive-tuning.json`：設定選択の生出力と集計
- `results/tesseract-summary.json`：主比較、速度、容量、2行診断
- `results/tesseract-details.json`：588件のOCR生出力、時刻、正解文、確認状態、失敗状態
- `results/tesseract-background.json`：背景抑制81件の生出力と集計

## 未完了

- 正解韓国語の人による確認
- 未使用動画による独立評価
- 2行字幕を複数件使った再現性確認
- 黄色字幕、英韓混在、別フォント、別解像度の十分な件数による層別評価

これらは結果から除外したのではなく、未完了として残している。
今回の不採用判断は、固定した開発データと実行条件に対する判断である。

## 参照資料

- [Tesseractの公式言語データ](https://tesseract-ocr.github.io/tessdoc/Data-Files.html)
- [tessdata_bestの公式説明](https://tesseract-ocr.github.io/tessdoc/Data-Files-in-tessdata_best.html)
- [Tesseractの画質改善とページ分割](https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html)
- [Tesseract CLI](https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html)
- [OpenCVの二値化](https://docs.opencv.org/4.13.0/d7/d4d/tutorial_py_thresholding.html)
