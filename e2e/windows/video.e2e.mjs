import assert from "node:assert/strict";
import { browser } from "@wdio/globals";

async function videoState() {
  return browser.execute(() => {
    const video = document.querySelector("video");
    if (!video) return null;
    return {
      currentTime: video.currentTime,
      duration: video.duration,
      error: video.error?.code ?? null,
      paused: video.paused,
      readyState: video.readyState,
      videoWidth: video.videoWidth,
    };
  });
}

describe("Windows Tauri playback", () => {
  it("opens the fixture and advances the actual media clock", async () => {
    // Target the existing app window explicitly. The embedded driver does not
    // need the optional window-management IPC plugin to test this window.
    await browser.switchToWindow(await browser.getWindowHandle());
    console.log(`App URL: ${await browser.getUrl()}`);
    const select = await browser.$("button=動画を選択");
    await select.waitForClickable();
    await select.click();

    await browser.waitUntil(
      async () => {
        const state = await videoState();
        if (state?.error) throw new Error(`Media error: ${state.error}`);
        return state?.readyState >= 2 && state.videoWidth === 320;
      },
      {
        timeoutMsg:
          "The fixture did not load through the Tauri asset protocol.",
      },
    );

    const before = await videoState();
    assert.ok(Math.abs(before.duration - 8) < 0.1);
    assert.ok(before.paused);
    // Native media controls are rendered inside the WebView. Start the same video element.
    await browser.execute(async () => {
      const video = document.querySelector("video");
      await video.play();
    });

    await browser.waitUntil(
      async () => {
        const state = await videoState();
        return (
          state &&
          !state.paused &&
          state.currentTime - before.currentTime >= 0.5
        );
      },
      { timeoutMsg: "The video clock did not advance by 0.5 seconds." },
    );
    const after = await videoState();
    console.log(
      `Media clock: ${before.currentTime.toFixed(3)}s -> ${after.currentTime.toFixed(3)}s`,
    );
  });
});
