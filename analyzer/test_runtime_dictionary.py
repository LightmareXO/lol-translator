from pathlib import Path
import unittest
from unittest.mock import patch

from analyzer import runtime_cli


class FakeLocalClient:
    last_chat_payload = None

    def __init__(self, timeout=60):
        self.timeout = timeout

    def request(self, endpoint, payload=None):
        if endpoint == "tags":
            return {
                "models": [
                    {
                        "name": runtime_cli.TRANSLATION_MODEL,
                        "digest": "test-digest",
                        "size": 123,
                        "details": {"family": "qwen3"},
                    }
                ]
            }
        if endpoint == "show":
            return {"model_info": {"general.finetune": "Instruct"}}
        if endpoint == "version":
            return {"version": "test"}
        if endpoint == "chat":
            type(self).last_chat_payload = payload
            return {
                "done": True,
                "message": {"content": "ボリベアがアルティメットを使う"},
            }
        raise AssertionError(f"unexpected endpoint: {endpoint}")


class RuntimeDictionaryTests(unittest.TestCase):
    def setUp(self):
        FakeLocalClient.last_chat_payload = None
        self.root = Path(runtime_cli.__file__).resolve().parent

    def test_translator_sends_only_relevant_terms_and_returns_trace(self):
        with patch.object(runtime_cli, "LocalClient", FakeLocalClient):
            translator = runtime_cli.Translator(self.root)
            translated, trace = translator.translate_with_trace("볼베 궁")

        self.assertEqual(translated, "ボリベアがアルティメットを使う")
        targets = {
            target_id
            for match in trace["matches"]
            for target_id in match["target_ids"]
        }
        self.assertIn("champion:Volibear", targets)
        self.assertIn("game:ultimate", targets)
        self.assertEqual(
            trace["dictionary_sha256"],
            translator.configuration()["dictionary"]["sha256"],
        )

        content = FakeLocalClient.last_chat_payload["messages"][0]["content"]
        self.assertIn("볼베", content)
        self.assertIn("궁", content)
        self.assertNotIn("플래시 = フラッシュ", content)
        self.assertNotIn("덤불 조끼", content)

    def test_translate_preserves_string_return_contract(self):
        with patch.object(runtime_cli, "LocalClient", FakeLocalClient):
            translator = runtime_cli.Translator(self.root)
            translated = translator.translate("일반 문장")

        self.assertEqual(translated, "ボリベアがアルティメットを使う")
        content = FakeLocalClient.last_chat_payload["messages"][0]["content"]
        self.assertNotIn("Terminology reference", content)


if __name__ == "__main__":
    unittest.main()
