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
    return <p className="playback-subtitle-message">日本語訳がありません。</p>;
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
      {korean && (
        <>
          <div className="playback-subtitle-heading">
            <span>韓国語</span>
            {corrected && <span className="subtitle-badge">原文修正済み</span>}
          </div>
          <p
            className="playback-subtitle-text playback-subtitle-korean"
            lang="ko"
          >
            {korean}
          </p>
        </>
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
    const subtitles = activeSubtitles(project.subtitles, currentTime).filter(
      (subtitle) => {
        const displayableJapanese =
          subtitle.translation.user_ja?.trim() ||
          (subtitle.translation.status === "completed" &&
            subtitle.translation.generated_ja?.trim());
        return effectiveKorean(subtitle).trim() || displayableJapanese;
      },
    );
    content =
      subtitles.length > 0 ? (
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
