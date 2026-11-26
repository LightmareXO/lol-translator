import { spawn } from "node:child_process";
import { createWriteStream } from "node:fs";
import { mkdir, mkdtemp, writeFile } from "node:fs/promises";
import { createServer } from "node:net";
import { join, resolve } from "node:path";
import { finished } from "node:stream/promises";

if (process.platform !== "win32") {
  throw new Error("Run this test on Windows with WebView2 installed.");
}

const work = resolve(".windows-e2e");
const runs = join(work, "runs");
await mkdir(runs, { recursive: true });
const artifacts = await mkdtemp(join(runs, "run-"));
const fixture = join(artifacts, "fixture.mp4");
const profile = join(artifacts, "webview-profile");
const target = join(work, "target");
const binary = join(target, "release", "lol-translator.exe");
const buildConfig = join(artifacts, "tauri.e2e.json");

console.log(`Windows E2E artifacts: ${artifacts}`);

async function run(command, args, logName, env = process.env) {
  const log = createWriteStream(join(artifacts, logName));
  const child = spawn(command, args, {
    env,
    stdio: ["ignore", "pipe", "pipe"],
  });
  child.stdout.pipe(log, { end: false });
  child.stderr.pipe(log, { end: false });
  try {
    await new Promise((resolveRun, reject) => {
      child.once("error", reject);
      child.once("close", (code, signal) => {
        if (code === 0) resolveRun();
        else
          reject(
            new Error(`${command} exited with ${code ?? signal} (${logName})`),
          );
      });
    });
  } finally {
    log.end();
    await finished(log);
  }
}

function localPort() {
  return new Promise((resolvePort, reject) => {
    const listener = createServer();
    listener.once("error", reject);
    listener.listen(0, "127.0.0.1", () => {
      const { port } = listener.address();
      listener.close((error) => {
        if (error) reject(error);
        else resolvePort(port);
      });
    });
  });
}

await run(
  process.env.LOL_TRANSLATOR_FFMPEG || "ffmpeg",
  [
    "-hide_banner",
    "-loglevel",
    "error",
    "-f",
    "lavfi",
    "-i",
    "testsrc2=size=320x180:rate=30:duration=8",
    "-an",
    "-c:v",
    "libx264",
    "-pix_fmt",
    "yuv420p",
    "-movflags",
    "+faststart",
    fixture,
  ],
  "fixture.log",
);

await writeFile(
  buildConfig,
  JSON.stringify({
    build: {
      beforeBuildCommand:
        "bun --bun vite build --mode windows-e2e --outDir .windows-e2e/dist",
      frontendDist: "../.windows-e2e/dist",
      features: ["windows-e2e"],
    },
    app: { security: { assetProtocol: { scope: [fixture] } } },
  }),
);

console.log("Building the Windows E2E app...");
await run(
  "node",
  [
    "node_modules/@tauri-apps/cli/tauri.js",
    "build",
    "--no-bundle",
    "--config",
    buildConfig,
  ],
  "build.log",
  {
    ...process.env,
    CARGO_TARGET_DIR: target,
    VITE_LOL_TRANSLATOR_E2E_VIDEO: fixture,
  },
);

console.log("Running WebdriverIO against the Windows app...");
await run(
  "node",
  ["node_modules/@wdio/cli/bin/wdio.js", "run", "e2e/windows/wdio.conf.mjs"],
  "runner.log",
  {
    ...process.env,
    LOL_TRANSLATOR_E2E_ARTIFACTS: artifacts,
    LOL_TRANSLATOR_E2E_BINARY: binary,
    LOL_TRANSLATOR_E2E_PORT: String(await localPort()),
    WEBVIEW2_USER_DATA_FOLDER: profile,
  },
);
console.log("Windows E2E passed.");
