"""Standalone browser exporter for X bookmarks."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from bookmark_query import IndexPaths, default_paths
from bookmark_sync import sync_bookmarks

DEFAULT_DEBUG_PORT = 9223
DEFAULT_TIMEOUT_SECONDS = 15 * 60
DEFAULT_STAGNANT_ROUNDS = 8
DEFAULT_CHROME_PATH = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


NODE_EXPORTER = r'''
import { spawn } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";

const options = JSON.parse(process.env.X_BOOKMARKS_EXPORT_OPTIONS || "{}");
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const port = Number(options.debugPort || 9223);
const baseUrl = `http://127.0.0.1:${port}`;
const startedAt = Date.now();
const timeoutMs = Number(options.timeoutMs || 900000);
const stagnantRounds = Number(options.stagnantRounds || 8);
const quiet = Boolean(options.quiet);

function log(message) {
  if (!quiet) console.error(message);
}

async function cdpJson(path, init = {}) {
  const response = await fetch(`${baseUrl}${path}`, init);
  if (!response.ok) {
    throw new Error(`CDP HTTP ${response.status} for ${path}`);
  }
  return await response.json();
}

async function waitForCdp() {
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    try {
      return await cdpJson("/json/version");
    } catch (_error) {
      await sleep(500);
    }
  }
  throw new Error(`Chrome CDP did not start on ${baseUrl}`);
}

async function ensureChrome() {
  try {
    await cdpJson("/json/version");
    return false;
  } catch (_error) {
    const chromePath = options.chromePath || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
    const userDataDir = options.userDataDir;
    if (!userDataDir) throw new Error("userDataDir is required");
    await mkdir(userDataDir, { recursive: true });
    const args = [
      `--remote-debugging-port=${port}`,
      `--user-data-dir=${userDataDir}`,
      "--no-first-run",
      "--no-default-browser-check",
      "--disable-background-networking",
      "about:blank",
    ];
    if (options.headless) args.unshift("--headless=new");
    const child = spawn(chromePath, args, { detached: true, stdio: "ignore" });
    child.unref();
    await waitForCdp();
    return true;
  }
}

async function newTarget() {
  try {
    return await cdpJson(`/json/new?${encodeURIComponent("about:blank")}`, { method: "PUT" });
  } catch (_error) {
    return await cdpJson(`/json/new?${encodeURIComponent("about:blank")}`);
  }
}

class CdpClient {
  constructor(wsUrl) {
    this.nextId = 1;
    this.pending = new Map();
    this.events = new Map();
    this.ws = new WebSocket(wsUrl);
  }

  async open() {
    await new Promise((resolve, reject) => {
      this.ws.addEventListener("open", resolve, { once: true });
      this.ws.addEventListener("error", reject, { once: true });
    });
    this.ws.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      if (message.id && this.pending.has(message.id)) {
        const { resolve, reject } = this.pending.get(message.id);
        this.pending.delete(message.id);
        if (message.error) reject(new Error(message.error.message || JSON.stringify(message.error)));
        else resolve(message.result || {});
        return;
      }
      if (message.method && this.events.has(message.method)) {
        for (const handler of this.events.get(message.method)) handler(message.params || {});
      }
    });
  }

  send(method, params = {}) {
    const id = this.nextId++;
    const payload = JSON.stringify({ id, method, params });
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(payload);
    });
  }

  once(method) {
    return new Promise((resolve) => {
      const handler = (params) => {
        const handlers = this.events.get(method) || [];
        this.events.set(method, handlers.filter((candidate) => candidate !== handler));
        resolve(params);
      };
      const handlers = this.events.get(method) || [];
      handlers.push(handler);
      this.events.set(method, handlers);
    });
  }

  async eval(expression) {
    const result = await this.send("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
      userGesture: true,
    });
    if (result.exceptionDetails) {
      throw new Error(result.exceptionDetails.text || "Runtime.evaluate failed");
    }
    return result.result?.value;
  }

  close() {
    this.ws.close();
  }
}

const injector = String.raw`
(() => {
  window.__xBookmarks = [];
  window.__xSeen = new Set();

  function addTweet(t) {
    if (!t || !t.rest_id || window.__xSeen.has(t.rest_id)) return;
    window.__xSeen.add(t.rest_id);
    const leg = t.legacy || {};
    const userResult = (((t.core || {}).user_results || {}).result || {});
    const userCore = userResult.core || {};
    const userLegacy = userResult.legacy || {};
    const rawMedia = (leg.extended_entities && leg.extended_entities.media) || (leg.entities && leg.entities.media) || [];
    const media = rawMedia.map((m) => {
      const thumb = m.media_url_https || "";
      if (m.type === "video" || m.type === "animated_gif") {
        const variants = (m.video_info && m.video_info.variants) || [];
        const mp4s = variants
          .filter((v) => v.content_type === "video/mp4" && v.url)
          .sort((a, b) => (b.bitrate || 0) - (a.bitrate || 0));
        if (mp4s.length) return { type: m.type === "animated_gif" ? "gif" : "video", url: mp4s[0].url };
        return thumb ? { type: "photo", url: thumb } : null;
      }
      return thumb ? { type: "photo", url: thumb } : null;
    }).filter(Boolean);

    window.__xBookmarks.push({
      id: t.rest_id,
      author: userCore.name || userLegacy.name || "Unknown",
      handle: "@" + (userCore.screen_name || userLegacy.screen_name || "unknown"),
      timestamp: leg.created_at || "",
      text: leg.full_text || leg.text || "",
      media,
      hashtags: ((leg.entities && leg.entities.hashtags) || []).map((h) => h.text),
      urls: ((leg.entities && leg.entities.urls) || []).map((u) => u.expanded_url).filter(Boolean),
    });
  }

  function processEntry(e) {
    if (!e) return;
    const ic = e.content && (e.content.itemContent || (e.content.item && e.content.item.itemContent));
    if (ic && ic.tweet_results) {
      let t = ic.tweet_results.result;
      if (t) {
        if (t.__typename === "TweetWithVisibilityResults" || t.__typename === "TweetWithVisibilityResult") t = t.tweet || t;
        addTweet(t);
      }
    }
    if (e.content && e.content.items) e.content.items.forEach((i) => processEntry({ content: i.item || i }));
  }

  function findInstructions(obj, depth = 0) {
    if (!obj || typeof obj !== "object" || depth > 8) return null;
    if (Array.isArray(obj)) return null;
    if (Array.isArray(obj.instructions)) return obj.instructions;
    for (const key of Object.keys(obj)) {
      const result = findInstructions(obj[key], depth + 1);
      if (result) return result;
    }
    return null;
  }

  function processData(data) {
    const instructions = findInstructions(data) || [];
    instructions.forEach((instruction) => {
      (instruction.entries || []).forEach(processEntry);
      (instruction.moduleItems || []).forEach(processEntry);
    });
  }

  const originalFetch = window.fetch;
  window.fetch = async function(...args) {
    const response = await originalFetch.apply(this, args);
    try {
      const url = args[0] instanceof Request ? args[0].url : String(args[0]);
      if (url.includes("/graphql/")) processData(await response.clone().json());
    } catch (_error) {}
    return response;
  };

  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSend = XMLHttpRequest.prototype.send;
  const xhrUrls = new WeakMap();
  XMLHttpRequest.prototype.open = function(...args) {
    xhrUrls.set(this, String(args[1] || ""));
    return originalOpen.apply(this, args);
  };
  XMLHttpRequest.prototype.send = function(...args) {
    const xhr = this;
    const url = xhrUrls.get(xhr) || "";
    if (url.includes("/graphql/")) {
      xhr.addEventListener("load", () => {
        try { processData(JSON.parse(xhr.responseText)); } catch (_error) {}
      });
    }
    return originalSend.apply(this, args);
  };
})();
`;

function ensureTime() {
  if (Date.now() - startedAt > timeoutMs) {
    throw new Error(`Timed out after ${timeoutMs}ms`);
  }
}

const startedChrome = await ensureChrome();
const target = await newTarget();
const client = new CdpClient(target.webSocketDebuggerUrl);
await client.open();
try {
  await client.send("Page.enable");
  await client.send("Runtime.enable");
  const load = client.once("Page.loadEventFired");
  await client.send("Page.navigate", { url: "https://x.com/i/bookmarks" });
  await Promise.race([load, sleep(15000)]);
  await sleep(2000);
  const currentUrl = await client.eval("location.href");
  if (String(currentUrl).includes("/login") || String(currentUrl).includes("/i/flow/login")) {
    throw new Error("NOT_LOGGED_IN: log into X in the configured Chrome profile, then retry");
  }
  log(`Page loaded: ${currentUrl}`);
  await client.eval(injector);
  log("Interceptors injected. Auto-scrolling...");

  let stagnant = 0;
  let lastCount = 0;
  while (true) {
    ensureTime();
    await client.eval(`(() => {
      window.scrollTo(0, document.documentElement.scrollHeight);
      const col = document.querySelector('[data-testid="primaryColumn"]');
      if (col) col.scrollTo(0, col.scrollHeight);
    })()`);
    await sleep(1000);
    const count = Number(await client.eval("window.__xBookmarks.length")) || 0;
    if (count > lastCount) {
      log(`  Captured ${count} bookmarks...`);
      stagnant = 0;
      lastCount = count;
      continue;
    }
    stagnant += 1;
    if (stagnant >= stagnantRounds) {
      await sleep(2500);
      const finalCount = Number(await client.eval("window.__xBookmarks.length")) || 0;
      if (finalCount === lastCount) break;
      lastCount = finalCount;
      stagnant = 0;
    }
  }

  const bookmarks = await client.eval("window.__xBookmarks");
  const output = {
    exportDate: new Date().toISOString(),
    totalBookmarks: bookmarks.length,
    source: "bookmark",
    bookmarks,
  };
  if (!bookmarks.length) {
    throw new Error("No bookmarks captured; X may have changed its response shape or the profile is not fully loaded");
  }
  if (options.output) {
    await mkdir(options.outputDir, { recursive: true });
    await writeFile(options.output, JSON.stringify(output, null, 2));
    console.log(JSON.stringify({ output: options.output, count: bookmarks.length, url: currentUrl, startedChrome }));
  } else {
    console.log(JSON.stringify(output, null, 2));
  }
} finally {
  client.close();
}
'''


def default_export_profile() -> Path:
    return Path.home() / ".x-bookmarks" / "chrome-profile"


def default_export_path(paths: IndexPaths | None = None) -> Path:
    current_paths = paths or default_paths()
    return current_paths.data_dir / "exports" / "x-bookmarks-latest.json"


def export_x_bookmarks(
    *,
    output: Path | None = None,
    paths: IndexPaths | None = None,
    user_data_dir: Path | None = None,
    chrome_path: Path | None = None,
    debug_port: int = DEFAULT_DEBUG_PORT,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    stagnant_rounds: int = DEFAULT_STAGNANT_ROUNDS,
    headless: bool = False,
    quiet: bool = False,
) -> dict:
    current_paths = paths or default_paths()
    export_path = (output or default_export_path(current_paths)).expanduser()
    export_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path = (user_data_dir or default_export_profile()).expanduser()
    chrome = (chrome_path or DEFAULT_CHROME_PATH).expanduser()
    options = {
        "output": str(export_path),
        "outputDir": str(export_path.parent),
        "userDataDir": str(profile_path),
        "chromePath": str(chrome),
        "debugPort": debug_port,
        "timeoutMs": timeout_seconds * 1000,
        "stagnantRounds": stagnant_rounds,
        "headless": headless,
        "quiet": quiet,
    }
    env = {**os.environ, "X_BOOKMARKS_EXPORT_OPTIONS": json.dumps(options)}
    result = subprocess.run(
        ["node", "--input-type=module", "-e", NODE_EXPORTER],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout_seconds + 60,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"x-bookmarks export failed: {detail}")
    summary = json.loads(result.stdout)
    return {
        **summary,
        "profile": str(profile_path),
        "debug_port": debug_port,
        "stderr": result.stderr,
    }


def export_and_sync_x_bookmarks(
    *,
    output: Path | None = None,
    paths: IndexPaths | None = None,
    user_data_dir: Path | None = None,
    chrome_path: Path | None = None,
    debug_port: int = DEFAULT_DEBUG_PORT,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    stagnant_rounds: int = DEFAULT_STAGNANT_ROUNDS,
    headless: bool = False,
    run_extract: bool = False,
    run_categorize: bool = False,
    use_regex: bool = False,
    quiet: bool = False,
) -> dict:
    current_paths = paths or default_paths()
    export_result = export_x_bookmarks(
        output=output,
        paths=current_paths,
        user_data_dir=user_data_dir,
        chrome_path=chrome_path,
        debug_port=debug_port,
        timeout_seconds=timeout_seconds,
        stagnant_rounds=stagnant_rounds,
        headless=headless,
        quiet=quiet,
    )
    sync_result = sync_bookmarks(
        input_file=Path(export_result["output"]),
        run_extract=run_extract,
        run_categorize=run_categorize,
        use_regex=use_regex,
        paths=current_paths,
    )
    return {
        "export": {key: value for key, value in export_result.items() if key != "stderr"},
        "sync": sync_result,
    }
