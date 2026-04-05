"""Phase 2: bookmark categorization via Claude Haiku or regex rules."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from bookmark_paths import resolve_base_dir
from text_repair import repair_value

if TYPE_CHECKING:
    import anthropic


BATCH_SIZE = 10
MODEL = "claude-haiku-4-5-20251001"
MAX_CONTENT_PER_BOOKMARK = 800
HEBREW_RE = re.compile(r"[\u0590-\u05FF]")
GITHUB_REPO_RE = re.compile(r"github\.com/([\w.-]+)/([\w.-]+)", re.IGNORECASE)
MENTION_RE = re.compile(r"@([A-Za-z0-9_]{2,})")
ENTITY_RE = re.compile(r"\b[A-Z][A-Za-z0-9+._-]{2,}\b")

CATEGORY_RULES: list[tuple[str, list[re.Pattern[str]]]] = [
    (
        "AI & Machine Learning",
        [
            re.compile(r"\b(ai|llm|llms|ml|machine learning|deep learning|transformer|rag|embedding|prompt)\b", re.IGNORECASE),
            re.compile(r"\b(openai|anthropic|claude|chatgpt|gpt-4|gpt-5|gemini|copilot|cursor|langchain|ollama)\b", re.IGNORECASE),
        ],
    ),
    (
        "DevTools",
        [
            re.compile(r"\b(observability|opentelemetry|otel|monitoring|tracing|logging|metrics)\b", re.IGNORECASE),
            re.compile(r"\b(kubernetes|docker|terraform|aws|gcp|cloudflare|linux|devops|ci/cd|infra|infrastructure)\b", re.IGNORECASE),
            re.compile(r"\b(postgres|redis|clickhouse|database|sql|sre|incident|debugging)\b", re.IGNORECASE),
        ],
    ),
    (
        "Software Engineering",
        [
            re.compile(r"\b(python|javascript|typescript|node\.js|nodejs|react|next\.js|go|golang|rust|java|swift)\b", re.IGNORECASE),
            re.compile(r"\b(api|sdk|framework|library|testing|refactor|compiler|programming|software engineering)\b", re.IGNORECASE),
        ],
    ),
    (
        "Security",
        [
            re.compile(r"\b(security|cve-\d{4}-\d+|vulnerability|exploit|zero-day|malware|phishing|auth|oauth|osint)\b", re.IGNORECASE),
            re.compile(r"\b(hack|hacker|cyber|breach|supply chain|backdoor|rce|xss|csrf)\b", re.IGNORECASE),
        ],
    ),
    (
        "Startups & Business",
        [
            re.compile(r"\b(startup|founder|saas|pricing|growth|sales|marketing|business|revenue|product market fit)\b", re.IGNORECASE),
            re.compile(r"\b(product management|launch|ship|customers|enterprise|go-to-market|distribution)\b", re.IGNORECASE),
        ],
    ),
    (
        "Personal Finance",
        [
            re.compile(r"\b(finance|investing|stocks?|market|trading|valuation|capital|money|economics)\b", re.IGNORECASE),
            re.compile(r"\b(bitcoin|ethereum|crypto|web3|defi)\b", re.IGNORECASE),
        ],
    ),
    (
        "News & Current Events",
        [
            re.compile(r"\b(news|politics|policy|election|war|military|geopolitics|government|senate|president)\b", re.IGNORECASE),
            re.compile(r"\b(israel|gaza|iran|ukraine|china|russia|media)\b", re.IGNORECASE),
        ],
    ),
    (
        "Hardware",
        [
            re.compile(r"\b(hardware|chip|gpu|cpu|raspberry pi|robotics|iot|electronics|firmware|device)\b", re.IGNORECASE),
            re.compile(r"\b(drone|sensor|embedded|homelab|maker)\b", re.IGNORECASE),
        ],
    ),
    (
        "Design",
        [
            re.compile(r"\b(design|ux|ui|typography|brand|visual design|figma)\b", re.IGNORECASE),
        ],
    ),
    (
        "Productivity",
        [
            re.compile(r"\b(productivity|workflow|automation|obsidian|note-taking|pkm|macos|alfred|raycast)\b", re.IGNORECASE),
        ],
    ),
    (
        "Personal Development",
        [
            re.compile(r"\b(career|learning|leadership|management|hiring|book|books|education|study)\b", re.IGNORECASE),
        ],
    ),
    (
        "Lifestyle",
        [
            re.compile(r"\b(health|fitness|wellness|travel|sleep|food|lifestyle|biology|climate|energy)\b", re.IGNORECASE),
        ],
    ),
    (
        "Humor & Memes",
        [
            re.compile(r"\b(meme|memes|joke|funny|lol|lmao|shitpost)\b", re.IGNORECASE),
        ],
    ),
]

STOP_ENTITIES = {
    "This",
    "That",
    "When",
    "Why",
    "What",
    "How",
    "With",
    "From",
    "Into",
    "Your",
    "Have",
    "Just",
    "New",
    "Thread",
    "Open",
}


def _get_cli_oauth_token() -> str | None:
    """Read Claude Code OAuth token from macOS Keychain."""
    try:
        raw = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        if not raw:
            return None
        decoded = bytes.fromhex(raw).decode("utf-8", errors="ignore")
        match = re.search(r'"accessToken":"(sk-ant-[^"]+)"', decoded)
        return match.group(1) if match else None
    except Exception:
        return None


def _make_client() -> Any:
    """Create Anthropic client: ANTHROPIC_API_KEY env → CLI OAuth token."""
    try:
        import anthropic
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "AI categorization requires the optional 'anthropic' dependency. "
            "Install with `uv sync --extra ai`, `uv add anthropic`, or use `categorize --regex`."
        ) from error
    if os.environ.get("ANTHROPIC_API_KEY"):
        return anthropic.Anthropic()
    token = _get_cli_oauth_token()
    if token:
        return anthropic.Anthropic(
            auth_token=token,
            default_headers={"anthropic-beta": "oauth-2025-04-20"},
        )
    raise RuntimeError("No API key. Set ANTHROPIC_API_KEY or log into Claude Code CLI.")


SYSTEM_PROMPT = """You categorize Twitter/X bookmarks. For each bookmark, return:
- categories: 1-3 topic categories (use consistent, broad names like "AI & Machine Learning", "Software Engineering", "Startups & Business", "DevTools", "Personal Finance", "Hebrew Content", "Humor & Memes", etc.)
- entities: key people, tools, companies, or concepts mentioned (max 5)
- summary: one-line summary (max 100 chars)
- language: "en", "he", or ISO code
- importance: integer 1-5 (5 = must-read insight/resource, 4 = very useful, 3 = solid content, 2 = mildly interesting, 1 = low-value/meme/noise)
- type: one of "article", "tool", "thread", "insight", "media", "humor"

Return valid JSON array matching the input order. No markdown fences."""


def build_bookmark_text(bookmark: dict) -> str:
    """Build input text for a single bookmark."""
    handle = bookmark["handle"] if bookmark["handle"].startswith("@") else f"@{bookmark['handle']}"
    parts = [f"{handle}: {bookmark['text'][:500]}"]
    link_pages = bookmark.get("linked_pages", [])
    if not link_pages and bookmark.get("extracted"):
        link_pages = [bookmark["extracted"]]
    for page in link_pages[:2]:
        title = str(page.get("title", "")).strip()
        description = str(page.get("description", "")).strip()
        preview = str(page.get("preview", "")).strip()
        content = str(page.get("content", "")).strip()
        url = str(page.get("url", "")).strip()
        if title:
            parts.append(f"Linked: {title}")
        if description and description.casefold() != title.casefold():
            parts.append(description[:300])
        if preview and preview.casefold() not in {title.casefold(), description.casefold()}:
            parts.append(preview[:MAX_CONTENT_PER_BOOKMARK])
        elif content:
            parts.append(content[:MAX_CONTENT_PER_BOOKMARK])
        if url:
            parts.append(f"Link URL: {url}")
    if bookmark.get("urls"):
        parts.append("URLs: " + ", ".join(bookmark["urls"][:3]))
    return "\n".join(parts)


def categorize_batch(client: Any, batch: list[dict]) -> list[dict]:
    """Send a batch of bookmarks to Claude for categorization."""
    items = [f"[{idx}] {build_bookmark_text(bookmark)}" for idx, bookmark in enumerate(batch)]
    user_msg = f"Categorize these {len(batch)} bookmarks:\n\n" + "\n\n---\n\n".join(items)

    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )

    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3]

    results = json.loads(text)
    if len(results) != len(batch):
        print(f"  WARNING: expected {len(batch)} results, got {len(results)}", file=sys.stderr)
    return results


def _combined_text(bookmark: dict) -> str:
    link_pages = bookmark.get("linked_pages", [])
    if not link_pages and bookmark.get("extracted"):
        link_pages = [bookmark["extracted"]]
    extracted_parts = []
    for page in link_pages:
        extracted_parts.extend(
            [
                str(page.get("title", "")),
                str(page.get("description", "")),
                str(page.get("preview", "")),
                str(page.get("content", ""))[:1200],
                str(page.get("site_name", "")),
                str(page.get("url", "")),
            ]
        )
    parts = [
        str(bookmark.get("text", "")),
        *[part for part in extracted_parts if part],
        " ".join(bookmark.get("hashtags", [])),
        " ".join(bookmark.get("urls", [])),
    ]
    return "\n".join(part for part in parts if part)


def _external_domains(bookmark: dict) -> list[str]:
    domains: list[str] = []
    seen: set[str] = set()
    for url in bookmark.get("urls", []):
        domain = urlparse(url).netloc.casefold().removeprefix("www.")
        if not domain or domain in {"x.com", "twitter.com", "t.co"} or domain in seen:
            continue
        seen.add(domain)
        domains.append(domain)
    return domains


def _dedupe(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def _detect_language(text: str) -> str:
    return "he" if HEBREW_RE.search(text) else "en"


def _extract_entities(bookmark: dict, combined_text: str) -> list[str]:
    entities: list[str] = []

    for owner, repo in GITHUB_REPO_RE.findall(" ".join(bookmark.get("urls", []))):
        entities.append(f"{owner}/{repo}")
    for mention in MENTION_RE.findall(combined_text):
        entities.append(f"@{mention}")
    for candidate in ENTITY_RE.findall(combined_text):
        if candidate in STOP_ENTITIES:
            continue
        entities.append(candidate)

    return _dedupe(entities)[:5]


def _infer_type(bookmark: dict, categories: list[str], domains: list[str], text: str) -> str:
    lowered = text.casefold()
    if bookmark.get("media"):
        return "media"
    if "Humor & Memes" in categories:
        return "humor"
    if "thread" in lowered or "🧵" in text or re.search(r"\b1/\d+\b", lowered):
        return "thread"
    if any(domain in {"github.com", "pypi.org", "npmjs.com", "crates.io"} for domain in domains):
        return "tool"
    if re.search(r"\b(cli|sdk|library|framework|repo|tool)\b", lowered):
        return "tool"
    if bookmark.get("urls"):
        return "article"
    return "insight"


def _infer_importance(bookmark: dict, categories: list[str], bookmark_type: str) -> int:
    if bookmark_type == "humor":
        return 1

    score = 2
    if bookmark.get("urls") or bookmark.get("extracted"):
        score += 1
    if {"AI & Machine Learning", "DevTools", "Software Engineering", "Security"} & set(categories):
        score += 1
    if bookmark_type in {"article", "tool", "thread"} or len(str(bookmark.get("text", ""))) > 180:
        score += 1
    return max(1, min(score, 5))


def _build_summary(bookmark: dict) -> str:
    extracted = bookmark.get("extracted", {})
    candidate = str(extracted.get("title", "")).strip() or str(bookmark.get("text", "")).strip()
    candidate = " ".join(candidate.split())
    if len(candidate) <= 100:
        return candidate
    return candidate[:99].rstrip() + "…"


def classify_bookmark_regex(bookmark: dict) -> dict:
    """Cheap deterministic fallback for categorization."""
    combined_text = _combined_text(bookmark)
    categories: list[str] = []
    for category, patterns in CATEGORY_RULES:
        if any(pattern.search(combined_text) for pattern in patterns):
            categories.append(category)

    language = _detect_language(combined_text)
    if language == "he":
        categories.append("Hebrew Content")

    domains = _external_domains(bookmark)
    if any(domain in {"github.com", "gitlab.com", "npmjs.com", "pypi.org", "crates.io"} for domain in domains):
        categories.append("Software Engineering")
    if any(domain in {"arxiv.org", "huggingface.co"} for domain in domains):
        categories.append("AI & Machine Learning")

    categories = _dedupe(categories)[:3]
    bookmark_type = _infer_type(bookmark, categories, domains, combined_text)

    if not categories and bookmark_type == "humor":
        categories = ["Humor & Memes"]
    if not categories:
        categories = ["Uncategorized"]

    return {
        "categories": categories,
        "entities": _extract_entities(bookmark, combined_text),
        "summary": _build_summary(bookmark),
        "language": language,
        "importance": _infer_importance(bookmark, categories, bookmark_type),
        "type": bookmark_type,
    }


def _load_input_data(input_file: Path | None = None) -> tuple[Path, dict, list[dict]]:
    if input_file is None:
        base_dir = resolve_base_dir()
        enriched_file = base_dir / "enriched.json"
        bookmarks_file = base_dir / "bookmarks.json"
        target = enriched_file if enriched_file.exists() else bookmarks_file
    else:
        target = input_file
    with target.open(encoding="utf-8") as handle:
        data = repair_value(json.load(handle))
    return target, data, data["bookmarks"]


def _load_existing_categorizations(output_file: Path, force: bool) -> dict[str, dict]:
    cat_map: dict[str, dict] = {}
    if output_file.exists() and not force:
        with output_file.open(encoding="utf-8") as handle:
            existing = repair_value(json.load(handle))
        for bookmark in existing["bookmarks"]:
            if bookmark.get("ai"):
                cat_map[bookmark["id"]] = bookmark["ai"]
        print(f"Resuming: {len(cat_map)} already categorized")
    return cat_map


def _save_categorized(data: dict, bookmarks: list[dict], cat_map: dict[str, dict], output_file: Path) -> None:
    categorized = []
    for bookmark in bookmarks:
        output_bookmark = {**bookmark}
        if bookmark["id"] in cat_map:
            output_bookmark["ai"] = cat_map[bookmark["id"]]
        categorized.append(output_bookmark)

    with output_file.open("w", encoding="utf-8") as handle:
        json.dump({**data, "bookmarks": categorized}, handle, indent=2, ensure_ascii=False)


def _print_category_summary(cat_map: dict[str, dict]) -> None:
    categories = Counter()
    for ai in cat_map.values():
        for category in ai.get("categories", []):
            categories[category] += 1

    print("\nTop categories:")
    for category, count in categories.most_common(15):
        print(f"  {category}: {count}")


def run_categorization(
    force: bool = False,
    use_regex: bool = False,
    input_file: Path | None = None,
    output_file: Path | None = None,
) -> None:
    target_output = output_file or (resolve_base_dir() / "categorized.json")
    input_path, data, bookmarks = _load_input_data(input_file=input_file)
    print(f"Reading from {input_path.name}")

    cat_map = {} if force else _load_existing_categorizations(target_output, force=False)
    if force:
        print("Force mode: re-categorizing all bookmarks")

    todo = [bookmark for bookmark in bookmarks if force or bookmark["id"] not in cat_map]
    mode_label = "regex" if use_regex else "LLM"
    print(f"Categorizing {len(todo)} bookmarks with {mode_label}...")

    if use_regex:
        for idx, bookmark in enumerate(todo, start=1):
            cat_map[bookmark["id"]] = classify_bookmark_regex(bookmark)
            if idx % 250 == 0:
                print(f"  {idx}/{len(todo)}")
                _save_categorized(data, bookmarks, cat_map, target_output)
        _save_categorized(data, bookmarks, cat_map, target_output)
        print(f"\nDone: {len(cat_map)}/{len(bookmarks)} categorized")
        _print_category_summary(cat_map)
        return

    client = _make_client()

    for start in range(0, len(todo), BATCH_SIZE):
        batch = todo[start : start + BATCH_SIZE]
        batch_num = start // BATCH_SIZE + 1
        total_batches = (len(todo) + BATCH_SIZE - 1) // BATCH_SIZE
        print(f"  Batch {batch_num}/{total_batches}...", end=" ", flush=True)

        try:
            results = categorize_batch(client, batch)
            for bookmark, result in zip(batch, results):
                cat_map[bookmark["id"]] = {
                    "categories": result.get("categories", []),
                    "entities": result.get("entities", []),
                    "summary": result.get("summary", ""),
                    "language": result.get("language", "en"),
                    "importance": result.get("importance", 3),
                    "type": result.get("type", "insight"),
                }
            print(f"OK ({len(results)} categorized)")
        except Exception as exc:
            print(f"FAIL: {exc}", file=sys.stderr)
            time.sleep(2)

        if batch_num % 5 == 0:
            _save_categorized(data, bookmarks, cat_map, target_output)
        time.sleep(0.3)

    _save_categorized(data, bookmarks, cat_map, target_output)
    print(f"\nDone: {len(cat_map)}/{len(bookmarks)} categorized")
    _print_category_summary(cat_map)
