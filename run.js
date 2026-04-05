const page = await browser.getPage("x-bookmarks");

await page.goto("https://x.com/i/bookmarks", { waitUntil: "networkidle" });
await new Promise((r) => setTimeout(r, 2000));

const url = page.url();
if (url.includes("/login") || url.includes("/i/flow/login")) {
  console.error("ERROR: Not logged in. Log into X in your Chrome browser first.");
  process.exit(1);
}

console.error("Page loaded:", url);

// Inject interceptors
await page.evaluate(() => {
  window.__xBookmarks = [];
  window.__xSeen = new Set();

  function addTweet(t) {
    if (!t || !t.rest_id || window.__xSeen.has(t.rest_id)) return;
    window.__xSeen.add(t.rest_id);
    var leg = t.legacy || {};
    var usr = (t.core && t.core.user_results && t.core.user_results.result && t.core.user_results.result.legacy) || {};
    var rawMedia = (leg.extended_entities && leg.extended_entities.media) || (leg.entities && leg.entities.media) || [];
    var media = rawMedia.map(function(m) {
      var thumb = m.media_url_https || "";
      if (m.type === "video" || m.type === "animated_gif") {
        var variants = (m.video_info && m.video_info.variants) || [];
        var mp4s = variants.filter(function(v) { return v.content_type === "video/mp4" && v.url; })
          .sort(function(a, b) { return (b.bitrate || 0) - (a.bitrate || 0); });
        if (mp4s.length) return { type: m.type === "animated_gif" ? "gif" : "video", url: mp4s[0].url };
        return thumb ? { type: "photo", url: thumb } : null;
      }
      return thumb ? { type: "photo", url: thumb } : null;
    }).filter(Boolean);

    window.__xBookmarks.push({
      id: t.rest_id,
      author: usr.name || "Unknown",
      handle: "@" + (usr.screen_name || "unknown"),
      timestamp: leg.created_at || "",
      text: leg.full_text || leg.text || "",
      media: media,
      hashtags: ((leg.entities && leg.entities.hashtags) || []).map(function(h) { return h.text; }),
      urls: ((leg.entities && leg.entities.urls) || []).map(function(u) { return u.expanded_url; }).filter(Boolean)
    });
  }

  function processEntry(e) {
    if (!e) return;
    var ic = e.content && (e.content.itemContent || (e.content.item && e.content.item.itemContent));
    if (ic && ic.tweet_results) {
      var t = ic.tweet_results.result;
      if (t) {
        if (t.__typename === "TweetWithVisibilityResults" || t.__typename === "TweetWithVisibilityResult") t = t.tweet || t;
        addTweet(t);
      }
    }
    if (e.content && e.content.items) e.content.items.forEach(function(i) { processEntry({ content: i.item || i }); });
  }

  function findInstructions(obj, depth) {
    if (!obj || typeof obj !== "object" || (depth || 0) > 6) return null;
    if (Array.isArray(obj)) return null;
    if (Array.isArray(obj.instructions)) return obj.instructions;
    for (var k in obj) {
      if (Object.prototype.hasOwnProperty.call(obj, k)) {
        var r = findInstructions(obj[k], (depth || 0) + 1);
        if (r) return r;
      }
    }
    return null;
  }

  function processData(d) {
    var instr = findInstructions(d) || [];
    instr.forEach(function(i) {
      (i.entries || []).forEach(processEntry);
      (i.moduleItems || []).forEach(processEntry);
    });
  }

  var origFetch = window.fetch;
  window.fetch = async function() {
    var r = await origFetch.apply(this, arguments);
    try {
      var u = arguments[0] instanceof Request ? arguments[0].url : String(arguments[0]);
      if (u.includes("/graphql/")) { var d = await r.clone().json(); processData(d); }
    } catch (ex) {}
    return r;
  };

  var origOpen = XMLHttpRequest.prototype.open;
  var origSend = XMLHttpRequest.prototype.send;
  var xhrUrls = new WeakMap();
  XMLHttpRequest.prototype.open = function() { xhrUrls.set(this, String(arguments[1] || "")); return origOpen.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function() {
    var xhr = this, u = xhrUrls.get(xhr) || "";
    if (u.includes("/graphql/")) { xhr.addEventListener("load", function() { try { processData(JSON.parse(xhr.responseText)); } catch (ex) {} }); }
    return origSend.apply(this, arguments);
  };
});

console.error("Interceptors injected. Auto-scrolling...");

// Auto-scroll loop
var stagnant = 0;
var lastCount = 0;

while (true) {
  await page.evaluate(() => {
    window.scrollTo(0, document.documentElement.scrollHeight);
    var col = document.querySelector('[data-testid="primaryColumn"]');
    if (col) col.scrollTo(0, col.scrollHeight);
  });

  await new Promise((r) => setTimeout(r, 1000));

  var count = await page.evaluate(() => window.__xBookmarks.length);

  if (count > lastCount) {
    console.error("  Captured " + count + " bookmarks...");
    stagnant = 0;
    lastCount = count;
  } else {
    stagnant++;
    if (stagnant >= 8) {
      await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
      await new Promise((r) => setTimeout(r, 2500));
      var finalCount = await page.evaluate(() => window.__xBookmarks.length);
      if (finalCount === lastCount) {
        console.error("Auto-scroll complete. Total: " + finalCount + " bookmarks.");
        break;
      }
      lastCount = finalCount;
      stagnant = 0;
    }
  }
}

// Output JSON to stdout
var bookmarks = await page.evaluate(() => window.__xBookmarks);
var output = {
  exportDate: new Date().toISOString(),
  totalBookmarks: bookmarks.length,
  source: "bookmark",
  bookmarks: bookmarks
};

console.log(JSON.stringify(output, null, 2));
console.error("Done! " + bookmarks.length + " bookmarks exported.");
