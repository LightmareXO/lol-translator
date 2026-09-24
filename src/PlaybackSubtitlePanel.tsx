import type { ReactNode } from "react";
import {
  type AnalysisProject,
  activeSubtitles,
  effectiveJapanese,
  effectiveKorean,
  type SubtitleRecord,
} from "./analysisProject";

interface PlaybackSubtitlePanelProps {
  project: AnalysisProject | null;
  currentTime: number;
}

interface ProcessingError {
  phase?: unknown;
  timestamp_seconds?: unknown;
  message?: unknown;
}

function ocrErrorAtTime(
  project: AnalysisProject,
  currentTime: number,
): string | null {
  const sampleSeconds = Math.max(
    project.analysis.sample_interval_ms / 1000,
    0.001,
  );
  const error = project.processing.errors.find((candidate) => {
    const { phase, timestamp_seconds: timestamp } =
      candidate as ProcessingError;
    return (
      phase === "ocr" &&
      typeof timestamp === "number" &&
      timestamp <= currentTime &&
      currentTime < timestamp + sampleSeconds
    );
  }) as ProcessingError | undefined;
  return typeof error?.message === "string" && error.message.trim()
    ? error.message
    : error
      ? "OCR処理に失敗しました。"
      : null;
}

function JapaneseSubtitle({ subtitle }: { subtitle: SubtitleRecord }) {
  const userJapanese = subtitle.translation.user_ja?.trim();
  if (userJapanese) {
    return (
      <>
        <span className="subtitle-badge">ユーザー修正</span>
        <p
          className="playback-subtitle-text playback-subtitle-japanese"
          lang="ja"
        >
          {effectiveJapanese(subtitle)}
        </p>
      </>
    );
  }

  if (subtitle.translation.status === "stale") {
    return (
      <p className="playback-subtitle-message" role="status">
        韓国語原文が修正されています。再翻訳が必要です。
      </p>
    );
  }
  if (subtitle.translation.status === "pending") {
    return <p className="playback-subtitle-message">日本語訳を準備中です。</p>;
  }
  if (subtitle.translation.status === "error") {
    return (
      <p className="playback-subtitle-message playback-subtitle-error">
        翻訳に失敗しました
        {subtitle.translation.error ? `：${subtitle.translation.error}` : "。"}
      </p>
    );
  }

  const japanese = effectiveJapanese(subtitle).trim();
  return japanese ? (
    <p className="playback-subtitle-text playback-subtitle-japanese" lang="ja">
      {japanese}
    </p>
  ) : (
    <p className="playback-subtitle-message">日本語訳がありません。</p>
  );
}

function SubtitleItem({ subtitle }: { subtitle: SubtitleRecord }) {
  const korean = effectiveKorean(subtitle).trim();
  const corrected = Boolean(subtitle.corrected_ko?.trim());
  return (
    <article className="playback-subtitle-item">
      <div className="playback-subtitle-heading">
        <span>韓国語</span>
        {corrected && <span className="subtitle-badge">原文修正済み</span>}
      </div>
      {korean ? (
        <p
          className="playback-subtitle-text playback-subtitle-korean"
          lang="ko"
        >
          {korean}
        </p>
      ) : (
        <p className="playback-subtitle-message">韓国語原文がありません。</p>
      )}
      <div className="playback-subtitle-heading">
        <span>日本語</span>
      </div>
      <JapaneseSubtitle subtitle={subtitle} />
    </article>
  );
}

export function PlaybackSubtitlePanel({
  project,
  currentTime,
}: PlaybackSubtitlePanelProps) {
  let content: ReactNode;
  if (!project) {
    content = (
      <p className="playback-subtitle-empty">
        解析結果はまだありません。解析後、ここに韓国語原文と日本語訳を表示します。
      </p>
    );
  } else if (
    currentTime < project.analysis.start_seconds ||
    currentTime >= project.analysis.end_seconds
  ) {
    content = (
      <p className="playback-subtitle-empty">この時刻は解析範囲外です。</p>
    );
  } else {
    const subtitles = activeSubtitles(project.subtitles, currentTime);
    const ocrError =
      subtitles.length === 0 ? ocrErrorAtTime(project, currentTime) : null;
    content = ocrError ? (
      <p className="playback-subtitle-empty playback-subtitle-error">
        この時刻のOCRに失敗しました：{ocrError}
      </p>
    ) : subtitles.length > 0 ? (
      <div className="playback-subtitle-list">
        {subtitles.map((subtitle) => (
          <SubtitleItem key={subtitle.id} subtitle={subtitle} />
        ))}
      </div>
    ) : (
      <p className="playback-subtitle-empty">この時刻に字幕はありません。</p>
    );
  }

  return (
    <section className="playback-subtitles" aria-label="再生位置の字幕">
      {content}
    </section>
  );
}
