import unittest

from bookmark_mcp import handle_request


class BookmarkMcpTest(unittest.TestCase):
    def test_initialize(self) -> None:
        result = handle_request("initialize", {})
        self.assertEqual(result["serverInfo"]["name"], "x-bookmarks")

    def test_tools_list(self) -> None:
        result = handle_request("tools/list", {})
        tool_names = {tool["name"] for tool in result["tools"]}
        self.assertIn("search_bookmarks", tool_names)
        self.assertIn("bookmark_context", tool_names)


if __name__ == "__main__":
    unittest.main()
