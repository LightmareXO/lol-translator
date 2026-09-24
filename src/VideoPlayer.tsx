import { useCallback, useEffect, useRef, useState } from "react";
import { AnalysisWorkspace } from "./AnalysisWorkspace";
import {
  type AnalysisProject,
  activeSubtitles,
  effectiveJapanese,
} from "./analysisProject";
import { SubtitleRegionOverlay } from "./SubtitleRegionOverlay";
import {
  getVideoContentRect,
  type Rectangle,
  type SubtitleRegion,
} from "./subtitleRegion";

interface VideoPlayerProps {
  path: string;
  url: string;
  initialProject?: AnalysisProject | null;
  onRegionChange?: (region: SubtitleRegion | null) => void;
  onBusyChange?: (busy: boolean) => void;
}

export function VideoPlayer({
  path,
  url,
  initialProject,
  onRegionChange,
  onBusyChange,
}: VideoPlayerProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [bounds, setBounds] = useState<Rectangle | null>(null);
  const [editing, setEditing] = useState(false);
  const [region, setRegion] = useState<SubtitleRegion | null>(
    initialProject?.analysis.subtitle_region ?? null,
  );
  const [duration, setDuration] = useState(0);
  const [currentTime, setCurrentTime] = useState(0);
  const [project, setProject] = useState<AnalysisProject | null>(
    initialProject ?? null,
  );
  const [processing, setProcessing] = useState(false);
  const [lineSplitRatio, setLineSplitRatio] = useState<number | null>(
    initialProject?.analysis.line_split_ratio ?? null,
  );
  const [status, setStatus] = useState<"loading" | "ready" | "error">(
    "loading",
  );
  const name = path.split(/[\\/]/).pop() || path;
  const subtitles = project
    ? activeSubtitles(project.subtitles, currentTime)
    : [];

  const updateProcessing = useCallback(
    (busy: boolean) => {
      setProcessing(busy);
      onBusyChange?.(busy);
    },
    [onBusyChange],
  );

  useEffect(() => {
    if (!processing) return;
    videoRef.current?.pause();
    setEditing(false);
  }, [processing]);

  useEffect(
    () => () => {
      onBusyChange?.(false);
    },
    [onBusyChange],
  );

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

  function syncTime() {
    const video = videoRef.current;
    if (video) setCurrentTime(video.currentTime);
  }

  function seek(seconds: number) {
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = seconds;
    setCurrentTime(seconds);
  }

  return (
    <div className="selected-video">
      <p className="file-name" title={path}>
        {name}
      </p>
      <div className="region-toolbar">
        <button
          type="button"
          disabled={processing || status !== "ready" || !bounds}
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
          disabled={processing || !region}
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
        {/* biome-ignore lint/a11y/useMediaCaption: Captions are rendered from the local analysis project below. */}
        <video
          ref={videoRef}
          className="video"
          aria-label={`動画：${name}`}
          src={url}
          controls={!editing}
          preload="metadata"
          playsInline
          onLoadedMetadata={() => {
            measureVideo();
            const video = videoRef.current;
            setDuration(video?.duration ?? 0);
            syncTime();
          }}
          onLoadedData={() => {
            setStatus("ready");
            measureVideo();
          }}
          onPlay={() => {
            if (editing) videoRef.current?.pause();
          }}
          onTimeUpdate={syncTime}
          onSeeked={syncTime}
          onRateChange={syncTime}
          onPause={syncTime}
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
            lineSplitRatio={lineSplitRatio}
            onChange={updateRegion}
            onExit={() => setEditing(false)}
          />
        )}
        {subtitles.some((subtitle) => effectiveJapanese(subtitle)) && (
          <div className="translated-caption" aria-live="off">
            {subtitles.map((subtitle) => (
              <span className="translated-caption-line" key={subtitle.id}>
                {effectiveJapanese(subtitle)}
              </span>
            ))}
          </div>
        )}
      </div>
      {region && <p className="selection-help">字幕範囲を選択済み</p>}
      <AnalysisWorkspace
        path={path}
        region={region}
        duration={duration}
        project={project}
        setProject={setProject}
        onSeek={seek}
        onBusyChange={updateProcessing}
        onLineSplitChange={setLineSplitRatio}
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
