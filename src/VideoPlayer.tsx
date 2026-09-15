import { useState } from "react";

interface VideoPlayerProps {
  path: string;
  url: string;
}

export function VideoPlayer({ path, url }: VideoPlayerProps) {
  const [status, setStatus] = useState<"loading" | "ready" | "error">(
    "loading",
  );
  const name = path.split(/[\\/]/).pop() || path;

  return (
    <div className="selected-video">
      <p className="file-name" title={path}>
        {name}
      </p>
      {/* biome-ignore lint/a11y/useMediaCaption: Local videos have no separate caption track at this stage. */}
      <video
        className="video"
        aria-label={`動画：${name}`}
        src={url}
        controls
        preload="auto"
        playsInline
        onLoadedData={() => setStatus("ready")}
        onError={() => setStatus("error")}
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
