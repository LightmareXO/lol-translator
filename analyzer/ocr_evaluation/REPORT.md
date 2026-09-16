# LoL韓国語字幕OCR 比較検証報告

## 結論

1行のLoL韓国語字幕には、`korean_PP-OCRv5_mobile_rec` を認識専用で使い、入力前に固定の1.5倍コントラスト補正を施す構成を採用する。

同じ24枚での主指標CERはPaddleOCRが0.1729、EasyOCRの最良条件が0.8947だった。
PaddleOCRは推論中央値18.1msで、EasyOCRの106.7msより約5.9倍速い。
モデルと未削減Python環境を含む展開容量もPaddleOCRの方が約145.6MiB小さい。

ただし、この決定は1本の動画に含まれる1行字幕だけを対象にした暫定採用である。
対象動画に2行字幕がないことを確認済みのため、2行字幕は別動画で追加検証する。
2行字幕へ今回の数値を一般化しない。

## 実行環境

- OS: Microsoft Windows 11 Home 10.0.26200、x64
- CPU: AMD Ryzen 7 9800X3D、8コア16論理プロセッサ
- メモリ: 31.6GiB
- Python: 3.10.11、64bit
- CPUスレッド: 4
- バッチサイズ: 1
- ウォームアップ: 各構成3回
- OCR構成は逐次実行し、並列測定していない
- FFmpeg: 8.1.1

PaddleOCRは3.7.0、PaddlePaddleはWindows x64 CPU wheelの3.4.0、EasyOCRは1.7.2、PyTorchはCPU wheelの2.14.0を使った。
依存関係の完全な一覧はlockファイルに保存した。

## テストデータ

元動画のYouTube IDは `QK95uTvf7ks`、SHA-256は `41bae7253acff6cd403cd63990bb97938168141bbf24cf800e1cd14d24e2d26a` である。
動画名と絶対パスは共有結果へ含めていない。

Issue #3で指定した縦位置を再利用し、最長字幕を切らないよう横方向だけを広げた。
正規化座標とピクセル座標は次のとおりである。

```text
x=0.019791666666666666
y=0.7981481481481482
width=0.9604166666666667
height=0.08888888888888889

1920x1080: x=38, y=862, width=1844, height=96
```

8種類の字幕から60fpsで連続する3フレームずつ、計24枚を抽出した。
正解文はOCR出力を使わず、中央フレームと24枚のコンタクトシートを目視して確定した。
未確認の正解文は0件である。

| 条件 | 画像数 | 異なる字幕数 |
|---|---:|---:|
| 1行 | 24 | 8 |
| 2行 | 0 | 0 |
| 短文 | 9 | 3 |
| 中程度 | 12 | 4 |
| 長文 | 3 | 1 |
| 明るい背景 | 15 | 5 |
| 暗い背景 | 9 | 3 |
| 動きのある背景 | 15 | 5 |
| 戦闘背景 | 9 | 3 |
| ハングル字母を含む | 6 | 2 |
| ラテン文字を含む | 3 | 1 |

タグは重複するため、合計は24にならない。
動画・抽出画像・モデル・仮想環境・キャッシュはGitへ含めていない。

## OCR構成と前処理

比較した認識モデルは次の2つである。

- PaddleOCR `korean_PP-OCRv5_mobile_rec`: 公式資料で韓国語・英語・数字を対象とするPP-OCRv5 mobile認識モデルとして掲載されている。
- EasyOCR `korean_g2`: `Reader(["ko", "en"], detector=False)` で検出モデルを読み込まず、`recognize()` に字幕範囲全体を渡した。

両方式とも、文字検出や画像ごとの行境界は使っていない。
1行字幕だけの比較なので、行結合や改行変換も行っていない。

外部前処理は両方式で同じ実装を使った。

- `raw`: OpenCVでBGRとして読み込むだけ
- `scale2x`: `INTER_CUBIC` で縦横2倍
- `grayscale`: 8bitグレースケール化し、同値3チャンネルBGRへ戻す
- `contrast`: 中点127.5を固定して1.5倍の線形コントラスト補正、0〜255でクリップ

二値化は追加しなかった。
無加工・グレースケール・コントラストで精度差を判断でき、失敗例から二値化が必要だとする根拠が得られなかったためである。

## 評価方法

主指標は、全画像の編集距離合計を正解文字数合計で割るcorpus CERである。
評価前にUnicode NFC正規化を行う。
合成済みハングル音節は1 Unicodeコードポイント、`ㄱ` や `ㅊ` などの字母もそれぞれ1コードポイントとして数える。

主評価では空白・句読点・改行を保持する。
補助指標としてUnicode空白をすべて除いたCERも別に算出した。
生のOCR出力と正規化後の出力は両方保存した。
空文字や推論失敗は除外せず、空の予測として扱う。

速度は画像読込と前処理を除くOCR推論時間、および画像読込・前処理・推論を含む合計時間に分けた。
中央値とp95は24枚から計算し、p95は `(n - 1) × 0.95` の位置で線形補間した。
OSのファイルキャッシュは制御していない。

安定性は同じ字幕の隣接2組を8グループで評価した。
各グループの変化率と正規化編集距離を出し、最後にグループを同じ重みで平均した。

ピークメモリはOCR方式ごとに別プロセスで、親プロセスと生存中の子プロセスのRSS合計を50ms間隔で測った。

## 精度・速度・安定性

| エンジン | 前処理 | CER | 空白除外CER | 完全一致 | 推論中央値 | 推論p95 | 合計中央値 | 揺れ率 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| PaddleOCR | 無加工 | 0.1905 | 0.0948 | 3/24 | 17.4ms | 38.3ms | 19.9ms | 0.2500 |
| PaddleOCR | 2倍拡大 | 0.1880 | 0.0917 | 2/24 | 15.3ms | 23.1ms | 18.4ms | 0.3125 |
| PaddleOCR | グレースケール | 0.1729 | 0.1101 | 0/24 | 21.3ms | 38.4ms | 24.1ms | 0.4375 |
| **PaddleOCR** | **コントラスト** | **0.1729** | **0.0703** | **3/24** | **18.1ms** | **25.0ms** | **21.8ms** | **0.3125** |
| EasyOCR | 無加工 | 0.8947 | 0.6911 | 0/24 | 106.7ms | 115.5ms | 109.2ms | 0.6875 |
| EasyOCR | 2倍拡大 | 0.9123 | 0.6881 | 0/24 | 109.5ms | 124.8ms | 112.5ms | 0.8750 |
| EasyOCR | グレースケール | 0.8947 | 0.6911 | 0/24 | 105.2ms | 117.0ms | 107.9ms | 0.6875 |
| EasyOCR | コントラスト | 0.9649 | 0.6881 | 0/24 | 99.7ms | 110.5ms | 104.2ms | 0.7500 |

全8構成で推論失敗は0件だった。

PaddleOCR＋コントラストの隣接フレーム変化率はグループ平均0.3125、隣接出力間の正規化編集距離は0.0197だった。
EasyOCRの最良精度条件である無加工はそれぞれ0.6875、0.1424だった。
PaddleOCRは8グループ中5グループで3フレームすべて同じ出力になった。

コントラスト補正とグレースケールの主CERは同率だが、コントラスト補正は空白除外CERが低く、完全一致も3件ある。
無加工より主CERを0.0175、空白除外CERを0.0245改善したため、精度を優先してコントラスト補正を採用する。
同一データで前処理を選び評価した数値なので、独立した検証データに対する改善保証ではない。

## 起動時間とメモリ

| エンジン・前処理 | キャッシュ済み初期化 | スクリプト開始から初認識 | Peak RSS |
|---|---:|---:|---:|
| PaddleOCR・コントラスト | 0.98秒 | 2.05秒 | 365.2MiB |
| EasyOCR・無加工 | 0.08秒 | 2.47秒 | 376.8MiB |

「スクリプト開始」はPythonが対象モジュールを実行し始めた時点で、OSによるPythonプロセス生成時間は含まない。
モデル取得済みの別プロセスで測った。
OSキャッシュは制御していない。

空のモデルディレクトリからの初回は、モデル取得と初期化を分離できないAPIだった。
合算の初期化時間はPaddleOCR 10.65秒、EasyOCR 1.04秒だった。
PaddleOCRは取得済み初期化との差から、今回の回線・キャッシュ条件ではモデル取得と展開に約9.6秒の追加負荷があったと推定する。
この差分はネットワークだけの厳密な測定値ではない。

## 代表的な失敗例

PaddleOCR＋コントラストは字幕外の文字をほぼ連結しなかったが、次の誤りが残った。

| 画像 | 正解 | 出力 | 傾向 |
|---|---|---|---|
| g01_f02 | `바로 빙결다리서폿인데` | `바로빙결다리서뜻인데` | 空白脱落、`폿→뜻` |
| g03_f02 | `선요우무도 ㄱㅊ긴한데 상대조합 따라 딴딴해야할거같으면 블클 갑니다` | `선요우무도 ㄱ츠긴한데 상대조합 따라 딴딴해야할거같으면블클같니다` | 字母、空白、近似音節 |
| g04_f02 | `빙결마공점이 ㄹㅇ 로밍 영향력이 지림` | `빙결마공점이르로밍영향력이 지림` | 字母表現の崩れ、空白脱落 |
| g06_f02 | `플 빼고 다음 턴에 잡으면 되죠` | `플빼고다음턴에잡으면되죠` | 文字は一致するが空白をすべて脱落 |
| g08_f02 | `방마저스탯으로 다재다능도 완성시켜줌` | `방마저스탯으로다재다능도 완성시켜즘` | 空白脱落、`줌→즘` |

EasyOCRは字幕範囲の左右に残るゲームUIを字幕と同じ1行へ連結した。
例えばg01_f02は `3 >_   바로 방결다리서쫓인데   미-입 인` となり、字幕外ノイズがCERとフレーム間の揺れを大きくした。
文字検出を追加すれば改善する可能性はあるが、認識専用という同条件の比較ではなく、検出モデルの容量と速度も増えるため今回の採用構成にはしない。

## 容量とIssue #7への引き継ぎ

| 構成 | 仮想環境 | site-packages | モデル | CPython展開 | 未削減配布概算 |
|---|---:|---:|---:|---:|---:|
| PaddleOCR | 794.73MiB | 791.20MiB | 13.03MiB | 15.90MiB | 823.65MiB |
| EasyOCR | 937.96MiB | 935.20MiB | 15.34MiB | 15.90MiB | 969.20MiB |

CPython 3.10.11 Windows x64埋め込み版はZIP 8.23MiB、展開後15.90MiBだった。
仮想環境はWindows venvが外部のベースPythonを参照するため、配布概算ではCPython展開分を別に足した。

これはpip、メタデータ、比較用psutil、未使用のPaddleOCR/PaddleX機能を含む安全側の未削減値である。
アプリの圧縮インストーラーはIssue #7でまだ作っていないため、全体の圧縮サイズは未測定である。
圧縮済みと展開後の値を混同しない。

Paddle側の大きな内訳は `paddle` 約380.4MiB、OpenCV約121.0MiB、pandas約52.6MiB、modelscope約37.3MiBである。
Issue #7では次を行う。

1. CPython埋め込み版へPaddleOCR構成だけをvendorする。
2. `korean_PP-OCRv5_mobile_rec` だけを同梱し、EasyOCRと比較用依存は含めない。
3. pip、キャッシュ、テスト、学習用機能、未使用モデルを除いた実ランタイムを再計測する。
4. 開発環境と同じ24枚で、同梱ランタイムの認識結果が一致することを確認する。
5. インストーラーの圧縮サイズとインストール後サイズを別々に記録する。

## Windows対応とライセンス確認

- PaddlePaddle公式Windows手順はPython 3.9〜3.13、64bit x86_64とCPU wheelを案内している。
- PaddleOCR公式文書はPython 3.8以降を対象とし、`TextRecognition(model_name=..., device="cpu")` の認識専用APIを提供している。
- EasyOCR公式READMEはWindowsでPyTorchを先に導入し、CPUのみならCUDAなしを選ぶよう案内している。
- CPython公式文書は埋め込み版をアプリローカル配布向けとしているが、Microsoft C Runtimeは含まれず、インストーラー側の責任で用意する必要がある。

ライブラリの確認結果は、PaddleOCRとPaddlePaddleがApache-2.0、EasyOCRがApache-2.0、PyTorchがBSD系ライセンスである。
再配布時は各wheelに含まれるライセンス・NOTICEと第三者ライセンスも同梱する。

一方、取得した2つのモデルファイル内にはモデル固有の独立したライセンス文書がなかった。
公式プロジェクト配下の配布物ではあるが、モデル再配布条件をプロジェクトライセンスだけから断定せず、Issue #7で配布前に上流へ確認する。

公式資料:

- [PaddleOCR text recognition module](https://www.paddleocr.ai/latest/en/version3.x/module_usage/text_recognition.html)
- [PaddleOCR PP-OCRv5 multilingual models](https://www.paddleocr.ai/latest/en/version3.x/algorithm/PP-OCRv5/PP-OCRv5_multi_languages.html)
- [PaddleOCR installation](https://www.paddleocr.ai/latest/en/version3.x/installation.html)
- [PaddlePaddle Windows CPU installation](https://www.paddlepaddle.org.cn/documentation/docs/en/install/pip/windows-pip_en.html)
- [PaddleOCR Apache-2.0 license](https://github.com/PaddlePaddle/PaddleOCR/blob/main/LICENSE)
- [PaddlePaddle Apache-2.0 license](https://github.com/PaddlePaddle/Paddle/blob/develop/LICENSE)
- [EasyOCR README and Windows installation note](https://github.com/JaidedAI/EasyOCR/blob/master/README.md)
- [EasyOCR recognition-only API and Korean model selection](https://github.com/JaidedAI/EasyOCR/blob/master/easyocr/easyocr.py)
- [EasyOCR Apache-2.0 license](https://github.com/JaidedAI/EasyOCR/blob/master/LICENSE)
- [PyTorch license](https://github.com/pytorch/pytorch/blob/main/LICENSE)
- [CPython embeddable package](https://docs.python.org/3.10/using/windows.html#the-embeddable-package)

## 採用・不採用判断

採用構成は次のとおりである。

```text
字幕範囲の画像
  -> 1.5倍の固定コントラスト補正
  -> PaddleOCR TextRecognition
  -> korean_PP-OCRv5_mobile_rec
  -> CPU 4スレッド、batch_size=1
```

EasyOCRは今回の認識専用条件では、PaddleOCRよりCERが0.7218高く、約5.9倍遅く、未削減配布概算も約145.6MiB大きいので不採用とする。

PaddleOCRの2倍拡大は無加工に対する精度改善が小さく、完全一致が減るため採用しない。
掲載した実測では、推論中央値は2倍拡大15.3ms、無加工17.4msであり、2倍拡大の方が短かった。
一時画像の画素数は4倍になるが、この結果から処理時間が増えるとは結論できない。
各条件は別プロセスで1回ずつ測り、OSキャッシュや他アプリの負荷は制御していないため、反復測定なしに小さな速度差の原因を断定しない。
グレースケールは主CERが同率でも、空白除外CERと完全一致でコントラスト補正に劣るため採用しない。

## 制約と追加検証条件

- 1本の動画、8字幕、24枚で前処理選定と評価を兼ねており、一般的な韓国語字幕全体の優劣を証明しない。
- 2行字幕は0件である。別動画から少なくとも6種類、各3隣接フレームの計18枚を追加し、行分割なしの認識専用構成を最初に試す。
- 2行を1行として誤認する場合だけ、固定の上下2分割と文字検出ありを別構成として比較する。行は上から下、各行内は左から右に結合し、行間は `\n` とする。
- 別フォント、字幕サイズ、配信者、解像度の動画を追加し、今回選んだコントラスト補正を独立データで再確認する。
- 2行字幕または別動画でPaddleOCRのCERが悪化する場合は、採用判断を再開する。
- モデル再配布条件と、CPython埋め込み版が必要とするMicrosoft C Runtimeの配布方法はIssue #7で解決する。

## 成果物

- `manifest.json`: 抽出元識別情報、時刻、座標、画像ID、タグ、同一字幕グループ、目視確認済み正解文
- `extract_frames.py`: SHA-256と解像度を検証する再抽出
- `run_paddle.py` / `run_easyocr.py`: 隔離プロセスでのCPU推論・時間・メモリ・容量測定
- `aggregate.py`: 入力対応を検証し、CER・速度・安定性・条件別結果を生成
- `results/details.csv`: 192件の画像単位詳細結果
- `results/summary.json`: 自動集計結果
- `results/capacity.json`: Python・依存・モデルの容量実測
- `requirements-*.txt` / `requirements-*-lock.txt`: 直接依存と実測環境
- `test_ocr_evaluation.py`: 指標・座標・manifest・集計の単体テスト
