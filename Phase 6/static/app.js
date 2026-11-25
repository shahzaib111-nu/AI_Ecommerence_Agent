/* Saira Fashion House — storefront, AI chat and staff console.
   Vanilla JS + Tailwind (CDN). Talks to the FastAPI backend in app.py. */
"use strict";

/* ============================== utils ============================== */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (n) => "Rs " + Number(n || 0).toLocaleString("en-PK");
const ls = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { localStorage.setItem(k, JSON.stringify(v)); },
  del(k) { localStorage.removeItem(k); },
};
const debounce = (fn, ms = 250) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

async function api(path, { method, body } = {}) {
  const headers = { "Content-Type": "application/json" };
  const tok = ls.get("sfh_token", null);
  if (tok) headers.Authorization = "Bearer " + tok;
  const res = await fetch(path, { method: method || (body !== undefined ? "POST" : "GET"), headers, body: body !== undefined ? JSON.stringify(body) : undefined });
  let data = null;
  try { data = await res.json(); } catch { data = null; }
  if (!res.ok) {
    const msg = (data && (data.error || data.detail)) || `Request failed (${res.status})`;
    const e = new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    e.status = res.status; e.data = data;
    if (res.status === 401 && tok && path.startsWith("/api/") && !path.startsWith("/api/auth")) { /* stale token */ }
    throw e;
  }
  return data;
}

function toast(msg, kind = "ok") {
  const colors = { ok: "bg-brand-700", err: "bg-rose-600", info: "bg-slate-800" };
  const el = document.createElement("div");
  el.className = `pointer-events-auto animate-pop rounded-xl px-4 py-3 text-sm font-semibold text-white shadow-soft ${colors[kind] || colors.ok}`;
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; el.style.transition = "opacity .3s"; }, 3200);
  setTimeout(() => el.remove(), 3600);
}

/* ============================== product art ============================== */
const COLOR_HEX = {
  Black: "#1f2328", White: "#f4f4f2", Pink: "#f2a7c3", Blue: "#3d7fd6", Navy: "#1f3264", "Dark Blue": "#1d3f7a",
  "Sky Blue": "#86c5ee", "Light Blue": "#a9cfee", Green: "#3d9b5f", Emerald: "#118a68", Red: "#d23b3b", Maroon: "#7b1f32",
  Yellow: "#f2cf4a", Brown: "#7a5236", Grey: "#8b9099", Beige: "#dcc7a4", Cream: "#f1e6cf", Gold: "#c9a43c",
  Mustard: "#d0a22a", Olive: "#6f7a3a", Peach: "#f6b799", Lilac: "#c3a6dc", Mint: "#a7e0c8", Teal: "#1d8a8a",
  Burgundy: "#6d1630", Wine: "#722f37", Camel: "#c19a6b", Charcoal: "#3b3f45", Ivory: "#f6f1e3", Khaki: "#b9a77a", Tan: "#c69c6d",
};
const hex = (c) => COLOR_HEX[c] || "#0a7075";
function tint(h, amt = 0.85) {
  const n = parseInt(h.slice(1), 16), r = n >> 16, g = (n >> 8) & 255, b = n & 255;
  const m = (x) => Math.round(x + (255 - x) * amt);
  return `rgb(${m(r)},${m(g)},${m(b)})`;
}
function shade(h, amt = 0.25) {
  const n = parseInt(h.slice(1), 16), r = n >> 16, g = (n >> 8) & 255, b = n & 255;
  const m = (x) => Math.round(x * (1 - amt));
  return `rgb(${m(r)},${m(g)},${m(b)})`;
}
const SHAPES = {
  tunic: '<path d="M42 14 60 22 78 14l22 14-8 20-8-4 2 84H34l2-84-8 4-8-20z"/><path d="M60 22v34" class="ln"/>',
  tee: '<path d="M40 22 60 28 80 22l24 16-10 18-10-6v70H36V50l-10 6-10-18z"/>',
  dress: '<path d="M46 14 60 20 74 14l8 16-6 20 20 78H24l20-78-6-20z"/><path d="M44 50h32" class="ln"/>',
  frock: '<path d="M48 20 60 26l12-6 6 16-6 14 26 62H22l26-62-6-14z"/><path d="M48 50h24" class="ln"/>',
  pants: '<path d="M38 14h44l8 114H66l-6-74-6 74H30z"/><path d="M38 24h44" class="ln"/>',
  abaya: '<path d="M48 10 60 16l12-6 16 20-4 14 12 88H24l12-88-4-14z"/><path d="M60 16v116" class="ln"/>',
  scarf: '<path d="M30 30q30-20 60 0l6 80q-16 10-22-10l-4-50q-10 8-20 0l-4 50q-6 20-22 10z"/>',
  hoodie: '<path d="M44 18q16-14 32 0l24 14-8 24-8-6v72H36V50l-8 6-8-24z"/><path d="M48 92h24v14H48z" class="ln"/>',
  vest: '<path d="M40 16h14l6 34 6-34h14l8 14-2 94H34l-2-94z"/><circle cx="60" cy="70" r="2.5" class="dot"/><circle cx="60" cy="86" r="2.5" class="dot"/>',
  shoe: '<path d="M14 92q2-18 20-20l24-2q14-12 26-4l22 18q6 12-4 16l-82 2q-8-2-6-10z"/><path d="M20 98h84" class="ln"/>',
  bag: '<path d="M44 52q0-26 16-26t16 26" class="ln" fill="none" stroke-width="5"/><path d="M26 52h68l6 72H20z"/>',
  belt: '<path d="M8 60h104v20H8z"/><rect x="48" y="55" width="22" height="30" rx="3" class="ln" fill="none" stroke-width="4"/>',
  fabric: '<rect x="22" y="40" width="76" height="22" rx="6"/><rect x="18" y="64" width="84" height="22" rx="6"/><rect x="24" y="88" width="72" height="22" rx="6"/>',
  shalwar: '<path d="M42 10 60 18l18-8 22 14-8 18-8-4 2 54H34l2-54-8 4-8-18z"/><path d="M40 84h40l6 46H66l-6-28-6 28H34z"/>',
};
function shapeFor(name = "", category = "") {
  const n = name.toLowerCase(), c = category.toLowerCase();
  if (/khussa|chappal|sandal/.test(n)) return "shoe";
  if (/bag|tote/.test(n)) return "bag";
  if (/belt/.test(n)) return "belt";
  if (/unstitched/.test(n) || /unstitched/.test(c)) return "fabric";
  if (/hijab|dupatta|shawl/.test(n)) return "scarf";
  if (/abaya/.test(n)) return "abaya";
  if (/hood|sweater|sweatshirt|cardigan/.test(n)) return "hoodie";
  if (/waistcoat/.test(n)) return "vest";
  if (/frock/.test(n)) return "frock";
  if (/shalwar/.test(n)) return "shalwar";
  if (/t-shirt|polo/.test(n)) return "tee";
  if (/trouser|pants|jeans|chino|palazzo/.test(n)) return "pants";
  if (/suit|piece|co-ord|party|stitched|organza|velvet/.test(n) || c === "women stitched") return "dress";
  return "tunic";
}
function art(p, color, cls = "") {
  const col = color || (p.colors && p.colors[0]) || p.color || "Navy";
  const h = hex(col), shp = shapeFor(p.name, p.category);
  const light = ["White", "Ivory", "Cream", "Beige", "Mint", "Yellow", "Peach", "Light Blue", "Sky Blue", "Lilac"].includes(col);
  const stroke = light ? "rgba(0,0,0,.18)" : "rgba(255,255,255,.18)";
  return `<svg viewBox="0 0 120 140" class="${cls}" role="img" aria-label="${esc(p.name)} in ${esc(col)}">
    <defs><linearGradient id="g${shp}${h.slice(1)}" x1="0" x2="1" y1="0" y2="1"><stop offset="0" stop-color="${tint(h, 0.82)}"/><stop offset="1" stop-color="${tint(h, 0.66)}"/></linearGradient></defs>
    <rect width="120" height="140" fill="url(#g${shp}${h.slice(1)})"/>
    <ellipse cx="60" cy="132" rx="40" ry="5" fill="rgba(0,0,0,.08)"/>
    <g fill="${h}" stroke="${shade(h, 0.3)}" stroke-width="1.2" stroke-linejoin="round">${SHAPES[shp].replace(/class="ln"/g, `stroke="${stroke}" stroke-width="2" fill="none"`).replace(/class="dot"/g, `fill="${stroke}" stroke="none"`)}</g>
  </svg>`;
}

/* ============================== state ============================== */
const S = {
  user: ls.get("sfh_user", null),
  cart: ls.get("sfh_cart", []),
  status: null,
  sid: ls.get("sfh_sid", null) || newSid(),
  trace: ls.get("sfh_trace", false),
  lastMsgId: 0,
  chatLoaded: false,
  categories: [],
};
function newSid() { const s = "web-" + Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4); ls.set("sfh_sid", s); return s; }
const CITIES = ["Lahore", "Gujranwala", "Sialkot", "Sheikhupura", "Faisalabad", "Islamabad", "Rawalpindi", "Karachi", "Peshawar", "Multan", "Quetta", "Bahawalpur", "Hyderabad", "Sargodha", "Gujrat", "Sukkur", "Larkana", "Abbottabad", "Mardan", "Kasur", "Okara", "Sahiwal", "Jhang", "Rahim Yar Khan", "Dera Ghazi Khan", "Jhelum", "Wazirabad", "Mirpur", "Muzaffarabad"];

/* ============================== router ============================== */
function parseHash() {
  const h = location.hash.replace(/^#/, "") || "/";
  const [path, qs] = h.split("?");
  return { path: path || "/", params: new URLSearchParams(qs || "") };
}
const ROUTES = { "/": viewHome, "/shop": viewShop, "/track": viewTrack, "/account": viewAccount, "/admin": viewAdmin };
async function route() {
  const { path, params } = parseHash();
  const fn = ROUTES[path] || viewHome;
  $$("[data-nav]").forEach((a) => a.classList.toggle("navlink-on", a.dataset.nav === (path.slice(1) || "home")));
  $("#chatFab").classList.toggle("hidden", path === "/admin");
  $("#mobileNav").classList.add("hidden");
  window.scrollTo({ top: 0 });
  try { await fn(params); } catch (e) { $("#view").innerHTML = errorBox(e.message); }
}
window.addEventListener("hashchange", route);
const errorBox = (m) => `<div class="mx-auto max-w-xl px-4 py-16 text-center"><div class="text-4xl">⚠️</div><p class="mt-3 text-slate-600 dark:text-slate-300">${esc(m)}</p><a href="#/" class="btn-primary mt-4">Go home</a></div>`;

/* ============================== product cards ============================== */
function priceHtml(p, big = false) {
  const sz = big ? "text-2xl" : "text-base";
  return `<span class="font-display ${sz} font-bold text-brand-800 dark:text-brand-300">${money(p.price)}</span>` +
    (p.is_sale && p.original_price > p.price ? ` <span class="text-xs text-slate-400 line-through">${money(p.original_price)}</span>` : "");
}
function stockBadge(av) {
  if (av <= 0) return `<span class="pill bg-rose-100 text-rose-700 dark:bg-rose-950 dark:text-rose-300">Out of stock</span>`;
  if (av <= 10) return `<span class="pill bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300">Only ${av} left</span>`;
  return `<span class="pill bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300">In stock</span>`;
}
function productCard(p) {
  const firstCol = p.colors?.[0];
  return `<article class="group card flex flex-col overflow-hidden transition hover:-translate-y-0.5 hover:shadow-soft">
    <button class="relative block aspect-[6/7] overflow-hidden" data-open="${esc(p.product_id)}" aria-label="View ${esc(p.name)}">
      ${art(p, firstCol, "h-full w-full transition duration-300 group-hover:scale-105")}
      ${p.is_sale ? `<span class="absolute left-3 top-3 pill bg-rose-500 text-white">SALE</span>` : ""}
    </button>
    <div class="flex flex-1 flex-col gap-1.5 p-4">
      <div class="text-[11px] font-bold uppercase tracking-wide text-slate-400">${esc(p.category)}</div>
      <button class="text-left font-display font-semibold leading-snug hover:text-brand-700 dark:hover:text-brand-300" data-open="${esc(p.product_id)}">${esc(p.name)}</button>
      <div>${priceHtml(p)}</div>
      <div class="flex items-center gap-1">${(p.colors || []).slice(0, 6).map((c) => `<span title="${esc(c)}" class="h-3.5 w-3.5 rounded-full ring-1 ring-black/10" style="background:${hex(c)}"></span>`).join("")}<span class="ml-auto">${stockBadge(p.available)}</span></div>
      <div class="mt-auto flex gap-2 pt-2">
        <button class="btn-primary btn-sm flex-1" data-open="${esc(p.product_id)}">Choose options</button>
        <button class="btn-ghost btn-sm" data-ask="Tell me about the ${esc(p.name)}" title="Ask the assistant">Ask</button>
      </div>
    </div>
  </article>`;
}

/* ============================== HOME ============================== */
const CAT_ART = {
  Accessories: ["Handbag Tote", "Tan"], Footwear: ["Khussa", "Maroon"], Kids: ["Kids Frock", "Pink"],
  "Men Kurta": ["Kurta", "Navy"], "Men Shalwar Kameez": ["Kameez Shalwar", "Cream"], "Men Unstitched": ["Unstitched", "Grey"],
  "Men Waistcoat": ["Waistcoat", "Black"], "Men Western": ["Polo T-Shirt", "Olive"], "Men Winter": ["Zip Hoodie", "Charcoal"],
  "Women Accessories": ["Chiffon Dupatta", "Mint"], "Women Bottoms": ["Palazzo Pants", "Beige"], "Women Kurti": ["Kurti", "Mustard"],
  "Women Modest": ["Abaya", "Brown"], "Women Stitched": ["Lawn 3-Piece Stitched", "Lilac"], "Women Unstitched": ["Unstitched", "Teal"],
  "Women Winter": ["Pashmina Shawl", "Camel"],
};
async function viewHome() {
  const [cats, sale, rev] = await Promise.all([api("/api/categories"), api("/api/products?sale=true&in_stock=true&limit=8"), api("/api/reviews").catch(() => null)]);
  S.categories = cats.categories;
  const feat = await api("/api/products?sort=stock&limit=4");
  $("#view").innerHTML = `
  <section class="relative overflow-hidden bg-gradient-to-br from-brand-950 via-brand-900 to-brand-700 text-white">
    <div class="absolute -right-24 -top-24 h-96 w-96 rounded-full bg-brand-500/20 blur-3xl"></div>
    <div class="relative mx-auto grid max-w-7xl items-center gap-10 px-4 py-14 md:grid-cols-2 md:py-20">
      <div>
        <span class="pill bg-white/10 text-brand-100 ring-1 ring-white/20">New · AI shopping assistant in English & Roman Urdu</span>
        <h1 class="mt-4 font-display text-4xl font-bold leading-tight md:text-5xl">Pakistani fashion, delivered to your door.</h1>
        <p class="mt-4 max-w-lg text-brand-100">Kurtas, stitched suits, khussas, winter wear and more — with live stock, Cash on Delivery everywhere, and an assistant that can check sizes, place your order and track it in seconds.</p>
        <div class="mt-7 flex flex-wrap gap-3">
          <a href="#/shop" class="btn bg-white text-brand-800 hover:bg-brand-50">Shop the collection</a>
          <button class="btn ring-1 ring-white/50 hover:bg-white/10" data-chat>💬 Chat with Eman</button>
        </div>
        <div class="mt-8 grid max-w-md grid-cols-3 gap-3 text-center text-xs">
          <div class="rounded-2xl bg-white/10 p-3"><div class="font-display text-xl font-bold">${S.status?.products ?? 50}</div>products</div>
          <div class="rounded-2xl bg-white/10 p-3"><div class="font-display text-xl font-bold">COD</div>all Pakistan</div>
          <div class="rounded-2xl bg-white/10 p-3"><div class="font-display text-xl font-bold">7 days</div>exchange</div>
        </div>
      </div>
      <div class="grid grid-cols-2 gap-4">
        ${feat.items.map((p, i) => `<button data-open="${esc(p.product_id)}" class="group overflow-hidden rounded-3xl bg-white/5 ring-1 ring-white/15 ${i % 2 ? "translate-y-6" : ""}">
          ${art(p, p.colors[i % p.colors.length], "aspect-square w-full transition group-hover:scale-105")}
          <div class="flex items-center justify-between px-3 py-2 text-left text-xs"><span class="font-semibold">${esc(p.name)}</span><span class="text-brand-200">${money(p.price)}</span></div></button>`).join("")}
      </div>
    </div>
  </section>

  <section class="mx-auto max-w-7xl px-4 py-12">
    <div class="flex items-end justify-between"><h2 class="font-display text-2xl font-bold">Shop by category</h2><a href="#/shop" class="text-sm font-bold text-brand-700 hover:underline dark:text-brand-300">View all →</a></div>
    <div class="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-8">
      ${cats.categories.map((c) => `<a href="#/shop?cat=${encodeURIComponent(c.category)}" class="card group flex flex-col items-center gap-2 p-3 text-center transition hover:border-brand-400">
        <div class="h-16 w-14 overflow-hidden rounded-xl">${art({ name: CAT_ART[c.category]?.[0] || c.category, category: c.category }, CAT_ART[c.category]?.[1] || "Teal", "h-full w-full")}</div>
        <div class="text-xs font-bold leading-tight">${esc(c.category)}</div><div class="text-[11px] text-slate-500">${c.products} items · from ${money(c.min_price)}</div></a>`).join("")}
    </div>
  </section>

  ${sale.items.length ? `<section class="mx-auto max-w-7xl px-4 pb-12">
    <div class="flex items-end justify-between"><h2 class="font-display text-2xl font-bold">On sale now</h2><a href="#/shop?sale=1" class="text-sm font-bold text-brand-700 hover:underline dark:text-brand-300">All sale items →</a></div>
    <p class="mt-1 text-sm text-slate-500">Sale items are final sale and can't be exchanged.</p>
    <div class="mt-5 grid grid-cols-2 gap-4 md:grid-cols-4">${sale.items.slice(0, 4).map(productCard).join("")}</div>
  </section>` : ""}

  <section class="mx-auto max-w-7xl px-4 pb-12">
    <div class="card grid gap-8 overflow-hidden p-6 md:grid-cols-[1fr_1.2fr] md:p-10">
      <div>
        <h2 class="font-display text-2xl font-bold">Ask Eman anything</h2>
        <p class="mt-2 text-slate-500">Our assistant works from the live catalog and store policy — it never invents prices or stock. Refunds, discounts and complaints go straight to our staff with a ticket number, and you can keep shopping while you wait.</p>
        <div class="mt-5 flex flex-wrap gap-2">
          ${["Black linen kurta M mein hai?", "Women kurti under 2500", "Karachi delivery charges?", "Track order 10231", "Exchange policy kya hai?", "Winter ke liye kuch dikhao"].map((q) => `<button class="chip" data-ask="${esc(q)}">${esc(q)}</button>`).join("")}
        </div>
      </div>
      <div class="grid gap-3 sm:grid-cols-2">
        ${[["🧠", "Understands Roman Urdu", "“bhai blk kurta mdm mein hai?” works just like English."], ["📦", "Orders end-to-end", "Checks stock, reserves it for 15 minutes and confirms before placing."], ["🔒", "Private by design", "Order status only with the order number and the matching phone."], ["🤝", "Real people when needed", "Hand-offs get a ticket; staff reply right in your chat."]]
          .map(([i, t, d]) => `<div class="rounded-2xl bg-sand-100 p-4 dark:bg-slate-800"><div class="text-2xl">${i}</div><div class="mt-2 font-bold">${t}</div><div class="mt-1 text-sm text-slate-500 dark:text-slate-400">${d}</div></div>`).join("")}
      </div>
    </div>
  </section>

  ${rev && rev.count ? `<section class="mx-auto max-w-7xl px-4 pb-6"><div class="flex flex-wrap items-center gap-3 text-sm"><span class="font-display text-2xl font-bold text-amber-500">★ ${rev.average}</span><span class="text-slate-500">from ${rev.count} customer review(s)</span>
    ${(rev.recent || []).slice(0, 3).map((r) => `<span class="card px-3 py-1.5 text-xs">“${esc(r.comment)}” — ${esc(r.user_name || "Customer")}</span>`).join("")}
    <button class="text-sm font-bold text-brand-700 hover:underline dark:text-brand-300" id="rateBtn">Leave a review</button></div></section>` : ""}`;
  $("#rateBtn")?.addEventListener("click", openAbout);
}

/* ============================== SHOP ============================== */
async function viewShop(params) {
  if (!S.categories.length) S.categories = (await api("/api/categories")).categories;
  const st = {
    q: params.get("q") || "", cat: params.get("cat") || "", gender: params.get("gender") || "",
    sale: params.get("sale") === "1", stock: params.get("stock") === "1", sort: params.get("sort") || "featured",
    max: params.get("max") || "", limit: 24,
  };
  const setParam = (k, v) => {
    const p = parseHash().params;
    if (v === "" || v === false || v === null) p.delete(k); else p.set(k, v === true ? "1" : v);
    location.hash = "#/shop" + (p.toString() ? "?" + p.toString() : "");
  };
  const gtab = (g, label) => `<button class="chip ${st.gender === g ? "chip-on" : ""}" data-g="${g}">${label}</button>`;
  const cats = S.categories.filter((c) => !st.gender || c.category.toLowerCase().startsWith(st.gender));
  $("#view").innerHTML = `
  <div class="mx-auto max-w-7xl px-4 py-8">
    <div class="flex flex-wrap items-end gap-3">
      <div><h1 class="font-display text-3xl font-bold">${esc(st.cat || (st.sale ? "Sale" : st.gender ? st.gender[0].toUpperCase() + st.gender.slice(1) : "All products"))}</h1>
      <p id="shopCount" class="text-sm text-slate-500">Loading…</p></div>
      <div class="ml-auto flex flex-wrap items-center gap-2">
        <input id="shopQ" value="${esc(st.q)}" class="input w-56 rounded-full py-2" placeholder="Search…">
        <select id="shopSort" class="input w-auto rounded-full py-2">
          ${[["featured", "Featured"], ["price_asc", "Price: low to high"], ["price_desc", "Price: high to low"], ["name", "Name"], ["stock", "Most in stock"]].map(([v, l]) => `<option value="${v}" ${st.sort === v ? "selected" : ""}>${l}</option>`).join("")}
        </select>
      </div>
    </div>
    <div class="mt-5 flex flex-wrap gap-2">${gtab("", "All")}${gtab("men", "Men")}${gtab("women", "Women")}${gtab("kids", "Kids")}
      <span class="mx-1 w-px bg-slate-200 dark:bg-slate-700"></span>
      <button class="chip ${st.sale ? "chip-on" : ""}" id="fSale">🏷️ Sale</button>
      <button class="chip ${st.stock ? "chip-on" : ""}" id="fStock">✓ In stock</button>
      <select id="fMax" class="input w-auto rounded-full py-1.5 text-xs"><option value="">Any price</option>${[1500, 2500, 3500, 5000, 8000].map((v) => `<option value="${v}" ${String(st.max) === String(v) ? "selected" : ""}>Under ${money(v)}</option>`).join("")}</select>
    </div>
    <div class="mt-3 flex flex-wrap gap-2">${cats.map((c) => `<button class="chip ${st.cat === c.category ? "chip-on" : ""}" data-cat="${esc(c.category)}">${esc(c.category)} <span class="ml-1 opacity-60">${c.products}</span></button>`).join("")}</div>
    <div id="grid" class="mt-6 grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-4"></div>
    <div class="mt-8 text-center"><button id="more" class="btn-ghost hidden">Load more</button></div>
  </div>`;
  $$("[data-g]").forEach((b) => (b.onclick = () => { const p = parseHash().params; p.delete("cat"); if (b.dataset.g) p.set("gender", b.dataset.g); else p.delete("gender"); location.hash = "#/shop?" + p.toString(); }));
  $$("[data-cat]").forEach((b) => (b.onclick = () => setParam("cat", st.cat === b.dataset.cat ? "" : b.dataset.cat)));
  $("#fSale").onclick = () => setParam("sale", !st.sale);
  $("#fStock").onclick = () => setParam("stock", !st.stock);
  $("#fMax").onchange = (e) => setParam("max", e.target.value);
  $("#shopSort").onchange = (e) => setParam("sort", e.target.value === "featured" ? "" : e.target.value);
  $("#shopQ").oninput = debounce((e) => setParam("q", e.target.value.trim()), 450);
  let offset = 0;
  const load = async (append) => {
    const p = new URLSearchParams({ limit: st.limit, offset, sort: st.sort });
    if (st.q) p.set("q", st.q); if (st.cat) p.set("category", st.cat); if (st.gender) p.set("gender", st.gender);
    if (st.sale) p.set("sale", "true"); if (st.stock) p.set("in_stock", "true"); if (st.max) p.set("max_price", st.max);
    const d = await api("/api/products?" + p);
    $("#shopCount").textContent = `${d.total} product${d.total === 1 ? "" : "s"}`;
    const html = d.items.map(productCard).join("");
    if (append) $("#grid").insertAdjacentHTML("beforeend", html); else $("#grid").innerHTML = html || `<div class="col-span-full py-16 text-center text-slate-500">No products match. <button class="font-bold text-brand-700" data-ask="${esc(st.q || "Show me products")}">Ask the assistant</button></div>`;
    offset += d.items.length;
    $("#more").classList.toggle("hidden", offset >= d.total);
  };
  $("#more").onclick = () => load(true);
  await load(false);
  if (st.q) { const i = $("#shopQ"); i.focus(); i.setSelectionRange(i.value.length, i.value.length); }
}

/* ============================== PRODUCT MODAL ============================== */
async function openProduct(pid, pre = {}) {
  openModal(`<div class="grid place-items-center p-20 text-slate-400">Loading…</div>`);
  let p;
  try { p = await api("/api/products/" + encodeURIComponent(pid)); } catch (e) { closeModal(); toast(e.message, "err"); return; }
  const variants = p.variants_list;
  const sel = { color: pre.color && p.colors.includes(pre.color) ? pre.color : (p.colors.find((c) => variants.some((v) => v.color === c && v.available > 0)) || p.colors[0]), size: pre.size || null, qty: 1 };
  const find = () => variants.find((v) => v.color === sel.color && v.size === sel.size);
  const render = () => {
    const v = find();
    const maxQ = v ? Math.min(10, v.available) : 10;
    if (sel.qty > Math.max(1, maxQ)) sel.qty = Math.max(1, maxQ);
    $("#modalBox").innerHTML = `
    <button data-close-modal class="absolute right-4 top-4 z-10 grid h-9 w-9 place-items-center rounded-full bg-white/80 hover:bg-white dark:bg-slate-800" aria-label="Close"><svg class="h-5 w-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg></button>
    <div class="grid md:grid-cols-2">
      <div class="relative">${art(p, sel.color, "h-full min-h-[320px] w-full md:rounded-l-3xl")}${p.is_sale ? `<span class="absolute left-4 top-4 pill bg-rose-500 text-white">SALE · final sale</span>` : ""}</div>
      <div class="flex flex-col gap-4 p-6 md:p-8">
        <div><div class="text-xs font-bold uppercase tracking-wide text-slate-400">${esc(p.category)}</div>
          <h2 class="mt-1 font-display text-2xl font-bold">${esc(p.name)}</h2><div class="mt-2">${priceHtml(p, true)}</div></div>
        <p class="text-sm text-slate-600 dark:text-slate-300">${esc(p.description || "")}</p>
        <div><div class="label">Color · <span class="normal-case text-slate-700 dark:text-slate-200">${esc(sel.color)}</span></div>
          <div class="flex flex-wrap gap-2">${p.colors.map((c) => { const any = variants.some((x) => x.color === c && x.available > 0);
            return `<button data-color="${esc(c)}" title="${esc(c)}${any ? "" : " (out of stock)"}" class="relative h-9 w-9 rounded-full ring-offset-2 transition dark:ring-offset-slate-900 ${sel.color === c ? "ring-2 ring-brand-600" : "ring-1 ring-black/10"} ${any ? "" : "opacity-40"}" style="background:${hex(c)}"></button>`; }).join("")}</div></div>
        <div><div class="label">Size</div>
          <div class="flex flex-wrap gap-2">${p.sizes.map((s) => { const x = variants.find((y) => y.color === sel.color && y.size === s); const ok = x && x.available > 0;
            return `<button data-size="${esc(s)}" ${ok ? "" : "disabled"} class="min-w-[44px] rounded-xl border px-3 py-2 text-sm font-bold transition ${sel.size === s ? "border-brand-600 bg-brand-600 text-white" : "border-slate-300 hover:border-brand-500 dark:border-slate-600"} disabled:cursor-not-allowed disabled:opacity-35 disabled:line-through">${esc(s)}</button>`; }).join("")}</div></div>
        <div class="text-sm">${v ? (v.available > 0 ? `${stockBadge(v.available)} <span class="ml-1 text-slate-500">${v.available} available · SKU ${esc(v.sku)}</span>` : stockBadge(0)) : `<span class="text-slate-500">Choose a size to see stock.</span>`}</div>
        <div class="flex items-center gap-3">
          <div class="flex items-center rounded-full border border-slate-300 dark:border-slate-600">
            <button id="qMinus" class="h-10 w-10 text-lg font-bold" aria-label="Decrease quantity">−</button><span class="w-8 text-center font-bold">${sel.qty}</span><button id="qPlus" class="h-10 w-10 text-lg font-bold" aria-label="Increase quantity">+</button></div>
          <button id="addCart" class="btn-primary flex-1" ${v && v.available > 0 ? "" : "disabled"}>${v ? (v.available > 0 ? "Add to cart · " + money(v.price * sel.qty) : "Out of stock") : "Select a size"}</button>
        </div>
        <button class="btn-ghost" data-ask="Is the ${esc(sel.color)} ${esc(p.name)}${sel.size ? " size " + esc(sel.size) : ""} available?">💬 Ask Eman about this</button>
        <ul class="space-y-1 border-t border-slate-200 pt-4 text-xs text-slate-500 dark:border-slate-800">
          <li>🚚 Rs 250 delivery to major cities, Rs 350 elsewhere · free over Rs 10,000</li>
          <li>💵 Cash on Delivery · max 10 pieces per item</li>
          <li>🔁 ${p.is_sale ? "Sale item — final sale, no exchange" : "Exchange within 7 days (unused, with tags)"}</li>
        </ul>
      </div>
    </div>`;
    $$("[data-color]", $("#modalBox")).forEach((b) => (b.onclick = () => { sel.color = b.dataset.color; const x = find(); if (!x || x.available <= 0) sel.size = null; render(); }));
    $$("[data-size]", $("#modalBox")).forEach((b) => (b.onclick = () => { sel.size = b.dataset.size; render(); }));
    $("#qMinus").onclick = () => { sel.qty = Math.max(1, sel.qty - 1); render(); };
    $("#qPlus").onclick = () => { const x = find(); sel.qty = Math.min(x ? Math.min(10, x.available) : 10, sel.qty + 1); render(); };
    $("#addCart").onclick = () => { const x = find(); if (!x) return; addToCart({ sku: x.sku, qty: sel.qty, product_id: p.product_id, name: p.name, color: x.color, size: x.size, price: x.price, category: p.category }); closeModal(); };
  };
  render();
}

/* ============================== CART ============================== */
function saveCart() { ls.set("sfh_cart", S.cart); const n = S.cart.reduce((a, i) => a + i.qty, 0); $("#cartCount").textContent = n; $("#cartCount").classList.toggle("hidden", !n); }
function addToCart(item) {
  const ex = S.cart.find((i) => i.sku === item.sku);
  if (ex) ex.qty = Math.min(10, ex.qty + item.qty); else S.cart.push(item);
  saveCart(); toast(`Added ${item.name} (${item.color}, ${item.size}) to cart`); openCart();
}
function openCart() { $("#cartPanel").classList.remove("hidden"); renderCart(); }
function closeCart() { $("#cartPanel").classList.add("hidden"); }
async function renderCart(done) {
  const body = $("#cartBody");
  if (done) {
    body.innerHTML = `<div class="py-10 text-center"><div class="text-5xl">🎉</div><h3 class="mt-3 font-display text-xl font-bold">Order confirmed!</h3>
      <p class="mt-2 text-sm text-slate-500">Order number ${done.order_ids.map((i) => "<b>#" + i + "</b>").join(", ")} · ${money(done.grand_total)} payable on delivery.<br>Delivery to ${esc(done.city)} in ${done.eta_days[0]}-${done.eta_days[1]} working days.</p>
      <a href="#/track?order=${done.order_ids[0]}&phone=${esc(done.phone)}" class="btn-primary mt-5" data-close-cart>Track this order</a>
      <button class="btn-ghost mt-2 w-full" data-close-cart>Continue shopping</button></div>`;
    return;
  }
  if (!S.cart.length) {
    body.innerHTML = `<div class="py-16 text-center"><div class="text-5xl">🛍️</div><p class="mt-3 text-slate-500">Your cart is empty.</p><a href="#/shop" class="btn-primary mt-4" data-close-cart>Start shopping</a></div>`;
    return;
  }
  const u = S.user && S.user.role !== "admin" ? S.user : null;
  const form = ls.get("sfh_checkout", {});
  body.innerHTML = `
  <ul class="divide-y divide-slate-200 dark:divide-slate-800">${S.cart.map((i, k) => `
    <li class="flex gap-3 py-3"><button data-open="${esc(i.product_id)}" class="h-20 w-16 shrink-0 overflow-hidden rounded-xl">${art(i, i.color, "h-full w-full")}</button>
      <div class="min-w-0 flex-1"><div class="font-semibold leading-tight">${esc(i.name)}</div><div class="text-xs text-slate-500">${esc(i.color)} · ${esc(i.size)} · ${money(i.price)}</div>
        <div class="mt-2 flex items-center gap-2"><div class="flex items-center rounded-full border border-slate-300 text-sm dark:border-slate-600"><button data-dec="${k}" class="h-7 w-7 font-bold">−</button><span class="w-6 text-center">${i.qty}</span><button data-inc="${k}" class="h-7 w-7 font-bold">+</button></div>
          <button data-rm="${k}" class="text-xs font-semibold text-rose-600 hover:underline">Remove</button><span class="ml-auto font-bold">${money(i.price * i.qty)}</span></div>
        <div class="text-xs text-rose-600" id="prob-${esc(i.sku)}"></div></div></li>`).join("")}</ul>
  <form id="coForm" class="mt-4 space-y-3 border-t border-slate-200 pt-4 dark:border-slate-800" novalidate>
    <h3 class="font-display font-bold">Delivery details</h3>
    <div class="grid grid-cols-2 gap-3">
      <div class="col-span-2 sm:col-span-1"><label class="label" for="coName">Full name</label><input id="coName" class="input" value="${esc(form.name || u?.name || "")}" autocomplete="name"><div class="text-xs text-rose-600" data-err="name"></div></div>
      <div class="col-span-2 sm:col-span-1"><label class="label" for="coPhone">Mobile</label><input id="coPhone" class="input" value="${esc(form.phone || u?.phone || "")}" placeholder="03001234567" inputmode="tel" autocomplete="tel"><div class="text-xs text-rose-600" data-err="phone"></div></div>
      <div class="col-span-2"><label class="label" for="coAddr">Address</label><input id="coAddr" class="input" value="${esc(form.address || "")}" placeholder="House, street, area" autocomplete="street-address"><div class="text-xs text-rose-600" data-err="address"></div></div>
      <div class="col-span-2"><label class="label" for="coCity">City</label><input id="coCity" class="input" list="cityList" value="${esc(form.city || "")}" placeholder="Lahore"><datalist id="cityList">${CITIES.map((c) => `<option value="${c}">`).join("")}</datalist><div class="text-xs text-rose-600" data-err="city"></div></div>
    </div>
    <div id="quote" class="rounded-2xl bg-sand-100 p-4 text-sm dark:bg-slate-800">Calculating…</div>
    <div id="coErr" class="text-sm font-semibold text-rose-600"></div>
    <button class="btn-primary w-full" id="coBtn">Place order · Cash on Delivery</button>
    <p class="text-center text-xs text-slate-500">Pay in cash when the parcel arrives. You'll get an order number to track it.</p>
  </form>`;
  $$("[data-inc]", body).forEach((b) => (b.onclick = () => { const i = S.cart[b.dataset.inc]; i.qty = Math.min(10, i.qty + 1); saveCart(); renderCart(); }));
  $$("[data-dec]", body).forEach((b) => (b.onclick = () => { const i = S.cart[b.dataset.dec]; i.qty = Math.max(1, i.qty - 1); saveCart(); renderCart(); }));
  $$("[data-rm]", body).forEach((b) => (b.onclick = () => { S.cart.splice(Number(b.dataset.rm), 1); saveCart(); renderCart(); }));
  const remember = () => ls.set("sfh_checkout", { name: $("#coName").value, phone: $("#coPhone").value, address: $("#coAddr").value, city: $("#coCity").value });
  const quote = async () => {
    remember();
    try {
      const q = await api("/api/cart/quote", { body: { items: S.cart.map((i) => ({ sku: i.sku, qty: i.qty })), city: $("#coCity").value || null } });
      $$("[id^=prob-]", body).forEach((e) => (e.textContent = ""));
      q.problems.forEach((p) => { const el = document.getElementById("prob-" + p.sku); if (el) el.textContent = p.problem === "insufficient_stock" ? `Only ${p.available} available` : p.problem === "quantity_above_limit" ? "Max 10 per item" : ""; });
      const cod = q.problems.find((p) => p.problem === "cod_above_limit");
      const toFree = q.free_delivery_min - q.product_total;
      $("#quote").innerHTML = `<div class="flex justify-between"><span>Products</span><span>${money(q.product_total)}</span></div>
        <div class="flex justify-between"><span>Delivery${$("#coCity").value ? " (" + esc($("#coCity").value) + ")" : ""}</span><span>${q.delivery ? money(q.delivery) : "FREE"}</span></div>
        <div class="mt-2 flex justify-between border-t border-slate-300 pt-2 text-base font-bold dark:border-slate-600"><span>Total (COD)</span><span>${money(q.grand_total)}</span></div>
        ${toFree > 0 ? `<div class="mt-2 text-xs text-brand-700 dark:text-brand-300">Add ${money(toFree)} more for free delivery.</div>` : ""}
        ${$("#coCity").value ? `<div class="mt-1 text-xs text-slate-500">Estimated delivery: ${q.eta_days[0]}-${q.eta_days[1]} working days</div>` : ""}
        ${cod ? `<div class="mt-2 rounded-lg bg-amber-100 p-2 text-xs text-amber-800 dark:bg-amber-950 dark:text-amber-200">COD orders above ${money(cod.limit)} need staff confirmation — placing it will open a ticket and our team will call you.</div>` : ""}`;
    } catch (e) { $("#quote").textContent = e.message; }
  };
  $("#coCity").oninput = debounce(quote, 300);
  ["#coName", "#coPhone", "#coAddr"].forEach((s) => ($(s).oninput = remember));
  quote();
  $("#coForm").onsubmit = async (ev) => {
    ev.preventDefault();
    $$("[data-err]", body).forEach((e) => (e.textContent = "")); $("#coErr").textContent = "";
    const btn = $("#coBtn"); btn.disabled = true; btn.textContent = "Placing order…";
    try {
      const r = await api("/api/checkout", { body: { session_id: S.sid, items: S.cart.map((i) => ({ sku: i.sku, qty: i.qty })), name: $("#coName").value, phone: $("#coPhone").value, address: $("#coAddr").value, city: $("#coCity").value } });
      S.cart = []; saveCart(); renderCart(r); toast("Order placed — thank you!");
    } catch (e) {
      const d = e.data || {};
      if (d.errors) Object.entries(d.errors).forEach(([k, v]) => { const el = $(`[data-err="${k}"]`, body); if (el) el.textContent = v; });
      $("#coErr").textContent = d.errors ? "" : e.message;
      if (d.escalated) toast(`Ticket #${d.ticket} opened — staff will contact you`, "info");
      btn.disabled = false; btn.textContent = "Place order · Cash on Delivery"; quote();
    }
  };
}

/* ============================== TRACK ============================== */
const STEPS = ["Placed", "Packed", "Shipped", "Delivered"];
async function viewTrack(params) {
  $("#view").innerHTML = `
  <div class="mx-auto max-w-2xl px-4 py-12">
    <h1 class="font-display text-3xl font-bold">Track your order</h1>
    <p class="mt-1 text-slate-500">For your privacy we need both the order number and the phone number used on the order.</p>
    <form id="trForm" class="card mt-6 grid gap-3 p-5 sm:grid-cols-[1fr_1fr_auto]">
      <div><label class="label" for="trId">Order number</label><input id="trId" class="input" placeholder="10231" inputmode="numeric" value="${esc(params.get("order") || "")}"></div>
      <div><label class="label" for="trPhone">Phone</label><input id="trPhone" class="input" placeholder="03001234567" inputmode="tel" value="${esc(params.get("phone") || (S.user?.role !== "admin" ? S.user?.phone || "" : ""))}"></div>
      <button class="btn-primary self-end">Track</button>
    </form>
    <div id="trOut" class="mt-6"></div>
    <p class="mt-6 text-sm text-slate-500">Tip: try order <b>10231</b> with phone <b>03001234567</b>. Or <button class="font-bold text-brand-700 dark:text-brand-300" data-ask="Mera order kahan hai?">ask the assistant</button>.</p>
  </div>`;
  const go = async () => {
    const out = $("#trOut");
    out.innerHTML = `<div class="text-slate-400">Checking…</div>`;
    try {
      const r = await api("/api/track", { body: { order_id: $("#trId").value, phone: $("#trPhone").value } });
      const idx = STEPS.indexOf(r.status);
      out.innerHTML = `<div class="card p-6">
        <div class="flex flex-wrap items-center gap-2"><h2 class="font-display text-xl font-bold">Order #${r.order_id}</h2><span class="pill ${r.status === "Cancelled" ? "bg-rose-100 text-rose-700" : "bg-brand-100 text-brand-800"}">${esc(r.status)}</span></div>
        <div class="mt-1 text-sm text-slate-500">${esc(r.item)} · qty ${r.qty} · ${money(r.total)} · ordered ${esc(r.order_date)}</div>
        ${r.status === "Cancelled" ? "" : `<ol class="mt-6 grid grid-cols-4 gap-2">${STEPS.map((s, i) => `<li class="text-center"><div class="mx-auto grid h-9 w-9 place-items-center rounded-full text-sm font-bold ${i <= idx ? "bg-brand-600 text-white" : "bg-slate-200 text-slate-500 dark:bg-slate-800"}">${i < idx ? "✓" : i + 1}</div><div class="mt-1 text-xs font-semibold ${i <= idx ? "" : "text-slate-400"}">${s}</div></li>`).join("")}</ol>
        <div class="mb-6"></div>`}
        <dl class="mt-2 grid grid-cols-2 gap-3 text-sm">
          <div><dt class="label">Courier</dt><dd>${esc(r.courier || "Assigned when shipped")}</dd></div>
          <div><dt class="label">Tracking number</dt><dd class="font-mono">${esc(r.tracking_no || "—")}</dd></div>
          <div><dt class="label">City</dt><dd>${esc(r.city)}</dd></div>
          <div><dt class="label">${r.status === "Delivered" ? "Delivered on" : "Estimated delivery"}</dt><dd>${r.status === "Delivered" ? esc(r.delivered_date || "—") : `${r.eta_days[0]}-${r.eta_days[1]} working days`}</dd></div>
        </dl>
        ${r.status === "Delivered" ? `<button class="btn-ghost btn-sm mt-5" data-ask="Order ${r.order_id} exchange karna hai">Request an exchange</button>` : ""}
      </div>`;
    } catch (e) { out.innerHTML = `<div class="card border-rose-200 bg-rose-50 p-4 text-sm font-semibold text-rose-700 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300">${esc(e.message)}</div>`; }
  };
  $("#trForm").onsubmit = (e) => { e.preventDefault(); go(); };
  if (params.get("order") && $("#trPhone").value) go();
}

/* ============================== ACCOUNT ============================== */
const statusPill = (s) => `<span class="pill ${({ Placed: "bg-sky-100 text-sky-800", Packed: "bg-violet-100 text-violet-800", Shipped: "bg-amber-100 text-amber-800", Delivered: "bg-emerald-100 text-emerald-800", Cancelled: "bg-rose-100 text-rose-700" })[s] || "bg-slate-100"}">${esc(s)}</span>`;
async function viewAccount() {
  if (!S.user) { $("#view").innerHTML = `<div class="mx-auto max-w-md px-4 py-20 text-center"><h1 class="font-display text-2xl font-bold">Sign in to see your orders</h1><p class="mt-2 text-slate-500">Orders placed with your account's phone number — on the website or in the chat — show up here.</p><button class="btn-primary mt-5" data-auth="login">Sign in</button></div>`; return; }
  const d = await api("/api/my/orders");
  $("#view").innerHTML = `
  <div class="mx-auto max-w-5xl px-4 py-10">
    <h1 class="font-display text-3xl font-bold">Hi, ${esc(d.user.name)} 👋</h1>
    <p class="mt-1 text-slate-500">${esc(d.user.email)} · ${esc(d.user.phone || "")}</p>
    <div class="card mt-6 overflow-x-auto">
      ${d.orders.length ? `<table class="w-full text-sm"><thead class="border-b border-slate-200 dark:border-slate-800"><tr><th class="th">Order</th><th class="th">Item</th><th class="th">Qty</th><th class="th">Total</th><th class="th">Status</th><th class="th">Date</th><th class="th"></th></tr></thead>
        <tbody class="divide-y divide-slate-100 dark:divide-slate-800">${d.orders.map((o) => `<tr><td class="td font-bold">#${o.order_id}</td><td class="td">${esc(o.item)}</td><td class="td">${o.qty}</td><td class="td">${money(o.total)}</td><td class="td">${statusPill(o.status)}</td><td class="td text-slate-500">${esc(o.order_date)}</td>
        <td class="td"><a class="font-bold text-brand-700 hover:underline dark:text-brand-300" href="#/track?order=${o.order_id}&phone=${esc(o.phone)}">Track</a></td></tr>`).join("")}</tbody></table>`
      : `<div class="p-10 text-center text-slate-500">No orders yet with ${esc(d.user.phone)}. <a href="#/shop" class="font-bold text-brand-700">Start shopping</a></div>`}
    </div>
  </div>`;
}

/* ============================== ADMIN ============================== */
async function viewAdmin(params) {
  if (!S.user || S.user.role !== "admin") {
    $("#view").innerHTML = `<div class="mx-auto max-w-md px-4 py-20 text-center"><div class="text-4xl">🔐</div><h1 class="mt-2 font-display text-2xl font-bold">Staff area</h1><p class="mt-2 text-slate-500">Sign in with a staff account to see the dashboard, hand-offs and orders.</p><button class="btn-primary mt-5" data-auth="login">Staff sign in</button></div>`;
    return;
  }
  const tab = params.get("tab") || "dashboard";
  const tabs = [["dashboard", "Dashboard"], ["handoffs", "Hand-offs"], ["orders", "Orders"], ["evaluation", "Evaluation & model"]];
  $("#view").innerHTML = `<div class="mx-auto max-w-7xl px-4 py-8">
    <div class="flex flex-wrap items-center gap-2"><h1 class="mr-4 font-display text-2xl font-bold">Staff console</h1>
      ${tabs.map(([k, l]) => `<a href="#/admin?tab=${k}" class="navlink ${tab === k ? "navlink-on" : ""}">${l}</a>`).join("")}
      <button id="resetStore" class="btn-ghost btn-sm ml-auto">Reset demo data</button></div>
    <div id="adminBody" class="mt-6"></div></div>`;
  $("#resetStore").onclick = async () => {
    if (!confirm("Reset catalog, stock, orders, chats and tickets back to the original dataset? Accounts and reviews are kept.")) return;
    await api("/api/reset", { body: {} }); toast("Demo data reset"); route();
  };
  await ({ dashboard: adminDashboard, handoffs: adminHandoffs, orders: adminOrders, evaluation: adminEval }[tab] || adminDashboard)(params);
}
const kpi = (n, l, sub = "") => `<div class="card p-4"><div class="font-display text-2xl font-bold text-brand-800 dark:text-brand-300">${n}</div><div class="text-xs font-semibold text-slate-500">${l}</div>${sub ? `<div class="mt-1 text-[11px] text-slate-400">${sub}</div>` : ""}</div>`;
const bars = (obj, color = "bg-brand-500") => { const e = Object.entries(obj || {}).sort((a, b) => b[1] - a[1]); const mx = Math.max(1, ...e.map((x) => x[1]));
  return e.length ? e.map(([k, v]) => `<div class="grid grid-cols-[150px_1fr_36px] items-center gap-2 py-1 text-sm"><span class="truncate" title="${esc(k)}">${esc(k.replace(/_/g, " "))}</span><div class="h-2.5 rounded-full bg-slate-100 dark:bg-slate-800"><div class="h-2.5 rounded-full ${color}" style="width:${(v / mx) * 100}%"></div></div><span class="text-right font-semibold">${v}</span></div>`).join("") : `<p class="text-sm text-slate-400">Nothing yet.</p>`; };
const table = (rows, cols) => rows && rows.length ? `<div class="overflow-x-auto"><table class="w-full text-sm"><thead><tr>${cols.map((c) => `<th class="th">${c[1]}</th>`).join("")}</tr></thead><tbody class="divide-y divide-slate-100 dark:divide-slate-800">${rows.map((r) => `<tr>${cols.map((c) => `<td class="td">${c[2] ? c[2](r) : esc(r[c[0]])}</td>`).join("")}</tr>`).join("")}</tbody></table></div>` : `<p class="text-sm text-slate-400">Nothing yet.</p>`;

async function adminDashboard() {
  const d = await api("/api/dashboard");
  $("#adminBody").innerHTML = `
  <div class="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
    ${kpi(d.conversations, "Conversations")}${kpi(d.orders, "Orders (all)", `${d.new_orders} placed via chat/web`)}${kpi(money(d.revenue), "Order value", `${money(d.new_revenue)} new`)}
    ${kpi(d.open_escalations, "Open hand-offs", `${d.total_escalations} total`)}${kpi(d.handled_percent + "%", "Chats handled by AI")}${kpi(d.reviews?.average ? "★ " + d.reviews.average : "—", "Customer rating", `${d.reviews?.count || 0} reviews`)}
  </div>
  <div class="mt-4 grid gap-4 lg:grid-cols-3">
    <div class="card p-5"><h3 class="font-bold">What customers ask (intents)</h3><div class="mt-3">${bars(d.intent_counts)}</div></div>
    <div class="card p-5"><h3 class="font-bold">Orders by status</h3><div class="mt-3">${bars(d.orders_by_status, "bg-sky-500")}</div><h3 class="mt-5 font-bold">Hand-off reasons</h3><div class="mt-3">${bars(d.escalation_reasons, "bg-amber-500")}</div></div>
    <div class="card p-5"><h3 class="font-bold">Top products</h3><div class="mt-2">${table(d.top_products, [["name", "Product"], ["n", "Orders"], ["units", "Units"]])}</div></div>
  </div>
  <div class="mt-4 grid gap-4 lg:grid-cols-2">
    <div class="card p-5"><h3 class="font-bold">Low stock (1–4 left)</h3><div class="mt-2 max-h-72 overflow-y-auto scrollbar-thin">${table(d.low_stock, [["name", "Product"], ["color", "Color"], ["size", "Size"], ["stock", "Left"]])}</div></div>
    <div class="card p-5"><h3 class="font-bold">Out of stock</h3><div class="mt-2 max-h-72 overflow-y-auto scrollbar-thin">${table(d.out_of_stock, [["name", "Product"], ["color", "Color"], ["size", "Size"], ["sku", "SKU"]])}</div></div>
  </div>
  <div class="card mt-4 p-5"><h3 class="font-bold">Recent activity</h3><div class="mt-2">${table(d.recent_audit, [["ts", "Time", (r) => new Date(r.ts * 1000).toLocaleString()], ["action", "Action"], ["detail", "Detail", (r) => `<span class="font-mono text-xs">${esc(String(r.detail).slice(0, 140))}</span>`]])}</div></div>`;
}

async function adminHandoffs(params) {
  const filter = params.get("f") || "open";
  const d = await api("/api/escalations" + (filter === "all" ? "" : "?status=" + filter));
  const sel = Number(params.get("id")) || d.escalations[0]?.id;
  $("#adminBody").innerHTML = `<div class="grid gap-4 lg:grid-cols-[380px_1fr]">
    <div class="card overflow-hidden"><div class="flex gap-1 border-b border-slate-200 p-2 dark:border-slate-800">${["open", "resolved", "all"].map((f) => `<a class="navlink ${f === filter ? "navlink-on" : ""}" href="#/admin?tab=handoffs&f=${f}">${f[0].toUpperCase() + f.slice(1)}</a>`).join("")}</div>
      <ul class="max-h-[70vh] divide-y divide-slate-100 overflow-y-auto scrollbar-thin dark:divide-slate-800">${d.escalations.map((x) => `<li><a href="#/admin?tab=handoffs&f=${filter}&id=${x.id}" class="block px-4 py-3 hover:bg-slate-50 dark:hover:bg-slate-800 ${x.id === sel ? "bg-brand-50 dark:bg-slate-800" : ""}">
        <div class="flex items-center gap-2"><b>#${x.id}</b><span class="pill bg-amber-100 text-amber-800">${esc(x.reason.replace(/_/g, " "))}</span>${x.status === "open" ? "" : `<span class="pill bg-emerald-100 text-emerald-800">resolved</span>`}<span class="ml-auto text-[11px] text-slate-400">${new Date(x.created_at * 1000).toLocaleString()}</span></div>
        <div class="mt-1 line-clamp-2 text-sm text-slate-600 dark:text-slate-300">${esc(x.message || x.detail)}</div></a></li>`).join("") || `<li class="p-6 text-center text-sm text-slate-400">No ${filter} hand-offs. Ask for a refund in the chat to create one.</li>`}</ul></div>
    <div id="hoDetail" class="card min-h-[300px] p-5">${sel ? "Loading…" : `<p class="text-sm text-slate-400">Select a hand-off.</p>`}</div></div>`;
  if (!sel) return;
  const x = await api("/api/escalations/" + sel);
  const e = x.escalation;
  $("#hoDetail").innerHTML = `
    <div class="flex flex-wrap items-center gap-2"><h3 class="font-display text-lg font-bold">Ticket #${e.id}</h3><span class="pill bg-amber-100 text-amber-800">${esc(e.reason.replace(/_/g, " "))}</span>
      <span class="text-xs text-slate-500">${e.customer ? "Customer: " + esc(e.customer) + " · " : ""}${e.order_id ? "Order #" + e.order_id + " · " : ""}session ${esc(e.session_id)}</span>
      ${e.status === "open" ? `<button id="resolveBtn" class="btn-primary btn-sm ml-auto">Mark resolved</button>` : `<span class="pill ml-auto bg-emerald-100 text-emerald-800">resolved</span>`}</div>
    <div class="mt-1 text-xs text-slate-500">Rule: ${esc(e.detail)}</div>
    <div id="transcript" class="scrollbar-thin mt-4 max-h-[48vh] space-y-2 overflow-y-auto rounded-2xl bg-slate-50 p-3 dark:bg-slate-950">${x.transcript.map((m) => `
      <div class="flex ${m.role === "customer" ? "justify-end" : ""}"><div class="max-w-[80%] rounded-2xl px-3 py-2 text-sm ${m.role === "customer" ? "bg-brand-600 text-white" : m.role === "staff" ? "bg-violet-100 text-violet-900 dark:bg-violet-950 dark:text-violet-100" : "bg-white dark:bg-slate-800"}"><div class="mb-0.5 text-[10px] font-bold uppercase opacity-60">${m.role}${m.action ? " · " + esc(m.action) : ""}</div><div class="whitespace-pre-wrap">${esc(m.text)}</div></div></div>`).join("")}</div>
    <form id="replyForm" class="mt-3 flex gap-2"><input id="replyText" class="input" placeholder="Reply to the customer — it appears in their chat" maxlength="2000"><button class="btn-primary">Send</button></form>`;
  const tr = $("#transcript"); tr.scrollTop = tr.scrollHeight;
  $("#replyForm").onsubmit = async (ev) => { ev.preventDefault(); const t = $("#replyText").value.trim(); if (!t) return; await api(`/api/escalations/${e.id}/reply`, { body: { text: t } }); toast("Reply sent to the customer's chat"); route(); };
  $("#resolveBtn")?.addEventListener("click", async () => { await api(`/api/escalations/${e.id}/resolve`, { body: {} }); toast("Ticket resolved"); route(); });
}

async function adminOrders(params) {
  const q = params.get("q") || "", st = params.get("s") || "";
  const d = await api("/api/orders?limit=200" + (q ? "&q=" + encodeURIComponent(q) : "") + (st ? "&status=" + st : ""));
  $("#adminBody").innerHTML = `
  <div class="mb-3 flex flex-wrap gap-2"><input id="oq" class="input w-64 rounded-full" placeholder="Search id, name, phone…" value="${esc(q)}">
    <select id="os" class="input w-auto rounded-full"><option value="">All statuses</option>${d.statuses.map((s) => `<option ${s === st ? "selected" : ""}>${s}</option>`).join("")}</select>
    <span class="self-center text-sm text-slate-500">${d.orders.length} orders</span></div>
  <div class="card">${table(d.orders, [["order_id", "Order", (o) => `<b>#${o.order_id}</b><div class="text-[11px] text-slate-400">${esc(o.channel || "")}</div>`], ["customer_name", "Customer", (o) => `${esc(o.customer_name)}<div class="text-[11px] text-slate-400">${esc(o.phone)}</div>`],
    ["item", "Item"], ["qty", "Qty"], ["city", "City", (o) => `${esc(o.city)}${o.address ? `<div class="text-[11px] text-slate-400">${esc(o.address)}</div>` : ""}`], ["total", "Total", (o) => money(o.total)], ["order_date", "Date"],
    ["status", "Status", (o) => `<select data-oid="${o.order_id}" class="input w-auto py-1 text-xs">${d.statuses.map((s) => `<option ${s === o.status ? "selected" : ""}>${s}</option>`).join("")}</select>${o.tracking_no ? `<div class="mt-1 font-mono text-[11px] text-slate-400">${esc(o.courier)} ${esc(o.tracking_no)}</div>` : ""}`]])}</div>`;
  const nav = () => { const p = new URLSearchParams({ tab: "orders" }); const qv = $("#oq").value.trim(), sv = $("#os").value; if (qv) p.set("q", qv); if (sv) p.set("s", sv); location.hash = "#/admin?" + p; };
  $("#oq").oninput = debounce(nav, 500); $("#os").onchange = nav;
  $$("[data-oid]").forEach((s) => (s.onchange = async () => {
    if (s.value === "Cancelled" && !confirm(`Cancel order #${s.dataset.oid}? Its stock goes back to the catalog.`)) { route(); return; }
    try { const o = await api("/api/orders/" + s.dataset.oid, { method: "PATCH", body: { status: s.value } }); toast(`Order #${o.order_id} → ${o.status}`); route(); } catch (e) { toast(e.message, "err"); }
  }));
}

async function adminEval() {
  const [last, model] = await Promise.all([api("/api/evaluation"), api("/api/model")]);
  const pct = (x) => (x === undefined || x === null ? "—" : (x * 100).toFixed(1) + "%");
  const evalHtml = (d) => d && d.overall ? `<div class="grid grid-cols-2 gap-3 md:grid-cols-4">${kpi(pct(d.overall.action_accuracy), "Action accuracy", d.overall.n + " conversations")}${kpi(pct(d.overall.escalation_precision), "Hand-off precision")}${kpi(pct(d.overall.escalation_recall), "Hand-off recall")}${kpi(pct(d.overall.escalation_f1), "Hand-off F1")}</div>
    <div class="mt-3">${table([["All", d.overall], ["Dev split", d.dev], ["Test split", d.test]].map(([k, v]) => ({ k, ...v })), [["k", "Set"], ["n", "n"], ["a", "Accuracy", (r) => pct(r.action_accuracy)], ["p", "Precision", (r) => pct(r.escalation_precision)], ["r", "Recall", (r) => pct(r.escalation_recall)], ["f", "F1", (r) => pct(r.escalation_f1)]])}</div>
    ${d.rows ? `<details class="mt-3"><summary class="cursor-pointer text-sm font-bold">Mismatches (${d.rows.filter((r) => r.expected_action !== r.predicted_action).length})</summary><div class="mt-2">${table(d.rows.filter((r) => r.expected_action !== r.predicted_action), [["conv_id", "Conv"], ["split", "Split"], ["expected_action", "Expected"], ["predicted_action", "Predicted"]])}</div></details>` : ""}
    ${d.seconds ? `<p class="mt-2 text-xs text-slate-400">Ran in ${d.seconds}s</p>` : ""}` : `<p class="text-sm text-slate-400">Not run yet.</p>`;
  const m = model.metrics;
  $("#adminBody").innerHTML = `
  <div class="card p-5"><div class="flex flex-wrap items-center gap-3"><h3 class="font-bold">Replay all labelled conversations</h3><button id="evalRun" class="btn-primary btn-sm ml-auto">▶ Run evaluation</button></div>
    <p class="mt-1 text-sm text-slate-500">Every conversation in <code>conversations.json</code> is replayed against a fresh database and the agent's final action and hand-off decision are compared with the labels.</p>
    <div id="evalOut" class="mt-4">${evalHtml(last)}</div></div>
  <div class="mt-4 grid gap-4 lg:grid-cols-2">
    <div class="card p-5"><h3 class="font-bold">Intent model</h3>
      ${m ? `<p class="mt-1 text-sm text-slate-500">${esc(m.model)} · ${m.final_examples} training examples · ${m.classes.length} intents · trained in ${m.train_seconds}s</p>
      <div class="mt-3 grid grid-cols-2 gap-3">${kpi(pct(m.heldout.accuracy), "Held-out accuracy", `macro F1 ${pct(m.heldout.macro_f1)} · n=${m.heldout.n}`)}${kpi(pct(m.test_conversation_turns.accuracy), "Test-conversation turns", `never trained on · n=${m.test_conversation_turns.n}`)}</div>
      <details class="mt-3"><summary class="cursor-pointer text-sm font-bold">Per-intent scores (held-out)</summary><div class="mt-2 max-h-80 overflow-y-auto scrollbar-thin">${table(Object.entries(m.heldout.per_intent).map(([k, v]) => ({ k, ...v })), [["k", "Intent"], ["precision", "P", (r) => pct(r.precision)], ["recall", "R", (r) => pct(r.recall)], ["f1", "F1", (r) => pct(r.f1)], ["support", "n"]])}</div></details>
      <p class="mt-3 text-xs text-slate-400">Retrain with <code>python train_model.py</code>. Rules handle safety-critical cases (refunds, limits, privacy); the model routes everything else.</p>` : `<p class="text-sm text-slate-400">No model metrics found — run <code>python train_model.py</code>.</p>`}</div>
    <div class="card p-5"><h3 class="font-bold">Concurrency test — can the last unit be oversold?</h3>
      <p class="mt-1 text-sm text-slate-500">Simulated customers race to reserve the same variant at the same moment. Reservations are atomic, so the total reserved can never exceed stock.</p>
      <form id="contForm" class="mt-3 flex flex-wrap items-end gap-2"><div><label class="label">SKU</label><input id="cSku" class="input w-32" value="SKU0003"></div><div><label class="label">Customers</label><input id="cN" type="number" min="2" max="64" value="8" class="input w-24"></div><button class="btn-primary">▶ Race</button></form>
      <div id="contOut" class="mt-3 text-sm"></div></div>
  </div>`;
  $("#evalRun").onclick = async () => { const b = $("#evalRun"); b.disabled = true; b.textContent = "Running…"; try { const d = await api("/api/evaluation/run", { body: {} }); $("#evalOut").innerHTML = evalHtml(d); toast("Evaluation finished"); } catch (e) { toast(e.message, "err"); } b.disabled = false; b.textContent = "▶ Run evaluation"; };
  $("#contForm").onsubmit = async (ev) => { ev.preventDefault(); try { const d = await api("/api/test/contention", { body: { sku: $("#cSku").value.trim(), customers: Number($("#cN").value) || 8 } });
    $("#contOut").innerHTML = `<div class="rounded-xl ${d.oversold ? "bg-rose-50 text-rose-800" : "bg-emerald-50 text-emerald-800"} p-3"><b>${esc(d.sku)}</b>: ${d.available_before} available · ${d.customers} customers raced → <b>${d.reservations_won} won</b> · available during the race: ${d.available_after} · oversold: <b>${d.oversold ? "YES — bug!" : "No ✓"}</b><div class="text-xs opacity-70">Test reservations are released afterwards.</div></div>`; } catch (e) { $("#contOut").textContent = e.message; } };
}

/* ============================== MODAL ============================== */
function openModal(html, wide = true) {
  const m = $("#modal"), b = $("#modalBox");
  b.className = `relative max-h-[92vh] w-full ${wide ? "max-w-3xl" : "max-w-md"} animate-pop overflow-y-auto rounded-3xl bg-white shadow-2xl dark:bg-slate-900`;
  b.innerHTML = html; m.classList.remove("hidden"); m.classList.add("flex");
}
function closeModal() { $("#modal").classList.add("hidden"); $("#modal").classList.remove("flex"); clearInterval(S.typeTimer); }
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { closeModal(); closeCart(); closeChat(); } });

/* ============================== AUTH ============================== */
function renderAuthArea() {
  const u = S.user, a = $("#authArea");
  if (u) {
    a.innerHTML = `<div class="relative"><button id="userBtn" class="flex items-center gap-2 rounded-full py-1 pl-1 pr-3 text-sm font-semibold hover:bg-slate-100 dark:hover:bg-slate-800"><span class="grid h-7 w-7 place-items-center rounded-full ${u.role === "admin" ? "bg-violet-600" : "bg-brand-600"} text-xs font-bold text-white">${esc((u.name || "?")[0].toUpperCase())}</span><span class="hidden sm:inline">${esc(u.name.split(" ")[0])}</span></button>
      <div id="userMenu" class="absolute right-0 top-11 hidden w-48 rounded-2xl border border-slate-200 bg-white p-1 text-sm shadow-soft dark:border-slate-700 dark:bg-slate-900">
        <div class="px-3 py-2 text-xs text-slate-500">${esc(u.email)}${u.role === "admin" ? " · staff" : ""}</div>
        ${u.role === "admin" ? `<a href="#/admin" class="block rounded-xl px-3 py-2 hover:bg-slate-100 dark:hover:bg-slate-800">Staff console</a>` : `<a href="#/account" class="block rounded-xl px-3 py-2 hover:bg-slate-100 dark:hover:bg-slate-800">My orders</a>`}
        <button id="logoutBtn" class="block w-full rounded-xl px-3 py-2 text-left text-rose-600 hover:bg-slate-100 dark:hover:bg-slate-800">Sign out</button></div></div>`;
    $("#userBtn").onclick = (e) => { e.stopPropagation(); $("#userMenu").classList.toggle("hidden"); };
    $("#logoutBtn").onclick = async () => { try { await api("/api/auth/logout", { body: {} }); } catch {} ls.del("sfh_token"); ls.del("sfh_user"); S.user = null; renderAuthArea(); toast("Signed out", "info"); if (/admin|account/.test(location.hash)) location.hash = "#/"; };
  } else {
    a.innerHTML = `<button class="btn-ghost btn-sm" data-auth="login">Sign in</button>`;
  }
  const isAdmin = u?.role === "admin";
  ["#navAccount", "#mNavAccount"].forEach((s) => $(s).classList.toggle("hidden", !u || isAdmin));
  ["#navAdmin", "#mNavAdmin"].forEach((s) => $(s).classList.toggle("hidden", !isAdmin));
}
document.addEventListener("click", () => $("#userMenu")?.classList.add("hidden"));
function openAuth(mode = "login") {
  const demo = S.status?.demo_staff_login;
  openModal(`<div class="p-7">
    <button data-close-modal class="absolute right-4 top-4 grid h-8 w-8 place-items-center rounded-full hover:bg-slate-100 dark:hover:bg-slate-800" aria-label="Close"><svg class="h-5 w-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg></button>
    <h2 class="font-display text-2xl font-bold">${mode === "signup" ? "Create your account" : "Welcome back"}</h2>
    <p class="mt-1 text-sm text-slate-500">${mode === "signup" ? "Your orders — from the website or the chat — appear under My orders." : "Sign in to see your orders and leave reviews."}</p>
    <form id="authForm" class="mt-5 space-y-3" novalidate>
      ${mode === "signup" ? `<div><label class="label" for="aName">Full name</label><input id="aName" class="input" autocomplete="name" required></div>
      <div><label class="label" for="aPhone">Mobile number</label><input id="aPhone" class="input" placeholder="03001234567" inputmode="tel" autocomplete="tel" required></div>` : ""}
      <div><label class="label" for="aEmail">Email</label><input id="aEmail" type="email" class="input" autocomplete="email" required></div>
      <div><label class="label" for="aPass">Password</label><div class="relative"><input id="aPass" type="password" class="input pr-16" autocomplete="${mode === "signup" ? "new-password" : "current-password"}" required><button type="button" id="eye" class="absolute right-3 top-1/2 -translate-y-1/2 text-xs font-bold text-brand-700">Show</button></div>
        ${mode === "signup" ? `<p class="mt-1 text-xs text-slate-500">At least 8 characters, with a number and a symbol (@ # !).</p>` : ""}</div>
      <div id="aErr" class="text-sm font-semibold text-rose-600"></div>
      <button class="btn-primary w-full" id="aBtn">${mode === "signup" ? "Create account" : "Sign in"}</button>
    </form>
    <p class="mt-4 text-center text-sm text-slate-500">${mode === "signup" ? `Already have an account? <button class="font-bold text-brand-700" data-auth="login">Sign in</button>` : `New here? <button class="font-bold text-brand-700" data-auth="signup">Create an account</button>`}</p>
    ${demo && mode === "login" ? `<div class="mt-4 rounded-xl bg-violet-50 p-3 text-xs text-violet-800 dark:bg-violet-950 dark:text-violet-200"><b>Staff demo login:</b> admin@saira.pk / Admin@1234 <button class="ml-1 font-bold underline" id="fillDemo">fill</button><br><span class="opacity-75">Set ADMIN_EMAIL / ADMIN_PASSWORD when you deploy.</span></div>` : ""}
    ${mode === "signup" ? `<p class="mt-3 text-center text-xs text-slate-400">Tip: sign up with phone 03001234567 to see the sample orders already placed with it.</p>` : ""}
  </div>`, false);
  $("#eye").onclick = () => { const i = $("#aPass"); i.type = i.type === "password" ? "text" : "password"; $("#eye").textContent = i.type === "password" ? "Show" : "Hide"; };
  $("#fillDemo")?.addEventListener("click", () => { $("#aEmail").value = "admin@saira.pk"; $("#aPass").value = "Admin@1234"; });
  $("#authForm").onsubmit = async (e) => {
    e.preventDefault(); $("#aErr").textContent = "";
    const body = { email: $("#aEmail").value.trim(), password: $("#aPass").value };
    if (mode === "signup") { body.name = $("#aName").value.trim(); body.phone = $("#aPhone").value.trim();
      if (body.password.length < 8 || !/\d/.test(body.password) || !/[^A-Za-z0-9]/.test(body.password)) { $("#aErr").textContent = "Password needs 8+ characters, a number and a symbol."; return; } }
    const btn = $("#aBtn"); btn.disabled = true;
    try {
      const d = await api("/api/auth/" + mode, { body });
      ls.set("sfh_token", d.token); ls.set("sfh_user", d.user); S.user = d.user; renderAuthArea(); closeModal();
      toast(`Welcome, ${d.user.name}!`);
      if (d.user.role === "admin") location.hash = "#/admin"; else route();
    } catch (err) { $("#aErr").textContent = err.message; btn.disabled = false; }
  };
}

/* ============================== ABOUT / REVIEWS ============================== */
const OWNER_TEXT = "Hello and welcome! I'm Eman Fatima, the owner of this website.\n\nI built this assistant for Saira Fashion House so you can check stock, place orders and track parcels at any hour, even after the store closes. Every answer comes from our real catalog and policy. When something needs a person, the assistant says so honestly and passes your case to our team — and you can keep shopping meanwhile.\n\nI hope you enjoy shopping with us!";
async function openAbout() {
  const rev = await api("/api/reviews").catch(() => ({ count: 0 }));
  let stars = 0;
  openModal(`<div class="p-7">
    <button data-close-modal class="absolute right-4 top-4 grid h-8 w-8 place-items-center rounded-full hover:bg-slate-100 dark:hover:bg-slate-800" aria-label="Close"><svg class="h-5 w-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg></button>
    <div class="flex gap-5"><img src="/assets/char_greeting.webp" alt="Eman Fatima" class="h-40 w-28 shrink-0 rounded-2xl bg-sand-100 object-cover object-top">
      <div><h2 class="font-display text-2xl font-bold">Eman Fatima</h2><div class="text-xs font-bold uppercase tracking-wide text-slate-400">Owner · Saira Fashion House</div><p id="ownerText" class="mt-3 min-h-[120px] whitespace-pre-wrap text-sm leading-relaxed"></p></div></div>
    <div class="mt-5 border-t border-slate-200 pt-5 dark:border-slate-800">
      <div class="flex items-center gap-2"><b>Rate your experience</b>${rev.count ? `<span class="ml-auto text-sm text-slate-500">★ ${rev.average} from ${rev.count} review(s)</span>` : ""}</div>
      <div id="stars" class="mt-2 flex gap-1 text-3xl">${[1, 2, 3, 4, 5].map((n) => `<button data-s="${n}" class="text-slate-300 transition hover:scale-110" aria-label="${n} star">★</button>`).join("")}</div>
      <textarea id="revText" rows="2" class="input mt-2" placeholder="Anything you'd like to tell us? (optional)" maxlength="500"></textarea>
      <div class="mt-2 flex items-center gap-2"><button id="revBtn" class="btn-primary btn-sm">Submit review</button><span id="revNote" class="text-xs text-slate-500">${S.user ? "" : "Sign in to leave a review."}</span></div>
      ${(rev.recent || []).length ? `<ul class="mt-4 space-y-2">${rev.recent.map((r) => `<li class="rounded-xl bg-sand-100 p-3 text-sm dark:bg-slate-800"><span class="text-amber-500">${"★".repeat(r.stars)}</span> “${esc(r.comment)}” <span class="text-xs text-slate-500">— ${esc(r.user_name || "Customer")}</span></li>`).join("")}</ul>` : ""}
    </div></div>`, false);
  let i = 0; clearInterval(S.typeTimer);
  S.typeTimer = setInterval(() => { const el = $("#ownerText"); if (!el) return clearInterval(S.typeTimer); el.textContent = OWNER_TEXT.slice(0, ++i); if (i >= OWNER_TEXT.length) clearInterval(S.typeTimer); }, 12);
  const paint = (n) => $$("#stars button").forEach((b) => b.classList.toggle("text-amber-400", Number(b.dataset.s) <= n));
  $$("#stars button").forEach((b) => { b.onmouseenter = () => paint(Number(b.dataset.s)); b.onmouseleave = () => paint(stars); b.onclick = () => { stars = Number(b.dataset.s); paint(stars); }; });
  $("#revBtn").onclick = async () => {
    if (!S.user) { openAuth("login"); return; }
    if (!stars) { $("#revNote").textContent = "Pick 1 to 5 stars first."; return; }
    try { const d = await api("/api/reviews", { body: { stars, comment: $("#revText").value.trim() || null } }); $("#revNote").textContent = `Thank you! Average is now ★ ${d.summary.average}.`; toast("Thanks for your review!"); }
    catch (e) { $("#revNote").textContent = e.message; }
  };
}

/* ============================== CHAT ============================== */
const HELPER = { greeting: "/assets/char_greeting.webp", happy: "/assets/char_happy.webp", sorry: "/assets/char_sorry.webp" };
function setHelper(state, cap) { $("#helperImg").src = HELPER[state] || HELPER.greeting; if (cap) $("#helperCap").textContent = cap; }
function openChat() {
  $("#chatPanel").classList.remove("hidden"); $("#chatBadge").classList.add("hidden");
  if (!S.chatLoaded) loadHistory();
  setTimeout(() => $("#chatText").focus(), 50);
}
function closeChat() { $("#chatPanel").classList.add("hidden"); }
async function ask(text) { openChat(); if (!S.chatLoaded) await loadHistory(); send(text); }

function fmt(text) { return esc(text).replace(/\n/g, "<br>").replace(/(#\d{4,6})/g, '<b>$1</b>'); }
function traceHtml(tr) {
  if (!tr) return "";
  const dc = { ACT: "bg-emerald-100 text-emerald-800", ASK: "bg-amber-100 text-amber-800", BLOCK: "bg-rose-100 text-rose-700", HANDOFF: "bg-violet-100 text-violet-800" }[tr.decision] || "bg-slate-100";
  const ml = tr.ml ? `${esc(tr.ml.intent)} ${(tr.ml.confidence * 100).toFixed(0)}%` : "—";
  return `<details class="trace mt-1.5 ${S.trace ? "" : "hidden"} rounded-xl bg-slate-50 p-2 text-[11px] text-slate-600 dark:bg-slate-950 dark:text-slate-400"><summary class="cursor-pointer font-bold"><span class="pill ${dc}">${esc(tr.decision)}</span> ${esc(tr.action)} · ${Math.round((tr.confidence || 0) * 100)}%</summary>
    <dl class="mt-1 grid grid-cols-[70px_1fr] gap-x-2 gap-y-0.5"><dt>intent</dt><dd>${esc(tr.intent)}</dd><dt>route</dt><dd>${esc(tr.route || "—")}</dd><dt>model</dt><dd>${ml}</dd>${tr.reason ? `<dt>reason</dt><dd>${esc(tr.reason)}</dd>` : ""}
    ${Object.keys(tr.entities || {}).length ? `<dt>entities</dt><dd class="break-all font-mono">${esc(JSON.stringify(tr.entities))}</dd>` : ""}
    ${(tr.tools || []).length ? `<dt>tools</dt><dd class="break-all font-mono">${tr.tools.map((t) => esc(t.name + " " + JSON.stringify(t.result).slice(0, 160))).join("<br>")}</dd>` : ""}</dl></details>`;
}
function msgHtml(m) {
  if (m.role === "customer") return `<div class="flex justify-end"><div class="max-w-[85%] whitespace-pre-wrap break-words rounded-2xl rounded-tr-md bg-brand-600 px-3.5 py-2 text-sm text-white">${esc(m.text)}</div></div>`;
  if (m.role === "staff") return `<div class="flex"><div class="max-w-[88%] rounded-2xl rounded-tl-md border border-violet-200 bg-violet-50 px-3.5 py-2 text-sm text-violet-900 dark:border-violet-900 dark:bg-violet-950 dark:text-violet-100"><div class="mb-0.5 text-[10px] font-bold uppercase tracking-wide opacity-70">👩‍💼 Staff${m.meta?.by ? " · " + esc(m.meta.by) : ""}</div>${fmt(m.text)}</div></div>`;
  const meta = m.meta || {};
  const prods = (meta.products || []).map((p) => `<button data-open="${esc(p.product_id)}" ${p.color ? `data-color="${esc(p.color)}"` : ""} ${p.size ? `data-size="${esc(p.size)}"` : ""} class="w-32 shrink-0 overflow-hidden rounded-xl border border-slate-200 bg-white text-left transition hover:border-brand-400 dark:border-slate-700 dark:bg-slate-900">
      ${art(p, p.color || p.colors?.[0], "aspect-[6/5] w-full")}<div class="p-2"><div class="truncate text-xs font-bold">${esc(p.name)}</div><div class="text-[11px] text-slate-500">${p.color ? esc(p.color) + (p.size ? " · " + esc(p.size) : "") : (p.colors || []).length + " colors"}</div><div class="text-xs font-bold text-brand-700 dark:text-brand-300">${money(p.price)}</div></div></button>`).join("");
  const order = meta.order ? `<div class="mt-2 rounded-xl bg-emerald-50 p-2.5 text-xs text-emerald-900 dark:bg-emerald-950 dark:text-emerald-100">✅ Order ${meta.order.order_ids.map((i) => "#" + i).join(", ")} · ${money(meta.order.grand_total)}<a class="ml-2 font-bold underline" href="#/track?order=${meta.order.order_ids[0]}&phone=${esc(meta.order.phone || "")}">Track</a></div>` : "";
  const ticket = meta.ticket ? `<div class="mt-1.5"><span class="pill bg-violet-100 text-violet-800 dark:bg-violet-950 dark:text-violet-200">🎫 Ticket #${meta.ticket} · staff notified</span></div>` : "";
  return `<div class="flex gap-2"><img src="/assets/char_greeting.webp" alt="" class="mt-1 h-7 w-7 shrink-0 rounded-full bg-sand-100 object-cover object-top">
    <div class="min-w-0 max-w-[88%]"><div class="break-words rounded-2xl rounded-tl-md bg-white px-3.5 py-2 text-sm shadow-sm dark:bg-slate-800">${fmt(m.text)}${order}${ticket}</div>
    ${prods ? `<div class="scrollbar-thin mt-2 flex gap-2 overflow-x-auto pb-1">${prods}</div>` : ""}${traceHtml(meta.trace)}</div></div>`;
}
function appendMsg(m) { const box = $("#msgs"); box.insertAdjacentHTML("beforeend", msgHtml(m)); box.scrollTop = box.scrollHeight; }
function setSuggestions(list) {
  $("#sugg").innerHTML = (list || []).map((s) => `<button class="chip shrink-0" data-say="${esc(s)}">${esc(s)}</button>`).join("");
  $("#sugg").classList.toggle("hidden", !(list || []).length);
}
function greetLocal() {
  appendMsg({ role: "agent", text: "Assalam o alaikum! I'm Eman, the shopping assistant of Saira Fashion House. 👋\nAsk me about products, prices, stock, delivery or your order — in English or Roman Urdu.", meta: {} });
  setSuggestions(["Show me kurtas", "Black linen kurta M mein hai?", "Women kurti under 2500", "Track my order", "Delivery charges to Karachi?"]);
}
function setTickets(ids) {
  S.openTickets = ids || [];
  const bar = $("#ticketBar");
  bar.classList.toggle("hidden", !S.openTickets.length); bar.classList.toggle("flex", !!S.openTickets.length);
  bar.innerHTML = S.openTickets.length ? `🎫 Ticket${S.openTickets.length > 1 ? "s" : ""} ${S.openTickets.map((i) => "#" + i).join(", ")} open — our staff will reply here (10 AM – 8 PM). You can keep chatting meanwhile.` : "";
}
async function loadHistory() {
  S.chatLoaded = true;
  $("#msgs").innerHTML = "";
  try {
    const d = await api(`/api/chat/history?session_id=${encodeURIComponent(S.sid)}`);
    if (!d.messages.length) greetLocal();
    d.messages.forEach(appendMsg);
    const lastAgent = [...d.messages].reverse().find((m) => m.role === "agent");
    if (lastAgent) setSuggestions(lastAgent.meta?.suggestions);
    S.lastMsgId = d.messages.length ? d.messages[d.messages.length - 1].id : 0;
    setTickets(d.open_tickets);
  } catch { greetLocal(); }
}
let sending = false;
async function send(text) {
  text = (text || "").trim();
  if (!text || sending) return;
  sending = true;
  $("#chatText").value = ""; autoGrow();
  appendMsg({ role: "customer", text });
  setSuggestions([]);
  const box = $("#msgs");
  box.insertAdjacentHTML("beforeend", `<div id="typing" class="flex gap-2"><img src="/assets/char_greeting.webp" alt="" class="h-7 w-7 rounded-full bg-sand-100 object-cover object-top"><div class="flex gap-1 rounded-2xl bg-white px-4 py-3 shadow-sm dark:bg-slate-800">${[0, 1, 2].map((i) => `<i class="h-1.5 w-1.5 animate-blink rounded-full bg-brand-500" style="animation-delay:${i * 0.18}s"></i>`).join("")}</div></div>`);
  box.scrollTop = box.scrollHeight;
  setHelper("greeting", "Checking the catalog and policy…");
  try {
    const r = await api("/api/chat", { body: { session_id: S.sid, message: text } });
    $("#typing")?.remove();
    appendMsg({ role: "agent", text: r.reply, meta: { products: r.products, trace: r.trace, order: r.order, ticket: r.ticket } });
    setSuggestions(r.suggestions);
    S.lastMsgId = Math.max(S.lastMsgId, r.message_id || 0);
    const tr = r.trace || {};
    if (tr.decision === "HANDOFF" || tr.decision === "BLOCK") setHelper("sorry", "A team member will help with this — you can keep chatting.");
    else if (["create_order", "create_exchange"].includes(tr.action)) setHelper("happy", "Done! All sorted. 🎉");
    else setHelper("greeting", "AI assistant · staff available 10 AM – 8 PM");
    if (r.ticket && !(S.openTickets || []).includes(r.ticket)) setTickets([...(S.openTickets || []), r.ticket]);
    if (r.order) toast(`Order ${r.order.order_ids.map((i) => "#" + i).join(", ")} confirmed!`);
  } catch (e) {
    $("#typing")?.remove();
    appendMsg({ role: "agent", text: "Sorry — I couldn't reach the server. Please check your connection and try again.", meta: {} });
    setHelper("sorry", "Connection problem");
  } finally { sending = false; }
}
async function pollStaff() {
  if (!S.openTickets?.length && $("#chatPanel").classList.contains("hidden")) return;
  try {
    const d = await api(`/api/chat/history?session_id=${encodeURIComponent(S.sid)}&after=${S.lastMsgId}`);
    d.messages.forEach((m) => {
      S.lastMsgId = Math.max(S.lastMsgId, m.id);
      if (m.role === "staff") { appendMsg(m); if ($("#chatPanel").classList.contains("hidden")) { $("#chatBadge").classList.remove("hidden"); toast("New reply from our staff 💬", "info"); } }
    });
    setTickets(d.open_tickets);
  } catch {}
}
setInterval(pollStaff, 6000);
function autoGrow() { const t = $("#chatText"); t.style.height = "auto"; t.style.height = Math.min(128, t.scrollHeight) + "px"; }
$("#chatText").addEventListener("input", autoGrow);
$("#chatText").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send($("#chatText").value); } });
$("#chatForm").onsubmit = (e) => { e.preventDefault(); send($("#chatText").value); };
$("#chatFab").onclick = openChat;
$("#newChat").onclick = () => { S.sid = newSid(); S.lastMsgId = 0; $("#msgs").innerHTML = ""; setTickets([]); greetLocal(); setHelper("greeting", "New conversation"); };
const paintTraceBtn = () => { $("#traceToggle").textContent = "Reasoning: " + (S.trace ? "on" : "off"); $("#traceToggle").classList.toggle("bg-white/20", S.trace); };
$("#traceToggle").onclick = () => { S.trace = !S.trace; ls.set("sfh_trace", S.trace); $$("#msgs .trace").forEach((d) => d.classList.toggle("hidden", !S.trace)); paintTraceBtn(); };
paintTraceBtn();

/* ============================== global clicks ============================== */
document.addEventListener("click", (e) => {
  const t = e.target.closest("[data-open],[data-ask],[data-say],[data-chat],[data-auth],[data-close-modal],[data-close-cart],[data-close-chat]");
  if (!t) return;
  if (t.dataset.open !== undefined) { e.preventDefault(); openProduct(t.dataset.open, { color: t.dataset.color, size: t.dataset.size }); }
  else if (t.dataset.ask !== undefined) { e.preventDefault(); closeModal(); ask(t.dataset.ask); }
  else if (t.dataset.say !== undefined) { send(t.dataset.say); }
  else if (t.dataset.chat !== undefined) { openChat(); }
  else if (t.dataset.auth !== undefined) { openAuth(t.dataset.auth); }
  else if (t.dataset.closeModal !== undefined) { closeModal(); }
  else if (t.dataset.closeCart !== undefined) { closeCart(); }
  else if (t.dataset.closeChat !== undefined) { closeChat(); }
});
$("#cartBtn").onclick = openCart;
$("#menuBtn").onclick = () => $("#mobileNav").classList.toggle("hidden");
$("#aboutBtn").onclick = openAbout;
const goSearch = (q) => { location.hash = "#/shop?q=" + encodeURIComponent(q.trim()); };
$("#headSearch").onsubmit = (e) => { e.preventDefault(); goSearch($("#headQ").value); };
$("#mSearch").onsubmit = (e) => { e.preventDefault(); goSearch($("#mQ").value); };

/* ============================== theme ============================== */
const MOON = '<svg class="h-5 w-5" fill="currentColor" viewBox="0 0 24 24"><path d="M20.6 14.2A8.5 8.5 0 0 1 9.8 3.4a.6.6 0 0 0-.8-.8 10.5 10.5 0 1 0 12.4 12.4.6.6 0 0 0-.8-.8z"/></svg>';
const SUN = '<svg class="h-5 w-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4m11.4-11.4 1.4-1.4"/></svg>';
function applyTheme(t) { document.documentElement.classList.toggle("dark", t === "dark"); $("#themeBtn").innerHTML = t === "dark" ? SUN : MOON; ls.set("sfh_theme", t); }
$("#themeBtn").onclick = () => applyTheme(document.documentElement.classList.contains("dark") ? "light" : "dark");
applyTheme(ls.get("sfh_theme", matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));

/* ============================== boot ============================== */
(async function boot() {
  saveCart(); renderAuthArea();
  try {
    S.status = await api("/api/status");
    $("#srvDot").className = "h-2 w-2 rounded-full bg-emerald-400";
    $("#srvText").textContent = `Store open · ${S.status.products} products · ${S.status.categories} categories`;
  } catch {
    $("#srvDot").className = "h-2 w-2 rounded-full bg-rose-500";
    $("#srvText").textContent = "server offline — run python app.py";
  }
  if (S.user) {   // refresh the session; drop a stale token
    try { const d = await api("/api/auth/me"); if (!d.user) throw 0; S.user = d.user; ls.set("sfh_user", d.user); }
    catch { ls.del("sfh_token"); ls.del("sfh_user"); S.user = null; }
    renderAuthArea();
  }
  route();
  try { const d = await api(`/api/chat/history?session_id=${encodeURIComponent(S.sid)}`); setTickets(d.open_tickets); S.lastMsgId = d.messages.length ? d.messages[d.messages.length - 1].id : 0; } catch {}
})();
