// X/Twitter Bookmark Exporter
// Usage: Open x.com/i/bookmarks (or x.com/<user>/likes), paste this into the browser console.
// Scroll through your bookmarks (or click Auto-scroll), then click the purple Export button.
// Downloads a JSON file with all captured bookmarks.

(async function () {
  if (
    !location.hostname.includes("twitter.com") &&
    !location.hostname.includes("x.com")
  ) {
    alert("Run this on x.com/i/bookmarks or x.com/username/likes");
    return;
  }

  const isLikes = location.pathname.includes("/likes");
  const source = isLikes ? "like" : "bookmark";
  const label = isLikes ? "likes" : "bookmarks";
  const all = [];
  const seen = new Set();

  // ── Tweet parser ──────────────────────────────────────────────────────────

  function addTweet(t) {
    if (!t?.rest_id || seen.has(t.rest_id)) return;
    seen.add(t.rest_id);

    const leg = t.legacy ?? {};
    const userResult = t.core?.user_results?.result ?? {};
    // X API 2025+: name/screen_name moved to user_result.core
    const userCore = userResult.core ?? {};
    const userLegacy = userResult.legacy ?? {};
    const usr = {
      name: userCore.name ?? userLegacy.name,
      screen_name: userCore.screen_name ?? userLegacy.screen_name,
    };

    const rawMedia =
      leg.extended_entities?.media ?? leg.entities?.media ?? [];
    const media = rawMedia
      .map((m) => {
        const thumb = m.media_url_https ?? "";
        if (m.type === "video" || m.type === "animated_gif") {
          const mp4s = (m.video_info?.variants ?? [])
            .filter((v) => v.content_type === "video/mp4" && v.url)
            .sort((a, b) => (b.bitrate ?? 0) - (a.bitrate ?? 0));
          if (mp4s.length)
            return {
              type: m.type === "animated_gif" ? "gif" : "video",
              url: mp4s[0].url,
            };
          return thumb ? { type: "photo", url: thumb } : null;
        }
        return thumb ? { type: "photo", url: thumb } : null;
      })
      .filter(Boolean);

    all.push({
      id: t.rest_id,
      author: usr.name ?? "Unknown",
      handle: "@" + (usr.screen_name ?? "unknown"),
      timestamp: leg.created_at ?? "",
      text: leg.full_text ?? leg.text ?? "",
      media,
      hashtags: (leg.entities?.hashtags ?? []).map((h) => h.text),
      urls: (leg.entities?.urls ?? [])
        .map((u) => u.expanded_url)
        .filter(Boolean),
    });

    exportBtn.textContent = `Export ${all.length} ${label} →`;
  }

  // ── GraphQL response walker ───────────────────────────────────────────────

  function processEntry(e) {
    if (!e) return;
    const ic = e.content?.itemContent ?? e.content?.item?.itemContent;
    if (ic?.tweet_results) {
      let t = ic.tweet_results.result;
      if (t) {
        if (
          t.__typename === "TweetWithVisibilityResults" ||
          t.__typename === "TweetWithVisibilityResult"
        )
          t = t.tweet ?? t;
        addTweet(t);
      }
    }
    if (e.content?.items)
      e.content.items.forEach((i) =>
        processEntry({ content: i.item ?? i })
      );
  }

  function findInstructions(obj, depth = 0) {
    if (!obj || typeof obj !== "object" || depth > 6) return null;
    if (Array.isArray(obj)) return null;
    if (Array.isArray(obj.instructions)) return obj.instructions;
    for (const k of Object.keys(obj)) {
      const r = findInstructions(obj[k], depth + 1);
      if (r) return r;
    }
    return null;
  }

  function processData(d) {
    const instr = findInstructions(d) ?? [];
    instr.forEach((i) => {
      (i.entries ?? []).forEach(processEntry);
      (i.moduleItems ?? []).forEach(processEntry);
    });
  }

  // ── UI: Export button ─────────────────────────────────────────────────────

  const exportBtn = document.createElement("button");
  exportBtn.textContent = `Scroll then click to Export →`;
  Object.assign(exportBtn.style, {
    position: "fixed",
    top: "12px",
    right: "12px",
    zIndex: "2147483647",
    padding: "10px 18px",
    background: "#4f46e5",
    color: "#fff",
    border: "none",
    borderRadius: "8px",
    cursor: "pointer",
    fontSize: "14px",
    fontWeight: "700",
    boxShadow: "0 0 0 2px rgba(99,102,241,.4),0 4px 16px rgba(0,0,0,.4)",
    fontFamily: "system-ui,sans-serif",
  });

  function doExport() {
    // Restore originals
    window.fetch = origFetch;
    XMLHttpRequest.prototype.open = origOpen;
    XMLHttpRequest.prototype.send = origSend;
    [exportBtn, autoBtn].forEach((el) => {
      try {
        document.body.removeChild(el);
      } catch (e) {}
    });
    if (!all.length) {
      alert(
        `No ${label} captured. Use Auto-scroll or scroll manually first.`
      );
      return;
    }
    const blob = new Blob(
      [JSON.stringify({ exportDate: new Date().toISOString(), totalBookmarks: all.length, source, bookmarks: all }, null, 2)],
      { type: "application/json" }
    );
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${source}s.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    console.log(`✅ Downloaded ${all.length} ${label}!`);
  }

  exportBtn.onclick = doExport;

  // ── UI: Auto-scroll button ────────────────────────────────────────────────

  const autoBtn = document.createElement("button");
  autoBtn.textContent = "▶ Auto-scroll";
  Object.assign(autoBtn.style, {
    position: "fixed",
    top: "58px",
    right: "12px",
    zIndex: "2147483647",
    padding: "8px 14px",
    background: "#18181b",
    color: "#a1a1aa",
    border: "1px solid #3f3f46",
    borderRadius: "8px",
    cursor: "pointer",
    fontSize: "12px",
    fontWeight: "600",
    fontFamily: "system-ui,sans-serif",
  });

  let autoScrolling = false;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  async function runAutoScroll() {
    let stagnant = 0;
    let lastCount = all.length;
    while (autoScrolling) {
      window.scrollTo(0, document.documentElement.scrollHeight);
      const col = document.querySelector('[data-testid="primaryColumn"]');
      col?.scrollTo(0, col.scrollHeight);
      await sleep(900);
      if (all.length > lastCount) {
        stagnant = 0;
        lastCount = all.length;
      } else {
        stagnant++;
        if (stagnant >= 8) {
          window.scrollTo(0, document.documentElement.scrollHeight);
          await sleep(2000);
          if (all.length === lastCount) {
            autoScrolling = false;
            autoBtn.textContent = `✅ Done — ${all.length} captured`;
            autoBtn.style.cssText +=
              ";background:#14532d;color:#86efac;border:1px solid #166534";
            console.log(
              `✅ Auto-scroll complete! ${all.length} ${label} ready. Click Export.`
            );
            return;
          }
          stagnant = 0;
        }
      }
    }
    autoBtn.textContent = "▶ Auto-scroll";
    autoBtn.style.background = "#18181b";
    autoBtn.style.color = "#a1a1aa";
    autoBtn.style.border = "1px solid #3f3f46";
  }

  autoBtn.onclick = function () {
    if (autoScrolling) {
      autoScrolling = false;
      return;
    }
    autoScrolling = true;
    autoBtn.textContent = "⏸ Stop";
    autoBtn.style.background = "#4f46e5";
    autoBtn.style.color = "#fff";
    autoBtn.style.border = "none";
    runAutoScroll();
  };

  document.body.appendChild(exportBtn);
  document.body.appendChild(autoBtn);

  // ── Intercept fetch + XHR to capture GraphQL responses ────────────────────

  const origFetch = window.fetch;
  window.fetch = async function (...args) {
    const r = await origFetch.apply(this, args);
    try {
      const u = args[0] instanceof Request ? args[0].url : String(args[0]);
      if (u.includes("/graphql/")) {
        const d = await r.clone().json();
        processData(d);
      }
    } catch (e) {}
    return r;
  };

  const origOpen = XMLHttpRequest.prototype.open;
  const origSend = XMLHttpRequest.prototype.send;
  const xhrUrls = new WeakMap();

  XMLHttpRequest.prototype.open = function (...args) {
    xhrUrls.set(this, String(args[1] ?? ""));
    return origOpen.apply(this, args);
  };

  XMLHttpRequest.prototype.send = function (...args) {
    const xhr = this;
    const u = xhrUrls.get(xhr) ?? "";
    if (u.includes("/graphql/")) {
      xhr.addEventListener("load", function () {
        try {
          processData(JSON.parse(xhr.responseText));
        } catch (e) {}
      });
    }
    return origSend.apply(this, args);
  };

  console.log(
    `✅ Script active. Scroll through your ${label}, then click the purple button.`
  );
})();
