/**
 * ASAP Adapter 公共脚本 — v3.3.0
 * 所有页面共享：主题切换、JSON高亮、HTML转义、JS错误栏、日志渲染
 */

// ══ JS 全局错误栏 ══
window.onerror = function(msg, url, line) {
  const bar = document.getElementById("jsErrorBar");
  if (bar) { bar.textContent = "\u26a0 JS错误: " + msg + " (\u884c" + line + ") \u2014 \u70b9\u51fb\u5173\u95ed"; bar.style.display = "block"; }
};
window.addEventListener('unhandledrejection', function(e) {
  const bar = document.getElementById("jsErrorBar");
  if (bar) { bar.textContent = "\u26a0 \u5f02\u6b65\u9519\u8bef: " + (e.reason?.message || e.reason) + " \u2014 \u70b9\u51fb\u5173\u95ed"; bar.style.display = "block"; }
});

// ══ 主题切换 ══
function initTheme() {
  const saved = localStorage.getItem("theme");
  if (saved) document.documentElement.setAttribute("data-theme", saved);
  const btn = document.querySelector(".theme-toggle");
  if (btn) btn.textContent = (saved || "dark") === "dark" ? "\ud83c\udf19" : "\u2600\ufe0f";
}
function toggleTheme() {
  const html = document.documentElement;
  const current = html.getAttribute("data-theme");
  const next = current === "dark" ? "light" : "dark";
  html.setAttribute("data-theme", next);
  localStorage.setItem("theme", next);
  const btn = document.querySelector(".theme-toggle");
  if (btn) btn.textContent = next === "dark" ? "\ud83c\udf19" : "\u2600\ufe0f";
}

// ══ JSON 语法高亮 ══
function jsonHighlight(s) {
  if (!s) return "";
  let html = s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  return html
    .replace(/"([^"]+)":/g, '<span style="color:var(--accent);">"$1"</span>:')
    .replace(/: "([^"]*)"/g, ': <span style="color:var(--success);">"$1"</span>')
    .replace(/: (\d+\.?\d*)/g, ': <span style="color:var(--warning);">$1</span>')
    .replace(/: (true|false)/g, ': <span style="color:#f0a060;">$1</span>')
    .replace(/: (null)/g, ': <span style="color:var(--text-muted);">$1</span>');
}

// ══ HTML 转义 ══
function escHtml(s) {
  if (!s) return "";
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

// ══ 导航栏激活 ══
function initNav(path) {
  document.querySelectorAll(".nav-tab").forEach(a => {
    a.classList.remove("nav-tab-active");
    if (a.getAttribute("href") === path) a.classList.add("nav-tab-active");
  });
}

// ══ 统一日志轮询（懒渲染：只追加新条目） ══
let _logEntries = [];   // 已渲染的日志条目缓存
function pollLogs(containerId, countId, autoScrollId, opts) {
  opts = opts || {};
  const source = opts.source || "all";
  const limit = opts.limit || 50;
  const interval = opts.interval || 3000;
  const onNewEntry = opts.onNewEntry || null;  // 回调: fn(entry)

  const container = document.getElementById(containerId);
  if (!container) return;

  const fn = async () => {
    try {
      const resp = await fetch("/api/asap/logs?limit=" + limit + "&source=" + source);
      const data = await resp.json();
      const logs = data.logs || [];

      // 更新计数
      const countEl = document.getElementById(countId);
      if (countEl) countEl.textContent = "(\u5171" + data.total + "\u6761)";

      // 无日志
      if (logs.length === 0) {
        if (_logEntries.length === 0) {
          container.innerHTML = '<div style="color:var(--text-muted);padding:10px;text-align:center;">\u65e0\u8bf7\u6c42\u8bb0\u5f55</div>';
        }
        return;
      }

      // 判断新增
      const oldLen = _logEntries.length;
      if (logs.length > oldLen) {
        const newEntries = logs.slice(oldLen);
        _logEntries = logs;
        renderLogEntries(container, newEntries, onNewEntry, true);
        // 自动滚动
        const autoScroll = document.getElementById(autoScrollId);
        if (!autoScroll || autoScroll.checked) {
          container.scrollTop = container.scrollHeight;
        }
        // 限制 DOM 节点
        while (container.children.length > 300) container.removeChild(container.firstChild);
      }
    } catch (e) { console.error("[Logs] \u8f6e\u8be2\u5931\u8d25:", e); }
  };

  // 初始加载
  fn();
  return setInterval(fn, interval);
}

// ══ 渲染日志条目到 DOM ══
function renderLogEntries(container, entries, onNewEntry, prepend) {
  const colors = {
    control: "var(--warning)",
    query: "var(--accent)",
    zone: "var(--success)",
    system: "var(--text-muted)",
  };

  entries.forEach(e => {
    if (onNewEntry) onNewEntry(e);

    const div = document.createElement("div");
    div.style.cssText = "margin:4px 0;padding:6px 8px;background:var(--card-bg);border-radius:6px;border-left:3px solid " +
      (colors[e.source] || colors[e.category] || "var(--accent)");

    const time = e.time || "";
    const sourceLabel = e.source || e.category || "";
    const methodPath = (e.method && e.endpoint)
      ? ' <span style="font-size:9px;color:var(--text-muted);font-family:monospace;">' + escHtml(e.method) + ' ' + escHtml(e.endpoint) + '</span>'
      : '';
    const reqSummary = e.request ? escHtml(JSON.stringify(e.request)).slice(0, 100) : "";

    const reqJson = e.request ? JSON.stringify(e.request, null, 2) : "";
    const respJson = e.response
      ? (typeof e.response === "string" ? e.response : JSON.stringify(e.response, null, 2))
      : "";

    div.innerHTML =
      '<div style="display:flex;justify-content:space-between;cursor:pointer;" ' +
        'onclick="var d=this.nextElementSibling;d.style.display=d.style.display===\'none\'?\'block\':\'none\'">' +
        '<span>' +
          '<span style="font-size:10px;color:var(--text-muted);margin-right:8px;">' + time + '</span>' +
          '<span style="font-size:10px;color:' + (colors[sourceLabel] || "var(--accent)") + ';margin-right:8px;">' + sourceLabel + '</span>' +
          methodPath +
          '<span style="font-size:11px;">' + reqSummary + '</span>' +
        '</span>' +
        '<span style="font-size:10px;color:var(--text-muted);">' + (e.status || "") + ' \u25bc</span>' +
      '</div>' +
      '<div style="display:none;margin-top:4px;font-size:10px;">' +
        (reqJson ? '<div style="color:var(--warning);">\ud83d\udce4 \u8bf7\u6c42</div><pre style="background:rgba(0,0,0,0.2);padding:4px 8px;border-radius:4px;max-height:150px;overflow:auto;margin:2px 0;">' + jsonHighlight(reqJson) + '</pre>' : '') +
        (respJson ? '<div style="color:var(--success);">\ud83d\udce5 \u54cd\u5e94</div><pre style="background:rgba(0,0,0,0.2);padding:4px 8px;border-radius:4px;max-height:150px;overflow:auto;margin:2px 0;">' + jsonHighlight(respJson) + '</pre>' : '') +
      '</div>';

    if (prepend) {
      container.appendChild(div);
    } else {
      container.insertBefore(div, container.firstChild);
    }
  });
}
