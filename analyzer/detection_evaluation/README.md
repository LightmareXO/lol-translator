# 字幕区間検出の評価

Issue #15の画像ベース検出を、OCR文字列の正しさとは分けて確認する。

`manifest.json`は検出結果を見る前に、調整用区間、固定検証用区間、ROI、行分割、閾値、照合規則を固定した記録である。`QK95uTvf7ks`の0〜120秒と`Ns8VQexyJes`の360〜420秒は実装中に出力を観測しているため、固定検証とは呼ばない。固定検証は、それまで未観測だった120〜180秒と600〜660秒を使う。同一動画内の別区間なので、未知動画に対する独立評価ではない。

動画、抽出画像、モデル、個人用パス、アプリが生成した完全な結果JSONはコミットしない。コミットする評価結果は、動画IDと区間IDで参照できる正解境界、集計値、再現手順に限定する。

主評価は正解表示時間が1.2秒以上の字幕である。全字幕の値と1.2秒未満の欠落は補助評価へ分ける。フェードは200msサンプル上で最初と最後に判読できる時刻を境界とする。行IDごとに1対1対応させ、IoU 0.5以上を一致候補とする。同一正解に複数検出が対応した場合は過分割、複数正解が一つの検出へ入った場合は誤統合として別集計する。開始・終了の絶対誤差は一致ペアだけで中央値とp95を出す。

黄色・白色の2行字幕は`ns-tuning-360-420`内の6分10秒付近で別途確認する。これは固定検証の合否ではなく、行の片側だけが変化したときに他方を再生成しないことの実機確認である。

集計は次のように実行する。
完全な解析結果JSONはリポジトリ外へ置き、`区間ID=パス`の形で渡す。

```powershell
python analyzer/detection_evaluation/evaluate.py `
  --manifest analyzer/detection_evaluation/manifest.json `
  --annotations analyzer/detection_evaluation/annotations.json `
  --new-result "qk-verification-120-180=<画像方式の結果JSON>" `
  --legacy-result "qk-verification-120-180=<旧方式の結果JSON>" `
  --new-result "ns-verification-600-660=<画像方式の結果JSON>" `
  --legacy-result "ns-verification-600-660=<旧方式の結果JSON>" `
  --new-result "ns-tuning-360-420=<画像方式の結果JSON>" `
  --legacy-result "ns-tuning-360-420=<旧方式の結果JSON>" `
  --output analyzer/detection_evaluation/results.json
```

実測値と採用判断は`REPORT.md`に記録する。
