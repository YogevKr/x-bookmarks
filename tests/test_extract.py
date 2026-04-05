import json
from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
import unittest
from unittest import mock

from extract import _normalize_target_url, extract_url, list_extract_failures, retry_extract_failures, run_extraction


class _FakeResponse:
    def __init__(self, body: bytes, content_type: str, url: str) -> None:
        self._body = body
        self._url = url
        self.headers = {"Content-Type": content_type}

    def read(self, _limit: int | None = None) -> bytes:
        return self._body

    def geturl(self) -> str:
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class ExtractTest(unittest.TestCase):
    def test_normalize_target_url_rewrites_youtube_live(self) -> None:
        normalized = _normalize_target_url("https://www.youtube.com/live/z4zXicOAF28?si=abc&t=5106")
        self.assertEqual(normalized, "https://www.youtube.com/watch?v=z4zXicOAF28")

    def test_extract_url_accepts_metadata_only_from_summarize(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["summarize"],
            returncode=0,
            stdout='{"extracted":{"title":"Playlist title","description":"Playlist summary","content":"","wordCount":0,"siteName":"youtube"}}',
            stderr="",
        )
        with mock.patch("extract.subprocess.run", return_value=completed):
            with mock.patch("extract.urlopen", side_effect=AssertionError("fallback not expected")):
                result = extract_url("https://example.com/playlist?list=abc")
        self.assertIsNotNone(result)
        self.assertEqual(result["title"], "Playlist title")
        self.assertEqual(result["description"], "Playlist summary")

    def test_extract_url_falls_back_to_html_metadata(self) -> None:
        html = b"""
        <html>
          <head>
            <title>Browser Use Skills</title>
            <meta name=\"description\" content=\"Metadata fallback works\" />
            <meta property=\"og:site_name\" content=\"Product Hunt\" />
          </head>
          <body><main>Useful launch page with enough text to build preview content.</main></body>
        </html>
        """
        completed = subprocess.CompletedProcess(args=["summarize"], returncode=1, stdout="", stderr="bad")
        with mock.patch("extract.subprocess.run", return_value=completed):
            with mock.patch(
                "extract.urlopen",
                return_value=_FakeResponse(html, "text/html; charset=utf-8", "https://www.producthunt.com/products/browser-use/launches/browser-use-skills"),
            ):
                result = extract_url("https://www.producthunt.com/products/browser-use/launches/browser-use-skills")
        self.assertIsNotNone(result)
        self.assertEqual(result["title"], "Browser Use Skills")
        self.assertEqual(result["site_name"], "Product Hunt")
        self.assertIn("Metadata fallback works", result["description"])
        self.assertIn("Useful launch page", result["content"])

    def test_extract_url_falls_back_to_plain_text(self) -> None:
        completed = subprocess.CompletedProcess(args=["summarize"], returncode=1, stdout="", stderr="bad")
        text = b"llms.txt fallback content with enough text to keep as extracted context"
        with mock.patch("extract.subprocess.run", return_value=completed):
            with mock.patch(
                "extract.urlopen",
                return_value=_FakeResponse(text, "text/plain; charset=utf-8", "https://docs.anthropic.com/llms.txt"),
            ):
                result = extract_url("https://docs.anthropic.com/llms.txt")
        self.assertIsNotNone(result)
        self.assertEqual(result["title"], "llms.txt")
        self.assertIn("fallback content", result["content"])

    def test_run_extraction_persists_terminal_failure(self) -> None:
        payload = {
            "exportDate": "2026-04-05",
            "source": "bookmark",
            "bookmarks": [
                {
                    "id": "1",
                    "author": "A",
                    "handle": "@a",
                    "timestamp": "2026-04-05T00:00:00Z",
                    "text": "Post",
                    "urls": ["http://api.openai.com"],
                    "media": [],
                    "hashtags": [],
                }
            ],
        }
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "bookmarks.json").write_text(json.dumps(payload), encoding="utf-8")
            with mock.patch("extract.resolve_base_dir", return_value=base):
                with mock.patch(
                    "extract._extract_url_with_status",
                    return_value=(None, {"reason": "unsupported_api_endpoint", "terminal": True, "attempts": 1, "failed_at": "2026-04-05T00:00:00+00:00"}),
                ):
                    with mock.patch("extract.time.sleep", return_value=None):
                        run_extraction()
            enriched = json.loads((base / "enriched.json").read_text(encoding="utf-8"))
            bookmark = enriched["bookmarks"][0]
            self.assertIn("extract_failures", bookmark)
            self.assertTrue(bookmark["extract_failures"]["http://api.openai.com"]["terminal"])

    def test_run_extraction_skips_cached_terminal_failures(self) -> None:
        payload = {
            "exportDate": "2026-04-05",
            "source": "bookmark",
            "bookmarks": [
                {
                    "id": "1",
                    "author": "A",
                    "handle": "@a",
                    "timestamp": "2026-04-05T00:00:00Z",
                    "text": "Post",
                    "urls": ["http://api.openai.com"],
                    "media": [],
                    "hashtags": [],
                }
            ],
        }
        enriched_payload = {
            **payload,
            "bookmarks": [
                {
                    **payload["bookmarks"][0],
                    "extract_failures": {
                        "http://api.openai.com": {
                            "reason": "unsupported_api_endpoint",
                            "terminal": True,
                            "attempts": 2,
                            "failed_at": "2026-04-05T00:00:00+00:00",
                        }
                    },
                }
            ],
        }
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "bookmarks.json").write_text(json.dumps(payload), encoding="utf-8")
            (base / "enriched.json").write_text(json.dumps(enriched_payload), encoding="utf-8")
            with mock.patch("extract.resolve_base_dir", return_value=base):
                with mock.patch("extract._extract_url_with_status", side_effect=AssertionError("cached terminal failure should not retry")):
                    with mock.patch("extract.time.sleep", return_value=None):
                        run_extraction()
            enriched = json.loads((base / "enriched.json").read_text(encoding="utf-8"))
            bookmark = enriched["bookmarks"][0]
            self.assertEqual(bookmark["extract_failures"]["http://api.openai.com"]["attempts"], 2)

    def test_list_extract_failures_filters_by_domain(self) -> None:
        payload = {
            "bookmarks": [
                {
                    "id": "1",
                    "author": "A",
                    "handle": "@a",
                    "extract_failures": {
                        "https://github.com/acme/repo": {
                            "reason": "timeout",
                            "terminal": False,
                            "attempts": 1,
                            "failed_at": "2026-04-05T00:00:00+00:00",
                        }
                    },
                }
            ]
        }
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "enriched.json").write_text(json.dumps(payload), encoding="utf-8")
            with mock.patch("extract.resolve_base_dir", return_value=base):
                rows = list_extract_failures(domain="github.com")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["bookmark_id"], "1")

    def test_retry_extract_failures_retries_terminal_when_requested(self) -> None:
        payload = {
            "exportDate": "2026-04-05",
            "source": "bookmark",
            "bookmarks": [
                {
                    "id": "1",
                    "author": "A",
                    "handle": "@a",
                    "timestamp": "2026-04-05T00:00:00Z",
                    "text": "Post",
                    "urls": ["http://api.openai.com"],
                    "media": [],
                    "hashtags": [],
                    "extract_failures": {
                        "http://api.openai.com": {
                            "reason": "unsupported_api_endpoint",
                            "terminal": True,
                            "attempts": 2,
                            "failed_at": "2026-04-05T00:00:00+00:00",
                        }
                    },
                }
            ],
        }
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "bookmarks.json").write_text(json.dumps(payload), encoding="utf-8")
            (base / "enriched.json").write_text(json.dumps(payload), encoding="utf-8")
            with mock.patch("extract.resolve_base_dir", return_value=base):
                with mock.patch(
                    "extract._extract_url_with_status",
                    return_value=(
                        {
                            "url": "http://api.openai.com",
                            "title": "Recovered",
                            "description": "",
                            "content": "Recovered content",
                            "preview": "Recovered content",
                            "word_count": 2,
                            "site_name": "OpenAI",
                        },
                        None,
                    ),
                ):
                    with mock.patch("extract.time.sleep", return_value=None):
                        result = retry_extract_failures(include_terminal=True)
            self.assertEqual(result["matched_urls"], 1)
            enriched = json.loads((base / "enriched.json").read_text(encoding="utf-8"))
            bookmark = enriched["bookmarks"][0]
            self.assertNotIn("extract_failures", bookmark)
            self.assertEqual(bookmark["extracted"]["title"], "Recovered")


if __name__ == "__main__":
    unittest.main()
