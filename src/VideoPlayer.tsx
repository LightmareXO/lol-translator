import {
  type KeyboardEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { AnalysisWorkspace } from "./AnalysisWorkspace";
import { type AnalysisProject, formatTime } from "./analysisProject";
import { PlaybackSubtitlePanel } from "./PlaybackSubtitlePanel";
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
  const playbackRef = useRef<HTMLElement>(null);
  const fullscreenScroll = useRef<number | null>(null);
  const [bounds, setBounds] = useState<Rectangle | null>(null);
  const [editing, setEditing] = useState(false);
  const [region, setRegion] = useState<SubtitleRegion | null>(
    initialProject?.analysis.subtitle_region ?? null,
  );
  const [duration, setDuration] = useState(0);
  const [currentTime, setCurrentTime] = useState(0);
  const [paused, setPaused] = useState(true);
  const [volume, setVolume] = useState(1);
  const [muted, setMuted] = useState(false);
  const [playbackRate, setPlaybackRate] = useState(1);
  const [fullscreen, setFullscreen] = useState(false);
  const [playbackError, setPlaybackError] = useState<string | null>(null);
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
  const playbackDisabled = editing || processing || status !== "ready";

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

  useEffect(() => {
    let restoreFrame = 0;
    function syncFullscreen() {
      const active = document.fullscreenElement === playbackRef.current;
      setFullscreen(active);
      if (!active && fullscreenScroll.current !== null) {
        const scrollY = fullscreenScroll.current;
        fullscreenScroll.current = null;
        restoreFrame = requestAnimationFrame(() => window.scrollTo(0, scrollY));
      }
    }
    document.addEventListener("fullscreenchange", syncFullscreen);
    return () => {
      document.removeEventListener("fullscreenchange", syncFullscreen);
      cancelAnimationFrame(restoreFrame);
    };
  }, []);

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
    if (!video || !Number.isFinite(seconds)) return;
    video.currentTime = Math.max(0, Math.min(duration, seconds));
    syncTime();
  }

  function syncDuration() {
    const seconds = videoRef.current?.duration ?? 0;
    setDuration(Number.isFinite(seconds) ? seconds : 0);
  }

  function play(video: HTMLVideoElement) {
    setPlaybackError(null);
    void video.play().catch(() => {
      setPlaybackError("動画を再生できませんでした。");
    });
  }

  function togglePlayback() {
    const video = videoRef.current;
    if (!video || playbackDisabled) return;
    if (video.paused) play(video);
    else video.pause();
  }

  function seekBy(seconds: number) {
    const video = videoRef.current;
    if (video) seek(video.currentTime + seconds);
  }

  async function toggleFullscreen() {
    const playback = playbackRef.current;
    if (!playback) return;
    setPlaybackError(null);
    try {
      if (document.fullscreenElement === playback) {
        await document.exitFullscreen();
      } else {
        fullscreenScroll.current = window.scrollY;
        await playback.requestFullscreen();
      }
    } catch {
      if (document.fullscreenElement !== playback)
        fullscreenScroll.current = null;
      setPlaybackError("全画面表示を切り替えられませんでした。");
    }
  }

  function handlePlaybackKey(event: KeyboardEvent<HTMLButtonElement>) {
    if (
      playbackDisabled ||
      event.defaultPrevented ||
      event.nativeEvent.isComposing ||
      event.altKey ||
      event.ctrlKey ||
      event.metaKey ||
      event.shiftKey
    )
      return;
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      seekBy(event.key === "ArrowLeft" ? -5 : 5);
    }
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
      <section
        ref={playbackRef}
        className="playback"
        aria-label="動画の再生操作"
      >
        <div className="video-stage">
          {/* biome-ignore lint/a11y/useMediaCaption: Captions are rendered from the local analysis project below. */}
          <video
            ref={videoRef}
            className="video"
            aria-label={`動画：${name}`}
            src={url}
            preload="metadata"
            playsInline
            onLoadedMetadata={() => {
              measureVideo();
              syncDuration();
              syncTime();
            }}
            onLoadedData={() => {
              setStatus("ready");
              measureVideo();
            }}
            onDurationChange={syncDuration}
            onPlay={(event) => {
              if (editing || processing) event.currentTarget.pause();
              else setPaused(false);
            }}
            onTimeUpdate={syncTime}
            onSeeking={syncTime}
            onSeeked={syncTime}
            onRateChange={(event) =>
              setPlaybackRate(event.currentTarget.playbackRate)
            }
            onVolumeChange={(event) => {
              setVolume(event.currentTarget.volume);
              setMuted(event.currentTarget.muted);
            }}
            onPause={() => {
              setPaused(true);
              syncTime();
            }}
            onEnded={() => {
              setPaused(true);
              syncTime();
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
              lineSplitRatio={lineSplitRatio}
              onChange={updateRegion}
              onExit={() => setEditing(false)}
            />
          )}
        </div>
        <PlaybackSubtitlePanel project={project} currentTime={currentTime} />
        <div className="playback-controls">
          <div className="playback-seek">
            <input
              type="range"
              aria-label="再生位置"
              aria-valuetext={formatTime(currentTime)}
              min={0}
              max={duration}
              step="any"
              value={Math.min(currentTime, duration)}
              disabled={playbackDisabled || duration === 0}
              onChange={(event) => seek(event.currentTarget.valueAsNumber)}
            />
            <span className="playback-time">
              {formatTime(currentTime)} / {formatTime(duration)}
            </span>
          </div>
          <button
            type="button"
            className="secondary"
            aria-label="10秒戻る"
            disabled={playbackDisabled || duration === 0}
            onClick={() => seekBy(-10)}
          >
            −10秒
          </button>
          <button
            type="button"
            disabled={playbackDisabled}
            onClick={togglePlayback}
            onKeyDown={handlePlaybackKey}
            aria-keyshortcuts="Space ArrowLeft ArrowRight"
            title="Spaceで再生・一時停止、左右の矢印キーで5秒移動"
          >
            {paused ? "再生" : "一時停止"}
          </button>
          <button
            type="button"
            className="secondary"
            aria-label="10秒進む"
            disabled={playbackDisabled || duration === 0}
            onClick={() => seekBy(10)}
          >
            ＋10秒
          </button>
          <button
            className="secondary"
            type="button"
            disabled={status !== "ready"}
            onClick={() => {
              const video = videoRef.current;
              if (video) video.muted = !video.muted;
            }}
          >
            {muted ? "ミュート解除" : "ミュート"}
          </button>
          <label className="playback-volume">
            音量
            <input
              type="range"
              min={0}
              max={1}
              step={0.01}
              value={volume}
              disabled={status !== "ready"}
              onChange={(event) => {
                const video = videoRef.current;
                if (!video) return;
                video.volume = event.currentTarget.valueAsNumber;
                if (video.volume > 0) video.muted = false;
              }}
            />
          </label>
          <label className="playback-rate">
            再生速度
            <select
              value={playbackRate}
              disabled={status !== "ready"}
              onChange={(event) => {
                const video = videoRef.current;
                if (video)
                  video.playbackRate = Number(event.currentTarget.value);
              }}
            >
              {[0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2].map((rate) => (
                <option key={rate} value={rate}>
                  {rate}×
                </option>
              ))}
            </select>
          </label>
          <button
            className="secondary"
            type="button"
            disabled={!fullscreen && playbackDisabled}
            onClick={toggleFullscreen}
          >
            {fullscreen ? "全画面を終了" : "全画面"}
          </button>
        </div>
        {playbackError && (
          <p className="error" role="alert">
            {playbackError}
          </p>
        )}
      </section>
      {region && <p className="selection-help">字幕範囲を選択済み</p>}
      <AnalysisWorkspace
        path={path}
        region={region}
        duration={duration}
        currentTime={currentTime}
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
