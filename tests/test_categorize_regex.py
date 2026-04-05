import json
import tempfile
import unittest
from pathlib import Path

from categorize import classify_bookmark_regex, run_categorization


class RegexCategorizationTest(unittest.TestCase):
    def test_classify_bookmark_regex_detects_ai_tooling(self) -> None:
        bookmark = {
            "id": "1",
            "handle": "@builder",
            "author": "Builder",
            "text": "New Claude Code CLI for AI agents on GitHub",
            "timestamp": "Thu Jan 01 12:00:00 +0000 2026",
            "urls": ["https://github.com/acme/agent-memory"],
            "hashtags": ["ai"],
            "media": [],
        }
        ai = classify_bookmark_regex(bookmark)
        self.assertIn("AI & Machine Learning", ai["categories"])
        self.assertEqual(ai["type"], "tool")
        self.assertEqual(ai["language"], "en")

    def test_classify_bookmark_regex_detects_hebrew_security(self) -> None:
        bookmark = {
            "id": "2",
            "handle": "@sec",
            "author": "Sec",
            "text": "מדריך לאבטחת Kubernetes ולזיהוי CVE-2026-1234",
            "timestamp": "Thu Jan 01 12:00:00 +0000 2026",
            "urls": ["https://example.com/k8s-security"],
            "hashtags": [],
            "media": [],
        }
        ai = classify_bookmark_regex(bookmark)
        self.assertIn("Security", ai["categories"])
        self.assertIn("Hebrew Content", ai["categories"])
        self.assertEqual(ai["language"], "he")

    def test_run_categorization_regex_writes_categorized_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_dir = Path(tmpdir)
            input_file = temp_dir / "bookmarks.json"
            output_file = temp_dir / "categorized.json"
            input_file.write_text(
                json.dumps(
                    {
                        "bookmarks": [
                            {
                                "id": "1",
                                "handle": "@builder",
                                "author": "Builder",
                                "text": "OpenTelemetry observability guide for Kubernetes",
                                "timestamp": "Thu Jan 01 12:00:00 +0000 2026",
                                "urls": ["https://opentelemetry.io/docs"],
                                "hashtags": [],
                                "media": [],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            run_categorization(use_regex=True, input_file=input_file, output_file=output_file)

            data = json.loads(output_file.read_text(encoding="utf-8"))
            self.assertEqual(len(data["bookmarks"]), 1)
            self.assertIn("ai", data["bookmarks"][0])
            self.assertIn("DevTools", data["bookmarks"][0]["ai"]["categories"])


if __name__ == "__main__":
    unittest.main()
