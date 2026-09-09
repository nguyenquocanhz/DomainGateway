/* Domain Gateway — client logic (vanilla, không build step) */
(() => {
  "use strict";

  // Giá trị khởi tạo lấy từ HTML, nhưng /api/summary mới là nguồn thật:
  // đổi ngưỡng trong trang Cài đặt phải phản ánh ngay mà không cần tải lại trang.
  let WARN = +document.body.dataset.warn || 30;
  let CRIT = +document.body.dataset.critical || 7;

  const state = {
    domains: [],
    summary: {},
    view: "overview",
    layout: "table",
    page: 1,
    pageSize: 25,
    themePref: "dark",
    registrars: null,
    registryRegion: "all",
    regOpen: {},        // các đoạn điều kiện người dùng tự mở/đóng, khoá "khuvuc:truong"
    filter: "all",
    query: "",
    sort: { key: "days_left", dir: 1 },
    openMenu: null,
    polling: null,
  };

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  const esc = (v) =>
    String(v ?? "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const STATUS_TEXT = {
    active: "Đang hoạt động",
    expiring: "Sắp hết hạn",
    critical: "Nguy cấp",
    expired: "Đã hết hạn",
    unknown: "Chưa rõ",
  };

  // Mỗi icon phải nói được một điều mà chữ không nói, hoặc nói nhanh hơn chữ.
  const svg = (body, cls = "ico") =>
    `<svg class="${cls}" viewBox="0 0 16 16" aria-hidden="true">${body}</svg>`;

  const ICON = {
    // --- trạng thái vòng đời tên miền ---
    check: svg('<path d="m3 8.5 3.2 3.2L13 5"/>'),                                   // còn hạn
    clock: svg('<circle cx="8" cy="8" r="6"/><path d="M8 4.5V8l2.4 1.6"/>'),          // đang đếm ngược
    warn: svg('<path d="M8 2.5 15 14H1z"/><path d="M8 6.5v3.2M8 11.8v.2"/>'),         // cần xử lý ngay
    quest: svg('<circle cx="8" cy="8" r="6"/><path d="M6.3 6.2a1.8 1.8 0 1 1 2.4 1.7c-.5.2-.7.6-.7 1.1M8 11.6v.2"/>'), // chưa đọc được dữ liệu

    // --- thuộc tính tên miền ---
    lock: svg('<rect x="3.5" y="7" width="9" height="6.5" rx="1.3"/><path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2"/>', "ico lock-ico"), // khoá chuyển nhượng
    globe: svg('<circle cx="8" cy="8" r="6"/><path d="M2 8h12M8 2a10 10 0 0 1 0 12A10 10 0 0 1 8 2"/>'),
    calendar: svg('<rect x="2.5" y="3.5" width="11" height="10" rx="1.3"/><path d="M2.5 6.5h11M5.5 2.2v2.6M10.5 2.2v2.6"/>'),

    // --- điều hướng ---
    table: svg('<rect x="2" y="3" width="12" height="10" rx="1.5"/><path d="M2 6.4h12M6.4 6.4V13"/>'),
    layers: svg('<path d="m8 2 6 3-6 3-6-3 6-3M2 8l6 3 6-3M2 11.5l6 3 6-3"/>'),
    search: svg('<circle cx="7" cy="7" r="4.5"/><path d="m10.5 10.5 3 3"/>'),
    book: svg('<rect x="4" y="2.5" width="8.5" height="11" rx="1.2"/><path d="M4 5.2H2.4M4 8H2.4M4 10.8H2.4"/><path d="M6.4 6h3.7M6.4 8.6h2.2"/>'),
    bell: svg('<path d="M12.1 6.4a4.1 4.1 0 1 0-8.2 0c0 3.9-1.4 4.9-1.4 4.9h11s-1.4-1-1.4-4.9Z"/><path d="M9.3 13.4a1.5 1.5 0 0 1-2.6 0"/>'),

    // --- khác ---
    dots: svg('<circle cx="8" cy="3.5" r=".9" fill="currentColor" stroke="none"/><circle cx="8" cy="8" r=".9" fill="currentColor" stroke="none"/><circle cx="8" cy="12.5" r=".9" fill="currentColor" stroke="none"/>'),
    sun: svg('<circle cx="8" cy="8" r="3.1"/><path d="M8 1.4v1.7M8 12.9v1.7M14.6 8h-1.7M3.1 8H1.4M12.66 3.34l-1.2 1.2M4.54 11.46l-1.2 1.2M12.66 12.66l-1.2-1.2M4.54 4.54l-1.2-1.2"/>'),
    moon: svg('<path d="M13.4 9.6A5.9 5.9 0 0 1 6.4 2.6a5.9 5.9 0 1 0 7 7Z"/>'),
    chevron: svg('<path d="M6.2 3.5 10.7 8l-4.5 4.5"/>', "ico chev"),  // xoay 90° khi mở
    // Vạch giữa là bắt buộc: bỏ nó ra thì ở 14px hai mũi tên dính nhau thành
    // hình ✕ (trông như nút đóng) và hình ◇ — đã thử và phải sửa.
    expandAll: svg('<path d="M3.4 8h9.2"/><path d="M5.8 4.4 8 2.2l2.2 2.2"/><path d="M5.8 11.6 8 13.8l2.2-2.2"/>'),   // hai mũi tên rẽ ra
    collapseAll: svg('<path d="M3.4 8h9.2"/><path d="M5.8 2.2 8 4.4l2.2-2.2"/><path d="M5.8 13.8 8 11.6l2.2 2.2"/>'), // hai mũi tên chụm vào
    prev: svg('<path d="M9.8 3.5 5.3 8l4.5 4.5"/>'),
    next: svg('<path d="M6.2 3.5 10.7 8l-4.5 4.5"/>'),
    emptyBox: svg('<path d="M2 6.2 4 2.5h8l2 3.7v6.1a1.2 1.2 0 0 1-1.2 1.2H3.2A1.2 1.2 0 0 1 2 12.3z"/><path d="M2 6.2h12M6.2 8.8h3.6"/>'),
  };

  // Icon của trang hiện tại — cùng ký hiệu với mục điều hướng tương ứng
  const VIEW_ICON = {
    overview: "table", expiring: "clock", providers: "layers",
    lookup: "search", registry: "book", settings: "bell",
  };

  const statusIcon = (s) =>
    s === "active" ? ICON.check
      : s === "unknown" ? ICON.quest
        : s === "expiring" ? ICON.clock : ICON.warn;

  const fmtDate = (iso) => {
    if (!iso) return null;
    const d = new Date(iso);
    return isNaN(d) ? null : d.toLocaleDateString("vi-VN", { day: "2-digit", month: "2-digit", year: "numeric" });
  };

  const fmtDays = (n) => {
    if (n === null || n === undefined) return "—";
    if (n < 0) return `quá ${Math.abs(n)} ngày`;
    if (n < 400) return `${n} ngày`;
    return `${(n / 365).toFixed(1)} năm`;
  };

  const relTime = (iso) => {
    if (!iso) return "chưa tra cứu";
    const diff = (Date.now() - new Date(iso).getTime()) / 1000;
    if (isNaN(diff)) return "chưa tra cứu";
    if (diff < 90) return "vừa xong";
    if (diff < 5400) return `${Math.round(diff / 60)} phút trước`;
    if (diff < 172800) return `${Math.round(diff / 3600)} giờ trước`;
    return `${Math.round(diff / 86400)} ngày trước`;
  };

  function toast(msg, kind = "info") {
    const el = document.createElement("div");
    el.className = "toast";
    el.dataset.kind = kind;
    el.textContent = msg;
    $("#toastStack").appendChild(el);
    setTimeout(() => el.remove(), 5200);
  }

  async function api(url, opts = {}) {
    const res = await fetch(url, {
      headers: { "Content-Type": "application/json" },
      ...opts,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    let data = null;
    try { data = await res.json(); } catch { /* body rỗng */ }
    if (!res.ok) throw new Error((data && (data.error || data.reason)) || `HTTP ${res.status}`);
    return data;
  }

  /* ======================= tải & vẽ dữ liệu ======================= */
  async function load() {
    const [domains, summary] = await Promise.all([
      api("/api/domains"),
      api("/api/summary"),
    ]);
    state.domains = domains;
    state.summary = summary;
    if (summary.warn_days) WARN = +summary.warn_days;
    if (summary.critical_days) CRIT = +summary.critical_days;
    renderAll();
  }

  function renderAll() {
    renderStats();
    renderTable();
    renderSidebar();
  }

  function renderSidebar() {
    const s = state.summary;
    const soon = (s.expiring || 0) + (s.critical || 0) + (s.expired || 0);
    const badge = $("#navBadgeExpiring");
    badge.textContent = soon;
    badge.dataset.zero = soon === 0 ? "1" : "0";
    // relTime() tra ve "chua tra cuu" khi chua co moc nao -> ghep them
    // "Tra cuu " o dau se ra "Tra cuu chua tra cuu". Tach hai truong hop.
    const refreshed = s.last_refresh ? "Tra cứu " + relTime(s.last_refresh)
                                     : "Chưa tra cứu lần nào";
    $("#lastRefresh").textContent = refreshed;
    $("#healthRow").title = refreshed;   // tooltip khi sidebar đang thu gọn
    $("#healthDot").dataset.state = (s.unknown || 0) > 0 ? "stale" : "ok";
    updateNotifyBadge(s.notify_configured, s.last_notify);
  }

  function renderStats() {
    const s = state.summary;
    const soon = (s.expiring || 0) + (s.critical || 0);
    const next = s.next_expiry;
    const urgent = (s.critical || 0) + (s.expired || 0);
    const cards = [
      { icon: "globe", label: "Tổng tên miền", value: s.total || 0,
        sub: `${s.providers || 0} nhà cung cấp` },
      { icon: "clock", label: "Sắp hết hạn", value: soon, cls: soon ? "is-warn" : "",
        sub: `trong ${WARN} ngày tới` },
      { icon: urgent ? "warn" : "check", label: "Cần xử lý ngay", value: urgent,
        cls: urgent ? "is-danger" : "is-ok",
        sub: `dưới ${CRIT} ngày hoặc đã hết hạn` },
      { icon: "calendar", label: "Hết hạn gần nhất",
        value: next ? fmtDays(next.days_left) : "—",
        sub: next ? `${next.domain} · ${fmtDate(next.date_iso) || next.date}` : "chưa có dữ liệu",
        small: true },
    ];
    $("#statRow").innerHTML = cards.map((c) => `
      <div class="stat ${c.cls || ""}">
        <div class="stat-head">${ICON[c.icon]}<span class="stat-label">${esc(c.label)}</span></div>
        <div class="stat-value" ${c.small ? 'style="font-size:19px"' : ""}>${esc(c.value)}</div>
        <div class="stat-sub">${esc(c.sub)}</div>
      </div>`).join("");
  }

  function visibleRows() {
    const q = state.query.trim().toLowerCase();
    let rows = state.domains.slice();

    if (state.view === "expiring") {
      rows = rows.filter((r) => r.days_left !== null && r.days_left <= WARN);
    }
    if (state.filter === "critical") rows = rows.filter((r) => r.status === "critical" || r.status === "expired");
    else if (state.filter === "expiring") rows = rows.filter((r) => r.status === "expiring" || r.status === "critical");
    else if (state.filter === "unknown") rows = rows.filter((r) => r.status === "unknown");

    if (q) {
      rows = rows.filter((r) =>
        [r.domain, r.provider, r.registrar, r.registrant, (r.tags || []).join(" "), (r.nameservers || []).join(" ")]
          .join(" ").toLowerCase().includes(q));
    }

    // Cột Site chỉ bật khi thực sự có dữ liệu Cloudflare. Chưa đồng bộ bao giờ
    // mà vẫn hiện một cột toàn dấu gạch ngang thì chỉ tổ chiếm chỗ.
    const coCf = state.domains.some((d) => d.cf_ket_luan);
    const bang = $("#domainTable");
    if (bang) bang.classList.toggle("co-site", coCf);

    const { key, dir } = state.sort;
    rows.sort((a, b) => {
      let x = a[key], y = b[key];
      if (key === "days_left") {
        x = x === null ? Infinity : x;
        y = y === null ? Infinity : y;
      }
      if (key === "provider") { x = a.provider || a.registrar || ""; y = b.provider || b.registrar || ""; }
      // Sắp theo mức độ đáng lo, không theo bảng chữ cái: "website tắt"
      // phải lên đầu, "chưa quét" xuống cuối.
      if (key === "cf_ket_luan") {
        const uu = (v) => CF_THU_TU[v] ?? (!v ? 9 : v.startsWith("zone-") ? 2.5 : 5);
        x = uu(a.cf_ket_luan); y = uu(b.cf_ket_luan);
      }
      if (typeof x === "string") return x.localeCompare(y, "vi") * dir;
      return ((x > y) - (x < y)) * dir;
    });
    return rows;
  }

  /** Ô "Site" — rỗng khi chưa đồng bộ Cloudflare bao giờ. */
  function siteBadge(r) {
    const n = cfNhan(r.cf_ket_luan);
    if (!n) return '<span class="dash">—</span>';
    const [muc, chu, ngan] = n;
    // Ô bảng hẹp nên hiện chữ ngắn; chữ đầy đủ để trong title, vì "Website tắt"
    // mà không nói vì sao thì người đọc phải đi tra chỗ khác.
    return `<span class="site-badge" data-muc="${esc(muc)}" title="${esc(chu)}">${esc(ngan || chu)}</span>`;
  }

  function rowHtml(r) {
    const days = r.days_left;
    const pct = days === null ? 0 : Math.max(2, Math.min(100, (days / 365) * 100));
    const provider = r.provider || r.registrar || "";
    const ns = r.nameservers || [];
    const nsText = ns.length
      ? `${esc(ns[0])}${ns.length > 1 ? ` <span class="more">+${ns.length - 1}</span>` : ""}`
      : '<span class="dash">—</span>';

    // data-label được CSS dùng làm nhãn khi bảng chuyển thành thẻ ở khổ hẹp
    return `
    <tr data-domain="${esc(r.domain)}">
      <td class="td-domain">
        <div class="cell-domain">
          <button class="domain-link" data-act="detail">${esc(r.domain)}</button>
          ${r.locked ? ICON.lock : ""}
        </div>
      </td>
      <td data-label="Trạng thái"><span class="status" data-s="${r.status}">${statusIcon(r.status)}${STATUS_TEXT[r.status]}</span></td>
      <td class="col-site" data-label="Site">${siteBadge(r)}</td>
      <td data-label="Còn lại">
        <div class="runway">
          <div class="runway-track"><div class="runway-fill" data-s="${r.status}" style="width:${pct}%"></div></div>
          <span class="runway-num">${fmtDays(days)}</span>
        </div>
      </td>
      <td data-label="Ngày hết hạn">${fmtDate(r.expires_at) ? esc(fmtDate(r.expires_at)) : '<span class="dash">—</span>'}</td>
      <td data-label="Nhà cung cấp">${provider
        ? `<span class="pill">${esc(provider)}</span>`
        : `<span class="pill is-empty" data-act="setprovider">+ đặt tên</span>`}</td>
      <td class="ns-cell" data-label="Nameserver">${nsText}</td>
      <td data-label="Tag">${(r.tags || []).length
        ? (r.tags || []).map((t) => `<span class="tag">${esc(t)}</span>`).join("")
        : '<span class="dash">—</span>'}</td>
      <td class="col-menu">
        <div class="row-menu">
          <button class="icon-btn" data-act="menu" aria-label="Tuỳ chọn">${ICON.dots}</button>
        </div>
      </td>
    </tr>`;
  }

  function cardHtml(r) {
    const days = r.days_left;
    const pct = days === null ? 0 : Math.max(2, Math.min(100, (days / 365) * 100));
    const provider = r.provider || r.registrar || "";
    const ns = r.nameservers || [];

    return `
    <article class="dcard" data-domain="${esc(r.domain)}" data-s="${r.status}">
      <div class="dcard-head">
        <button class="domain-link" data-act="detail">${esc(r.domain)}</button>
        ${r.locked ? ICON.lock : ""}
        <div class="row-menu">
          <button class="icon-btn" data-act="menu" aria-label="Tuỳ chọn">${ICON.dots}</button>
        </div>
      </div>

      <div class="dcard-status">
        <span class="status" data-s="${r.status}">${statusIcon(r.status)}${STATUS_TEXT[r.status]}</span>
        <span class="dcard-days">${fmtDays(days)}</span>
      </div>
      <div class="runway-track"><div class="runway-fill" data-s="${r.status}" style="width:${pct}%"></div></div>

      <dl class="dcard-meta">
        <div><dt>Hết hạn</dt>
          <dd>${fmtDate(r.expires_at) ? esc(fmtDate(r.expires_at)) : '<span class="dash">—</span>'}</dd></div>
        <div><dt>Nhà cung cấp</dt>
          <dd>${provider
            ? `<span class="pill">${esc(provider)}</span>`
            : `<span class="pill is-empty" data-act="setprovider">+ đặt tên</span>`}</dd></div>
        ${r.cf_ket_luan ? `<div><dt>Site</dt><dd>${siteBadge(r)}</dd></div>` : ""}
        <div><dt>Nameserver</dt>
          <dd class="mono">${ns.length
            ? esc(ns[0]) + (ns.length > 1 ? ` <span class="dash">+${ns.length - 1}</span>` : "")
            : '<span class="dash">—</span>'}</dd></div>
      </dl>

      ${(r.tags || []).length
        ? `<div class="dcard-tags">${r.tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>`
        : ""}
    </article>`;
  }

  /* ===================== phân trang ===================== */
  const PAGESIZE_KEY = "dg.pageSize";

  /** Dãy số trang rút gọn: luôn có trang đầu, trang cuối và lân cận trang hiện tại. */
  function pageWindow(current, total) {
    if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
    const out = new Set([1, total, current, current - 1, current + 1]);
    if (current <= 3) [2, 3, 4].forEach((n) => out.add(n));
    if (current >= total - 2) [total - 3, total - 2, total - 1].forEach((n) => out.add(n));
    const pages = [...out].filter((n) => n >= 1 && n <= total).sort((a, b) => a - b);
    const withGaps = [];
    pages.forEach((n, i) => {
      if (i && n - pages[i - 1] > 1) withGaps.push("…");
      withGaps.push(n);
    });
    return withGaps;
  }

  function renderPager(totalRows, pageCount) {
    const nav = $("#pagerNav");
    // Một trang thì không cần thanh điều hướng, nhưng vẫn giữ ô "mỗi trang"
    if (pageCount <= 1) { nav.innerHTML = ""; return; }

    const btn = (label, page, opts = {}) =>
      `<button class="pg-btn${opts.active ? " active" : ""}" type="button"
         data-page="${page}"${opts.disabled ? " disabled" : ""}
         ${opts.label ? `aria-label="${opts.label}"` : ""}>${label}</button>`;

    nav.innerHTML =
      btn(ICON.prev, state.page - 1, { disabled: state.page === 1, label: "Trang trước" }) +
      pageWindow(state.page, pageCount).map((p) =>
        p === "…" ? '<span class="pg-gap">…</span>' : btn(p, p, { active: p === state.page })
      ).join("") +
      btn(ICON.next, state.page + 1, { disabled: state.page === pageCount, label: "Trang sau" });
    void totalRows;
  }

  const resetPage = () => { state.page = 1; };

  function renderTable() {
    const rows = visibleRows();
    const grid = state.layout === "grid";
    const body = $("#tableBody");
    const cards = $("#cardGrid");

    $(".table-wrap").hidden = grid;
    cards.hidden = !grid;
    // Dọn vùng đang ẩn để không còn hai bản sao cùng data-domain trong DOM
    (grid ? body : cards).innerHTML = "";

    if (!rows.length) {
      const empty = `<div class="empty-state">
          ${state.domains.length ? ICON.search : ICON.emptyBox}
          <h3>Không có tên miền nào</h3>
          <p>${state.domains.length ? "Thử đổi bộ lọc hoặc từ khoá tìm kiếm." : 'Bấm "Thêm tên miền" để bắt đầu.'}</p>
        </div>`;
      if (grid) cards.innerHTML = empty;
      else body.innerHTML = `<tr><td colspan="9">${empty}</td></tr>`;
      $("#rowCount").textContent = "Hiển thị 0 tên miền";
      $("#groupHint").textContent = "";
      renderPager(0, 1);
      return;
    }

    // Cắt trang trước khi dựng DOM: danh sách dài chỉ render đúng phần đang xem
    const size = state.pageSize > 0 ? state.pageSize : rows.length;
    const pageCount = Math.max(1, Math.ceil(rows.length / size));
    state.page = Math.min(Math.max(1, state.page), pageCount);
    const start = (state.page - 1) * size;
    const pageRows = rows.slice(start, start + size);

    const target = grid ? cards : body;

    if (state.view === "providers") {
      const groups = new Map();
      for (const r of pageRows) {
        const key = r.provider || r.registrar || "Chưa rõ nhà cung cấp";
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(r);
      }
      const sorted = Array.from(groups.entries()).sort((a, b) => b[1].length - a[1].length);
      target.innerHTML = sorted.map(([name, list]) => {
        const head = `${esc(name)} <span class="count">— ${list.length} tên miền</span>`;
        return grid
          ? `<div class="grid-group">${head}</div>${list.map(cardHtml).join("")}`
          : `<tr class="group-row"><td colspan="9">${head}</td></tr>${list.map(rowHtml).join("")}`;
      }).join("");
      $("#groupHint").textContent = `Gom theo ${sorted.length} nhà cung cấp`;
    } else {
      target.innerHTML = pageRows.map(grid ? cardHtml : rowHtml).join("");
      $("#groupHint").textContent = "";
    }

    $("#rowCount").textContent = rows.length === state.domains.length && pageCount === 1
      ? `Hiển thị ${rows.length} tên miền`
      : `Hiển thị ${start + 1}–${start + pageRows.length} trên ${rows.length} tên miền`
        + (rows.length === state.domains.length ? "" : ` (lọc từ ${state.domains.length})`);
    renderPager(rows.length, pageCount);
    $$("th.sortable").forEach((th) => {
      th.querySelector(".sort-ico").textContent =
        th.dataset.sort === state.sort.key ? (state.sort.dir === 1 ? "↑" : "↓") : "";
    });
  }

  /* ===================== bảng ↔ lưới ===================== */
  const LAYOUT_KEY = "dg.layout";

  function setLayout(mode, persist = true) {
    state.layout = mode === "grid" ? "grid" : "table";
    $$("#viewToggle .vt-btn").forEach((b) =>
      b.classList.toggle("active", b.dataset.layout === state.layout));
    if (persist) {
      try { localStorage.setItem(LAYOUT_KEY, state.layout); } catch { /* chế độ riêng tư */ }
    }
    renderTable();
  }

  function initLayout() {
    let saved = "table";
    try { saved = localStorage.getItem(LAYOUT_KEY) || "table"; } catch { /* bỏ qua */ }
    state.layout = saved === "grid" ? "grid" : "table";
    $$("#viewToggle .vt-btn").forEach((b) =>
      b.classList.toggle("active", b.dataset.layout === state.layout));
  }

  /* ============================ drawer ============================ */
  function closeDrawer() {
    $("#drawer").hidden = true;
    $("#drawerScrim").hidden = true;
  }

  async function openDrawer(domain) {
    const r = state.domains.find((d) => d.domain === domain);
    if (!r) return;

    $("#drawerTitle").textContent = r.domain;
    $("#drawerSub").textContent =
      `${STATUS_TEXT[r.status]} · nguồn dữ liệu: ${r.source || "chưa tra cứu"}`;

    const kv = (label, value, mono) =>
      `<dt>${esc(label)}</dt><dd class="${mono ? "mono" : ""}">${value || '<span class="dash">—</span>'}</dd>`;

    $("#drawerBody").innerHTML = `
      ${r.error ? `<div class="drawer-section"><div class="err-box">${esc(r.error)}</div></div>` : ""}

      <div class="drawer-section">
        <h3>Vòng đời</h3>
        <dl class="kv">
          ${kv("Ngày hết hạn", fmtDate(r.expires_at) ? `${esc(fmtDate(r.expires_at))} <span class="muted">(${fmtDays(r.days_left)})</span>` : "")}
          ${kv("Ngày đăng ký", esc(fmtDate(r.created_at) || ""))}
          ${kv("Cập nhật cuối", esc(fmtDate(r.updated_at) || ""))}
          ${kv("Tra cứu lúc", esc(relTime(r.checked_at)))}
        </dl>
      </div>

      <div class="drawer-section">
        <h3>Nhà cung cấp</h3>
        <dl class="kv">
          ${kv("Bạn mua ở", esc(r.provider || ""))}
          ${kv("Registrar (registry)", esc(r.registrar || ""))}
          ${kv("Chủ thể", esc(r.registrant || ""))}
          ${kv("Tự động gia hạn", r.auto_renew === null ? "" : (r.auto_renew ? "Có" : "Không"))}
        </dl>
      </div>

      <div class="drawer-section">
        <h3>Kỹ thuật</h3>
        <dl class="kv">
          ${kv("Nameserver", (r.nameservers || []).map(esc).join("<br>"), true)}
          ${kv("Trạng thái EPP", (r.epp_status || []).map(esc).join("<br>"), true)}
          ${kv("DNSSEC", r.dnssec ? "Bật" : "Tắt")}
          ${kv("Khoá chuyển nhượng", r.locked ? "Có" : "Không")}
        </dl>
      </div>

      <div class="drawer-section">
        <h3>Ghi chú</h3>
        <div class="field">
          <textarea id="drawerNote" rows="3" placeholder="Tài khoản nào, thẻ nào thanh toán, ai quản lý...">${esc(r.note || "")}</textarea>
        </div>
      </div>

      <div class="drawer-section">
        <h3>Thao tác</h3>
        <div class="drawer-actions">
          <button class="btn sm" data-dact="save">Lưu ghi chú</button>
          <button class="btn sm" data-dact="provider">Đổi nhà cung cấp</button>
          <button class="btn sm" data-dact="tags">Sửa tag</button>
          <button class="btn sm" data-dact="expires">Đặt ngày hết hạn tay</button>
          <button class="btn sm" data-dact="refresh">Tra cứu lại</button>
          <button class="btn sm danger" data-dact="delete">Xoá khỏi kho</button>
        </div>
      </div>

      <div class="drawer-section">
        <h3>Lịch sử tra cứu</h3>
        <div id="drawerHistory" class="muted">Đang tải...</div>
      </div>`;

    $("#drawer").hidden = false;
    $("#drawerScrim").hidden = false;
    $("#drawer").dataset.domain = domain;

    try {
      const hist = await api(`/api/domains/${encodeURIComponent(domain)}/history`);
      $("#drawerHistory").innerHTML = hist.length
        ? hist.slice(0, 12).map((h) => `
          <div class="hist-row">
            <span class="mono">${esc((h.checked_at || "").slice(0, 16).replace("T", " "))}</span>
            <span>${h.expires_at ? esc(fmtDate(h.expires_at)) : `<span class="dash">lỗi</span>`}
              <span class="muted">${esc(h.source || "")}</span></span>
          </div>`).join("")
        : '<span class="muted">Chưa có lịch sử.</span>';
    } catch {
      $("#drawerHistory").innerHTML = '<span class="muted">Không tải được lịch sử.</span>';
    }
  }

  async function patchDomain(domain, body, okMsg) {
    try {
      await api(`/api/domains/${encodeURIComponent(domain)}`, { method: "PATCH", body });
      await load();
      if (okMsg) toast(okMsg, "ok");
    } catch (e) {
      toast("Lỗi: " + e.message, "err");
    }
  }

  /* ======================= tra cứu nền ======================= */
  async function startRefresh(domains, force) {
    try {
      const res = await api("/api/refresh", { method: "POST", body: { domains, force } });
      if (!res.started) {
        toast(res.reason === "cache"
          ? "Dữ liệu còn mới, không cần tra cứu lại. Giữ Shift khi bấm để ép tra cứu."
          : "Đang có tiến trình tra cứu khác chạy.");
        return;
      }
      toast(`Bắt đầu tra cứu ${res.total} tên miền...`);
      pollRefresh();
    } catch (e) {
      toast("Lỗi: " + e.message, "err");
    }
  }

  function pollRefresh() {
    clearInterval(state.polling);
    const bar = $("#progressBar"), fill = $("#progressFill");
    const btn = $("#btnRefresh"), icon = btn.querySelector(".spin-target");
    bar.hidden = false;
    btn.disabled = true;
    icon.classList.add("spinning");
    $("#healthDot").dataset.state = "busy";

    state.polling = setInterval(async () => {
      let st;
      try { st = await api("/api/refresh/status"); } catch { return; }

      const pct = st.total ? (st.done / st.total) * 100 : 0;
      fill.style.width = pct + "%";
      $("#refreshLabel").textContent = st.running ? `${st.done}/${st.total}` : "Tra cứu lại";

      if (!st.running) {
        clearInterval(state.polling);
        btn.disabled = false;
        icon.classList.remove("spinning");
        setTimeout(() => { bar.hidden = true; fill.style.width = "0"; }, 500);
        await load();
        if (st.failed && st.failed.length) {
          toast(`Xong ${st.ok} tên miền, ${st.failed.length} chưa đọc được ngày hết hạn.`, "err");
        } else if (st.total) {
          toast(`Đã cập nhật ${st.ok} tên miền.`, "ok");
        }
      }
    }, 900);
  }

  /* ========================= danh bạ registry ========================= */
  // Windows không render emoji cờ (regional indicator) nên dùng badge chữ cho chắc
  const FLAG = { vietnam: "VN", eu: "EU", myanmar: "MM", quoc_te: "INTL" };
  // Nhãn ngắn cho chip lọc; REGION_NAME dài hơn, dùng làm tiêu đề thẻ
  const REGION_SHORT = {
    vietnam: "Việt Nam", eu: "EU", myanmar: "Myanmar", quoc_te: "Quốc tế",
  };

  const REGION_NAME = {
    vietnam: "Việt Nam (.vn)",
    eu: "Liên minh châu Âu (.eu)",
    myanmar: "Myanmar (.com.mm)",
    quoc_te: "Quốc tế (gTLD)",
  };

  /* Bỏ dấu tiếng Việt để gõ "viet nam" vẫn ra "Việt Nam", "nhan hoa" ra "Nhân Hòa" */
  const deaccent = (v) =>
    String(v ?? "")
      .normalize("NFD").replace(/[̀-ͯ]/g, "")
      .replace(/đ/g, "d").replace(/Đ/g, "D")
      .toLowerCase();

  async function renderRegistry() {
    const box = $("#registryContent");
    // Nạp một lần rồi giữ lại: quay lại trang này hoặc gõ tìm kiếm không gọi API nữa
    if (!state.registrars) {
      box.innerHTML = '<p class="muted">Đang tải danh bạ...</p>';
      try {
        state.registrars = await api("/api/registrars");
      } catch (e) {
        box.innerHTML = `<p class="muted">Không tải được: ${esc(e.message)}</p>`;
        return;
      }
    }
    drawRegistry();
  }

  function drawRegistry() {
    const data = state.registrars || {};
    const box = $("#registryContent");
    const q = deaccent($("#registrySearch").value.trim());
    const keys = ["vietnam", "eu", "myanmar", "quoc_te"].filter((k) => data[k]);

    // Tính kết quả cho TỪNG khu vực một lần, rồi dùng lại cho cả chip lẫn phần thân.
    // Nếu tính hai lần thì số trên chip và số thực tế rất dễ lệch nhau.
    const results = keys.map((key) => matchRegion(key, data[key], q));

    renderRegionChips(results);

    const region = state.registryRegion;
    const scope = results.filter((r) => region === "all" || r.key === region);
    const scopeTotal = scope.reduce((n, r) => n + (r.sec.nha_dang_ky || []).length, 0);
    const shown = scope.reduce((n, r) => n + r.list.length, 0);

    $("#registryCount").textContent = q
      ? `Tìm thấy ${shown} trên ${scopeTotal} nhà đăng ký`
      : `${scopeTotal} nhà đăng ký · ${region === "all" ? `${keys.length} khu vực` : REGION_SHORT[region]}`;

    const html = scope
      .filter((r) => r.visible)
      .map((r) => regionCard(r.key, r.sec, r.list, r.proseOnly, q))
      .join("");

    box.innerHTML = html || `
      <div class="empty-state">
        ${ICON.search}
        <h3>Không có nhà đăng ký nào khớp</h3>
        <p>${region === "all"
          ? 'Thử từ khoá khác — ví dụ <code>việt nam</code>, <code>.eu</code>, <code>trustee</code>, <code>api</code>.'
          : `Không có kết quả trong khu vực <strong>${esc(REGION_SHORT[region])}</strong>. Thử chọn <em>Tất cả</em>.`}</p>
      </div>`;

    syncFoldAll();
  }

  /** Nút mở/thu gọn tất cả — bám theo những đoạn ĐANG hiện, không phải toàn bộ danh bạ. */
  function syncFoldAll() {
    const btn = $("#btnFoldAll");
    const folds = $$("#registryContent details.reg-fold");
    // Khu vực đang lọc không có đoạn điều kiện nào thì nút chẳng làm được gì
    btn.hidden = folds.length === 0;
    if (!folds.length) return;

    const allOpen = folds.every((d) => d.open);
    btn.innerHTML = (allOpen ? ICON.collapseAll : ICON.expandAll) +
      `<span>${allOpen ? "Thu gọn tất cả" : "Mở tất cả"}</span>`;
    btn.title = allOpen
      ? `Thu gọn ${folds.length} đoạn điều kiện`
      : `Mở ${folds.length} đoạn điều kiện đang hiện`;
  }

  function toggleAllFolds() {
    const folds = $$("#registryContent details.reg-fold");
    const open = !folds.every((d) => d.open);
    folds.forEach((d) => {
      d.open = open;
      // Ghi thẳng vào state chứ không đợi sự kiện "toggle": vẽ lại ngay sau đó
      // (đổi chip khu vực) sẽ đọc state trước khi sự kiện kịp chạy.
      state.regOpen[d.dataset.fold] = open;
    });
    syncFoldAll();
  }

  /** Đối chiếu một khu vực với từ khoá, trả về danh sách đã lọc và kiểu khớp. */
  function matchRegion(key, sec, q) {

    // Hai mức khớp khác nhau, và phải xử lý khác nhau:
    //  - danh tính khu vực (tên, mã, đuôi tên miền, registry): người dùng muốn
    //    xem cả khu vực -> mở hết danh sách.
    //  - phần diễn giải (điều kiện, cảnh báo): chỉ nên hiện thẻ khu vực cho họ
    //    đọc, KHÔNG mở hết danh sách, nếu không số đếm sẽ thổi phồng.
    const identityHay = deaccent([
      REGION_NAME[key], FLAG[key],
      sec.registry && sec.registry.ten, sec.registry && sec.registry.whois_port43,
      (sec.tlds || []).join(" "),
    ].join(" "));
    const proseHay = deaccent([sec.mo_ta, sec.yeu_cau, sec.canh_bao].join(" "));
    const identityMatch = !!q && identityHay.includes(q);
    const proseMatch = !!q && proseHay.includes(q);

    const list = (sec.nha_dang_ky || []).filter((n) => {
      if (!q || identityMatch) return true;
      // Cờ trustee hiện thành nhãn trên thẻ nên phải tìm được; "api" khớp cả
      // nhà đăng ký có API lẫn nơi ghi rõ "không công khai" — người dùng gõ
      // "api" thường là muốn xem nơi nào có, nên chỉ tính khi thực sự có link.
      const hasApi = n.api && !deaccent(n.api).includes("khong cong khai");
      return deaccent([
        n.ten, n.url, n.quoc_gia, n.loai, n.ghi_chu, n.api,
        n.trustee ? "trustee" : "",
        hasApi ? "co api" : "",
      ].join(" ")).includes(q);
    });

    // Chỉ khớp ở phần diễn giải mà không nhà đăng ký nào khớp -> vẫn hiện thẻ
    // (đoạn điều kiện chính là thứ người dùng đang tìm) kèm ghi chú cho rõ.
    const proseOnly = proseMatch && !identityMatch && !list.length;
    return {
      key, sec, list, identityMatch, proseMatch, proseOnly,
      visible: !q || identityMatch || proseMatch || list.length > 0,
    };
  }

  function renderRegionChips(results) {
    const q = deaccent($("#registrySearch").value.trim());
    const totalShown = results.reduce((n, r) => n + r.list.length, 0);

    const chip = (region, label, count, hasResult) => `
      <button class="chip${state.registryRegion === region ? " active" : ""}"
              data-region="${esc(region)}" type="button"
              ${q && !hasResult ? 'data-empty="1"' : ""}>
        ${esc(label)} <span class="chip-count">${count}</span>
      </button>`;

    $("#regionChips").innerHTML =
      chip("all", "Tất cả", totalShown, totalShown > 0) +
      results.map((r) =>
        chip(r.key, REGION_SHORT[r.key], r.list.length, r.visible)).join("");
  }

  /* ------------------------- xuất danh bạ ra CSV ------------------------- */
  /** Bọc ô CSV: chỉ thêm nháy kép khi thật sự cần, nháy trong ô thì nhân đôi. */
  // Excel và LibreOffice coi ô mở đầu bằng = + - @ là CÔNG THỨC và chạy nó ngay
  // khi mở file. Dữ liệu ở đây không phải do người dùng gõ hết — tên registrar
  // lấy thẳng từ WHOIS của bên thứ ba. Dấu nháy đơn ở đầu buộc đọc thành chữ.
  const KY_TU_CONG_THUC = /^[=+\-@\t\r]/;
  const csvCell = (v) => {
    let t = String(v ?? "");
    if (KY_TU_CONG_THUC.test(t)) t = "'" + t;
    return /[",\r\n]/.test(t) ? `"${t.replace(/"/g, '""')}"` : t;
  };

  function downloadBlob(filename, blob) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function downloadCsv(filename, rows) {
    // BOM để Excel nhận đúng UTF-8, không thì tiếng Việt ra ký tự lạ.
    // CRLF là kết thúc dòng chuẩn của RFC 4180, an toàn cho Excel trên Windows.
    const csv = "\ufeff" + rows.map((r) => r.map(csvCell).join(",")).join("\r\n");
    downloadBlob(filename, new Blob([csv], { type: "text/csv;charset=utf-8" }));
  }

  const EXPORT_FORMATS = [
    { ext: "xlsx", name: "Excel (.xlsx)", hint: "Ngày tháng lọc được, có sheet tóm tắt" },
    { ext: "pdf", name: "PDF", hint: "Để in hoặc gửi đi" },
    { ext: "md", name: "Markdown", hint: "Kèm ngữ cảnh, cho AI agent đọc", newTab: true },
    { ext: "csv", name: "CSV", hint: "Bảng tính cơ bản, mở được ở mọi nơi" },
  ];

  // Trang danh bạ xuất phần ĐANG LỌC, nên nói rõ trong phần gợi ý
  const REGISTRY_FORMATS = [
    { ext: "pdf", name: "PDF", hint: "Đúng phần đang lọc, để in hoặc gửi", fn: exportRegistryPdf },
    { ext: "csv", name: "CSV", hint: "Đúng phần đang lọc, mở bằng Excel", fn: exportRegistryCsv },
  ];

  /**
   * Kéo menu vào trong màn hình nếu nó thò ra ngoài.
   *
   * Menu neo theo mép phải của nút chủ. Ở khung 390px, nút Xuất nằm giữa thanh
   * trên nên menu rộng 244px thò hẳn ra ngoài mép TRÁI 60px, chữ cụt mất một
   * nửa. Không thể trông vào `scrollWidth` để phát hiện: tràn sang trái không
   * sinh thanh cuộn nào cả. Nắn bằng JS thay vì đóng cứng một breakpoint, để
   * nút có nằm ở đâu trên thanh thì menu vẫn vào đúng chỗ.
   */
  function nan_menu(pop) {
    const le = 8;
    const r = pop.getBoundingClientRect();
    const vw = document.documentElement.clientWidth;
    if (r.left < le) {
      pop.style.transform = `translateX(${Math.ceil(le - r.left)}px)`;
    } else if (r.right > vw - le) {
      pop.style.transform = `translateX(-${Math.ceil(r.right - vw + le)}px)`;
    }
  }

  function toggleExportMenu(host, formats) {
    if (state.openMenu) {
      const wasExport = state.openMenu.classList.contains("formats");
      state.openMenu.remove();
      state.openMenu = null;
      $("#btnExport").setAttribute("aria-expanded", "false");
      if (wasExport) return;   // bấm lần hai để đóng
    }

    const pop = document.createElement("div");
    pop.className = "menu-pop formats";
    pop.setAttribute("role", "menu");
    pop.innerHTML = formats.map((f) => `
      <button type="button" role="menuitem" data-ext="${f.ext}">
        <span class="fmt-name">${esc(f.name)}</span>
        <span class="fmt-hint">${esc(f.hint)}</span>
      </button>`).join("");
    host.appendChild(pop);
    nan_menu(pop);
    state.openMenu = pop;
    $("#btnExport").setAttribute("aria-expanded", "true");

    pop.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-ext]");
      if (!btn) return;
      const fmt = formats.find((f) => f.ext === btn.dataset.ext);
      pop.remove();
      state.openMenu = null;
      $("#btnExport").setAttribute("aria-expanded", "false");
      if (fmt.fn) { fmt.fn(); return; }
      // Markdown mở tab mới để đọc thẳng; các định dạng khác tải về
      if (fmt.newTab) window.open(`/api/export.${fmt.ext}`, "_blank", "noopener");
      else window.location.href = `/api/export.${fmt.ext}`;
      toast(`Đang xuất ${fmt.name}...`);
    });
  }

  /** Phạm vi đang lọc — dùng chung cho cả xuất CSV lẫn xuất PDF. */
  function registryScope() {
    const data = state.registrars;
    if (!data) return null;
    const q = deaccent($("#registrySearch").value.trim());
    const region = state.registryRegion;
    const scope = ["vietnam", "eu", "myanmar", "quoc_te"]
      .filter((k) => data[k])
      .map((k) => matchRegion(k, data[k], q))
      .filter((r) => region === "all" || r.key === region);
    return { scope, region, rawQuery: $("#registrySearch").value.trim() };
  }

  function registryFilename(ext, region, query) {
    const parts = ["danh-ba-nha-dang-ky"];
    if (region !== "all") parts.push(region.replace(/_/g, "-"));
    if (query) parts.push("loc");
    return parts.join("-") + "." + ext;
  }

  function exportRegistryCsv() {
    const ctx = registryScope();
    if (!ctx) { toast("Danh bạ chưa tải xong, thử lại sau một giây.", "err"); return; }
    const { scope, region, rawQuery: q } = ctx;

    const rows = [[
      "Khu vực", "Nhà đăng ký", "Website", "Quốc gia", "Loại hình",
      "Trustee", "API", "Ghi chú", "Đuôi tên miền khu vực", "Điều kiện khu vực",
    ]];
    for (const r of scope) {
      const tlds = (r.sec.tlds || []).join(" ");
      const dieuKien = r.sec.yeu_cau || "";
      for (const n of r.list) {
        rows.push([
          REGION_SHORT[r.key], n.ten, n.url, n.quoc_gia, n.loai,
          n.trustee ? "Có" : "", n.api, n.ghi_chu, tlds, dieuKien,
        ]);
      }
    }

    if (rows.length === 1) {
      toast("Không có nhà đăng ký nào khớp để xuất.", "err");
      return;
    }

    downloadCsv(registryFilename("csv", region, q), rows);
    toast(`Đã xuất ${rows.length - 1} nhà đăng ký ra CSV.`, "ok");
  }

  async function exportRegistryPdf() {
    const ctx = registryScope();
    if (!ctx) { toast("Danh bạ chưa tải xong, thử lại sau một giây.", "err"); return; }
    const { scope, region, rawQuery: q } = ctx;

    // Gửi phần đã lọc lên server; server chỉ dàn trang, không lọc lại
    const sections = scope.filter((r) => r.list.length).map((r) => ({
      code: FLAG[r.key],
      name: REGION_NAME[r.key],
      registry: (r.sec.registry && r.sec.registry.ten) || "",
      whois: (r.sec.registry && r.sec.registry.whois_port43) || "",
      tlds: r.sec.tlds || [],
      yeu_cau: r.sec.yeu_cau || "",
      canh_bao: r.sec.canh_bao || "",
      items: r.list.map((n) => ({
        ten: n.ten, url: n.url, quoc_gia: n.quoc_gia, loai: n.loai,
        trustee: !!n.trustee, api: n.api, ghi_chu: n.ghi_chu,
      })),
    }));

    if (!sections.length) { toast("Không có nhà đăng ký nào khớp để xuất.", "err"); return; }

    toast("Đang dựng PDF...");
    try {
      const res = await fetch("/api/registrars.pdf", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sections,
          query: q,
          region_label: region === "all" ? "" : REGION_SHORT[region],
        }),
      });
      if (!res.ok) {
        let msg = "HTTP " + res.status;
        try { msg = (await res.json()).error || msg; } catch { /* body không phải JSON */ }
        throw new Error(msg);
      }
      downloadBlob(registryFilename("pdf", region, q), await res.blob());
      const count = sections.reduce((n, sec) => n + sec.items.length, 0);
      toast(`Đã xuất ${count} nhà đăng ký ra PDF.`, "ok");
    } catch (e) {
      toast("Không xuất được PDF: " + e.message, "err");
    }
  }

  /**
   * Một đoạn văn dài (điều kiện đăng ký, lưu ý) gập lại được.
   * Dùng <details> của trình duyệt: có sẵn bàn phím và trình đọc màn hình,
   * không cần tự viết ARIA.
   */
  function foldHtml(key, field, label, text, q) {
    if (!text) return "";
    const id = key + ":" + field;
    // Từ khoá rơi đúng vào đoạn này thì mở sẵn — người dùng đang tìm chính nó.
    const auto = !!q && deaccent(text).includes(q);
    const open = state.regOpen[id] === undefined ? auto : state.regOpen[id];
    return `
      <details class="reg-fold" data-fold="${esc(id)}"${open ? " open" : ""}>
        <summary>
          ${ICON.chevron}
          <strong>${esc(label)}</strong>
          <span class="fold-peek">${esc(text)}</span>
        </summary>
        <p class="fold-body">${esc(text)}</p>
      </details>`;
  }

  function regionCard(key, sec, list, proseOnly, q) {
    const reg = sec.registry;
    return `
      <section class="reg-card">
        <h2><span class="reg-flag">${FLAG[key]}</span> ${esc(REGION_NAME[key])}</h2>

        ${reg ? `<div class="reg-meta">
          <strong>Registry:</strong> ${esc(reg.ten)} —
          <a href="${esc(reg.url)}" target="_blank" rel="noopener noreferrer">${esc(reg.url)}</a>
          ${reg.whois_port43 ? `<br><strong>WHOIS:43:</strong> <code>${esc(reg.whois_port43)}</code>` : ""}
          ${reg.rdap ? `<br><strong>RDAP:</strong> <code>${esc(reg.rdap)}</code>` : ""}
          ${reg.ghi_chu ? `<br>${esc(reg.ghi_chu)}` : ""}
        </div>` : `<div class="reg-meta">${esc(sec.mo_ta || "")}</div>`}

        ${foldHtml(key, "yeu_cau", "Điều kiện đăng ký", sec.yeu_cau, q)}
        ${foldHtml(key, "canh_bao", "Lưu ý", sec.canh_bao, q)}

        ${sec.tlds ? `<div class="reg-tlds">${sec.tlds.map((t) => `<span class="pill">${esc(t)}</span>`).join("")}</div>` : ""}

        ${proseOnly ? `<p class="muted reg-note">Khớp ở phần điều kiện chung của khu vực này,
          không có nhà đăng ký nào khớp riêng.</p>` : ""}

        <div class="reg-list">
          ${list.map((n) => `
            <div class="reg-item">
              <a href="${esc(n.url)}" target="_blank" rel="noopener noreferrer">${esc(n.ten)}</a>
              ${n.quoc_gia ? ` <span class="muted">· ${esc(n.quoc_gia)}</span>` : ""}
              ${n.loai ? ` <span class="muted">· ${esc(n.loai)}</span>` : ""}
              ${n.trustee ? ' <span class="tag">trustee</span>' : ""}
              ${n.ghi_chu ? `<p>${esc(n.ghi_chu)}</p>` : ""}
              ${n.api ? `<span class="api-tag">API: ${esc(n.api)}</span>` : ""}
            </div>`).join("")}
        </div>
      </section>`;
  }

  /* ============================ tra cứu lẻ ============================ */
  async function doLookup() {
    const input = $("#lookupInput");
    const domain = input.value.trim();
    if (!domain) return;
    const out = $("#lookupResult");
    out.innerHTML = '<p class="muted">Đang tra cứu...</p>';
    try {
      const r = await api(`/api/lookup?domain=${encodeURIComponent(domain)}`);
      const kv = (l, v) => `<dt>${esc(l)}</dt><dd>${v || '<span class="dash">—</span>'}</dd>`;
      out.innerHTML = `
        ${r.error ? `<div class="err-box" style="margin-bottom:14px">${esc(r.error)}</div>` : ""}
        <dl class="kv">
          ${kv("Tên miền", esc(r.domain))}
          ${kv("Trạng thái", `<span class="status" data-s="${r.status}">${statusIcon(r.status)}${STATUS_TEXT[r.status]}</span>`)}
          ${kv("Ngày hết hạn", fmtDate(r.expires_at) ? `${esc(fmtDate(r.expires_at))} <span class="muted">(${fmtDays(r.days_left)})</span>` : "")}
          ${kv("Ngày đăng ký", esc(fmtDate(r.created_at) || ""))}
          ${kv("Registrar", esc(r.registrar || ""))}
          ${kv("Chủ thể", esc(r.registrant || ""))}
          ${kv("Nameserver", (r.nameservers || []).map(esc).join("<br>"))}
          ${kv("EPP", (r.epp_status || []).map(esc).join("<br>"))}
          ${kv("DNSSEC", r.dnssec ? "Bật" : "Tắt")}
          ${kv("Nguồn", esc(r.source || ""))}
        </dl>
        <div class="drawer-actions" style="margin-top:16px">
          <button class="btn sm primary" id="lookupAdd">Thêm vào kho quản lý</button>
        </div>`;
      const addBtn = $("#lookupAdd");
      if (addBtn) addBtn.onclick = async () => {
        try {
          await api("/api/domains", { method: "POST", body: { domain: r.domain, lookup: true } });
          toast(`Đã thêm ${r.domain}`, "ok");
          await load();
        } catch (e) { toast("Lỗi: " + e.message, "err"); }
      };
    } catch (e) {
      out.innerHTML = `<div class="err-box">${esc(e.message)}</div>`;
    }
  }

  /* ====================== cài đặt & cảnh báo ====================== */
  function updateNotifyBadge(configured, lastNotify) {
    const badge = $("#notifyBadge");
    if (badge) {
      badge.dataset.state = configured ? "on" : "off";
      badge.textContent = configured
        ? (lastNotify ? `Đã kết nối · gửi ${relTime(lastNotify)}` : "Đã kết nối")
        : "Chưa kết nối";
    }
    const dot = $("#navDotNotify");
    if (dot) dot.hidden = !configured;
  }

  async function renderPending() {
    const box = $("#pendingList");
    if (!box) return;
    box.innerHTML = '<p class="muted">Đang tải...</p>';
    try {
      const d = await api("/api/notify/pending");
      $("#pendingSummary").textContent = d.total
        ? `${d.new_count} cảnh báo mới trên ${d.total} tên miền dưới ngưỡng`
        : "Không có tên miền nào dưới ngưỡng";
      box.innerHTML = d.pending.length
        ? d.pending.map((p) => `
          <div class="pending-row" data-b="${esc(p.bucket)}">
            <span class="pd-domain">${esc(p.domain)}</span>
            <span class="pd-flag" data-sent="${p.already_sent ? 1 : 0}">${p.already_sent ? "đã báo" : "chưa báo"}</span>
            <span class="pd-days">${fmtDays(p.days_left)}</span>
          </div>`).join("")
        : '<p class="muted">Chưa có tên miền nào chạm ngưỡng cảnh báo. Tất cả đang an toàn.</p>';
    } catch (e) {
      box.innerHTML = `<div class="err-box">${esc(e.message)}</div>`;
    }
  }

  // Nhãn cho từng kết luận zone. "khong-ban-ghi" là cái đáng đọc nhất: tên miền
  // còn hạn, zone bật, nhưng không trỏ tới đâu cả — website tắt mà không ai báo.
  // [mức, chữ đầy đủ (tooltip + trang Cài đặt), chữ ngắn (ô bảng)]
  const CF_NHAN = {
    "ok": ["ok", "Đang phục vụ", "Đang chạy"],
    "khong-ban-ghi": ["err", "Không có bản ghi nào — website tắt", "Website tắt"],
    "khong-thay": ["warn", "Không có trong tài khoản Cloudflare", "Ngoài Cloudflare"],
    "tam-dung": ["warn", "Zone đang tạm dừng", "Tạm dừng"],
    "thieu-quyen-dns": ["warn", "Token thiếu quyền Zone.DNS:Read", "Thiếu quyền"],
  };

  // Thứ tự ưu tiên khi sắp xếp cột Site — nhỏ hơn là đáng lo hơn.
  const CF_THU_TU = {
    "khong-ban-ghi": 0, "tam-dung": 1, "thieu-quyen-dns": 2,
    "khong-thay": 3, "ok": 4,
  };

  function cfNhan(ketLuan) {
    if (!ketLuan) return null;
    if (CF_NHAN[ketLuan]) return CF_NHAN[ketLuan];
    const chu = ketLuan.replace(/^zone-/, "Zone: ");
    return ["warn", chu, chu];
  }

  function veKetQuaCf(r) {
    const hop = $("#cfResult");
    const dong = Object.entries(r.theo_ket_luan || {})
      .filter(([k]) => k)
      .sort((a, b) => b[1] - a[1])
      .map(([k, n]) => {
        const [muc, chu] = cfNhan(k) || ["warn", k];
        return `<li data-muc="${esc(muc)}"><strong>${n}</strong> ${esc(chu)}</li>`;
      }).join("");
    hop.innerHTML = `<ul class="cf-tong">${dong}</ul>` + (
      (r.canh_bao || []).length
        ? `<p class="cf-canh-bao">${ICON.warn} Còn hạn nhưng không phục vụ gì: ` +
          `<strong>${r.canh_bao.map(esc).join(", ")}</strong></p>`
        : "");
    hop.hidden = false;
  }

  async function renderSettings() {
    try {
      const s = await api("/api/settings");
      state.settings = s;
      $("#setChatId").value = s.telegram_chat_id || "";
      $("#setWarn").value = s.warn_days;
      $("#setCrit").value = s.critical_days;
      $("#setCache").value = s.cache_ttl_hours;
      $("#setToken").value = "";
      $("#setToken").placeholder = s.telegram_token_set
        ? "Để trống nếu không đổi" : "123456789:AAF...";
      $("#tokenHint").textContent = s.telegram_token_set
        ? `Token đang lưu: ${s.telegram_token_masked} — để trống ô trên nếu không muốn thay đổi.`
        : "Chưa có token. Làm theo 3 bước ở trên rồi dán vào đây.";
      $("#cronCmd").textContent = s.cron_command || "";
      updateNotifyBadge(s.notify_configured, s.last_notify);

      $("#cfToken").value = "";
      $("#cfToken").placeholder = s.cloudflare_token_set
        ? "Để trống nếu không đổi" : "Dán API token đọc-chỉ vào đây";
      // Token Cloudflare là chuỗi đối không có cấu trúc công khai nào, nên
      // không hé lộ ký tự nào cả — khác token Telegram còn khoe được bot id.
      $("#cfHint").textContent = s.cloudflare_token_set
        ? "Token đang lưu. Để trống ô trên nếu không muốn thay đổi."
        : "Chưa có token. Làm theo 3 bước ở trên rồi dán vào đây.";
      const cfB = $("#cfBadge");
      cfB.dataset.state = s.cloudflare_token_set ? "on" : "off";
      cfB.textContent = s.cloudflare_token_set
        ? (s.last_cf_sync ? "Đồng bộ " + relTime(s.last_cf_sync) : "Đã có token")
        : "Chưa kết nối";
    } catch (e) {
      toast("Không tải được cài đặt: " + e.message, "err");
    }
    renderPending();
  }

  async function saveSettings(body, okMsg) {
    try {
      const s = await api("/api/settings", { method: "POST", body });
      state.settings = s;
      updateNotifyBadge(s.notify_configured, s.last_notify);
      toast(okMsg, "ok");
      return s;
    } catch (e) {
      toast("Lỗi: " + e.message, "err");
      return null;
    }
  }

  function bindSettings() {
    $("#btnSaveNotify").addEventListener("click", async () => {
      const token = $("#setToken").value.trim();
      const chat = $("#setChatId").value.trim();
      if (!token && !state.settings?.telegram_token_set) {
        toast("Cần dán bot token trước.", "err");
        return;
      }
      if (!chat) { toast("Cần nhập chat id.", "err"); return; }
      const s = await saveSettings({ telegram_bot_token: token, telegram_chat_id: chat },
        "Đã lưu cấu hình Telegram.");
      if (s) renderSettings();
    });

    $("#btnSaveCf").addEventListener("click", async () => {
      const token = $("#cfToken").value.trim();
      if (!token) { toast("Dán API token trước đã.", "err"); return; }
      const s = await saveSettings({ cloudflare_api_token: token }, "Đã lưu token Cloudflare.");
      if (s) renderSettings();
    });

    $("#btnVerifyCf").addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      try {
        const r = await api("/api/cloudflare/verify", { method: "POST", body: {} });
        toast(`Token hợp lệ (trạng thái: ${r.status}).`, "ok");
      } catch (err) {
        toast("Lỗi: " + err.message, "err");
      } finally { btn.disabled = false; }
    });

    $("#btnSyncCf").addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      btn.textContent = "Đang đọc...";
      try {
        const r = await api("/api/cloudflare/sync", { method: "POST", body: {} });
        veKetQuaCf(r);
        toast(`Đọc được ${r.zone_doc_duoc} zone trên ${r.tong} tên miền.`, "ok");
        await load();
        renderSettings();
      } catch (err) {
        toast("Lỗi: " + err.message, "err");
      } finally {
        btn.disabled = false;
        btn.textContent = "Đồng bộ ngay";
      }
    });

    $("#btnTestNotify").addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true;
      btn.textContent = "Đang gửi...";
      try {
        const r = await api("/api/notify/test", { method: "POST", body: {} });
        toast(`Đã gửi tin nhắn thử qua @${r.bot}. Mở Telegram để kiểm tra.`, "ok");
        renderSettings();
      } catch (err) {
        toast("Không gửi được: " + err.message, "err");
      } finally {
        btn.disabled = false;
        btn.textContent = "Gửi tin nhắn thử";
      }
    });

    $("#btnSaveThresholds").addEventListener("click", async () => {
      const s = await saveSettings({
        warn_days: $("#setWarn").value,
        critical_days: $("#setCrit").value,
        cache_ttl_hours: $("#setCache").value,
      }, "Đã lưu ngưỡng cảnh báo.");
      if (s) { await load(); renderPending(); }
    });

    const sendAlerts = async (force, btn) => {
      btn.disabled = true;
      try {
        const r = await api("/api/notify/send", { method: "POST", body: { force } });
        if (r.sent === 0) toast("Không có cảnh báo mới nào để gửi.");
        else toast(`Đã gửi cảnh báo cho ${r.sent} tên miền.`, "ok");
        renderSettings();
      } catch (err) {
        toast("Không gửi được: " + err.message, "err");
      } finally {
        btn.disabled = false;
      }
    };
    $("#btnSendNotify").addEventListener("click", (e) => sendAlerts(false, e.currentTarget));
    $("#btnForceNotify").addEventListener("click", (e) => sendAlerts(true, e.currentTarget));

    $("#btnResetNotify").addEventListener("click", async () => {
      if (!confirm("Xoá lịch sử chống trùng? Lần gửi tới sẽ báo lại từ đầu.")) return;
      try {
        const r = await api("/api/notify/reset", { method: "POST", body: {} });
        toast(`Đã xoá ${r.removed} bản ghi.`, "ok");
        renderPending();
      } catch (e) { toast("Lỗi: " + e.message, "err"); }
    });

    $("#btnCopyCron").addEventListener("click", async (e) => {
      const text = $("#cronCmd").textContent;
      try {
        await navigator.clipboard.writeText(text);
        toast("Đã sao chép lệnh.", "ok");
      } catch {
        // clipboard API cần HTTPS hoặc localhost — bôi đen sẵn để người dùng tự Ctrl+C
        const range = document.createRange();
        range.selectNodeContents($("#cronCmd"));
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        toast("Đã bôi đen lệnh, bấm Ctrl+C để sao chép.");
      }
      void e;
    });
  }

  /* ====================== sidebar thu gọn ====================== */
  const COLLAPSE_KEY = "dg.sidebarCollapsed";

  function setCollapsed(on, persist = true) {
    document.body.classList.toggle("sidebar-collapsed", on);
    const btn = $("#btnCollapse");
    if (btn) btn.title = (on ? "Mở rộng" : "Thu gọn") + " thanh bên (Ctrl+B)";
    if (persist) {
      try { localStorage.setItem(COLLAPSE_KEY, on ? "1" : "0"); } catch { /* chế độ riêng tư */ }
    }
  }

  function initCollapsed() {
    let on = false;
    try { on = localStorage.getItem(COLLAPSE_KEY) === "1"; } catch { /* bỏ qua */ }
    setCollapsed(on, false);
  }

  /* ===================== giao diện sáng / tối ===================== */
  const THEME_KEY = "dg.theme";
  const DARK_QUERY = window.matchMedia("(prefers-color-scheme: dark)");
  const THEME_PREFS = ["light", "dark", "system"];

  // "system" chỉ là ý định của người dùng; thứ ghi vào DOM luôn là màu cụ thể
  const resolveTheme = (pref) =>
    pref === "system" ? (DARK_QUERY.matches ? "dark" : "light") : pref;

  function applyTheme(pref, persist = true) {
    state.themePref = THEME_PREFS.includes(pref) ? pref : "dark";
    const actual = resolveTheme(state.themePref);
    document.documentElement.dataset.theme = actual;

    if (persist) {
      try { localStorage.setItem(THEME_KEY, state.themePref); } catch { /* riêng tư */ }
    }

    const nextIsDark = actual === "dark";
    $("#themeIco").innerHTML = nextIsDark ? ICON.sun : ICON.moon;
    $("#themeLabel").textContent = nextIsDark ? "Chuyển nền sáng" : "Chuyển nền tối";
    $("#btnTheme").title = nextIsDark ? "Chuyển sang nền sáng" : "Chuyển sang nền tối";
    $$("#themeSeg .seg-btn").forEach((b) =>
      b.classList.toggle("active", b.dataset.themePref === state.themePref));
  }

  function initTheme() {
    let pref = "dark";
    try { pref = localStorage.getItem(THEME_KEY) || "dark"; } catch { /* bỏ qua */ }
    applyTheme(pref, false);
    // Đang để "theo hệ thống" thì bám theo Windows khi người dùng đổi
    DARK_QUERY.addEventListener("change", () => {
      if (state.themePref === "system") applyTheme("system", false);
    });
  }

  /* ============ ngăn kéo menu ở khổ hẹp (≤900px) ============ */
  const NARROW = window.matchMedia("(max-width: 900px)");

  function setNavOpen(on) {
    document.body.classList.toggle("nav-open", on);
    $("#navScrim").hidden = !on;
    // Khoá cuộn nền khi ngăn kéo đang mở, tránh cuộn nhầm trang phía dưới
    document.documentElement.style.overflow = on ? "hidden" : "";
  }

  const isNavOpen = () => document.body.classList.contains("nav-open");

  function initPageSize() {
    let saved = 25;
    try { saved = Number(localStorage.getItem(PAGESIZE_KEY)); } catch { /* bỏ qua */ }
    const allowed = [0, 10, 25, 50, 100];
    state.pageSize = allowed.includes(saved) ? saved : 25;
    $("#pageSize").value = String(state.pageSize);
  }

  /* ============================== views ============================== */
  const TITLES = {
    overview: "Tên miền",
    expiring: "Sắp hết hạn",
    providers: "Theo nhà cung cấp",
    lookup: "Tra cứu tên miền",
    registry: "Danh bạ nhà đăng ký",
    settings: "Cài đặt & cảnh báo",
  };

  function setView(view) {
    state.view = view;
    setNavOpen(false);          // chọn xong thì đóng ngăn kéo trên điện thoại
    resetPage();
    $("#pageTitle").textContent = TITLES[view] || "Tên miền";
    $("#pageIcon").innerHTML = ICON[VIEW_ICON[view]] || ICON.globe;

    // Nút xuất CSV bám theo trang đang mở, để không xuất nhầm bảng
    const onRegistry = view === "registry";
    $("#btnExport").querySelector(".btn-label").textContent =
      onRegistry ? "Xuất danh bạ" : "Xuất";
    $("#btnExport").title = onRegistry
      ? "Xuất danh bạ nhà đăng ký đang lọc ra CSV"
      : "Xuất danh sách tên miền (Excel, PDF, Markdown, CSV)";
    $$(".nav-item").forEach((a) => a.classList.toggle("active", a.dataset.view === view));

    const isTable = ["overview", "expiring", "providers"].includes(view);
    $("#view-table").hidden = !isTable;
    $("#view-lookup").hidden = view !== "lookup";
    $("#view-registry").hidden = view !== "registry";
    $("#view-settings").hidden = view !== "settings";

    if (view === "registry") renderRegistry();
    if (view === "settings") renderSettings();
    if (isTable) renderTable();
  }

  /* ============================== modal ============================== */
  // Những trường registry tự trả về. Liệt kê ra để người dùng khỏi ngồi điền
  // tay những thứ mà tra cứu sẽ ghi đè ngay sau đó vài giây.
  const RDAP_TU_LAY = [
    "ngày hết hạn", "ngày đăng ký", "registrar", "nameserver",
    "khoá chuyển nhượng", "DNSSEC",
  ];

  function openModal() {
    const providers = Array.from(new Set(
      state.domains.map((d) => d.provider || d.registrar).filter(Boolean)));
    $("#providerList").innerHTML = providers.map((p) => `<option value="${esc(p)}">`).join("");
    $("#addDomains").value = "";
    $("#addProvider").value = "";
    $("#addTags").value = "";
    $("#addExpires").value = "";
    $("#addPinned").checked = false;

    const cuoi = RDAP_TU_LAY[RDAP_TU_LAY.length - 1];
    $("#autoNote").innerHTML = ICON.check +
      `<span><strong>Chỉ cần nhập tên miền.</strong> ` +
      `${RDAP_TU_LAY.slice(0, -1).join(", ")} và ${cuoi} đều do hệ thống tự đọc ` +
      `từ registry qua RDAP/WHOIS ngay sau khi thêm.</span>`;
    $("#addMoreIco").innerHTML = ICON.chevron;
    $("#addMore").open = false;      // luôn mở modal ở trạng thái gọn nhất

    $("#modalScrim").hidden = false;
    setTimeout(() => $("#addDomains").focus(), 30);
  }

  const closeModal = () => { $("#modalScrim").hidden = true; };

  async function submitModal() {
    const raw = $("#addDomains").value.trim();
    if (!raw) { toast("Nhập ít nhất một tên miền.", "err"); return; }
    const body = {
      domain: raw,
      provider: $("#addProvider").value.trim(),
      tags: $("#addTags").value.split(",").map((t) => t.trim()).filter(Boolean),
      expires_at: $("#addExpires").value || null,
      pinned: $("#addPinned").checked,
      lookup: true,
    };
    try {
      const res = await api("/api/domains", { method: "POST", body });
      closeModal();
      toast(`Đã thêm ${res.added.length} tên miền, đang tra cứu...`, "ok");
      await load();
      pollRefresh();
    } catch (e) {
      toast("Lỗi: " + e.message, "err");
    }
  }

  /* ============================== events ============================== */
  function bind() {
    $$(".nav-item").forEach((a) =>
      a.addEventListener("click", (e) => { e.preventDefault(); setView(a.dataset.view); }));

    $("#tableSearch").addEventListener("input", (e) => {
      state.query = e.target.value; resetPage(); renderTable();
    });
    $("#quickSearch").addEventListener("input", (e) => {
      state.query = e.target.value;
      resetPage();
      $("#tableSearch").value = e.target.value;
      if (!["overview", "expiring", "providers"].includes(state.view)) setView("overview");
      renderTable();
    });

    // Khi thu gọn, ô tìm nhanh chỉ còn cái icon: bấm vào thì mở lại sidebar rồi focus
    $(".quick-search").addEventListener("click", () => {
      if (document.body.classList.contains("sidebar-collapsed")) {
        setCollapsed(false);
        setTimeout(() => $("#quickSearch").focus(), 180);
      }
    });

    $("#btnCollapse").addEventListener("click", () =>
      setCollapsed(!document.body.classList.contains("sidebar-collapsed")));

    // Nút ở chân sidebar: bấm là đổi hẳn sang màu ngược lại (thoát chế độ "theo hệ thống")
    $("#btnTheme").addEventListener("click", () =>
      applyTheme(resolveTheme(state.themePref) === "dark" ? "light" : "dark"));

    $("#themeSeg").addEventListener("click", (e) => {
      const btn = e.target.closest(".seg-btn");
      if (btn) applyTheme(btn.dataset.themePref);
    });

    $("#btnNavToggle").addEventListener("click", () => setNavOpen(!isNavOpen()));
    $("#navScrim").addEventListener("click", () => setNavOpen(false));
    // Xoay ngang / đổi cỡ cửa sổ: đóng ngăn kéo để không kẹt lại ở khổ rộng
    NARROW.addEventListener("change", () => setNavOpen(false));

    $("#filterChips").addEventListener("click", (e) => {
      const chip = e.target.closest(".chip");
      if (!chip) return;
      $$("#filterChips .chip").forEach((c) => c.classList.toggle("active", c === chip));
      state.filter = chip.dataset.filter;
      resetPage();
      renderTable();
    });

    $$("th.sortable").forEach((th) => th.addEventListener("click", () => {
      const key = th.dataset.sort;
      state.sort = { key, dir: state.sort.key === key ? -state.sort.dir : 1 };
      resetPage();
      renderTable();
    }));

    $("#btnRefresh").addEventListener("click", (e) => startRefresh(null, e.shiftKey));
    $("#btnAdd").addEventListener("click", openModal);
    $("#btnExport").addEventListener("click", (e) => {
      toggleExportMenu(e.currentTarget.parentElement,
        state.view === "registry" ? REGISTRY_FORMATS : EXPORT_FORMATS);
    });
    $("#registrySearch").addEventListener("input", () => {
      // Từ khoá đổi thì bỏ ghi nhớ đóng/mở, để đoạn khớp từ khoá mới mở sẵn.
      state.regOpen = {};
      if (state.registrars) drawRegistry();
    });

    // "toggle" không nổi bọt nên phải bắt ở pha capture; nhớ lại để lần vẽ sau
    // (gõ tìm kiếm, đổi khu vực) không đóng sập những đoạn đang mở.
    $("#registryContent").addEventListener("toggle", (e) => {
      const id = e.target.dataset && e.target.dataset.fold;
      if (!id) return;
      state.regOpen[id] = e.target.open;
      syncFoldAll();   // đóng tay một đoạn thì nút phải quay về "Mở tất cả"
    }, true);

    $("#btnFoldAll").addEventListener("click", toggleAllFolds);

    $("#regionChips").addEventListener("click", (e) => {
      const chip = e.target.closest(".chip");
      if (!chip) return;
      state.registryRegion = chip.dataset.region;
      drawRegistry();
    });

    $("#btnLookup").addEventListener("click", doLookup);
    $("#lookupInput").addEventListener("keydown", (e) => { if (e.key === "Enter") doLookup(); });

    $("#modalClose").addEventListener("click", closeModal);
    $("#modalCancel").addEventListener("click", closeModal);
    $("#modalSave").addEventListener("click", submitModal);
    $("#modalScrim").addEventListener("click", (e) => { if (e.target.id === "modalScrim") closeModal(); });

    $("#drawerClose").addEventListener("click", closeDrawer);
    $("#drawerScrim").addEventListener("click", closeDrawer);

    $("#pageSize").addEventListener("change", (e) => {
      state.pageSize = Number(e.target.value) || 0;
      try { localStorage.setItem(PAGESIZE_KEY, String(state.pageSize)); } catch { /* riêng tư */ }
      resetPage();
      renderTable();
    });

    $("#pagerNav").addEventListener("click", (e) => {
      const btn = e.target.closest(".pg-btn");
      if (!btn || btn.disabled) return;
      state.page = Number(btn.dataset.page);
      renderTable();
      $(".search-hero").scrollIntoView({ behavior: "smooth", block: "nearest" });
    });

    $("#viewToggle").addEventListener("click", (e) => {
      const btn = e.target.closest(".vt-btn");
      if (btn) setLayout(btn.dataset.layout);
    });

    // Hành động trong bảng và trong lưới dùng chung một handler
    $("#view-table").addEventListener("click", (e) => {
      const tr = e.target.closest("[data-domain]");
      if (!tr) return;
      const domain = tr.dataset.domain;
      const act = e.target.closest("[data-act]")?.dataset.act;

      if (act === "detail") { openDrawer(domain); return; }
      if (act === "setprovider") { promptProvider(domain); return; }
      if (act === "menu") { toggleMenu(e.target.closest(".row-menu"), domain); return; }
    });

    // Hành động trong drawer
    $("#drawerBody").addEventListener("click", async (e) => {
      const act = e.target.closest("[data-dact]")?.dataset.dact;
      if (!act) return;
      const domain = $("#drawer").dataset.domain;

      if (act === "save") {
        await patchDomain(domain, { note: $("#drawerNote").value }, "Đã lưu ghi chú.");
      } else if (act === "provider") {
        promptProvider(domain);
      } else if (act === "tags") {
        promptTags(domain);
      } else if (act === "expires") {
        promptExpires(domain);
      } else if (act === "refresh") {
        closeDrawer();
        startRefresh([domain], true);
      } else if (act === "delete") {
        if (confirm(`Xoá ${domain} khỏi kho quản lý?`)) {
          await api(`/api/domains/${encodeURIComponent(domain)}`, { method: "DELETE" });
          closeDrawer();
          await load();
          toast(`Đã xoá ${domain}.`, "ok");
        }
      }
    });

    document.addEventListener("click", (e) => {
      if (state.openMenu && !e.target.closest(".row-menu, .export-wrap")) {
        state.openMenu.remove();
        state.openMenu = null;
        $("#btnExport").setAttribute("aria-expanded", "false");
      }
    });

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { closeModal(); closeDrawer(); setNavOpen(false); }
      if (!(e.ctrlKey || e.metaKey)) return;
      const key = e.key.toLowerCase();
      if (key === "k") {
        e.preventDefault();
        setCollapsed(false);
        $("#quickSearch").focus();
      } else if (key === "b") {
        e.preventDefault();
        setCollapsed(!document.body.classList.contains("sidebar-collapsed"));
      }
    });

    window.addEventListener("hashchange", () => {
      const v = location.hash.slice(1);
      if (TITLES[v]) setView(v);
    });
  }

  function promptProvider(domain) {
    const cur = state.domains.find((d) => d.domain === domain);
    const val = prompt(`Bạn mua ${domain} ở đâu?`, cur?.provider || cur?.registrar || "");
    if (val !== null) patchDomain(domain, { provider: val.trim() }, "Đã cập nhật nhà cung cấp.");
  }

  function promptTags(domain) {
    const cur = state.domains.find((d) => d.domain === domain);
    const val = prompt(`Tag cho ${domain} (cách nhau bằng dấu phẩy):`, (cur?.tags || []).join(", "));
    if (val !== null) {
      patchDomain(domain, { tags: val.split(",").map((t) => t.trim()).filter(Boolean) }, "Đã cập nhật tag.");
    }
  }

  function promptExpires(domain) {
    const cur = state.domains.find((d) => d.domain === domain);
    const val = prompt(
      `Ngày hết hạn nhập tay cho ${domain} (YYYY-MM-DD, để trống để xoá):`,
      (cur?.expires_at || "").slice(0, 10));
    if (val === null) return;
    patchDomain(domain, { expires_at: val.trim() || null, pinned: !!val.trim() },
      "Đã đặt ngày hết hạn thủ công.");
  }

  function toggleMenu(host, domain) {
    if (state.openMenu) { state.openMenu.remove(); state.openMenu = null; }
    const r = state.domains.find((d) => d.domain === domain);
    const pop = document.createElement("div");
    pop.className = "menu-pop";
    pop.innerHTML = `
      <button data-m="detail">Xem chi tiết</button>
      <button data-m="refresh">Tra cứu lại ngay</button>
      <button data-m="provider">Đổi nhà cung cấp</button>
      <button data-m="tags">Sửa tag</button>
      <button data-m="expires">Đặt ngày hết hạn tay</button>
      <hr>
      <button data-m="site">Mở website</button>
      <button data-m="whois">Mở WHOIS Namecheap</button>
      <hr>
      <button class="danger" data-m="delete">Xoá khỏi kho</button>`;
    host.appendChild(pop);
    nan_menu(pop);
    state.openMenu = pop;

    pop.addEventListener("click", async (e) => {
      const m = e.target.closest("[data-m]")?.dataset.m;
      if (!m) return;
      pop.remove();
      state.openMenu = null;

      if (m === "detail") openDrawer(domain);
      else if (m === "refresh") startRefresh([domain], true);
      else if (m === "provider") promptProvider(domain);
      else if (m === "tags") promptTags(domain);
      else if (m === "expires") promptExpires(domain);
      else if (m === "site") window.open(`https://${domain}`, "_blank", "noopener");
      else if (m === "whois") {
        window.open(`https://www.namecheap.com/domains/whois/result?domain=${encodeURIComponent(domain)}`,
          "_blank", "noopener");
      } else if (m === "delete") {
        if (confirm(`Xoá ${domain} khỏi kho quản lý?`)) {
          await api(`/api/domains/${encodeURIComponent(domain)}`, { method: "DELETE" });
          await load();
          toast(`Đã xoá ${domain}.`, "ok");
        }
      }
      void r;
    });
  }

  /* ============================== khởi động ============================== */
  initTheme();
  initCollapsed();
  initLayout();
  initPageSize();
  bind();
  bindSettings();
  const initial = location.hash.slice(1);
  setView(TITLES[initial] ? initial : "overview");
  load().catch((e) => toast("Không tải được dữ liệu: " + e.message, "err"));
  setInterval(renderSidebar, 60000);
})();
