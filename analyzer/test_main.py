import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from main import read_request


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.video = self.root / "韓国語 영상 [test].mp4"
        self.video.touch()
        self.path = self.root / "request.json"
        self.request = {
            "video_path": str(self.video),
            "subtitle_region": {"x": 0.1, "y": 0.7, "width": 0.8, "height": 0.2},
        }

    def write(self):
        self.path.write_text(json.dumps(self.request, ensure_ascii=False), encoding="utf-8")

    def test_valid_unicode_path(self):
        self.write()
        self.assertEqual(read_request(self.path), self.request)

    def test_invalid_regions(self):
        for field, value in [("x", -0.1), ("y", 1.1), ("width", 0), ("height", -1),
                             ("x", True), ("x", "0"), ("x", float("nan")),
                             ("x", float("inf")), ("width", 1), ("height", 0.5)]:
            with self.subTest(field=field, value=value):
                original = self.request["subtitle_region"][field]
                self.request["subtitle_region"][field] = value
                self.write()
                with self.assertRaises(ValueError):
                    read_request(self.path)
                self.request["subtitle_region"][field] = original

    def test_invalid_paths(self):
        for path in ["", "relative.mp4", str(self.root), str(self.root / "missing.mp4"), None]:
            with self.subTest(path=path):
                self.request["video_path"] = path
                self.write()
                with self.assertRaises(ValueError):
                    read_request(self.path)

    def test_invalid_schema_and_json(self):
        for raw in ["{", "[]", "{}", '{"video_path": "x"}',
                    json.dumps({**self.request, "subtitle_region": []}),
                    json.dumps({**self.request, "subtitle_region": {"x": 0}})]:
            with self.subTest(raw=raw):
                self.path.write_text(raw, encoding="utf-8")
                with self.assertRaises(ValueError):
                    read_request(self.path)

    def test_missing_request(self):
        with self.assertRaises(OSError):
            read_request(self.path)

    def test_cli_success_and_failure(self):
        self.write()
        command = [sys.executable, str(Path(__file__).with_name("main.py")), str(self.path)]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), self.request)
        self.path.write_text("{", encoding="utf-8")
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Invalid analysis request", result.stderr)


if __name__ == "__main__":
    unittest.main()
