import { invoke } from "@tauri-apps/api/core";
import { useRef, useState } from "react";
import type { SubtitleRegion } from "./subtitleRegion";

interface Props {
  path: string;
  region: SubtitleRegion | null;
}

// The parent remounts this panel when the input changes, keeping results tied
// to the exact video and region that were submitted.
export function AnalysisRequestPanel({ path, region }: Props) {
  const busy = useRef(false);
  const [pending, setPending] = useState(false);
  const [savedPath, setSavedPath] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function validate() {
    if (!region || busy.current) return;
    busy.current = true;
    setPending(true);
    setSavedPath(null);
    setError(null);
    try {
      const result = await invoke<string>("validate_analysis_request", {
        request: { video_path: path, subtitle_region: region },
      });
      setSavedPath(result);
    } catch (cause) {
      setError(
        typeof cause === "string"
          ? cause
          : "入力検証に失敗しました。Pythonの設定を確認して再試行してください。",
      );
    } finally {
      busy.current = false;
      setPending(false);
    }
  }

  return (
    <section aria-label="Pythonへの受け渡し">
      <button type="button" disabled={!region || pending} onClick={validate}>
        {pending ? "Pythonで検証中…" : "Pythonへ渡して入力を検証"}
      </button>
      <p className="selection-help">
        字幕範囲を指定して実行してください。OCR・翻訳はまだ行いません。
      </p>
      {savedPath && (
        <p className="file-name" role="status">
          Pythonで入力を検証しました。JSON保存先：{savedPath}
        </p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
