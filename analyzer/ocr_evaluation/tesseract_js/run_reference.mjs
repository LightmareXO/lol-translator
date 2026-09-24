import crypto from "node:crypto";
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import process from "node:process";
import zlib from "node:zlib";

import Tesseract from "tesseract.js";

const require = createRequire(import.meta.url);
const { simd } = require("wasm-feature-detect");
const { createWorker, OEM, PSM } = Tesseract;

function parseArguments(argv) {
  const result = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith("--") || value === undefined) {
      throw new Error(
        `引数は --name value の組で指定してください: ${key ?? ""}`,
      );
    }
    result[key.slice(2)] = value;
  }
  for (const required of [
    "prepared",
    "images",
    "output",
    "languages",
    "lang-path",
    "gzip",
    "psm",
    "model-label",
  ]) {
    if (!(required in result)) {
      throw new Error(`--${required} が必要です`);
    }
  }
  return result;
}

function sha256Buffer(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

function sha256File(filePath) {
  return sha256Buffer(fs.readFileSync(filePath));
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function stripTrailingNewlines(value) {
  return value.replace(/[\r\n]+$/u, "");
}

function packageMetadata(packageName, lock) {
  const packagePath = require.resolve(`${packageName}/package.json`);
  const payload = readJson(packagePath);
  const lockEntry = lock.packages?.[`node_modules/${packageName}`] ?? {};
  return {
    version: payload.version,
    npm_integrity: lockEntry.integrity ?? null,
  };
}

function languageModelMetadata(languagePath, languages, gzip, lock) {
  return Object.fromEntries(
    languages.split("+").map((language) => {
      const fileName = `${language}.traineddata${gzip ? ".gz" : ""}`;
      const filePath = path.join(languagePath, fileName);
      if (!fs.existsSync(filePath)) {
        throw new Error(`言語モデルがありません: ${filePath}`);
      }
      const content = fs.readFileSync(filePath);
      const expanded = gzip ? zlib.gunzipSync(content) : content;
      return [
        language,
        {
          package: packageMetadata(`@tesseract.js-data/${language}`, lock),
          file_name: fileName,
          bytes: content.length,
          sha256: sha256Buffer(content),
          uncompressed_bytes: expanded.length,
          uncompressed_sha256: sha256Buffer(expanded),
        },
      ];
    }),
  );
}

async function coreMetadata(lock) {
  const corePackagePath = path.dirname(
    require.resolve("tesseract.js-core/package.json"),
  );
  const hasSimd = await simd();
  const stem = hasSimd ? "tesseract-core-simd-lstm" : "tesseract-core-lstm";
  const files = [`${stem}.js`, `${stem}.wasm`];
  return {
    ...packageMetadata("tesseract.js-core", lock),
    selected_variant: hasSimd ? "simd-lstm" : "lstm",
    files: Object.fromEntries(
      files.map((fileName) => {
        const filePath = path.join(corePackagePath, fileName);
        return [
          fileName,
          {
            bytes: fs.statSync(filePath).size,
            sha256: sha256File(filePath),
          },
        ];
      }),
    ),
  };
}

async function main() {
  const args = parseArguments(process.argv.slice(2));
  const preparedPath = path.resolve(args.prepared);
  const imagesPath = path.resolve(args.images);
  const outputPath = path.resolve(args.output);
  const languagePath = path.resolve(args["lang-path"]);
  const gzip = args.gzip === "true";
  if (!gzip && args.gzip !== "false") {
    throw new Error("--gzip は true または false で指定してください");
  }
  const psm = Number(args.psm);
  if (![6, 7].includes(psm)) {
    throw new Error("この比較の --psm は 6 または 7 に限定します");
  }

  const prepared = readJson(preparedPath);
  const packageLock = readJson(new URL("./package-lock.json", import.meta.url));
  const worker = await createWorker(args.languages, OEM.LSTM_ONLY, {
    langPath: languagePath,
    gzip,
    cacheMethod: "none",
  });
  await worker.setParameters({
    tessedit_pageseg_mode: psm === 6 ? PSM.SINGLE_BLOCK : PSM.SINGLE_LINE,
  });

  const records = [];
  try {
    for (const item of prepared.cases) {
      const imagePath = path.join(imagesPath, item.image_file);
      if (sha256File(imagePath) !== item.image_sha256) {
        throw new Error(`入力画像ハッシュが一致しません: ${item.id}`);
      }
      const started = process.hrtime.bigint();
      let rawOutput = "";
      let confidence = null;
      let error = null;
      try {
        const result = await worker.recognize(imagePath);
        rawOutput = stripTrailingNewlines(result.data.text ?? "");
        confidence = result.data.confidence ?? null;
      } catch (exception) {
        error = `${exception?.name ?? "Error"}: ${exception?.message ?? exception}`;
      }
      const elapsedMs = Number(process.hrtime.bigint() - started) / 1_000_000;
      records.push({
        id: item.id,
        group_id: item.group_id,
        video_id: item.video_id,
        timestamp_seconds: item.timestamp_seconds,
        image_file: item.image_file,
        image_sha256: item.image_sha256,
        ground_truth: item.ground_truth,
        reference_status: item.reference_status,
        split: item.split,
        representative: item.representative,
        line_count: item.line_count,
        raw_output: rawOutput,
        confidence,
        inference_ms: elapsedMs,
        total_ms: elapsedMs,
        error,
      });
      process.stdout.write(
        `${item.id}: ${rawOutput.replaceAll("\n", " / ")}\n`,
      );
    }
  } finally {
    await worker.terminate();
  }

  const output = {
    schema_version: 1,
    engine_key: args["condition-id"] ?? "tesseract_js_reference",
    prepared_sha256: sha256File(preparedPath),
    condition: {
      tesseract_js: "5.1.1",
      languages: args.languages,
      oem: 1,
      psm,
      whole_roi: true,
      external_preprocessing: "none",
      line_splitting: "none",
      model_label: args["model-label"],
      gzip,
    },
    metadata: {
      node: process.version,
      platform: process.platform,
      arch: process.arch,
      tesseract_js: packageMetadata("tesseract.js", packageLock),
      execution_core: await coreMetadata(packageLock),
      language_models: languageModelMetadata(
        languagePath,
        args.languages,
        gzip,
        packageLock,
      ),
    },
    records,
  };
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, `${JSON.stringify(output, null, 2)}\n`, "utf8");
}

main().catch((error) => {
  process.stderr.write(
    `Tesseract.js参照条件の実行に失敗しました: ${error.stack ?? error}\n`,
  );
  process.exitCode = 1;
});
