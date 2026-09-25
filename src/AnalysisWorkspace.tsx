import { invoke } from "@tauri-apps/api/core";
import { save } from "@tauri-apps/plugin-dialog";
import {
  type Dispatch,
  type SetStateAction,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { AnalysisRangeSlider } from "./AnalysisRangeSlider";
import {
  type AnalysisProject,
  effectiveJapanese,
  effectiveKorean,
  formatTime,
  replaceSubtitle,
  type SubtitleRecord,
  updateCorrectedKorean,
  updateUserJapanese,
} from "./analysisProject";
import type { SubtitleRegion } from "./subtitleRegion";

interface Progress {
  state: "running" | "completed" | "failed" | "cancelled";
  phase: string;
  current: number;
  total: number;
  message: string;
  error: string | null;
}

interface JobSnapshot {
  job_id: string;
  progress: Progress;
  project: AnalysisProject | null;
}

interface ActiveJob {
  id: string;
  mode: "analysis" | "all" | "selected";
  selectedId?: string;
}

interface Props {
  path: string;
  region: SubtitleRegion | null;
  duration: number;
  currentTime: number;
  project: AnalysisProject | null;
  setProject: Dispatch<SetStateAction<AnalysisProject | null>>;
  onSeek: (seconds: number) => void;
  onBusyChange?: (busy: boolean) => void;
  onLineSplitChange?: (ratio: number | null) => void;
}

const INITIAL_PROGRESS: Progress = {
  state: "running",
  phase: "starting",
  current: 0,
  total: 0,
  message: "解析の開始を待っています",
  error: null,
};

function messageFrom(error: unknown): string {
  return typeof error === "string"
    ? error
    : error instanceof Error
      ? error.message
      : "不明なエラーが発生しました。";
}

export function AnalysisWorkspace({
  path,
  region,
  duration,
  currentTime,
  project,
  setProject,
  onSeek,
  onBusyChange,
  onLineSplitChange,
}: Props) {
  const savedSplit = project?.analysis.line_split_ratio;
  const [wholeVideo, setWholeVideo] = useState(
    project?.analysis.mode === "whole",
  );
  const [startSeconds, setStartSeconds] = useState(
    project?.analysis.start_seconds ?? 0,
  );
  const [endSeconds, setEndSeconds] = useState(
    project?.analysis.end_seconds ?? 0,
  );
  const [twoLines, setTwoLines] = useState(savedSplit != null);
  const [splitPercent, setSplitPercent] = useState(
    savedSplit == null ? 50 : Math.round(savedSplit * 100),
  );
  const [minimumDisplaySeconds, setMinimumDisplaySeconds] = useState(
    (project?.analysis.minimum_display_duration_ms ?? 1200) / 1000,
  );
  const [activeJob, setActiveJob] = useState<ActiveJob | null>(null);
  const [launchingJob, setLaunchingJob] = useState(false);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(
    project?.subtitles[0]?.id ?? null,
  );
  const polling = useRef(false);
  const launching = useRef(false);

  const busy = launchingJob || Boolean(activeJob);

  useEffect(() => {
    onBusyChange?.(busy);
  }, [busy, onBusyChange]);

  useEffect(() => {
    onLineSplitChange?.(twoLines ? splitPercent / 100 : null);
  }, [onLineSplitChange, splitPercent, twoLines]);

  useEffect(() => {
    if (duration > 0 && endSeconds === 0) {
      setEndSeconds(Math.min(duration, 60));
    }
  }, [duration, endSeconds]);

  useEffect(() => {
    if (!project) return;
    setSelectedId((current) =>
      current && project.subtitles.some((item) => item.id === current)
        ? current
        : (project.subtitles[0]?.id ?? null),
    );
  }, [project]);

  useEffect(() => {
    if (!activeJob) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function poll() {
      if (polling.current || disposed || !activeJob) return;
      polling.current = true;
      try {
        const snapshot = await invoke<JobSnapshot>("analysis_job_status", {
          jobId: activeJob.id,
        });
        if (disposed) return;
        setProgress(snapshot.progress);
        if (snapshot.progress.state === "completed" && snapshot.project) {
          if (activeJob.mode === "selected" && activeJob.selectedId) {
            const translated = snapshot.project.subtitles[0];
            setProject((current) =>
              current && translated
                ? {
                    ...replaceSubtitle(current, translated),
                    configuration: {
                      ...current.configuration,
                      translation:
                        snapshot.project?.configuration.translation ??
                        current.configuration.translation,
                    },
                  }
                : current,
            );
          } else {
            setProject(snapshot.project);
          }
          setNotice(
            activeJob.mode === "analysis"
              ? "解析が完了しました。字幕一覧から内容を確認できます。"
              : "保存済みOCRから翻訳を再実行しました。ユーザーが修正した日本語は保持しています。",
          );
          setActiveJob(null);
          return;
        }
        if (
          snapshot.progress.state === "failed" ||
          snapshot.progress.state === "cancelled"
        ) {
          if (snapshot.progress.error) setError(snapshot.progress.error);
          setActiveJob(null);
          return;
        }
      } catch (cause) {
        if (!disposed) {
          setError(messageFrom(cause));
          setActiveJob(null);
        }
        return;
      } finally {
        polling.current = false;
      }
      timer = setTimeout(poll, 400);
    }

    void poll();
    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
      polling.current = false;
    };
  }, [activeJob, setProject]);

  const selected = useMemo(
    () => project?.subtitles.find((item) => item.id === selectedId) ?? null,
    [project, selectedId],
  );
  const rangeValid =
    wholeVideo ||
    (Number.isFinite(startSeconds) &&
      Number.isFinite(endSeconds) &&
      startSeconds >= 0 &&
      endSeconds > startSeconds &&
      endSeconds <= duration);
  const minimumDurationValid =
    Number.isFinite(minimumDisplaySeconds) &&
    minimumDisplaySeconds >= 0 &&
    minimumDisplaySeconds <= 60;
  const timeStep = duration > 0 && duration < 1 ? 0.01 : 0.1;

  async function startAnalysis() {
    if (
      !region ||
      !rangeValid ||
      !minimumDurationValid ||
      activeJob ||
      launching.current
    )
      return;
    launching.current = true;
    setLaunchingJob(true);
    setError(null);
    setNotice(null);
    setProgress(INITIAL_PROGRESS);
    try {
      const started = await invoke<{ job_id: string }>("start_analysis", {
        request: {
          schema_version: 2,
          video_path: path,
          subtitle_region: region,
          analysis_range: {
            mode: wholeVideo ? "whole" : "range",
            start_seconds: wholeVideo ? null : startSeconds,
            end_seconds: wholeVideo ? null : endSeconds,
          },
          settings: {
            sample_interval_ms: 200,
            line_split_ratio: twoLines ? splitPercent / 100 : null,
            minimum_display_duration_ms: Math.round(
              minimumDisplaySeconds * 1000,
            ),
          },
        },
      });
      setActiveJob({ id: started.job_id, mode: "analysis" });
    } catch (cause) {
      setProgress(null);
      setError(messageFrom(cause));
    } finally {
      launching.current = false;
      setLaunchingJob(false);
    }
  }

  async function startRetranslation(mode: "all" | "selected") {
    if (!project || activeJob || launching.current) return;
    const selectedOnly = mode === "selected" ? selected : null;
    if (mode === "selected" && !selectedOnly) return;
    setError(null);
    setNotice(null);
    setProgress(INITIAL_PROGRESS);
    launching.current = true;
    setLaunchingJob(true);
    try {
      const input = selectedOnly
        ? { ...project, subtitles: [selectedOnly] }
        : project;
      const started = await invoke<{ job_id: string }>("start_retranslation", {
        project: input,
      });
      setActiveJob({
        id: started.job_id,
        mode,
        selectedId: selectedOnly?.id,
      });
    } catch (cause) {
      setProgress(null);
      setError(messageFrom(cause));
    } finally {
      launching.current = false;
      setLaunchingJob(false);
    }
  }

  async function cancelJob() {
    if (!activeJob) return;
    setError(null);
    try {
      await invoke("cancel_analysis_job", { jobId: activeJob.id });
      setProgress({
        ...INITIAL_PROGRESS,
        state: "cancelled",
        phase: "cancelled",
        message: "解析をキャンセルしました",
      });
      setActiveJob(null);
    } catch (cause) {
      setError(messageFrom(cause));
    }
  }

  async function saveProject() {
    if (!project) return;
    setError(null);
    try {
      const destination = await save({
        title: "解析結果を保存",
        defaultPath: `${project.source_video.name}.loltranslator.json`,
        filters: [{ name: "LoL Translator JSON", extensions: ["json"] }],
      });
      if (!destination) return;
      await invoke("save_analysis_project", {
        path: destination,
        project,
      });
      setNotice(`保存しました：${destination}`);
    } catch (cause) {
      setError(messageFrom(cause));
    }
  }

  function updateSelected(
    updater: (subtitle: SubtitleRecord) => SubtitleRecord,
  ) {
    if (!selected) return;
    const targetId = selected.id;
    setProject((current) => {
      const currentSubtitle = current?.subtitles.find(
        (item) => item.id === targetId,
      );
      return current && currentSubtitle
        ? replaceSubtitle(current, updater(currentSubtitle))
        : current;
    });
  }

  const percentage =
    progress && progress.total > 0
      ? Math.min(100, (progress.current / progress.total) * 100)
      : 0;

  return (
    <section className="analysis-workspace" aria-label="字幕解析">
      <div className="analysis-settings">
        <h2>事前解析</h2>
        <label className="check-row">
          <input
            type="checkbox"
            checked={wholeVideo}
            disabled={busy || duration <= 0}
            onChange={(event) => setWholeVideo(event.currentTarget.checked)}
          />
          動画全体を解析
        </label>
        <AnalysisRangeSlider
          duration={duration}
          currentSeconds={currentTime}
          startSeconds={startSeconds}
          endSeconds={endSeconds}
          disabled={busy}
          wholeVideo={wholeVideo}
          onChange={(start, end) => {
            setStartSeconds(start);
            setEndSeconds(end);
          }}
        />
        <div className="time-fields">
          <label>
            開始（秒）
            <input
              type="number"
              min="0"
              step={timeStep}
              value={Number.isFinite(startSeconds) ? startSeconds : ""}
              disabled={wholeVideo || busy || duration <= 0}
              onChange={(event) => {
                const value = event.currentTarget.value;
                setStartSeconds(value === "" ? Number.NaN : Number(value));
              }}
            />
          </label>
          <label>
            終了（秒）
            <input
              type="number"
              min="0"
              step={timeStep}
              value={Number.isFinite(endSeconds) ? endSeconds : ""}
              disabled={wholeVideo || busy || duration <= 0}
              onChange={(event) => {
                const value = event.currentTarget.value;
                setEndSeconds(value === "" ? Number.NaN : Number(value));
              }}
            />
          </label>
        </div>
        <p className="selection-help">
          解析時間に3分の上限はありません。初回確認は短い区間がおすすめです。抽出間隔は200msです。
        </p>
        <label>
          最小表示時間（秒）
          <input
            type="number"
            min="0"
            max="60"
            step="0.1"
            value={minimumDisplaySeconds}
            disabled={busy}
            onChange={(event) =>
              setMinimumDisplaySeconds(event.currentTarget.valueAsNumber)
            }
          />
        </label>
        <p className="selection-help">
          初期値は1.2秒です。0にすると長さによる除外を無効にしますが、全字幕の検出は保証しません。
        </p>
        {!minimumDurationValid && (
          <p className="error">最小表示時間は0〜60秒で指定してください。</p>
        )}
        <label className="check-row">
          <input
            type="checkbox"
            checked={twoLines}
            disabled={busy}
            onChange={(event) => setTwoLines(event.currentTarget.checked)}
          />
          1つのROIを上下2領域としてOCRする
        </label>
        {twoLines && (
          <>
            <label>
              上段の高さ：{splitPercent}%
              <input
                type="range"
                min="10"
                max="90"
                value={splitPercent}
                disabled={busy}
                onChange={(event) =>
                  setSplitPercent(event.currentTarget.valueAsNumber)
                }
              />
            </label>
            <p className="selection-help">
              水色の線より上と下を別の行IDとして追跡し、変化した行だけOCR・翻訳します。
            </p>
          </>
        )}
        {!rangeValid && !wholeVideo && duration > 0 && (
          <p className="error">
            終了時刻は開始より後、かつ動画の長さ以内にしてください。
          </p>
        )}
        <div className="button-row">
          <button
            type="button"
            disabled={
              !region ||
              !rangeValid ||
              !minimumDurationValid ||
              duration <= 0 ||
              busy
            }
            onClick={startAnalysis}
          >
            解析を開始
          </button>
          {activeJob && (
            <button type="button" className="secondary" onClick={cancelJob}>
              キャンセル
            </button>
          )}
        </div>
        {!region && (
          <p className="selection-help">先に字幕範囲を指定してください。</p>
        )}
        {progress && (
          <div className="progress-block" role="status">
            <p>
              {progress.message}
              {progress.total > 0 &&
                `（${progress.current}/${progress.total}）`}
            </p>
            <progress max="100" value={percentage} />
          </div>
        )}
      </div>

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="file-name" role="status">
          {notice}
        </p>
      )}

      {project && (
        <div className="subtitle-review">
          <div className="review-toolbar">
            <h2>字幕の確認と修正</h2>
            <div className="button-row">
              <button type="button" disabled={busy} onClick={saveProject}>
                JSONを保存
              </button>
              <button
                type="button"
                className="secondary"
                disabled={busy || project.subtitles.length === 0}
                onClick={() => startRetranslation("all")}
              >
                翻訳だけ全件再実行
              </button>
            </div>
          </div>
          <p className="selection-help">
            {project.subtitles.length}件 /{" "}
            {formatTime(project.analysis.start_seconds)}〜
            {formatTime(project.analysis.end_seconds)}
            。OCR生出力は修正で上書きしません。
          </p>
          <div className="review-grid">
            <nav className="subtitle-list" aria-label="字幕一覧">
              {project.subtitles.map((item) => (
                <button
                  type="button"
                  key={item.id}
                  className={item.id === selectedId ? "selected" : undefined}
                  onClick={() => {
                    setSelectedId(item.id);
                    onSeek(item.start_seconds);
                  }}
                >
                  <span>{formatTime(item.start_seconds)}</span>
                  <span>
                    {item.line_id}：{effectiveJapanese(item) || "（翻訳なし）"}
                  </span>
                </button>
              ))}
            </nav>
            {selected ? (
              <div className="subtitle-editor">
                {selected.image_png_base64 && (
                  <img
                    src={`data:image/png;base64,${selected.image_png_base64}`}
                    alt={`${formatTime(selected.start_seconds)}の字幕画像`}
                  />
                )}
                <dl>
                  <dt>時刻</dt>
                  <dd>
                    {formatTime(selected.start_seconds)}〜
                    {formatTime(selected.end_seconds)}
                  </dd>
                  <dt>行</dt>
                  <dd>{selected.line_id}</dd>
                  <dt>OCR生出力（不変）</dt>
                  <dd className="raw-output">{selected.ocr.raw_text}</dd>
                </dl>
                {selected.ocr.variants && selected.ocr.variants.length > 1 && (
                  <details className="ocr-variants">
                    <summary>
                      統合したOCR候補（{selected.ocr.variants.length}件）
                    </summary>
                    <ol>
                      {selected.ocr.variants.map((variant) => (
                        <li
                          key={`${variant.start_seconds}-${variant.end_seconds}-${variant.raw_text}`}
                        >
                          <span>
                            {formatTime(variant.start_seconds)}・信頼度
                            {variant.confidence == null
                              ? "不明"
                              : variant.confidence.toFixed(2)}
                          </span>
                          <code>{variant.raw_text}</code>
                        </li>
                      ))}
                    </ol>
                  </details>
                )}
                <label>
                  修正後の韓国語
                  <textarea
                    value={selected.corrected_ko ?? ""}
                    placeholder={selected.ocr.raw_text}
                    disabled={busy}
                    onChange={(event) => {
                      const value = event.currentTarget.value;
                      updateSelected((item) =>
                        updateCorrectedKorean(item, value),
                      );
                    }}
                  />
                </label>
                <p className="selection-help">
                  翻訳入力：{effectiveKorean(selected)} / 状態：
                  {selected.translation.status}
                </p>
                <label>
                  生成した日本語
                  <textarea
                    value={selected.translation.generated_ja ?? ""}
                    readOnly
                  />
                </label>
                <label>
                  ユーザー修正の日本語
                  <textarea
                    value={selected.translation.user_ja ?? ""}
                    placeholder="空の場合は生成訳を表示"
                    disabled={busy}
                    onChange={(event) => {
                      const value = event.currentTarget.value;
                      updateSelected((item) => updateUserJapanese(item, value));
                    }}
                  />
                </label>
                <button
                  type="button"
                  className="secondary"
                  disabled={busy}
                  onClick={() => startRetranslation("selected")}
                >
                  この字幕を再翻訳
                </button>
                <p className="selection-help">
                  再翻訳は生成訳だけ更新し、ユーザー修正訳を上書きしません。
                </p>
              </div>
            ) : (
              <p>字幕を選択してください。</p>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
