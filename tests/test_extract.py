import subprocess
import unittest
from unittest import mock

from extract import extract_url


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
    def test_extract_url_accepts_metadata_only_from_summarize(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["summarize"],
            returncode=0,
            stdout='{"extracted":{"title":"Playlist title","description":"Playlist summary","content":"","wordCount":0,"siteName":"youtube"}}',
            stderr="",
        )
        with mock.patch("extract.subprocess.run", return_value=completed):
            with mock.patch("extract.urlopen", side_effect=AssertionError("fallback not expected")):
                result = extract_url("https://youtube.com/playlist?list=abc")
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


if __name__ == "__main__":
    unittest.main()
