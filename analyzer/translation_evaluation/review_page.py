"""Generate an offline image/OCR/translation review page; never submit human ratings."""

import argparse
import html
from pathlib import Path

try:
    from .evaluate import read_json, initial_dataset
except ImportError:
    from evaluate import read_json, initial_dataset


def render(records, outputs, image_directory, destination, targets):
    esc = html.escape
    if isinstance(targets, list):
        targets_by_sample = {item["sample_id"]: item for item in targets}
    else:
        targets_by_sample = {}
    sections = []
    for row in records:
        if not row["primary"]:
            continue
        identifier = row["id"]
        path = image_directory / row.get("image_file", identifier + ".png")
        if not path.is_file():
            raise ValueError(f"missing review image: {identifier}")
        group = row["group_id"]
        target = targets_by_sample.get(identifier, targets.get("groups", {}).get(group, {}) if isinstance(targets, dict) else {})
        meanings = " / ".join(target.get("must_preserve", []))
        uncertainty = target.get("uncertainty", "AI目視転記と役割判定は人による未確認です。")
        reference = target.get("reference_ja", "")
        translations = []
        for output in sorted(outputs, key=lambda item: (item.get("condition", ""), item.get("model", ""))):
            if output["sample_id"] != identifier:
                continue
            label = esc(f"条件 {output['condition']} — {output['model']} / {output['experiment'][:10]}")
            translations.append(f'<article class="translation"><h3>{label}</h3><pre>{esc(output["output"])}</pre>'
                f'<small>API status: {esc(output["status"])} / {output["wall_ms"]:.0f} ms</small></article>')
        ratings = "".join(f'<label>条件 {condition}<select data-field="condition_{condition}">'
            '<option value="">未選択</option><option value="ok">○ 意味を保持</option>'
            '<option value="minor">△ 軽微</option><option value="major">× 意味が壊れる</option>'
            '<option value="unknown">不明</option></select></label>' for condition in "ABCD")
        sections.append(f'<section id="{esc(identifier)}"><h2>{esc(identifier)}</h2>'
            f'<p>{esc(str(row["timestamp_seconds"]))} 秒 / {esc(row.get("line_role", "speech"))}</p>'
            f'<img src="{esc(path.resolve().as_uri())}" alt="{esc(identifier)}の元字幕">'
            f'<dl><dt>画像からの転記（AI暫定）</dt><dd>{esc(row["source_ko"])}</dd>'
            f'<dt>OCR生出力</dt><dd>{esc(row["ocr_ko"])}</dd><dt>保持すべき意味（AI暫定）</dt><dd>{esc(meanings)}</dd>'
            f'<dt>参考となる意味（AI暫定）</dt><dd>{esc(reference)}</dd>'
            f'<dt>未確認事項</dt><dd>{esc(uncertainty)}</dd></dl>'
            f'<fieldset class="checklist" data-review-id="{esc(identifier)}"><legend>人による確認</legend>'
            '<label>原文<select data-field="source_status"><option value="">未選択</option>'
            '<option value="ok">画像と一致</option><option value="incorrect">修正が必要</option>'
            '<option value="unknown">不明</option></select></label>'
            '<label class="wide">正しい韓国語（修正がある場合）<input data-field="source_correction" type="text"></label>'
            f'<div class="ratings">{ratings}</div>'
            '<label class="wide">備考<textarea data-field="notes" rows="2"></textarea></label></fieldset>'
            f'<div class="translations">{"".join(translations)}</div></section>')
    document = '<!doctype html><html lang="ja"><meta charset="utf-8"><title>字幕翻訳の確認資料</title>'
    document += '<style>body{font:16px/1.7 system-ui;margin:2rem auto;max-width:1400px;padding:0 1rem;background:#f4f5f7;color:#18212b}section{background:white;padding:1.5rem;margin:1.5rem 0;border:1px solid #ccd2da;border-radius:8px}section.complete{border:2px solid #238636}img{width:100%;height:auto}pre{white-space:pre-wrap;overflow-wrap:anywhere}dt{font-weight:600}dd{margin:0 0 .8rem}.notice{background:#fff0ca;padding:1rem}.toolbar{position:sticky;top:0;z-index:2;display:flex;gap:.8rem;align-items:center;flex-wrap:wrap;background:#18212b;color:white;padding:.8rem;border-radius:8px}.toolbar button{padding:.55rem .8rem}.checklist{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.8rem;margin:1rem 0;border:2px solid #8c959f;border-radius:8px}.checklist label{display:grid;gap:.25rem}.checklist .wide,.ratings{grid-column:1/-1}.ratings{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:.8rem}select,input,textarea{box-sizing:border-box;width:100%;font:inherit;padding:.45rem}.translations{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.8rem}.translation{border:1px solid #ccd2da;padding:.8rem;border-radius:6px}.translation h3{margin-top:0}@media(max-width:800px){.ratings,.translations{grid-template-columns:1fr}.checklist{grid-template-columns:1fr}}</style>'
    document += '<h1>字幕翻訳の確認資料</h1><p class="notice">人による確認済みの評価ではありません。画像、転記、OCR、訳を照合するための資料です。モデル名を表示するため、このページ上での確認は盲検ではありません。入力はこのブラウザ内だけに自動保存され、外部へ送信されません。</p>'
    document += '<p>A: 転記 / B: OCR / C: 転記＋辞書 / D: OCR＋辞書。画像と原文を確認してから、各訳を○・△・×・不明で評価してください。</p>'
    document += '<div class="toolbar"><strong id="progress">0 / 0 件完了</strong><button id="next" type="button">次の未完了へ</button><button id="export" type="button">確認結果をJSON保存</button><label>JSONを読み込む<input id="import" type="file" accept="application/json"></label></div>'
    document += ''.join(sections)
    document += r'''<script>
const storageKey = "lolkrtranslator-human-review-v1:" + location.pathname;
const fields = [...document.querySelectorAll("[data-review-id]")];
let state = {};
try { state = JSON.parse(localStorage.getItem(storageKey) || "{}"); } catch { state = {}; }
function values(fieldset) {
  return Object.fromEntries([...fieldset.querySelectorAll("[data-field]")].map(input => [input.dataset.field, input.value]));
}
function isComplete(review) {
  return Boolean(review.source_status && ["A","B","C","D"].every(key => review["condition_" + key]));
}
function refresh() {
  let completed = 0;
  for (const fieldset of fields) {
    const review = values(fieldset);
    const done = isComplete(review);
    fieldset.closest("section").classList.toggle("complete", done);
    if (done) completed++;
  }
  document.querySelector("#progress").textContent = `${completed} / ${fields.length} 件完了`;
}
function save() {
  state = Object.fromEntries(fields.map(fieldset => [fieldset.dataset.reviewId, values(fieldset)]));
  try { localStorage.setItem(storageKey, JSON.stringify(state)); } catch { /* JSON export still works. */ }
  refresh();
}
function restore(next) {
  state = next && typeof next === "object" ? next : {};
  for (const fieldset of fields) {
    const review = state[fieldset.dataset.reviewId] || {};
    for (const input of fieldset.querySelectorAll("[data-field]")) input.value = review[input.dataset.field] || "";
  }
  save();
}
for (const fieldset of fields) fieldset.addEventListener("input", save);
document.querySelector("#next").addEventListener("click", () => {
  const pending = fields.find(fieldset => !isComplete(values(fieldset)));
  if (pending) pending.closest("section").scrollIntoView({behavior:"smooth"});
});
document.querySelector("#export").addEventListener("click", () => {
  save();
  const payload = {schema_version:1, exported_at:new Date().toISOString(), reviews:state};
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], {type:"application/json"}));
  link.download = "loltranslator-human-review.json";
  link.click();
  URL.revokeObjectURL(link.href);
});
document.querySelector("#import").addEventListener("change", async event => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    const payload = JSON.parse(await file.text());
    if (payload.schema_version !== 1 || !payload.reviews) throw new Error("形式が違います");
    restore(payload.reviews);
  } catch (error) { alert("確認結果を読み込めませんでした: " + error.message); }
});
restore(state);
</script></html>'''
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(document, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--outputs", type=Path)
    parser.add_argument("--targets", type=Path)
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    base = Path(__file__).parent
    records = read_json(args.dataset) if args.dataset else initial_dataset(
        read_json(base.parent / "ocr_evaluation/manifest.json"), base.parent / "ocr_evaluation/results/details.csv")
    render(records, read_json(args.outputs) if args.outputs else [], args.images, args.output,
           read_json(args.targets or base / "meaning_targets.json"))


if __name__ == "__main__":
    main()
