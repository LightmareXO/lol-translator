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
    sections = []
    for row in records:
        if not row["primary"]:
            continue
        identifier = row["id"]
        path = image_directory / row.get("image_file", identifier + ".png")
        if not path.is_file():
            raise ValueError(f"missing review image: {identifier}")
        group = row["group_id"]
        target = targets.get("groups", {}).get(group, {})
        meanings = " / ".join(target.get("must_preserve", []))
        uncertainty = target.get("uncertainty", "AI目視転記と役割判定は人による未確認です。")
        translations = []
        for output in outputs:
            if output["sample_id"] != identifier:
                continue
            label = esc(f"{output['model']} / {output['condition']} / {output['experiment'][:10]}")
            translations.append(f'<details><summary>{label}</summary><pre>{esc(output["output"])}</pre>'
                f'<small>API status: {esc(output["status"])} / {output["wall_ms"]:.0f} ms</small></details>')
        sections.append(f'<section id="{esc(identifier)}"><h2>{esc(identifier)}</h2>'
            f'<p>{esc(str(row["timestamp_seconds"]))} 秒 / {esc(row.get("line_role", "speech"))}</p>'
            f'<img src="{esc(path.resolve().as_uri())}" alt="{esc(identifier)}の元字幕">'
            f'<dl><dt>画像からの転記（AI暫定）</dt><dd>{esc(row["source_ko"])}</dd>'
            f'<dt>OCR生出力</dt><dd>{esc(row["ocr_ko"])}</dd><dt>保持すべき意味（AI暫定）</dt><dd>{esc(meanings)}</dd>'
            f'<dt>未確認事項</dt><dd>{esc(uncertainty)}</dd></dl>{"".join(translations)}</section>')
    document = '<!doctype html><html lang="ja"><meta charset="utf-8"><title>字幕翻訳の確認資料</title>'
    document += '<style>body{font:16px/1.7 system-ui;margin:2rem auto;max-width:1400px;padding:0 1rem;background:#f4f5f7;color:#18212b}section{background:white;padding:1.5rem;margin:1.5rem 0;border:1px solid #ccd2da;border-radius:8px}img{width:100%;height:auto}pre{white-space:pre-wrap;overflow-wrap:anywhere}dt{font-weight:600}dd{margin:0 0 .8rem}summary{cursor:pointer}details{padding:.5rem;border-top:1px solid #ddd}.notice{background:#fff0ca;padding:1rem}</style>'
    document += '<h1>字幕翻訳の確認資料</h1><p class="notice">人による確認済みの評価ではありません。画像、転記、OCR、訳を照合するための資料です。モデル名を表示するため、このページ上での確認は盲検ではありません。回答や編集内容を自動送信する機能はありません。</p>'
    document += '<p>A: 転記 / B: OCR / C: 転記＋辞書 / D: OCR＋辞書。各出力の全文は見出しを開いて確認できます。</p>'
    document += ''.join(sections) + '</html>'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(document, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--outputs", type=Path)
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    base = Path(__file__).parent
    records = read_json(args.dataset) if args.dataset else initial_dataset(
        read_json(base.parent / "ocr_evaluation/manifest.json"), base.parent / "ocr_evaluation/results/details.csv")
    render(records, read_json(args.outputs) if args.outputs else [], args.images, args.output,
           read_json(base / "meaning_targets.json"))


if __name__ == "__main__":
    main()
