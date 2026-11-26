import { writeFile } from "node:fs/promises";
import { join } from "node:path";
import { browser } from "@wdio/globals";

const artifacts = process.env.LOL_TRANSLATOR_E2E_ARTIFACTS;
const application = process.env.LOL_TRANSLATOR_E2E_BINARY;
const port = Number(process.env.LOL_TRANSLATOR_E2E_PORT);
if (!artifacts || !application || !Number.isInteger(port) || port <= 0) {
  throw new Error("Start the suite with bun run test:e2e:windows.");
}

export const config = {
  runner: "local",
  framework: "mocha",
  specs: ["./*.e2e.mjs"],
  maxInstances: 1,
  hostname: "127.0.0.1",
  outputDir: artifacts,
  logLevel: "info",
  waitforTimeout: 10000,
  connectionRetryTimeout: 15000,
  connectionRetryCount: 0,
  reporters: ["spec"],
  mochaOpts: { timeout: 30000 },
  services: [
    [
      "@wdio/tauri-service",
      {
        driverProvider: "embedded",
        embeddedPort: port,
        appBinaryPath: application,
        captureBackendLogs: true,
      },
    ],
  ],
  capabilities: [
    {
      browserName: "tauri",
      "tauri:options": { application },
    },
  ],
  async afterTest(test, _context, result) {
    if (result.passed) return;
    const name = test.title.replace(/[^a-z0-9_-]+/gi, "-");
    const prefix = join(artifacts, `failure-${name}`);
    await writeFile(
      `${prefix}.txt`,
      String(result.error?.stack ?? result.error),
    );
    try {
      await browser.saveScreenshot(`${prefix}.png`);
    } catch (error) {
      await writeFile(`${prefix}-screenshot.txt`, String(error));
    }
  },
};
