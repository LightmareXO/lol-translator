"""Sequential local Ollama experiments with immutable inputs and resumable results."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Ollama redirects are forbidden")


class LocalClient:
    def __init__(self, port=11434, timeout=180):
        if not 1 <= port <= 65535 or timeout <= 0:
            raise ValueError("invalid local port or timeout")
        self.base = f"http://127.0.0.1:{port}/api/"
        self.timeout = timeout
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, endpoint, payload=None):
        request = Request(self.base + endpoint,
                          data=None if payload is None else json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"})
        with self.opener.open(request, timeout=self.timeout) as response:
            result = json.load(response)
        if not isinstance(result, dict) or result.get("error"):
            raise ValueError("Ollama returned an invalid/error response")
        return result


def chat_with_retry(client, payload, attempts=2, sleep=time.sleep):
    errors = []
    last_response = None
    started = time.perf_counter()
    for attempt in range(attempts):
        try:
            response = client.request("chat", payload)
            last_response = response
            message = response.get("message", {})
            if response.get("done") is not True or not isinstance(message.get("content"), str):
                raise ValueError("incomplete chat response")
            if not message["content"].strip():
                if response.get("done_reason") == "length":
                    return {"status": "error", "response": response,
                            "errors": errors + [{"attempt": attempt + 1, "type": "OutputLimitWithoutTranslation", "http_status": None}],
                            "wall_ms": (time.perf_counter() - started) * 1000}
                raise ValueError("empty translation")
            return {"status": "ok", "response": response, "errors": errors,
                    "wall_ms": (time.perf_counter() - started) * 1000}
        except (HTTPError, URLError, TimeoutError, socket.timeout, ValueError) as error:
            # Do not serialize exception URLs/local paths into shareable data.
            code = getattr(error, "code", None)
            errors.append({"attempt": attempt + 1, "type": type(error).__name__, "http_status": code})
            if code is not None and code < 500:
                break
            if attempt + 1 < attempts:
                sleep(2 ** attempt)
    return {"status": "error", "response": last_response, "errors": errors,
            "wall_ms": (time.perf_counter() - started) * 1000}


def initial_dataset(manifest, details):
    selected = {}
    with Path(details).open(encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            if row["engine"] == "paddleocr" and row["preprocessing"] == "contrast":
                if row["image_id"] in selected:
                    raise ValueError("duplicate OCR record")
                selected[row["image_id"]] = row
    records = []
    for item in manifest["images"]:
        row = selected.pop(item["id"])
        if row["ground_truth"] != item["ground_truth"] or row["group_id"] != item["group_id"]:
            raise ValueError("OCR/manifest correspondence mismatch")
        records.append({"id": item["id"], "group_id": item["group_id"],
                        "video_id": manifest["source"]["video_id"],
                        "timestamp_seconds": item["timestamp_seconds"],
                        "split": "development_initial", "primary": item["id"].endswith("_f02"),
                        "source_ko": item["ground_truth"], "ocr_ko": row["raw_output"],
                        "reference_status": "ai_visual_transcription",
                        "line_role": "speech"})
    if selected:
        raise ValueError("extra OCR records")
    return records


_KOREAN_PARTICLES = (
    "은", "는", "이", "가", "을", "를", "에", "에서", "에게", "한테", "으로", "로",
    "와", "과", "도", "만", "의", "부터", "까지", "처럼", "보다", "하고", "랑", "이나", "나",
    "라고", "이라고",
)


def _contains_glossary_term(source_text, term):
    source = unicodedata.normalize("NFC", source_text).casefold()
    needle = unicodedata.normalize("NFC", term).casefold().strip()
    if not needle:
        return False
    offset = 0
    while (index := source.find(needle, offset)) >= 0:
        before_is_boundary = index == 0 or not source[index - 1].isalnum()
        end = index + len(needle)
        if before_is_boundary and (end == len(source) or not source[end].isalnum()):
            return True
        if before_is_boundary:
            for particle in _KOREAN_PARTICLES:
                particle_end = end + len(particle)
                if source.startswith(particle, end) and (
                    particle_end == len(source) or not source[particle_end].isalnum()
                ):
                    return True
        offset = index + 1
    return False


def glossary_text(glossary, source_text=None):
    return "\n".join(
        f"{' / '.join([entry['ko']] + entry['aliases'])} = {entry['ja']} ({entry['meaning']})"
        for entry in glossary["entries"]
        if entry["status"].startswith(("source_checked", "community_source_checked"))
        and (
            source_text is None
            or any(
                _contains_glossary_term(source_text, term)
                for term in [entry["ko"], *entry["aliases"]]
            )
        )
    )


def make_payload(model, text, with_glossary, prompts, glossary, glossary_filter_text=None):
    family = model.split(":")[0]
    if family not in ("translategemma", "qwen3"):
        raise ValueError("unsupported local model family")
    instruction = prompts[family]
    if with_glossary:
        selected_glossary = glossary_text(glossary, glossary_filter_text)
        if selected_glossary:
            instruction += "\n" + prompts["glossary_instruction"] + "\n" + selected_glossary
    payload = {"model": model, "messages": [{"role": "user", "content": instruction + "\n\n\n" + text}],
               "stream": False, "keep_alive": "10m", "options": prompts["options"]}
    if family == "qwen3":
        payload["think"] = prompts["qwen_think"]
    return payload


def jobs_for(records, model, prompts, glossary):
    for row in records:
        for condition in ("ABCD" if row["primary"] else "BD"):
            text = row["source_ko"] if condition in "AC" else row["ocr_ko"]
            yield {"sample_id": row["id"], "group_id": row["group_id"], "split": row["split"],
                   "primary": row["primary"], "condition": condition,
                   "payload": make_payload(model, text, condition in "CD", prompts, glossary)}


class Resources:
    """Sample server/runner RSS, not Python client RSS; GPU memory is whole-device."""
    def __init__(self):
        self.stop_event = threading.Event()
        self.peak_rss = None
        self.peak_gpu_mib = None
        self.samples = 0
        self.thread = threading.Thread(target=self.sample, daemon=True)

    def sample(self):
        try:
            import psutil
        except ImportError:
            psutil = None
        while not self.stop_event.is_set():
            if psutil:
                rss = 0
                found = False
                for process in psutil.process_iter(["name", "memory_info"]):
                    try:
                        if "ollama" in (process.info["name"] or "").lower():
                            rss += process.info["memory_info"].rss
                            found = True
                    except (psutil.Error, TypeError):
                        pass
                if found:
                    self.peak_rss = max(self.peak_rss or 0, rss)
            try:
                output = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                                        capture_output=True, text=True, timeout=3,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                values = [float(line) for line in output.stdout.splitlines()]
                if values:
                    self.peak_gpu_mib = max(self.peak_gpu_mib or 0, sum(values))
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
            self.samples += 1
            self.stop_event.wait(.5)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop_event.set()
        self.thread.join(timeout=5)

    def result(self):
        return {"ollama_processes_peak_rss_bytes": self.peak_rss,
                "whole_gpu_peak_used_mib": self.peak_gpu_mib,
                "sample_count": self.samples, "interval_seconds": .5,
                "note": "RSS sums Ollama processes. GPU usage includes other applications; not model-only VRAM."}


def cached_result(path, job, identity):
    if not path.exists():
        return None
    result = read_json(path)
    if result["job"] != job or result["identity"] != identity:
        raise ValueError("resume identity mismatch")
    body = {key: value for key, value in result.items() if key != "checksum"}
    if result.get("checksum") != digest(body):
        raise ValueError("saved result checksum mismatch")
    return result if result["status"] == "ok" else None


def validate_thinking(show, prompts):
    info = show.get("model_info", {})
    variant = info.get("general.finetune", "").lower()
    if variant == "thinking" and prompts.get("qwen_think") is False:
        raise ValueError("thinking-only model cannot be evaluated as non-thinking")
    if variant == "instruct" and prompts.get("qwen_think") is True:
        raise ValueError("instruct-only model cannot be evaluated as thinking")


def stored_identity(identity):
    return {**identity, "identity_sha256": digest(identity)}


def run(client, model, records, prompts, glossary, destination):
    models = client.request("tags")["models"]
    info = next((item for item in models if item["name"] == model), None)
    if info is None or ":cloud" in model:
        raise ValueError("requested model must already be installed locally")
    show = client.request("show", {"model": model})
    if show.get("remote_host") or show.get("remote_model"):
        raise ValueError("remote model is forbidden")
    validate_thinking(show, prompts)
    identity = {"model": model, "digest": info["digest"], "details": info["details"],
                "model_disk_bytes": info["size"], "ollama": client.request("version"),
                "template": show.get("template"), "model_parameters": show.get("parameters"),
                "model_info": show.get("model_info"), "capabilities": show.get("capabilities"),
                "prompts": prompts, "glossary": glossary, "dataset_sha256": digest(records),
                "runner_schema": 2, "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    identity_digest = digest(identity)
    directory = destination / identity_digest[:24]
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / "running.lock"
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.close(descriptor)
    try:
        existing_identity = directory / "identity.json"
        saved_identity = stored_identity(identity)
        if existing_identity.exists() and read_json(existing_identity) != saved_identity:
            raise ValueError("short experiment ID collision")
        save_json(existing_identity, saved_identity)
        jobs = list(jobs_for(records, model, prompts, glossary))
        pending = [(job, directory / (digest(job)[:32] + ".json")) for job in jobs]
        pending = [(job, path) for job, path in pending if cached_result(path, job, identity) is None]
        if not pending:
            return directory
        # Deliberate unload then unrelated warmup. Never counted as an evaluation subtitle.
        client.request("generate", {"model": model, "keep_alive": 0})
        with Resources() as resource:
            warmup = chat_with_retry(client, make_payload(model, "안녕하세요.", False, prompts, glossary))
        save_json(directory / f"warmup-{time.time_ns()}.json",
                  {**warmup, "resources": resource.result(), "ps": client.request("ps")})
        if warmup["status"] != "ok":
            raise RuntimeError("warmup failed")
        for index, (job, path) in enumerate(pending):
            current = next(item for item in client.request("tags")["models"] if item["name"] == model)
            if current["digest"] != identity["digest"]:
                raise ValueError("model changed during evaluation")
            with Resources() as resource:
                result = chat_with_retry(client, job["payload"])
            result.update({"identity": identity, "job": job, "resources": resource.result(),
                           "ps": client.request("ps")})
            result["checksum"] = digest(result)
            save_json(path, result)
            print(f"{model}: {index+1}/{len(pending)} {job['sample_id']} {job['condition']} {result['status']}", flush=True)
    finally:
        lock.unlink()
    return directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, choices=["translategemma:4b", "qwen3:4b", "qwen3:8b", "qwen3:4b-instruct-2507-q4_K_M"])
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--prompts", type=Path)
    parser.add_argument("--primary-only", action="store_true", help="Evaluate central frames only; keep auxiliary OCR data separately")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, default=11434)
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    base = Path(__file__).parent
    records = read_json(args.dataset) if args.dataset else initial_dataset(
        read_json(base.parent / "ocr_evaluation/manifest.json"), base.parent / "ocr_evaluation/results/details.csv")
    if len({r['id'] for r in records}) != len(records):
        raise ValueError("duplicate dataset ID")
    if args.primary_only:
        records = [row for row in records if row["primary"]]
    if not records:
        raise ValueError("empty dataset")
    print(run(LocalClient(args.port, args.timeout), args.model, records,
              read_json(args.prompts or base / "prompts.json"), read_json(base / "glossary.json"), args.output))


if __name__ == "__main__":
    main()
