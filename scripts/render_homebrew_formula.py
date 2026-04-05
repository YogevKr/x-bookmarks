#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
from pathlib import Path


FORMULA_TEMPLATE = """class XBookmarks < Formula
  include Language::Python::Virtualenv

  desc "Shell-first X bookmarks archive with local search and sync"
  homepage "https://github.com/YogevKr/x-bookmarks"
  url "{asset_url}"
  sha256 "{sha256}"
  license "MIT"

  depends_on "python@3.14"

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match version.to_s, shell_output("#{{bin}}/x-bookmarks version")
  end
end
"""


def render_formula(*, asset_url: str, sha256: str) -> str:
    return FORMULA_TEMPLATE.format(asset_url=asset_url, sha256=sha256)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render the x-bookmarks Homebrew formula")
    parser.add_argument("--asset-url", required=True, help="Release asset URL for the sdist tarball")
    parser.add_argument("--sha256", required=True, help="SHA256 checksum for the release asset")
    parser.add_argument("--output", type=Path, help="Optional path to write the formula file")
    args = parser.parse_args()

    formula = render_formula(asset_url=args.asset_url, sha256=args.sha256)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(formula, encoding="utf-8")
        return
    sys.stdout.write(formula)


if __name__ == "__main__":
    main()
