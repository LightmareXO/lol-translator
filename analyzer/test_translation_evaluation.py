import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError

from translation_evaluation.evaluate import (
    cached_result, chat_with_retry, digest, initial_dataset, jobs_for,
    make_payload, read_json, save_json,
)
from translation_evaluation.collect import export, summarise

BASE = Path(__file__).parent / "translation_evaluation"


class TranslationTests(unittest.TestCase):
    def setUp(self):
        self.prompts = read_json(BASE / "prompts.json")
        self.glossary = read_json(BASE / "glossary.json")

    def test_condition_pairing_preserves_input_and_does_not_leak_truth(self):
        rows = [{"id": "x", "group_id": "g", "split": "dev", "primary": True,
                 "source_ko": "SECRET_TRUTH", "ocr_ko": " raw  typo\n"}]
        jobs = list(jobs_for(rows, "qwen3:4b", self.prompts, self.glossary))
        self.assertEqual([j["condition"] for j in jobs], list("ABCD"))
        for j in jobs:
            content = j["payload"]["messages"][0]["content"]
            if j["condition"] in "BD":
                self.assertNotIn("SECRET_TRUTH", content)
                self.assertTrue(content.endswith(" raw  typo\n"))
            self.assertFalse(j["payload"]["think"])
            self.assertEqual(len(j["payload"]["messages"]), 1)
        self.assertEqual(jobs[0]["payload"]["messages"][0]["content"].replace("SECRET_TRUTH", " raw  typo\n"),
                         jobs[1]["payload"]["messages"][0]["content"])
        self.assertEqual(jobs[2]["payload"]["messages"][0]["content"].replace("SECRET_TRUTH", " raw  typo\n"),
                         jobs[3]["payload"]["messages"][0]["content"])

    def test_initial_dataset_counts_groups_not_frames(self):
        rows = initial_dataset(read_json(BASE.parent / "ocr_evaluation/manifest.json"),
                               BASE.parent / "ocr_evaluation/results/details.csv")
        self.assertEqual(len(rows), 24)
        self.assertEqual(sum(r["primary"] for r in rows), 8)
        self.assertEqual(len({r["group_id"] for r in rows}), 8)
        self.assertEqual(len(list(jobs_for(rows, "qwen3:4b", self.prompts, self.glossary))), 64)

    def test_translate_gemma_has_no_think_and_preserves_two_blank_lines(self):
        payload = make_payload("translategemma:4b", "한글", False, self.prompts, self.glossary)
        self.assertNotIn("think", payload)
        self.assertTrue(payload["messages"][0]["content"].endswith(":\n\n\n한글"))

    def test_timeout_is_bounded_and_recorded(self):
        client = Mock()
        client.request.side_effect = TimeoutError()
        sleep = Mock()
        result = chat_with_retry(client, {}, attempts=2, sleep=sleep)
        self.assertEqual(result["status"], "error")
        self.assertEqual(client.request.call_count, 2)
        self.assertEqual(len(result["errors"]), 2)

    def test_success_after_transient_failure_keeps_error_history(self):
        client = Mock()
        client.request.side_effect = [TimeoutError(), {"done": True, "message": {"content": "訳"}}]
        result = chat_with_retry(client, {}, sleep=Mock())
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["errors"]), 1)

    def test_bad_request_not_retried(self):
        client = Mock()
        client.request.side_effect = HTTPError("localhost", 400, "bad", None, None)
        self.assertEqual(chat_with_retry(client, {}, sleep=Mock())["status"], "error")
        self.assertEqual(client.request.call_count, 1)

    def test_partial_or_empty_response_is_not_success(self):
        for response in ({"done": False, "message": {"content": "partial"}},
                         {"done": True, "message": {"content": ""}}):
            client = Mock()
            client.request.return_value = response
            self.assertEqual(chat_with_retry(client, {}, sleep=Mock())["status"], "error")

    def test_output_limit_keeps_response_without_pointless_retry(self):
        client = Mock()
        client.request.return_value = {"done": True, "done_reason": "length", "eval_count": 2048,
                                       "message": {"content": "", "thinking": "unfinished"}}
        result = chat_with_retry(client, {}, sleep=Mock())
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["response"]["message"]["thinking"], "unfinished")
        self.assertEqual(client.request.call_count, 1)

    def test_resume_rejects_changed_model_prompt_or_corrupt_result(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "result.json"
            job = {"payload": {"text": "original"}}
            identity = {"digest": "model1", "prompt": "v1"}
            result = {"job": job, "identity": identity, "status": "ok"}
            result["checksum"] = digest(result)
            save_json(path, result)
            self.assertIsNotNone(cached_result(path, job, identity))
            for changed in ({"digest": "model2", "prompt": "v1"},
                            {"digest": "model1", "prompt": "v2"}):
                with self.assertRaisesRegex(ValueError, "identity"):
                    cached_result(path, job, changed)
            result["status"] = "error"
            save_json(path, result)
            with self.assertRaisesRegex(ValueError, "checksum"):
                cached_result(path, job, identity)


class CollectionTests(unittest.TestCase):
    def row(self, identifier="r1", sample="g_speech_f02"):
        return {"result_id": identifier, "sample_id": sample, "group_id": "g", "split": "dev",
                "experiment": "exp", "condition": "A", "primary": True, "model": "model",
                "status": "ok", "output": "訳", "wall_ms": 10, "response": {}, "thinking": ""}

    def review(self):
        return {"blind_id": "b1", "sample_id": "g_speech_f02", "output": "訳",
                "review_status": "ai_provisional", "severity": "major", "error_types": ["terminology"],
                "rationale": "wrong item", "reviewer": "independent AI", "evidence": [], "semantic_signature": "wrong item"}

    def test_provisional_is_never_counted_as_human_confirmation(self):
        result = summarise([self.row()], [self.review()], {"b1": "r1"})["conditions"][0]
        self.assertEqual(result["human_confirmed"]["determined_units"], 0)
        self.assertEqual(result["ai_provisional"]["major_fully_determined_groups"], 1)
        self.assertEqual(result["ai_provisional"]["error_types"]["terminology"], 1)

    def test_partially_reviewed_two_line_group_not_in_confirmed_denominator(self):
        result = summarise([self.row(), self.row("r2", "g_annotation_f02")], [self.review()], {"b1": "r1"})["conditions"][0]
        self.assertEqual(result["ai_provisional"]["determined_units"], 1)
        self.assertEqual(result["ai_provisional"]["fully_determined_groups"], 0)
        self.assertEqual(result["unreviewed_units"], 1)

    def test_mismatched_duplicate_or_unknown_review_is_rejected(self):
        for scoring, mapping in (([{**self.review(), "output": "changed"}], {"b1": "r1"}),
                                 ([self.review(), self.review()], {"b1": "r1"}), ([self.review()], {})):
            with self.assertRaises(ValueError):
                summarise([self.row()], scoring, mapping)

    def test_human_claim_requires_evidence(self):
        with self.assertRaisesRegex(ValueError, "evidence"):
            summarise([self.row()], [{**self.review(), "review_status": "human_confirmed"}], {"b1": "r1"})

    def test_export_addition_keeps_blind_ids_and_existing_reviews(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for count in (1, 2):
                record = {"identity": {"model": "m"}, "job": {"sample_id": str(count), "primary": True},
                          "response": {"message": {"content": "訳"}}, "status": "ok", "wall_ms": 1,
                          "errors": [], "resources": {}, "ps": {}}
                record["checksum"] = digest(record)
                save_json(root / "runs" / "exp" / f"{count}.json", record)
                export(root / "runs", root / "review")
                scoring = read_json(root / "review/scoring.json")
                if count == 1:
                    first = scoring[0]["blind_id"]
                    scoring[0]["rationale"] = "preserve my draft"
                    save_json(root / "review/scoring.json", scoring)
            preserved = next(r for r in scoring if r["blind_id"] == first)
            self.assertEqual(preserved["rationale"], "preserve my draft")
            self.assertEqual(len(scoring), 2)


if __name__ == "__main__":
    unittest.main()
