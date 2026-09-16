import { useCallback, useEffect, useRef, useState } from "react";
import { AnalysisRequestPanel } from "./AnalysisRequestPanel";
import { SubtitleRegionOverlay } from "./SubtitleRegionOverlay";
import {
  getVideoContentRect,
  type Rectangle,
  type SubtitleRegion,
} from "./subtitleRegion";

interface VideoPlayerProps {
  path: string;
  url: string;
  onRegionChange?: (region: SubtitleRegion | null) => void;
}

export function VideoPlayer({ path, url, onRegionChange }: VideoPlayerProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [bounds, setBounds] = useState<Rectangle | null>(null);
  const [editing, setEditing] = useState(false);
  const [region, setRegion] = useState<SubtitleRegion | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">(
    "loading",
  );
  const name = path.split(/[\\/]/).pop() || path;

  const measureVideo = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;
    setBounds(
      getVideoContentRect(video.getBoundingClientRect(), {
        width: video.videoWidth,
        height: video.videoHeight,
      }),
    );
  }, []);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    const observer = new ResizeObserver(measureVideo);
    observer.observe(video);
    video.addEventListener("resize", measureVideo);
    measureVideo();
    return () => {
      observer.disconnect();
      video.removeEventListener("resize", measureVideo);
    };
  }, [measureVideo]);

  function updateRegion(next: SubtitleRegion | null) {
    setRegion(next);
    onRegionChange?.(next);
  }

  return (
    <div className="selected-video">
      <p className="file-name" title={path}>
        {name}
      </p>
      <div className="region-toolbar">
        <button
          type="button"
          disabled={status !== "ready" || !bounds}
          aria-pressed={editing}
          onClick={() => {
            if (!editing) videoRef.current?.pause();
            setEditing(!editing);
          }}
        >
          {editing ? "範囲指定を終了" : "字幕範囲を指定"}
        </button>
        <button
          type="button"
          disabled={!region}
          onClick={() => updateRegion(null)}
        >
          範囲をクリア
        </button>
      </div>
      {editing && (
        <p id="region-instructions" className="selection-help">
          字幕を囲むようにドラッグしてください。選び直すと以前の範囲を置き換えます。
          Enterで映像全体を選択、Escでドラッグを取り消せます。
        </p>
      )}
      <div className="video-stage">
        {/* biome-ignore lint/a11y/useMediaCaption: Local videos have no separate caption track at this stage. */}
        <video
          ref={videoRef}
          className="video"
          aria-label={`動画：${name}`}
          src={url}
          controls={!editing}
          preload="auto"
          playsInline
          onLoadedMetadata={measureVideo}
          onLoadedData={() => {
            setStatus("ready");
            measureVideo();
          }}
          onPlay={() => {
            if (editing) videoRef.current?.pause();
          }}
          onError={() => {
            setStatus("error");
            setEditing(false);
            setBounds(null);
            updateRegion(null);
          }}
        />
        {bounds && status === "ready" && (
          <SubtitleRegionOverlay
            key={`${editing}-${bounds.x}-${bounds.y}-${bounds.width}-${bounds.height}`}
            bounds={bounds}
            editing={editing}
            region={region}
            onChange={updateRegion}
            onExit={() => setEditing(false)}
          />
        )}
      </div>
      {region && <p className="selection-help">字幕範囲を選択済み</p>}
      <AnalysisRequestPanel
        key={JSON.stringify(region)}
        path={path}
        region={region}
      />
      {status === "loading" && <p role="status">動画を読み込んでいます…</p>}
      {status === "error" && (
        <p className="error" role="alert">
          動画を再生できませんでした。ファイルが存在するか、破損していないかを確認し、
          MP4（H.264 /
          AAC）やWebMなど、再生できる形式の動画を選び直してください。
        </p>
      )}
    </div>
  );
}
