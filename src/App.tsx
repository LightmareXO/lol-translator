import { convertFileSrc, invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import { useState } from "react";
import type { AnalysisProject } from "./analysisProject";
import { VideoPlayer } from "./VideoPlayer";
import "./App.css";

interface SelectedVideo {
  path: string;
  url: string;
  revision: number;
  project: AnalysisProject | null;
}

const videoExtensions = ["mp4", "webm", "m4v", "mov", "mkv", "avi"];

function App() {
  const [video, setVideo] = useState<SelectedVideo | null>(null);
  const [selecting, setSelecting] = useState(false);
  const [selectionError, setSelectionError] = useState<string | null>(null);

  async function selectVideo() {
    setSelecting(true);
    setSelectionError(null);
    try {
      const path = await open({
        title: "動画ファイルを選択",
        multiple: false,
        directory: false,
        filters: [{ name: "動画", extensions: videoExtensions }],
      });
      if (path === null) return;

      const extension = path.split(".").pop()?.toLowerCase();
      if (!extension || !videoExtensions.includes(extension)) {
        setSelectionError(
          "動画ファイルを選択してください。MP4やWebMなどを選べます。",
        );
        return;
      }
      const url = convertFileSrc(path);
      setVideo((previous) => ({
        path,
        url,
        revision: (previous?.revision ?? 0) + 1,
        project: null,
      }));
    } catch {
      setSelectionError("ファイル選択に失敗しました。もう一度お試しください。");
    } finally {
      setSelecting(false);
    }
  }

  async function loadProject() {
    setSelecting(true);
    setSelectionError(null);
    try {
      const path = await open({
        title: "保存済み解析結果を開く",
        multiple: false,
        directory: false,
        filters: [{ name: "LoL Translator JSON", extensions: ["json"] }],
      });
      if (path === null) return;
      const project = await invoke<AnalysisProject>("load_analysis_project", {
        path,
      });
      setVideo((previous) => ({
        path: project.source_video.path,
        url: convertFileSrc(project.source_video.path),
        revision: (previous?.revision ?? 0) + 1,
        project,
      }));
    } catch (cause) {
      setSelectionError(
        typeof cause === "string"
          ? cause
          : "保存済み解析結果を読み込めませんでした。",
      );
    } finally {
      setSelecting(false);
    }
  }

  return (
    <main className="container">
      <header className="app-header">
        <h1>LoL Translator</h1>
        <p>字幕を翻訳したい動画を、PCから選んでください。</p>
      </header>
      <section className="workspace" aria-label="動画プレーヤー">
        <div className="toolbar">
          <div className="button-row">
            <button type="button" onClick={selectVideo} disabled={selecting}>
              {selecting ? "選択中…" : video ? "別の動画を選択" : "動画を選択"}
            </button>
            <button
              type="button"
              className="secondary"
              onClick={loadProject}
              disabled={selecting}
            >
              保存済みJSONを開く
            </button>
          </div>
          <p className="format-hint">推奨：MP4（H.264 / AAC）、WebM</p>
        </div>
        {selectionError && (
          <p className="error" role="alert">
            {selectionError}
          </p>
        )}
        {video ? (
          <VideoPlayer
            key={video.revision}
            path={video.path}
            url={video.url}
            initialProject={video.project}
          />
        ) : (
          <div className="empty-state">
            <p>動画が選択されていません</p>
            <p>「動画を選択」からファイルを開くと、ここで再生できます。</p>
          </div>
        )}
        <p className="footnote">
          同じ拡張子でも、映像・音声の形式によって再生できない場合があります。
        </p>
      </section>
    </main>
  );
}

export default App;
