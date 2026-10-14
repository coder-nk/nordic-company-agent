/* Signalpost UI - vanilla single-page app over the local JSON API (ui_server.py). No dependencies. */
"use strict";

/* ------------------------------------------------------------------ helpers */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const state = { index: [], stats: null, byOrg: new Map(), filter: "all", q: "", sort: "name", shown: 40, cache: new Map(), job: null, pollT: null };

const P = {
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  building: '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M9 7h1M14 7h1M9 11h1M14 11h1M9 15h1M14 15h1M10 21v-3h4v3"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.8-3.6 3.4-5.5 6.5-5.5s5.7 1.9 6.5 5.5"/><circle cx="17" cy="9" r="2.6"/><path d="M16.5 14.6c2.6.3 4.4 2 5 5"/>',
  pin: '<path d="M12 21s-7-6.2-7-11.5A7 7 0 0 1 19 9.5C19 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.7 3.8 5.7 3.8 9s-1.3 6.3-3.8 9c-2.5-2.7-3.8-5.7-3.8-9S9.5 5.7 12 3z"/>',
  chart: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  shield: '<path d="M12 3 4.5 6v5.5c0 4.6 3.2 8.3 7.5 9.5 4.3-1.2 7.5-4.9 7.5-9.5V6L12 3z"/><path d="m8.8 12 2.2 2.2 4.4-4.4"/>',
  alert: '<path d="M12 3 2 20h20L12 3z"/><path d="M12 10v4M12 17.5v.5"/>',
  ext: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  spark: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6"/>',
  brief: '<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M9 7V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2M3 13h18"/>',
  coins: '<ellipse cx="9" cy="7" rx="6" ry="3"/><path d="M3 7v4c0 1.7 2.7 3 6 3s6-1.3 6-3V7M3 11v4c0 1.7 2.7 3 6 3 1.2 0 2.3-.2 3.2-.5"/><circle cx="17" cy="16" r="4"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  back: '<path d="M15 6l-6 6 6 6"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon: '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5 9-5z"/><path d="m3 13 9 5 9-5"/>',
  file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5z"/><path d="M14 3v5h5M9 13h6M9 17h6"/>',
  bolt: '<path d="M13 2 4 14h7l-1 8 9-12h-7l1-8z"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
  trend: '<path d="m3 17 6-6 4 4 8-8"/><path d="M15 7h6v6"/>',
  down: '<path d="m3 7 6 6 4-4 8 8"/><path d="M15 17h6v-6"/>',
  news: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 8h6M7 12h10M7 16h10"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  refresh: '<path d="M20 11a8 8 0 0 0-14.6-4.5L4 8M4 4v4h4M4 13a8 8 0 0 0 14.6 4.5L20 16M20 20v-4h-4"/>',
  db: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
  eye: '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  question: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.5v.7M12 17v.5"/>',
};
const ico = (n, cls = "") => `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${P[n] || ""}</svg>`;

/* plain-language field dictionary */
const F = {
  legal_name: ["Legal name", "The official name registered in Enhetsregisteret, Norway's central business register."],
  organisation_number: ["Organisation number", "The unique 9-digit number every Norwegian legal entity gets. Everything here is matched to it."],
  legal_form: ["Legal form", "The type of entity, e.g. AS (private limited company), ASA (public limited), ENK (sole proprietorship)."],
  registry_status: ["Register status", "Whether the entity is active, bankrupt, under liquidation or deleted."],
  founding_date: ["Founded", "The date the company was founded, as registered."],
  registration_date: ["Registered", "The date it was entered in the business register."],
  business_address: ["Business address", "The address where the business is run, as registered."],
  postal_address: ["Postal address", "Where the company receives mail."],
  municipality: ["Municipality", "The municipality (kommune) of the business address."],
  county: ["County", "The county (fylke) of the business address."],
  industry: ["Industry (NACE)", "The official industry classification code chosen when registering."],
  sector: ["Sector", "The institutional sector code used by Statistics Norway."],
  employees: ["Employees", "Number of employees reported to NAV's employer register. Small counts are published as ranges."],
  share_capital: ["Share capital", "The registered share capital paid into the company."],
  activity: ["Registered activity", "What the company says it does, as written in the register."],
  statutory_purpose: ["Statutory purpose", "The purpose stated in the articles of association."],
  vat_registration: ["VAT registered", "Whether it is in the VAT register (Merverdiavgiftsregisteret)."],
  business_register_registration: ["Business register", "Whether it is in Foretaksregisteret (required for most businesses)."],
  audit_exemption: ["Audit exemption", "Small companies may choose not to have an auditor."],
  signature_rule: ["Who can sign", "Who can legally bind the company (signatur)."],
  prokura_rule: ["Prokura", "Who has general power of attorney to act for the company."],
  lei: ["LEI code", "Legal Entity Identifier, a global ID used in financial reporting."],
  lei_status: ["LEI status", "Whether the LEI registration is kept up to date."],
  registry_website: ["Website (as filed)", "The website the company itself entered in the register. Shared addresses (e.g. a property manager's) are marked as related."],
  registry_email: ["E-mail (as filed)", "E-mail address filed in the register."],
  registry_phone: ["Phone (as filed)", "Phone number filed in the register."],
  registry_mobile: ["Mobile (as filed)", "Mobile number filed in the register."],
  former_name: ["Former name", "A name the company used earlier."],
  group_membership: ["Part of a group", "Whether the register marks it as part of a group (konsern)."],
  revenue: ["Revenue", "Total operating income for the financial year."],
  sales_revenue: ["Sales revenue", "Income from selling goods and services."],
  operating_profit: ["Operating result", "Revenue minus operating costs, before financial items and tax."],
  net_profit: ["Net result", "What is left after all costs, financial items and tax. Negative means a loss."],
  profit_before_tax: ["Result before tax", "Operating result plus financial items, before tax."],
  equity: ["Equity", "What the company owns minus what it owes. Negative equity is a warning sign."],
  total_assets: ["Total assets", "Everything the company owns, at year end."],
  total_liabilities: ["Total debt", "Everything the company owes, at year end."],
  equity_ratio: ["Equity ratio", "Equity as a share of total assets. Higher means more financially solid."],
  operating_margin: ["Operating margin", "Operating result as a share of revenue."],
  cash_and_bank: ["Cash and bank", "Cash and bank deposits at year end."],
  payroll_expenses: ["Payroll costs", "Salaries, social security and pensions."],
  accounts_period: ["Accounting period", "The financial year the figures cover."],
  annual_report_copy: ["Filed annual report", "A scanned copy of the annual accounts as filed."],
  latest_submitted_accounts_year: ["Latest filed year", "The most recent year with filed annual accounts."],
  ceo: ["General manager", "Daglig leder: the person responsible for day-to-day management."],
  chair: ["Chair of the board", "Styrets leder: leads the board of directors."],
  deputy_chair: ["Deputy chair", "Nestleder in the board."],
  board_member: ["Board member", "Styremedlem: member of the board of directors."],
  deputy_board_member: ["Deputy board member", "Varamedlem: steps in when a board member cannot attend."],
  auditor: ["Auditor", "The audit firm that checks the annual accounts."],
  accountant: ["Accountant", "The firm that keeps the accounts."],
  owner: ["Owner", "Registered owner (for sole proprietorships and some partnerships)."],
  business_manager: ["Business manager", "Forretningsfører: an external manager, common for housing co-operatives."],
  contact_person: ["Contact person", "Registered contact person."],
  registered_workplace: ["Workplace", "A registered sub-unit (underenhet), such as a shop, office or plant."],
  registered_workplace_count: ["Workplaces", "Number of registered sub-units."],
  parent_company: ["Parent company", "The company that owns this one (from the register's group data)."],
  ultimate_parent: ["Top parent", "The company at the top of the group."],
  group_subsidiary: ["Subsidiary", "A company this one owns."],
  lei_parent: ["Parent (LEI)", "Parent company according to the global LEI system."],
  official_website: ["Verified website", "Only shown when we could prove the site belongs to this exact company."],
  website_verification: ["How the website was verified", "The proof we found that the website belongs to this company."],
  website_title: ["Website title", "The title of the company's home page."],
  website_description: ["Self-description", "How the company describes itself on its own website (company-reported)."],
  website_language: ["Website language", "Language declared by the website."],
  website_address: ["Address on website", "Address shown on the website (published only when it equals the registered address)."],
  brand_name: ["Brand", "The name the company uses publicly."],
  logo_url: ["Logo", "The company's logo file on its own site."],
  contact_email: ["E-mail", "A general mailbox shown on the company's own website."],
  contact_phone: ["Phone", "A phone number shown on the company's own website."],
  social_profile: ["Social profile", "A profile the company's own website links to, with a matching name."],
  careers_page: ["Careers page", "The jobs page on the company's own website."],
  news_page: ["News page", "The news or blog section on the company's own website."],
  news_item: ["News", "A dated post from the company's own website or feed."],
  job_posting: ["Job ad", "An active job advertisement on NAV Arbeidsplassen."],
  active_job_count: ["Active job ads", "Number of active job ads on NAV Arbeidsplassen."],
  hiring_status: ["Hiring status", "Whether the company has active job ads on NAV."],
  registry_event: ["Registry update", "A dated change recorded by Brønnøysundregistrene."],
  role_change_event: ["Role change", "A dated change to the board or management."],
  state_aid_grant: ["Public support", "A grant or aid registered in Støtteregisteret."],
  tax_incentive_project: ["SkatteFUNN project", "An R&D project in the SkatteFUNN tax-incentive scheme."],
  research_grant: ["Research Council project", "An application or grant from the Research Council of Norway."],
  food_inspection: ["Food-safety inspection", "A Mattilsynet inspection at one of its locations (smiley rating)."],
  wikidata_item: ["Wikidata", "The company's entry in Wikidata."],
  encyclopedia_entry: ["Wikipedia", "A Wikipedia article about the company."],
  stock_listing: ["Stock listing", "Where its shares are listed."],
};
const label = (f) => (F[f] ? F[f][0] : f.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase()));
const helpTxt = (f) => (F[f] ? F[f][1] : "");
const help = (f) => (helpTxt(f) ? `<span class="help" data-tip="${esc(helpTxt(f))}" aria-label="${esc(helpTxt(f))}">?</span>` : "");

const FAMILIES = {
  legal_identity: ["Legal identity", "Name, form, address, status, industry"],
  annual_accounts: ["Annual accounts", "Revenue, results, balance sheet"],
  leadership: ["Leadership", "Management, board, auditor"],
  workplaces: ["Workplaces", "Registered sub-units"],
  website: ["Verified website", "Identity-checked official site"],
  social_profiles: ["Social profiles", "Linked from the verified site"],
  hiring: ["Hiring", "Active job ads on NAV"],
  public_activity: ["Public activity", "Registry events, news, grants, inspections"],
  corporate_structure: ["Group & ownership", "Parents and subsidiaries"],
};
const MODULES = [
  ["registry", "Business register", "Enhetsregisteret: identity, address, industry, status", "building"],
  ["registry_feed", "Registry change feed", "Dated updates from Brønnøysundregistrene", "clock"],
  ["accounts", "Annual accounts", "Regnskapsregisteret: the last three filed years", "chart"],
  ["roles", "Board & management", "General manager, board, auditor", "users"],
  ["subunits", "Workplaces", "Registered shops, offices and plants", "pin"],
  ["website", "Official website", "Found and identity-checked before anything is used", "globe"],
  ["group", "Group structure", "Parent and subsidiaries", "layers"],
  ["gleif", "LEI register", "GLEIF: global legal entity identifier", "shield"],
  ["wikidata", "Wikidata", "Encyclopedia entries linked to the org. number", "info"],
  ["jobs", "Job ads", "NAV Arbeidsplassen", "brief"],
  ["state_aid", "Public support", "Støtteregisteret grants", "coins"],
  ["public_records", "Public records", "SkatteFUNN, Research Council, Mattilsynet", "file"],
  ["authorisations", "Signature & prokura", "Who can sign for the company", "file"],
  ["account_copies", "Filed reports", "Scanned annual-report copies", "file"],
];
const STATE_TXT = { available: "Found", not_available: "Nothing found", not_applicable: "Not applicable", ambiguous: "Unclear match", blocked: "Blocked by site", failed: "Could not check" };
const STATE_HELP = {
  available: "Found and verified, with a source you can open.",
  not_available: "We looked in the source and it has no such information.",
  not_applicable: "Does not apply to this type of entity.",
  ambiguous: "Something was found but we could not prove it belongs to this exact company, so it is not shown as a fact.",
  blocked: "The source refused automated access (robots.txt or similar).",
  failed: "The check could not be completed in this run (time or request budget, or a network error).",
};

function money(v, cur = "NOK") {
  if (v === null || v === undefined || v === "" || isNaN(+v)) return "—";
  const n = +v, a = Math.abs(n);
  let s;
  if (a >= 1e9) s = (a / 1e9).toFixed(a >= 1e10 ? 0 : 1) + " bn";
  else if (a >= 1e6) s = (a / 1e6).toFixed(a >= 1e8 ? 0 : 1) + " m";
  else if (a >= 1e3) s = Math.round(a / 1e3) + " k";
  else s = String(Math.round(a));
  return `${cur} ${n < 0 ? "−" : ""}${s}`.trim();
}
const fmtInt = (n) => (n === null || n === undefined ? "—" : Number(n).toLocaleString("en-US"));
const fmtOrg = (o) => (o ? String(o).replace(/(\d{3})(\d{3})(\d{3})/, "$1 $2 $3") : "");
function rel(iso) {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (isNaN(t)) return iso;
  const s = (Date.now() - t) / 1000;
  if (s < 90) return "just now";
  if (s < 3600) return Math.round(s / 60) + " min ago";
  if (s < 86400 * 2) return Math.round(s / 3600) + " h ago";
  if (s < 86400 * 60) return Math.round(s / 86400) + " days ago";
  return new Date(t).toISOString().slice(0, 10);
}
const date10 = (s) => (s ? String(s).slice(0, 10) : "");
function hashHue(s) { let h = 0; for (const c of String(s)) h = (h * 31 + c.charCodeAt(0)) >>> 0; return h % 360; }
function initials(name) {
  const stop = new Set(["AS", "ASA", "SA", "DA", "ANS", "ENK", "BA", "NUF", "KS", "IKS", "SF", "AB", "OG", "&", "-"]);
  const w = String(name || "?").replace(/[^\p{L}\p{N}& -]/gu, " ").split(/\s+/).filter((x) => x && !stop.has(x.toUpperCase()));
  return ((w[0] || "?")[0] + (w[1] ? w[1][0] : (w[0] || "").slice(1, 2))).toUpperCase();
}
const TONES = [["#2f8f6b", "#1d5c45"], ["#4f8197", "#2f5566"], ["#5d72a0", "#3d4d72"], ["#a87b35", "#6f5122"],
  ["#a2565b", "#6b373b"], ["#3b8686", "#255757"], ["#738553", "#4c5a35"], ["#5b6f86", "#3a4859"]];
function avatar(name, cls = "") {
  const t = TONES[hashHue(name) % TONES.length];
  return `<span class="avatar ${cls}" style="background:linear-gradient(135deg,${t[0]},${t[1]})" aria-hidden="true">${esc(initials(name))}</span>`;
}
function mod11(o) {
  if (!/^\d{9}$/.test(o)) return false;
  const w = [3, 2, 7, 6, 5, 4, 3, 2];
  let s = 0;
  for (let i = 0; i < 8; i++) s += +o[i] * w[i];
  let r = 11 - (s % 11);
  if (r === 11) r = 0;
  return r !== 10 && r === +o[8];
}
const digits = (s) => String(s || "").replace(/\D/g, "");
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("on");
  clearTimeout(toast.t);
  toast.t = setTimeout(() => t.classList.remove("on"), 2200);
}
async function api(path, opts) {
  const r = await fetch(path, opts);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(j.error || r.statusText), { status: r.status, body: j });
  return j;
}
function countUp(root = document) {
  $$("[data-count]", root).forEach((el) => {
    const end = +el.dataset.count, fmt = el.dataset.fmt || "int";
    const show = (v) => (el.textContent = fmt === "pct" ? v.toFixed(0) + "%" : fmtInt(Math.round(v)));
    if (REDUCED || !end) return show(end);
    const t0 = performance.now(), d = 1100;
    const step = (t) => { const k = Math.min(1, (t - t0) / d); show(end * (1 - Math.pow(1 - k, 3))); if (k < 1) requestAnimationFrame(step); };
    requestAnimationFrame(step);
  });
  requestAnimationFrame(() => $$("[data-w]", root).forEach((el) => (el.style.clipPath = `inset(0 ${100 - Math.max(+el.dataset.w, 0.6)}% 0 0 round 99px)`)));
}
const stagger = (i) => `style="animation-delay:${Math.min(i, 14) * 45}ms"`;
const pill = (s) => `<span class="chip ${esc(s.polarity || "info")}"><i></i>${esc(s.text)}</span>`;

/* ------------------------------------------------------------------ search */
function score(r, q) {
  if (!q) return 1;
  const n = String(r.name || "").toLowerCase(), d = digits(q);
  if (d.length >= 3 && String(r.orgnr).startsWith(d)) return 100 - (9 - d.length);
  let s = 0;
  if (n.startsWith(q)) s += 60;
  else if (n.includes(" " + q)) s += 45;
  else if (n.includes(q)) s += 30;
  if (String(r.municipality || "").toLowerCase().startsWith(q)) s += 12;
  if (String(r.industry || "").toLowerCase().includes(q)) s += 6;
  return s;
}
function searchIndex(q, n = 8) {
  q = q.trim().toLowerCase();
  if (!q) return [];
  return state.index.map((r) => [score(r, q), r]).filter((x) => x[0] > 0).sort((a, b) => b[0] - a[0] || String(a[1].name).localeCompare(b[1].name)).slice(0, n).map((x) => x[1]);
}
function optRow(r, i, on) {
  const sub = [fmtOrg(r.orgnr), r.municipality && r.municipality[0] + r.municipality.slice(1).toLowerCase(), r.industry].filter(Boolean).join(" · ");
  return `<a class="opt ${on ? "on" : ""}" data-i="${i}" href="#/company/${esc(r.orgnr)}">${avatar(r.name)}<div style="min-width:0"><b>${esc(r.name)}</b><span>${esc(sub)}</span></div><span class="r">${r.fin && r.fin.revenue != null ? esc(money(r.fin.revenue, r.fin.currency)) : ""}</span></a>`;
}
function researchOpt(d, i, on) {
  return `<a class="opt ${on ? "on" : ""}" data-i="${i}" href="#/research/${d}"><span class="avatar" style="background:var(--aurora);color:var(--aurora-ink)">${ico("bolt")}</span><div><b>Research ${fmtOrg(d)} now</b><span>Not in your library yet · runs the full agent (about 30 s)</span></div><span class="r">↵</span></a>`;
}

/* palette */
const pal = { sel: 0, items: [] };
function openPal(q = "") {
  $("#pal").classList.add("on");
  $("#scrim").classList.add("on");
  const inp = $("#palIn");
  inp.value = q;
  renderPal();
  setTimeout(() => inp.focus(), 30);
}
function closePal() { $("#pal").classList.remove("on"); if (!$("#drawer").classList.contains("on")) $("#scrim").classList.remove("on"); }
function renderPal() {
  const q = $("#palIn").value, d = digits(q);
  const res = searchIndex(q, 8);
  const rows = res.map((r, i) => optRow(r, i, i === pal.sel));
  pal.items = res.map((r) => "#/company/" + r.orgnr);
  if (d.length === 9 && mod11(d) && !state.byOrg.has(d)) { rows.unshift(researchOpt(d, 0, pal.sel === 0)); pal.items.unshift("#/research/" + d); }
  if (!q.trim()) {
    const nav = [["#/companies", "Browse all companies", "Filter by risk, hiring, growth and more", "list"], ["#/research", "Research a company", "Run the agent on any organisation number", "bolt"], ["#/about", "How it works", "Sources, verification and what each label means", "question"]];
    $("#palRes").innerHTML = nav.map((n, i) => `<a class="opt ${i === pal.sel ? "on" : ""}" data-i="${i}" href="${n[0]}"><span class="avatar" style="background:var(--glass-3);color:var(--ink)">${ico(n[3] === "list" ? "layers" : n[3])}</span><div><b>${n[1]}</b><span>${n[2]}</span></div></a>`).join("");
    pal.items = nav.map((n) => n[0]);
    return;
  }
  $("#palRes").innerHTML = rows.join("") || `<div class="empty small">No company matches “${esc(q)}”. Type a 9-digit organisation number to research it.</div>`;
}

/* ------------------------------------------------------------------ views: home */
function viewHome() {
  const st = state.stats || {};
  const ds = (st.datasets || []).map((d) => d.report).filter(Boolean);
  const req = ds.reduce((a, d) => a + (d.requests || 0), 0);
  const growing = state.index.filter((r) => r.fin && r.fin.growth > 20).length;
  const lossy = state.index.filter((r) => r.fin && r.fin.net_profit < 0).length;
  const featured = state.index.filter((r) => r.website && r.fin && r.fin.revenue).sort((a, b) => b.facts - a.facts).slice(0, 6);
  const fams = (st.coverage || []).map((c) => [c.key, [c.label, c.desc], c.pct]);
  return `
  <section class="hero">
    <span class="eyebrow fade-up"><span class="dot"></span>${fmtInt(st.companies)} verified company profiles · updated ${esc(rel(maxChecked()))}</span>
    <h1 class="fade-up" ${stagger(1)}>Every Norwegian company,<br><span class="grad-text">verified to the source.</span></h1>
    <p class="sub fade-up" ${stagger(2)}>Type a name or an organisation number. Signalpost reads the official registers and the company's own website, proves each fact belongs to the exact company, and shows you where it came from.</p>
    <div class="searchbig fade-up" ${stagger(3)}>
      <div class="glass sbar">${ico("search", "s")}<input id="heroQ" placeholder="Try “Hasle”, “Bergen” or 923609016" autocomplete="off" spellcheck="false" aria-label="Search companies"><button class="btn btn-primary" id="heroGo" type="button">${ico("arrow")}Search</button></div>
      <div class="suggest glass" id="heroSug"></div>
    </div>
    <div class="trust fade-up" ${stagger(4)}>
      <span>${ico("shield")}Official Norwegian registers</span><span>${ico("link")}Every fact links to its source</span><span>${ico("check")}Websites identity-checked</span><span>${ico("coins")}No paid APIs · $0 per run</span>
    </div>
  </section>

  <div class="grid g6 mt-l">
    ${statTile("building", "ice", st.companies, "Companies", "profiled end to end", 0)}
    ${statTile("file", "slate", st.facts, "Facts with evidence", "each one clickable", 1)}
    ${statTile("globe", "emerald", st.websites, "Verified websites", "proven to be the company's own", 2)}
    ${statTile("brief", "sky", st.hiring, "Hiring now", "active job ads on NAV", 3)}
    ${statTile("coins", "amber", st.grants, "Public support", "companies with grants", 4)}
    ${statTile("bolt", "rose", req, "Requests used", "third-party cost $0.00", 5)}
  </div>

  <div class="section-title"><div><h3>What we know, at a glance</h3><p>Share of companies where each type of information was found and verified.</p></div></div>
  <div class="glass card fade-up"><div class="cov">
    ${fams.map(([k, v, pct]) => `<div class="cov-row"><div class="name">${esc(v[0])}<small>${esc(v[1])}</small></div><div class="bar"><i data-w="${pct}"></i></div><div class="pct num">${pct < 1 && pct > 0 ? pct.toFixed(1) : pct.toFixed(0)}%</div></div>`).join("")}
  </div></div>

  <div class="section-title"><div><h3>Signals worth a look</h3><p>Click a card to see the companies behind it.</p></div><a class="btn btn-ghost" href="#/companies">All companies ${ico("arrow")}</a></div>
  <div class="grid g3">
    ${insight("alert", "rose", st.risk, "with risk flags", "Bankruptcy, liquidation, negative equity or overdue accounts", "risk", 0)}
    ${insight("trend", "emerald", growing, "growing over 20%", "Revenue up more than a fifth on the year before", "growing", 1)}
    ${insight("brief", "sky", st.hiring, "hiring right now", "Active job advertisements on NAV Arbeidsplassen", "hiring", 2)}
    ${insight("down", "amber", lossy, "made a loss", "Negative net result in the latest filed year", "loss", 3)}
    ${insight("coins", "ice", st.grants, "received public support", "Grants listed in Støtteregisteret", "grants", 4)}
    ${insight("globe", "slate", st.websites, "verified websites", "The site proves it belongs to the company", "website", 5)}
  </div>

  ${featured.length ? `<div class="section-title"><div><h3>Rich profiles</h3><p>Companies with a verified website and filed accounts.</p></div></div>
  <div class="grid g3">${featured.map((r, i) => featureCard(r, i)).join("")}</div>` : ""}`;
}
function maxChecked() { return state.index.reduce((m, r) => (r.checked_at && r.checked_at > m ? r.checked_at : m), ""); }
function statTile(icon, tint, v, l, d, i) {
  return `<div class="glass stat hover-lift fade-up" ${stagger(i + 4)}><div class="ico tint-${tint}">${ico(icon)}</div><div class="v num" data-count="${+v || 0}">0</div><div class="l">${esc(l)}</div><div class="d">${esc(d)}</div></div>`;
}
function insight(icon, tint, v, l, d, f, i) {
  return `<a class="glass insight hover-lift fade-up" ${stagger(i)} href="#/companies?f=${f}"><div class="ico tint-${tint}">${ico(icon)}</div><div><b class="num" data-count="${+v || 0}">0</b><div style="font-weight:600">${esc(l)}</div><span>${esc(d)}</span></div></a>`;
}
function featureCard(r, i) {
  const g = r.fin && r.fin.growth;
  return `<a class="glass card hover-lift fade-up" ${stagger(i)} href="#/company/${esc(r.orgnr)}" style="text-decoration:none;display:block">
    <div class="row">${avatar(r.name)}<div style="min-width:0"><div class="nm" style="font-weight:650;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(r.name)}</div><div class="small muted">${esc(fmtOrg(r.orgnr))} · ${esc(titleCase(r.municipality))}</div></div></div>
    <div class="row mt" style="justify-content:space-between"><div><div class="small muted">Revenue ${esc(r.fin.year)}</div><div style="font-size:20px;font-weight:700;letter-spacing:-.02em" class="num">${esc(money(r.fin.revenue, r.fin.currency))}</div></div>
    ${g != null ? `<span class="delta ${g >= 0 ? "up" : "down"}">${g >= 0 ? "▲" : "▼"} ${Math.abs(g).toFixed(0)}%</span>` : ""}</div>
    <div class="signals">${r.signals.filter((s) => s.signal !== "revenue").slice(0, 3).map(pill).join("")}</div></a>`;
}
const titleCase = (s) => String(s || "").toLowerCase().replace(/(^|[\s-])\p{L}/gu, (m) => m.toUpperCase());

/* ------------------------------------------------------------------ views: companies */
const FILTERS = [["all", "All"], ["risk", "Risk flags"], ["growing", "Growing"], ["profitable", "Profitable"], ["loss", "Loss-making"], ["hiring", "Hiring"], ["website", "Verified website"], ["grants", "Public support"]];
function passFilter(r, f) {
  switch (f) {
    case "risk": return r.signals.some((s) => s.polarity === "risk");
    case "growing": return r.fin && r.fin.growth > 20;
    case "profitable": return r.fin && r.fin.net_profit > 0;
    case "loss": return r.fin && r.fin.net_profit < 0;
    case "hiring": return r.hiring > 0;
    case "website": return !!r.website;
    case "grants": return r.grants > 0;
    default: return true;
  }
}
function companyRows() {
  const q = state.q.trim().toLowerCase();
  let rows = state.index.filter((r) => passFilter(r, state.filter));
  if (q) rows = rows.map((r) => [score(r, q), r]).filter((x) => x[0] > 0).sort((a, b) => b[0] - a[0]).map((x) => x[1]);
  const key = { revenue: (r) => -(r.fin && r.fin.revenue || -1e18), growth: (r) => -(r.fin && r.fin.growth != null ? r.fin.growth : -1e9), employees: (r) => -(r.employees || -1), facts: (r) => -r.facts };
  if (!q && key[state.sort]) rows = rows.slice().sort((a, b) => key[state.sort](a) - key[state.sort](b));
  return rows;
}
function viewCompanies() {
  return `<div class="section-title" style="margin-top:6px"><div><h3>Companies</h3><p>${fmtInt(state.index.length)} profiles. Search, filter and click any company for the full picture.</p></div>
    <a class="btn btn-primary" href="#/research">${ico("bolt")}Research a new company</a></div>
  <div class="toolbar glass">
    <label class="search">${ico("search")}<input id="listQ" value="${esc(state.q)}" placeholder="Filter by name, org. number, town or industry" autocomplete="off"></label>
    <select class="sel" id="sort" aria-label="Sort">${[["name", "Name A–Z"], ["revenue", "Highest revenue"], ["growth", "Fastest growth"], ["employees", "Most employees"], ["facts", "Most facts"]].map(([v, t]) => `<option value="${v}" ${state.sort === v ? "selected" : ""}>${t}</option>`).join("")}</select>
    <div class="wrapflex" style="width:100%">${FILTERS.map(([v, t]) => `<button class="chip ${state.filter === v ? "on" : ""}" data-f="${v}" type="button">${t}</button>`).join("")}<span class="small muted" id="cnt" style="margin-left:auto;align-self:center"></span></div>
  </div>
  <div class="list" id="list"></div><div class="more" id="more"></div>`;
}
function renderList() {
  const rows = companyRows();
  $("#cnt").textContent = `${fmtInt(rows.length)} shown`;
  const part = rows.slice(0, state.shown);
  $("#list").innerHTML = part.length ? part.map((r, i) => crow(r, i)).join("") : `<div class="glass empty">No companies match. ${digits(state.q).length === 9 && mod11(digits(state.q)) ? `<a href="#/research/${digits(state.q)}">Research ${fmtOrg(digits(state.q))} now →</a>` : "Try another filter."}</div>`;
  $("#more").innerHTML = rows.length > state.shown ? `<button class="btn btn-ghost" id="moreBtn" type="button">Show more (${fmtInt(rows.length - state.shown)} left)</button>` : "";
}
function crow(r, i) {
  const g = r.fin && r.fin.growth;
  return `<a class="crow glass hover-lift fade-up" ${stagger(i % 20)} href="#/company/${esc(r.orgnr)}">
    ${avatar(r.name)}
    <div style="min-width:0"><div class="nm">${esc(r.name)}</div><div class="meta">${esc(fmtOrg(r.orgnr))} · ${esc(r.form || "")}${r.municipality ? " · " + esc(titleCase(r.municipality)) : ""}</div></div>
    <div class="ind meta" title="${esc(r.industry || "")}">${esc(r.industry || "—")}</div>
    <div class="money num"><b>${r.fin && r.fin.revenue != null ? esc(money(r.fin.revenue, r.fin.currency)) : "—"}</b>${g != null ? `<span class="delta ${g >= 0 ? "up" : "down"}">${g >= 0 ? "▲" : "▼"} ${Math.abs(g).toFixed(0)}% y/y</span>` : `<span class="meta">${r.fin && r.fin.year ? "revenue " + esc(r.fin.year) : "no filed accounts"}</span>`}</div>
    <div class="sig wrapflex">${r.signals.filter((s) => s.signal !== "revenue").slice(0, 2).map(pill).join("")}</div>
    <div class="icons"><span class="${r.website ? "on" : ""}" title="${r.website ? "Verified website" : "No verified website"}">${ico("globe")}</span><span class="${r.hiring ? "on" : ""}" title="${r.hiring ? "Hiring" : "No active job ads"}">${ico("brief")}</span><span class="${r.grants ? "on" : ""}" title="${r.grants ? "Public support" : "No grants listed"}">${ico("coins")}</span></div>
  </a>`;
}

/* ------------------------------------------------------------------ views: company */
function claimsOf(env) {
  const by = {}, avail = {};
  for (const c of env.claims || []) {
    (by[c.field] = by[c.field] || []).push(c);
    if (c.availability === "available") (avail[c.field] = avail[c.field] || []).push(c);
  }
  return { by, avail, ev: Object.fromEntries((env.evidence || []).map((e) => [e.id, e])), claim: Object.fromEntries((env.claims || []).map((c) => [c.id, c])) };
}
const one = (K, f, relation) => (K.avail[f] || []).find((c) => !relation || (c.relation || "self") === relation) || null;
const many = (K, f) => K.avail[f] || [];
const LEVEL_TXT = { L1: "Organisation number printed on the site", L2: "Filed in the register and matches its contact details", L2e: "Domain of the registered e-mail, independently corroborated", L2r: "Filed by the company itself; its name is in the domain and title" };
function shown(c) {
  if (!c) return "—";
  if (c.field === "website_verification" && c.value && c.value.level) return LEVEL_TXT[c.value.level] || c.value.level;
  if (c.display) return c.display;
  const v = c.value;
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") return v.name || v.title || v.text || v.description || JSON.stringify(v);
  return String(v);
}
function factRow(c) {
  if (!c) return "";
  let v = shown(c);
  const relTxt = c.relation && c.relation !== "self" ? ` <small>(${esc(c.relation.replace(/_/g, " "))})</small>` : "";
  return `<div class="fact" data-c="${esc(c.id)}" tabindex="0" role="button"><div class="k">${esc(label(c.field))}${help(c.field)}</div><div class="v">${esc(v)}${relTxt}</div><div class="go">${ico("eye")}source</div></div>`;
}
function finSeries(K) {
  const yrs = {};
  for (const f of ["revenue", "operating_profit", "net_profit", "equity", "total_assets", "total_liabilities", "equity_ratio", "operating_margin"]) {
    for (const c of many(K, f)) {
      const p = c.period || {};
      if (p.scope !== "company" || !p.year) continue;
      (yrs[p.year] = yrs[p.year] || {})[f] = c;
    }
  }
  return Object.keys(yrs).map(Number).sort().map((y) => ({ year: y, ...yrs[y] }));
}
const val = (c) => (c && c.value !== null && c.value !== undefined && !isNaN(+c.value) ? +c.value : null);

function chartSVG(series, cur) {
  const W = 860, H = 250, L = 62, R = 14, T = 16, B = 30;
  const rv = series.map((s) => val(s.revenue)), np = series.map((s) => val(s.net_profit));
  const all = rv.concat(np).filter((x) => x !== null);
  if (!all.length) return "";
  let max = Math.max(0, ...all), min = Math.min(0, ...all);
  if (max === min) max = min + 1;
  const pad = (max - min) * 0.08;
  max += pad; if (min < 0) min -= pad;
  const y = (v) => T + (H - T - B) * (1 - (v - min) / (max - min));
  const n = series.length, bw = Math.min(64, ((W - L - R) / n) * 0.46);
  const x = (i) => L + ((W - L - R) / n) * (i + 0.5);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((k) => min + (max - min) * k);
  let g = ticks.map((t) => `<line class="gl" x1="${L}" x2="${W - R}" y1="${y(t)}" y2="${y(t)}"/><text x="${L - 8}" y="${y(t) + 4}" text-anchor="end">${esc(money(t, "").trim())}</text>`).join("");
  g += `<line x1="${L}" x2="${W - R}" y1="${y(0)}" y2="${y(0)}" stroke="var(--stroke-2)"/>`;
  series.forEach((s, i) => {
    const v = rv[i];
    if (v !== null) {
      const y0 = y(Math.max(0, v)), h = Math.abs(y(v) - y(0));
      g += `<rect x="${x(i) - bw / 2}" y="${y0}" width="${bw}" height="${Math.max(1, h)}" rx="8" fill="url(#barg)" data-c="${esc(s.revenue.id)}" style="cursor:pointer"><title>Revenue ${s.year}: ${esc(money(v, cur))}</title></rect>`;
    }
    g += `<text x="${x(i)}" y="${H - 8}" text-anchor="middle">${s.year}</text>`;
  });
  const pts = series.map((s, i) => (np[i] !== null ? [x(i), y(np[i]), s] : null)).filter(Boolean);
  if (pts.length > 1) g += `<polyline points="${pts.map((p) => p[0] + "," + p[1]).join(" ")}" fill="none" stroke="#3ddc97" stroke-width="2.5" stroke-linejoin="round"/>`;
  pts.forEach((p) => (g += `<circle cx="${p[0]}" cy="${p[1]}" r="5.5" fill="var(--bg)" stroke="${val(p[2].net_profit) < 0 ? "#f0575c" : "#3ddc97"}" stroke-width="2.5" data-c="${esc(p[2].net_profit.id)}" style="cursor:pointer"><title>Net result ${p[2].year}: ${esc(money(val(p[2].net_profit), cur))}</title></circle>`));
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Revenue and net result by year">${g}</svg>`;
}
function ring(pct, labelTxt) {
  const r = 26, c = 2 * Math.PI * r, p = Math.max(0, Math.min(100, pct || 0));
  return `<svg class="ring" viewBox="0 0 64 64" role="img" aria-label="${esc(labelTxt)}"><circle class="bg" cx="32" cy="32" r="${r}"/><circle class="fg" cx="32" cy="32" r="${r}" stroke-dasharray="${c}" stroke-dashoffset="${c}" data-off="${c * (1 - p / 100)}" transform="rotate(-90 32 32)"/></svg>`;
}

function viewCompanySkeleton() {
  return `<a class="back" href="#/companies">${ico("back")}All companies</a><div class="glass chero"><div class="skel" style="width:76px;height:76px;border-radius:24px"></div><div><div class="skel" style="height:34px;width:60%"></div><div class="skel mt" style="height:16px;width:40%"></div></div></div>
  <div class="grid g4 mt">${[0, 1, 2, 3].map(() => `<div class="glass kpi"><div class="skel" style="height:14px;width:50%"></div><div class="skel mt" style="height:28px;width:70%"></div></div>`).join("")}</div>`;
}
function viewMissing(org) {
  return `<a class="back" href="#/companies">${ico("back")}All companies</a><div class="glass card empty" style="padding:60px 24px"><div style="font-size:42px">${ico("search")}</div>
   <h2 style="justify-content:center">${esc(fmtOrg(org))} is not in your library yet</h2><p class="muted">Run the agent on it now. It checks the official registers and the company's website, usually in under a minute.</p>
   <a class="btn btn-primary mt" href="#/research/${esc(org)}">${ico("bolt")}Research ${esc(fmtOrg(org))}</a></div>`;
}
function viewCompany(env) {
  const K = claimsOf(env);
  const name = (one(K, "legal_name") || {}).value || env.legal_name || env.organisation_number;
  const form = (one(K, "legal_form") || {}).value || {};
  const status = (one(K, "registry_status") || {}).value;
  const addr = one(K, "business_address");
  const ind = many(K, "industry").sort((a, b) => ((a.value || {}).rank || 9) - ((b.value || {}).rank || 9))[0];
  const site = one(K, "official_website"), ver = one(K, "website_verification");
  const summ = env.summary || {};
  const sig = summ.signals || [];
  const fin = finSeries(K);
  const last = fin[fin.length - 1] || {}, prev = fin[fin.length - 2] || {};
  const cur = (one(K, "accounts_currency") || {}).value || "NOK";
  const g = val(last.revenue) && val(prev.revenue) > 0 && val(last.revenue) > 0 ? ((val(last.revenue) - val(prev.revenue)) / val(prev.revenue)) * 100 : null;
  const eqr = val(last.equity) !== null && val(last.total_assets) > 0 ? (val(last.equity) / val(last.total_assets)) * 100 : null;
  const emp = one(K, "employees");
  const famsAvail = new Set((env.claims || []).filter((c) => c.availability === "available").map((c) => c.family));
  const completeness = (Object.keys(FAMILIES).filter((f) => famsAvail.has(f)).length / Object.keys(FAMILIES).length) * 100;
  const nFacts = (env.claims || []).filter((c) => c.availability === "available").length;
  const hosts = new Set((env.evidence || []).map((e) => { try { return new URL(e.source_url).hostname.replace(/^www\./, ""); } catch (_) { return ""; } }).filter(Boolean));
  const statusChip = status && status !== "active" ? `<span class="chip risk"><i></i>${esc(String(status).replace(/_/g, " "))}</span>` : `<span class="chip positive"><i></i>Active</span>`;
  const sections = [];
  const S = (id, title, icon, lead, body, n) => { if (body) sections.push({ id, title, icon, n, html: `<section class="glass card fade-up" id="s-${id}"><h2><span class="ico">${ico(icon)}</span>${esc(title)}</h2>${lead ? `<p class="lead">${lead}</p>` : '<div style="height:12px"></div>'}${body}</section>` }); };

  // Summary
  const sents = (summ.sentences || []).filter((s) => s.topic !== "unknowns" && s.topic !== "signals");
  S("summary", "In plain words", "spark", "Written only from the facts below. Click a number to see its source.",
    sents.length ? `<div class="summary">${sents.map((s) => `<p>${esc(s.text)}${(s.claim_ids || []).slice(0, 4).map((cid, i) => (K.claim[cid] ? `<button class="cite" data-c="${esc(cid)}" type="button" aria-label="Source ${i + 1}">${i + 1}</button>` : "")).join("")}</p>`).join("")}</div>` : "", sents.length);
  // Financials
  if (fin.length) {
    const rowsF = [["revenue", "Revenue"], ["operating_profit", "Operating result"], ["net_profit", "Net result"], ["equity", "Equity"], ["total_assets", "Total assets"], ["total_liabilities", "Total debt"], ["equity_ratio", "Equity ratio"]];
    const cell = (c, f) => (c ? `<td class="c num" data-c="${esc(c.id)}" title="Open source">${esc(f === "equity_ratio" || f === "operating_margin" ? (val(c) * 100).toFixed(1) + " %" : money(val(c), cur))}</td>` : `<td class="faint">—</td>`);
    S("financials", "Financials", "chart", `Company accounts filed with Regnskapsregisteret, ${fin[0].year}–${last.year}. Bars show revenue, the line shows the net result.`,
      `${chartSVG(fin, cur)}<div class="legend"><span><i style="background:#a8d5e2"></i>Revenue</span><span><i style="background:#3ddc97"></i>Net result (red dot = loss)</span></div>
      <div style="overflow:auto"><table class="ftable"><thead><tr><th>${esc(cur)}</th>${fin.map((s) => `<th>${s.year}</th>`).join("")}</tr></thead><tbody>
      ${rowsF.filter(([f]) => fin.some((s) => s[f])).map(([f, t]) => `<tr><td>${esc(t)} ${help(f)}</td>${fin.map((s) => cell(s[f], f)).join("")}</tr>`).join("")}</tbody></table></div>`, fin.length);
  }
  // People
  const roles = ["ceo", "chair", "deputy_chair", "board_member", "deputy_board_member", "owner", "contact_person", "business_manager", "auditor", "accountant"];
  const ppl = roles.flatMap((f) => many(K, f).map((c) => ({ c, f })));
  S("people", "People & roles", "users", "From the official roles register. Hover the ? to see what each role means.",
    ppl.length ? `<div class="people">${ppl.map(({ c, f }) => `<div class="person" data-c="${esc(c.id)}" tabindex="0" role="button">${avatar((c.value || {}).name || "?")}<div style="min-width:0"><b>${esc((c.value || {}).name)}</b><span>${esc(label(f))}${(c.value || {}).last_changed ? " · since " + esc(date10(c.value.last_changed)) : ""}</span></div></div>`).join("")}</div>` : "", ppl.length);
  // Places
  const wps = many(K, "registered_workplace");
  S("places", "Address & workplaces", "pin", "Registered addresses and sub-units (shops, offices, plants).",
    `<div class="facts-list">${[addr, one(K, "postal_address"), one(K, "municipality"), one(K, "county")].map(factRow).join("")}${wps.slice(0, 12).map(factRow).join("")}</div>${wps.length > 12 ? `<p class="small muted">+ ${wps.length - 12} more workplaces in “All facts”.</p>` : ""}`, wps.length + 1);
  // Online
  if (site || many(K, "social_profile").length || many(K, "registry_website").length) {
    const lvl = ((ver || {}).value || {}).level;
    const vtxt = !site ? "" : lvl === "L1" ? ["Organisation number printed on the site", "We found this company's exact organisation number on the website, next to a label such as “Org.nr”."]
      : lvl === "L2r" ? ["Filed by the company itself", "The company entered this website in the business register, and the domain and page title carry its name. Contacts are only shown if they equal the register."]
        : ["Matches the register", "This is the website filed in the register, and the site shows the company's registered contact details and full legal name."];
    const socials = many(K, "social_profile");
    S("online", "Online presence", "globe", "A website is only shown when it is proven to belong to this exact company, never a parent's, a chain's or a namesake's.",
      `${site ? `<div class="verify ${lvl === "L1" ? "" : "weak"}"><div class="ico">${ico("shield")}</div><div><b><a href="${esc(site.value)}" target="_blank" rel="noopener noreferrer">${esc(site.display || site.value)}${ico("ext", "ext")}</a></b><span>${esc(vtxt[0])}. ${esc(vtxt[1])}</span></div></div>` : `<div class="gap"><div>${ico("info")}</div><div><b>No verified website</b><p>${esc(((K.by.official_website || [])[0] || {}).note || "No site could be proven to belong to this company.")}</p></div></div>`}
      <div class="facts-list">${[ver, one(K, "website_title"), one(K, "website_description"), one(K, "brand_name")].map(factRow).join("")}${many(K, "contact_email").concat(many(K, "contact_phone")).map(factRow).join("")}${socials.map(factRow).join("")}${[one(K, "careers_page"), one(K, "news_page")].map(factRow).join("")}${many(K, "registry_website").concat(many(K, "registry_email"), many(K, "registry_phone"), many(K, "registry_mobile")).map(factRow).join("")}</div>`, (site ? 1 : 0) + socials.length);
  }
  // Activity timeline
  const tl = [];
  const push = (c, when, what, why, color) => tl.push({ c, when: date10(when), what, why, color });
  many(K, "registry_event").forEach((c) => push(c, c.value.date, "Registry update", (c.display || "").replace(/^\S+\s/, ""), "#a8d5e2"));
  many(K, "role_change_event").forEach((c) => push(c, c.value.date, "Board or management change", "Roles updated in the register", "#8ab4f8"));
  many(K, "news_item").forEach((c) => push(c, c.value.published, c.value.title, "News on the company's website", "#3ddc97"));
  many(K, "job_posting").forEach((c) => push(c, c.value.published || c.value.date || c.effective_at, c.value.title, "Job ad on NAV Arbeidsplassen", "#8ab4f8"));
  many(K, "state_aid_grant").forEach((c) => push(c, c.value.date, `Public support${c.value.amount ? ": " + money(c.value.amount, c.value.currency || "NOK") : ""}`, c.value.grantor || c.value.scheme || "Støtteregisteret", "#f2b544"));
  many(K, "tax_incentive_project").forEach((c) => push(c, c.value.date, `SkatteFUNN: ${c.value.title || "R&D project"}`, `Status: ${c.value.status || "—"}`, "#3ddc97"));
  many(K, "research_grant").forEach((c) => push(c, c.value.date, `Research Council: ${c.value.title || "project"}`, `Status: ${c.value.status || "—"}`, "#3ddc97"));
  many(K, "food_inspection").forEach((c) => push(c, c.value.date, `Food-safety inspection: ${c.value.location_name || ""}`, c.display || "", "#f0575c"));
  tl.sort((a, b) => String(b.when).localeCompare(String(a.when)));
  S("activity", "Activity timeline", "clock", "Dated events from the register, the company's website, NAV, Støtteregisteret and other public records.",
    tl.length ? `<div class="timeline">${tl.slice(0, 40).map((t) => `<div class="tl" data-c="${esc(t.c.id)}" style="--c:${t.color}" tabindex="0" role="button"><div class="when">${esc(t.when || "undated")}</div><div class="what">${esc(t.what)}</div><div class="why">${esc(t.why)}</div></div>`).join("")}</div>${tl.length > 40 ? `<p class="small muted">+ ${tl.length - 40} older events in “All facts”.</p>` : ""}` : "", tl.length);
  // Group
  const grp = ["parent_company", "ultimate_parent", "lei_parent", "group_subsidiary", "group_member"].flatMap((f) => many(K, f));
  S("group", "Group & ownership", "layers", "Parents and subsidiaries, labelled as related entities, never mixed with this company's own facts.", grp.length ? `<div class="facts-list">${grp.map(factRow).join("")}</div>` : "", grp.length);
  // Identity
  const idf = ["organisation_number", "legal_form", "founding_date", "registration_date", "industry", "sector", "employees", "share_capital", "activity", "vat_registration", "business_register_registration", "audit_exemption", "signature_rule", "prokura_rule", "lei", "lei_status", "former_name", "wikidata_item", "encyclopedia_entry"];
  S("identity", "Legal identity", "building", "The company as registered with Brønnøysundregistrene.", `<div class="facts-list">${idf.flatMap((f) => many(K, f)).map(factRow).join("")}</div>`, idf.length);
  // Changes
  if ((env.changes || []).length) {
    S("changes", "Changes since last check", "refresh", "What is different from the previous run, with evidence for both the old and new values.",
      `<div class="facts-list">${env.changes.map((ch) => `<div class="fact" ${ch.new_claim_id || ch.old_claim_id ? `data-c="${esc(ch.new_claim_id || ch.old_claim_id)}"` : ""}><div class="k">${esc(label(ch.field))} <span class="chip ${ch.materiality === "material" ? "warn" : "info"}">${esc(ch.change_type)}</span></div><div class="v">${esc(ch.old_display || ch.old_value || "—")} → ${esc(ch.new_display || ch.new_value || "—")}</div><div class="go">${esc(date10(ch.detected_at))}</div></div>`).join("")}</div>`, env.changes.length);
  }
  // Gaps
  const unk = summ.unknowns || [];
  S("gaps", "What we could not find", "question", "Being honest about gaps: these were checked but are missing, unclear or could not be verified.",
    unk.length ? `<div class="gaps">${unk.map((u) => `<div class="gap"><span class="state ${esc(u.availability)}">${esc(STATE_TXT[u.availability] || u.availability)}</span><div><b>${esc(u.label)}</b><p>${esc(u.reason || STATE_HELP[u.availability] || "")}</p></div></div>`).join("")}</div>` : "", unk.length);
  // All facts
  const fams = {};
  (env.claims || []).forEach((c) => (fams[c.family || "other"] = fams[c.family || "other"] || []).push(c));
  S("all", "All facts", "file", `Every claim in the profile (${fmtInt((env.claims || []).length)}), including what was not found. Click any row to open its source.`,
    `<details class="all"><summary class="btn btn-ghost">Show all ${fmtInt((env.claims || []).length)} facts</summary>${Object.entries(fams).map(([f, cs]) => `<div class="fam-h">${esc((FAMILIES[f] || [f])[0])}</div><div class="facts-list">${cs.map((c) => (c.availability === "available" ? factRow(c) : `<div class="fact" data-c="${esc(c.id)}"><div class="k">${esc(label(c.field))}</div><div class="v"><span class="state ${esc(c.availability)}">${esc(STATE_TXT[c.availability] || c.availability)}</span> <small>${esc((c.note || "").slice(0, 140))}</small></div><div class="go">${ico("eye")}why</div></div>`)).join("")}</div>`).join("")}</details>`, (env.claims || []).length);

  return `<a class="back" href="#/companies">${ico("back")}All companies</a>
  <div class="glass chero fade-up">
    ${avatar(name, "lg")}
    <div style="min-width:0">
      <div class="row wrapflex" style="gap:8px;margin-bottom:8px">${statusChip}<span class="chip">${esc(form.description || form.code || "")}</span>${site ? `<span class="chip positive"><i></i>Verified website</span>` : ""}</div>
      <h1>${esc(name)}</h1>
      <div class="facts">
        <span>${ico("file")}Org. no. <b class="copy num" data-copy="${esc(env.organisation_number)}" title="Copy">${esc(fmtOrg(env.organisation_number))}</b></span>
        ${addr ? `<span>${ico("pin")}${esc(titleCase(((addr.value || {}).city || "")))}</span>` : ""}
        ${ind ? `<span>${ico("brief")}${esc(ind.value.description)}</span>` : ""}
        <span>${ico("clock")}Checked ${esc(rel((env.run || {}).completed_at))}</span>
      </div>
      ${sig.length ? `<div class="signals">${sig.map(pill).join("")}</div>` : ""}
    </div>
    <div class="actions">
      <a class="btn btn-primary" href="#/research/${esc(env.organisation_number)}">${ico("refresh")}Check for updates</a>
      <a class="btn btn-ghost" href="https://virksomhet.brreg.no/nb/oppslag/enheter/${esc(env.organisation_number)}" target="_blank" rel="noopener noreferrer">${ico("ext")}Brønnøysund</a>
      ${site ? `<a class="btn btn-ghost" href="${esc(site.value)}" target="_blank" rel="noopener noreferrer">${ico("globe")}Website</a>` : ""}
    </div>
  </div>
  <div class="grid g4 mt">
    <div class="glass kpi hover-lift fade-up" ${stagger(1)}><div class="l">${ico("chart", "")}Revenue ${esc(last.year || "")} ${help("revenue")}</div><div class="v num">${esc(money(val(last.revenue), cur))}</div><div class="s">${g !== null ? `<span class="delta ${g >= 0 ? "up" : "down"}">${g >= 0 ? "▲" : "▼"} ${Math.abs(g).toFixed(1)}%</span> vs ${prev.year}` : "no comparable prior year"}</div></div>
    <div class="glass kpi hover-lift fade-up" ${stagger(2)}><div class="l">Net result ${help("net_profit")}</div><div class="v num" style="color:${val(last.net_profit) < 0 ? "var(--rose)" : "inherit"}">${esc(money(val(last.net_profit), cur))}</div><div class="s">${val(last.net_profit) === null ? "not filed" : val(last.net_profit) >= 0 ? "profitable" : "a loss"}</div></div>
    <div class="glass kpi hover-lift fade-up kpi-ring" ${stagger(3)}><div><div class="l">Equity ratio ${help("equity_ratio")}</div><div class="v num">${eqr === null ? "—" : eqr.toFixed(0) + "%"}</div><div class="s">${eqr === null ? "no balance sheet" : eqr < 0 ? "negative equity" : eqr < 10 ? "thin" : eqr < 30 ? "moderate" : "solid"}</div></div>${eqr !== null ? ring(Math.max(0, eqr), "Equity ratio") : ""}</div>
    <div class="glass kpi hover-lift fade-up kpi-ring" ${stagger(4)}><div><div class="l">Profile completeness</div><div class="v num">${fmtInt(nFacts)} facts</div><div class="s">${hosts.size} source${hosts.size === 1 ? "" : "s"} · ${emp && emp.value && emp.value.count ? fmtInt(emp.value.count) + " employees" : emp ? esc(shown(emp)) : "employees n/a"}</div></div>${ring(completeness, "Profile completeness")}</div>
  </div>
  <div class="layout mt">
    <div class="grid">${sections.map((s) => s.html).join("")}</div>
    <aside class="glass toc" aria-label="Sections">${sections.map((s) => `<button type="button" data-jump="s-${s.id}">${ico(s.icon)}${esc(s.title)}<span class="n">${s.n || ""}</span></button>`).join("")}
      <div class="small muted" style="padding:12px 10px 4px;border-top:1px solid var(--stroke);margin-top:8px">Run ${esc((env.run || {}).run_id || "")}<br>${esc(date10((env.run || {}).completed_at))} · ${esc(((env.run || {}).agent || {}).llm ? "LLM" : "no LLM, no paid APIs")}</div></aside>
  </div>`;
}

/* ------------------------------------------------------------------ drawer */
function openFact(env, cid) {
  const K = claimsOf(env);
  const c = K.claim[cid];
  if (!c) return;
  const evs = (c.evidence_ids || []).map((id) => K.ev[id]).filter(Boolean);
  const conf = Math.round((c.confidence ?? 1) * 100);
  const relx = { parent: "about the parent company", ultimate_parent: "about the top parent", subunit: "about one of its workplaces", related: "about a related entity, not this company itself", group_member: "about a group company" }[c.relation] || "";
  const basis = { organisation_number_exact: "Matched by the exact organisation number in the official source.", registry_website_corroborated: "Registry-filed website that shows the registered contact details.", registry_website_filed_name_domain: "Website filed by the company itself; domain and title carry its name.", registry_email_domain_corroborated: "Domain of the registry e-mail with two independent corroborations." }[c.match_basis] || "";
  $("#drawer").innerHTML = `<button class="icon-btn x" id="dx" type="button" aria-label="Close">${ico("x")}</button>
    <div class="row" style="gap:8px"><span class="chip">${esc((FAMILIES[c.family] || [c.family || "other"])[0])}</span><span class="state ${esc(c.availability)}">${esc(STATE_TXT[c.availability] || c.availability)}</span></div>
    <h3 class="mt">${esc(label(c.field))}</h3><p class="muted small" style="margin:0">${esc(helpTxt(c.field))}</p>
    <div class="val">${c.availability === "available" ? esc(shown(c)) : esc(STATE_HELP[c.availability] || "")}</div>
    ${c.note ? `<p class="small muted">${esc(c.note)}</p>` : ""}
    ${c.availability === "available" ? `<div class="small muted mt">Confidence ${conf}%</div><div class="conf"><i style="width:${conf}%"></i></div>` : ""}
    <dl class="dl">
      ${c.period ? `<dt>Period</dt><dd>${esc(c.period.year ? "FY" + c.period.year : "")} ${esc(c.period.start || "")} → ${esc(c.period.end || "")} (${esc(c.period.scope || "")})</dd>` : ""}
      ${c.effective_at ? `<dt>Valid as of</dt><dd>${esc(date10(c.effective_at))}</dd>` : ""}
      ${relx ? `<dt>Relation</dt><dd>${esc(relx)}</dd>` : ""}
      ${basis ? `<dt>Identity match</dt><dd>${esc(basis)}</dd>` : ""}
      ${c.first_seen ? `<dt>First seen</dt><dd>${esc(date10(c.first_seen))}${c.last_seen ? " · last confirmed " + esc(date10(c.last_seen)) : ""}</dd>` : ""}
      ${c.method ? `<dt>Method</dt><dd class="small">${esc(c.method)}</dd>` : ""}
    </dl>
    ${evs.length ? `<h4 class="mt-l" style="margin-bottom:0">Where this comes from</h4>` : `<p class="small muted mt">${c.availability === "available" ? "" : "No source to show: nothing was found."}</p>`}
    ${evs.map((e, i) => `<div class="ev"><div class="src">${ico("link")}<span>${esc(e.source_name || hostOf(e.source_url))}</span></div>
      <dl class="dl" style="margin-top:10px">
        <dt>Source</dt><dd><a href="${esc(e.page_url || e.source_url)}" target="_blank" rel="noopener noreferrer">${esc(hostOf(e.page_url || e.source_url))}${ico("ext", "ext")}</a>${e.page_url && e.page_url !== e.source_url ? ` · <a class="small" href="${esc(e.source_url)}" target="_blank" rel="noopener noreferrer">raw data${ico("ext", "ext")}</a>` : ""}</dd>
        <dt>Retrieved</dt><dd>${esc(e.retrieved_at)} <span class="muted small">(${esc(rel(e.retrieved_at))})</span></dd>
        ${e.effective_at ? `<dt>Reporting date</dt><dd>${esc(date10(e.effective_at))}</dd>` : ""}
        ${e.licence ? `<dt>Licence</dt><dd>${esc(e.licence)}</dd>` : ""}
        ${e.locator ? `<dt>Location in source</dt><dd class="small" style="font-family:var(--mono)">${esc(e.locator)}</dd>` : ""}
        <dt>Content hash</dt><dd class="small copy" data-copy="${esc(e.content_sha256 || "")}" style="font-family:var(--mono)" title="Copy">${esc((e.content_sha256 || "").slice(0, 20))}…</dd>
      </dl>
      ${e.claim_span ? `<div class="small muted mt">Exact text in the source</div><div class="span-box" id="sp${i}"><mark>${esc(e.claim_span)}</mark></div>
      ${e.content_sha256 ? `<button class="btn btn-ghost mt" data-snap="${esc(e.content_sha256)}" data-i="${i}" data-q="${esc(e.claim_span)}" type="button">${ico("eye")}Show it in the archived copy</button>` : ""}` : ""}
    </div>`).join("")}`;
  $("#drawer").classList.add("on");
  $("#scrim").classList.add("on");
  setTimeout(() => $("#dx") && $("#dx").focus(), 50);
}
const hostOf = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch (_) { return u || ""; } };
function closeDrawer() { $("#drawer").classList.remove("on"); if (!$("#pal").classList.contains("on")) $("#scrim").classList.remove("on"); }

/* ------------------------------------------------------------------ views: research */
function viewResearch(org) {
  return `<div class="research"><div class="glass rbox fade-up">
    <h2 style="font-size:26px;letter-spacing:-.03em;margin:0">Research any Norwegian company</h2>
    <p class="muted" style="margin:6px 0 0">Enter a 9-digit organisation number. The agent checks the official registers, finds and verifies the company's website, and collects jobs, grants and public records. Every fact keeps its source.</p>
    <div class="rin"><label class="field" id="rf">${ico("search")}<input id="rq" inputmode="numeric" maxlength="11" placeholder="923 609 016" value="${esc(org ? fmtOrg(org) : "")}" aria-label="Organisation number"><span class="hint" id="rh"></span></label>
    <button class="btn btn-primary" id="rgo" type="button" disabled>${ico("bolt")}Start research</button></div>
    <div class="progress" id="rp" style="display:none"><i></i></div>
    <div class="steps" id="rs">${MODULES.map(([m, t, d, ic]) => `<div class="step" data-m="${m}"><div class="si">${ico(ic)}</div><div><b>${esc(t)}</b><span>${esc(d)}</span></div><span class="small muted" data-st></span></div>`).join("")}</div>
    <div id="rdone" class="mt"></div>
  </div></div>`;
}
function bindResearch(org) {
  const inp = $("#rq"), go = $("#rgo"), f = $("#rf"), h = $("#rh");
  const check = () => {
    const d = digits(inp.value);
    const ok = mod11(d);
    f.classList.toggle("ok", ok);
    f.classList.toggle("bad", d.length === 9 && !ok);
    h.innerHTML = d.length < 9 ? `<span class="muted">${d.length}/9 digits</span>` : ok ? `<span style="color:var(--aurora)">✓ valid number${state.byOrg.has(d) ? " · in library" : ""}</span>` : `<span style="color:var(--rose)">check digit does not match</span>`;
    go.disabled = !ok || !!state.job;
    return ok ? d : null;
  };
  inp.addEventListener("input", () => { const d = digits(inp.value).slice(0, 9); inp.value = fmtOrg(d) || d; check(); });
  inp.addEventListener("keydown", (e) => { if (e.key === "Enter" && check()) start(); });
  go.addEventListener("click", () => start());
  const start = async () => {
    const d = check();
    if (!d) return;
    go.disabled = true;
    $("#rp").style.display = "block";
    $$(".step").forEach((s) => { s.className = "step"; $("[data-st]", s).textContent = ""; });
    $$(".step").forEach((s) => { s.classList.add("wait"); $(".si", s).innerHTML = '<span class="spin"></span>'; });
    $("#rdone").innerHTML = "";
    try {
      const { job } = await api("/api/research", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ organisation_number: d }) });
      state.job = job;
      poll(job, d);
    } catch (e) { state.job = null; go.disabled = false; $("#rdone").innerHTML = `<div class="gap"><div>${ico("alert")}</div><div><b>Could not start</b><p>${esc(e.message)}</p></div></div>`; }
  };
  check();
  // auto-start only when the user asked for it (a research link or button), never on a page reload
  if (org && mod11(org) && state.autoStart === org && !state.job) { state.autoStart = null; start(); }
}
function poll(job, org) {
  clearTimeout(state.pollT);
  const tick = async () => {
    let j;
    try { j = await api("/api/jobs/" + job); } catch (_) { state.pollT = setTimeout(tick, 1200); return; }
    if (!$("#rs")) { if (j.status === "running") state.pollT = setTimeout(tick, 1500); else finishJob(j, org, false); return; }
    const seen = new Set();
    for (const ev of j.events) {
      seen.add(ev.module);
      const s = $(`.step[data-m="${ev.module}"]`);
      if (!s) continue;
      const cls = ev.availability === "available" ? "done" : ev.availability === "not_available" || ev.availability === "not_applicable" ? "none" : "warn";
      s.className = "step " + cls;
      $(".si", s).innerHTML = ico(cls === "done" ? "check" : cls === "none" ? "x" : "alert");
      $("[data-st]", s).textContent = `${STATE_TXT[ev.availability] || ev.availability || ""} · ${ev.t}s`;
    }
    $("#rp i").style.clipPath = `inset(0 ${100 - Math.min(100, (seen.size / MODULES.length) * 100)}% 0 0 round 99px)`;
    if (j.status === "running") { state.pollT = setTimeout(tick, 700); return; }
    finishJob(j, org, true);
  };
  tick();
}
async function finishJob(j, org, onPage) {
  state.job = null;
  if (j.status === "done") {
    await loadIndex();
    state.cache.delete(org);
    if (onPage) {
      $$(".step.wait").forEach((s) => { s.className = "step none"; $(".si", s).innerHTML = ico("x"); $("[data-st]", s).textContent = "Not run"; });
      $("#rp i").style.clipPath = "inset(0 0 0 0 round 99px)";
      $("#rdone").innerHTML = `<div class="verify"><div class="ico">${ico("check")}</div><div><b>Profile ready in ${j.elapsed}s</b><span>Every fact has its source attached.</span></div><a class="btn btn-primary" style="margin-left:auto" href="#/company/${org}">Open profile ${ico("arrow")}</a></div>`;
      $("#rgo").disabled = false;
    }
    toast("Research finished: " + fmtOrg(org));
  } else if (onPage) {
    $("#rdone").innerHTML = `<div class="gap"><div>${ico("alert")}</div><div><b>Research failed</b><p>${esc(j.error || "unknown error")}</p></div></div>`;
    $("#rgo").disabled = false;
  }
}

/* ------------------------------------------------------------------ views: about */
function viewAbout() {
  const steps = [["building", "ice", "1. Anchor the identity", "The organisation number is looked up in Enhetsregisteret, Norway's official business register. Everything else must match it exactly."],
    ["db", "sky", "2. Gather from official sources", "Annual accounts, board and management, workplaces, group links, LEI, job ads, public support, R&D projects and inspections, each from its official source."],
    ["shield", "emerald", "3. Verify the website", "A website is only accepted if it prints the exact organisation number, or is filed by the company with its name in the domain. Group, chain and namesake sites are rejected."],
    ["spark", "amber", "4. Explain and keep current", "A plain-language summary is written from the verified facts only. Re-running shows what changed, with evidence for both the old and new values."]];
  const states = Object.entries(STATE_TXT).map(([k, t]) => `<div class="gap" style="border-style:solid"><span class="state ${k}">${esc(t)}</span><div><p style="margin:0;color:var(--ink-2)">${esc(STATE_HELP[k])}</p></div></div>`).join("");
  return `<div class="section-title" style="margin-top:6px"><div><h3>How Signalpost works</h3><p>Four steps, no guesswork, no AI-generated facts.</p></div></div>
  <div class="grid g4">${steps.map((s, i) => `<div class="glass card hover-lift fade-up" ${stagger(i)}><div class="ico tint-${s[1]}" style="width:42px;height:42px;border-radius:14px;display:grid;place-items:center;margin-bottom:14px">${ico(s[0])}</div><h2 style="font-size:16px">${esc(s[2])}</h2><p class="muted small" style="margin:6px 0 0">${esc(s[3])}</p></div>`).join("")}</div>
  <div class="grid g2 mt-l">
    <div class="glass card"><h2><span class="ico">${ico("info")}</span>Reading a profile</h2><p class="lead">Every piece of information has one of these states. Missing data is never shown as zero.</p><div class="gaps">${states}</div></div>
    <div class="glass card"><h2><span class="ico">${ico("shield")}</span>Website verification levels</h2><p class="lead">How sure we are that a website is the company's own.</p>
      <div class="gaps">
        <div class="gap" style="border-style:solid"><span class="chip positive">Org. number on site</span><div><p style="margin:0">The exact organisation number is printed on the site next to a label like “Org.nr”. Strongest proof.</p></div></div>
        <div class="gap" style="border-style:solid"><span class="chip info">Matches the register</span><div><p style="margin:0">The website the company filed shows its registered e-mail or phone and its full legal name.</p></div></div>
        <div class="gap" style="border-style:solid"><span class="chip info">Filed by the company</span><div><p style="margin:0">The company filed this website itself and its name is in the domain and the title. Only registry-equal contacts are shown.</p></div></div>
        <div class="gap"><span class="chip warn">Unclear match</span><div><p style="margin:0">A site was found but could belong to a parent, chain or namesake, so it is not shown as the company's website.</p></div></div>
      </div></div>
  </div>
  <div class="glass card mt-l"><h2><span class="ico">${ico("db")}</span>Sources</h2><p class="lead">Official and open sources only. robots.txt is respected; no paid APIs and no social-media scraping.</p>
    <div class="facts-list">${[["Brønnøysundregistrene: Enhetsregisteret", "Identity, addresses, roles, workplaces, group links, change feeds", "NLOD 2.0"], ["Brønnøysundregistrene: Regnskapsregisteret", "Annual accounts, the last three filed years", "NLOD 2.0"], ["Støtteregisteret", "Public support grants", "NLOD 2.0"], ["NAV Arbeidsplassen", "Active job advertisements", "NAV API terms"], ["GLEIF", "LEI codes and parents", "CC0"], ["Wikidata", "Encyclopedia entries linked to the org. number", "CC0"], ["Forskningsrådet", "SkatteFUNN and Research Council projects", "NLOD 2.0"], ["Mattilsynet", "Food-safety inspections", "CC BY 4.0"], ["The company's own website", "Description, contacts, social profiles, careers, news, only after verification", "quoted as evidence"]].map((s) => `<div class="fact" style="cursor:default"><div class="k">${esc(s[0])}</div><div class="v" style="font-weight:450">${esc(s[1])}</div><div class="go">${esc(s[2])}</div></div>`).join("")}</div></div>`;
}

/* ------------------------------------------------------------------ router */
let currentEnv = null;
async function render() {
  const h = location.hash.replace(/^#/, "") || "/";
  const [path, query] = h.split("?");
  const parts = path.split("/").filter(Boolean);
  const tab = parts[0] === "company" ? "companies" : parts[0] || "home";
  $$(".tab").forEach((t) => t.classList.toggle("on", t.dataset.tab === tab));
  closeDrawer();
  const app = $("#app");
  currentEnv = null;
  if (!parts.length) { app.innerHTML = viewHome(); bindHome(); }
  else if (parts[0] === "companies") {
    const f = new URLSearchParams(query || "").get("f");
    if (f) { state.filter = f; state.shown = 40; }
    app.innerHTML = viewCompanies();
    bindCompanies();
  } else if (parts[0] === "company" && parts[1]) {
    const org = digits(parts[1]);
    app.innerHTML = viewCompanySkeleton();
    try {
      const env = state.cache.get(org) || (await api("/api/company/" + org));
      state.cache.set(org, env);
      if (location.hash.replace(/^#/, "") !== h) return;
      currentEnv = env;
      app.innerHTML = viewCompany(env);
      bindCompany();
      document.title = `${(env.legal_name || org)} · Signalpost`;
    } catch (e) { app.innerHTML = e.status === 404 ? viewMissing(org) : `<div class="glass card empty">${esc(e.message)}</div>`; }
    window.scrollTo({ top: 0 });
    return;
  } else if (parts[0] === "research") { app.innerHTML = viewResearch(digits(parts[1] || "")); bindResearch(digits(parts[1] || "")); }
  else if (parts[0] === "about") { app.innerHTML = viewAbout(); }
  else { app.innerHTML = `<div class="glass card empty">Page not found. <a href="#/">Go home</a></div>`; }
  document.title = "Signalpost · Norwegian company intelligence";
  countUp(app);
  window.scrollTo({ top: 0 });
}
function bindHome() {
  const q = $("#heroQ"), sug = $("#heroSug");
  let sel = -1, items = [];
  const draw = () => {
    const v = q.value, d = digits(v);
    const res = searchIndex(v, 6);
    items = res.map((r) => "#/company/" + r.orgnr);
    let html = res.map((r, i) => optRow(r, i, i === sel)).join("");
    if (d.length === 9 && mod11(d) && !state.byOrg.has(d)) { html = researchOpt(d, 0, sel === 0) + html; items.unshift("#/research/" + d); }
    sug.innerHTML = html;
    sug.classList.toggle("on", !!html);
  };
  q.addEventListener("input", () => { sel = -1; draw(); });
  q.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { sel = Math.min(items.length - 1, sel + 1); draw(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { sel = Math.max(0, sel - 1); draw(); e.preventDefault(); }
    else if (e.key === "Enter") { go(); }
    else if (e.key === "Escape") { sug.classList.remove("on"); }
  });
  const go = () => {
    const d = digits(q.value);
    if (items[Math.max(0, sel)]) goHash(items[Math.max(0, sel)]);
    else if (d.length === 9) goHash("#/research/" + d);
    else { state.q = q.value; state.filter = "all"; location.hash = "#/companies"; }
  };
  $("#heroGo").addEventListener("click", go);
  setTimeout(() => q.focus(), 300);
}
function bindCompanies() {
  renderList();
  $("#listQ").addEventListener("input", (e) => { state.q = e.target.value; state.shown = 40; renderList(); });
  $("#sort").addEventListener("change", (e) => { state.sort = e.target.value; renderList(); });
  $$("[data-f]").forEach((b) => b.addEventListener("click", () => { state.filter = b.dataset.f; state.shown = 40; $$("[data-f]").forEach((x) => x.classList.toggle("on", x === b)); history.replaceState(null, "", "#/companies" + (state.filter !== "all" ? "?f=" + state.filter : "")); renderList(); }));
  $("#more").addEventListener("click", (e) => { if (e.target.closest("#moreBtn")) { state.shown += 60; renderList(); } });
}
function bindCompany() {
  requestAnimationFrame(() => $$(".ring .fg").forEach((c) => (c.style.strokeDashoffset = c.dataset.off)));
}
function onCompanyClick(e) {
  if (!currentEnv) return;
  const j = e.target.closest("[data-jump]");
  if (j) { const t = document.getElementById(j.dataset.jump); if (t) window.scrollTo({ top: t.getBoundingClientRect().top + scrollY - 96, behavior: REDUCED ? "auto" : "smooth" }); return; }
  const cp = e.target.closest("[data-copy]");
  if (cp) { navigator.clipboard && navigator.clipboard.writeText(cp.dataset.copy).then(() => toast("Copied")); return; }
  if (e.target.closest("a[href^='http']") || e.target.closest(".help") || e.target.closest("summary")) return;
  const c = e.target.closest("[data-c]");
  if (c) openFact(currentEnv, c.dataset.c);
}

/* ------------------------------------------------------------------ global events */
async function loadIndex() {
  const j = await api("/api/index");
  state.index = j.companies;
  state.stats = j.stats;
  state.byOrg = new Map(j.companies.map((r) => [r.orgnr, r]));
  const st = j.stats;
  $("#footStats").textContent = `${fmtInt(st.companies)} companies · ${fmtInt(st.facts)} facts with sources · third-party cost $0.00`;
}
function setTheme(t) {
  document.documentElement.dataset.theme = t;
  localStorage.setItem("sp-theme", t);
  $("#theme").innerHTML = ico(t === "dark" ? "sun" : "moon");
}
document.addEventListener("keydown", (e) => {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test((document.activeElement || {}).tagName || "");
  if ((e.key === "k" && (e.metaKey || e.ctrlKey)) || (e.key === "/" && !typing)) { e.preventDefault(); openPal(); return; }
  if (e.key === "Escape") { closePal(); closeDrawer(); return; }
  if ($("#pal").classList.contains("on")) {
    if (e.key === "ArrowDown") { pal.sel = Math.min(pal.items.length - 1, pal.sel + 1); renderPal(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { pal.sel = Math.max(0, pal.sel - 1); renderPal(); e.preventDefault(); }
    else if (e.key === "Enter" && pal.items[pal.sel]) { goHash(pal.items[pal.sel]); closePal(); }
  }
});
$("#palIn").addEventListener("input", () => { pal.sel = 0; renderPal(); });
$("#palRes").addEventListener("click", () => closePal());
$("#openPal").addEventListener("click", () => openPal());
$("#scrim").addEventListener("click", () => { closePal(); closeDrawer(); });
$("#drawer").addEventListener("click", async (e) => {
  if (e.target.closest("#dx")) return closeDrawer();
  const cp = e.target.closest("[data-copy]");
  if (cp) { navigator.clipboard && navigator.clipboard.writeText(cp.dataset.copy).then(() => toast("Hash copied")); return; }
  const b = e.target.closest("[data-snap]");
  if (!b) return;
  b.disabled = true;
  b.innerHTML = '<span class="spin"></span> Loading archived copy…';
  try {
    const r = await api(`/api/snapshot/${b.dataset.snap}?q=${encodeURIComponent(b.dataset.q)}`);
    const box = $("#sp" + b.dataset.i);
    if (r.found && r.matched) { box.innerHTML = `${esc(r.before)}<mark>${esc(r.match)}</mark>${esc(r.after)}`; b.outerHTML = `<p class="small muted">Shown in the archived copy (${fmtInt(r.bytes)} bytes) stored when the source was read.</p>`; }
    else if (r.found) { b.outerHTML = `<p class="small muted">The archived copy exists (${fmtInt(r.bytes)} bytes) but the text is encoded differently; open the source link above.</p>`; }
    else { b.outerHTML = `<p class="small muted">No archived copy is stored for this dataset (only the 100-company smoke run keeps raw copies). Open the source link above.</p>`; }
  } catch (err) { b.outerHTML = `<p class="small muted">${esc(err.message)}</p>`; }
});
$("#theme").addEventListener("click", () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
// one delegated listener set for every view (views are re-rendered, the #app element is not)
$("#app").addEventListener("click", onCompanyClick);
$("#app").addEventListener("keydown", (e) => {
  const c = e.target.closest("[data-c]");
  if (currentEnv && c && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openFact(currentEnv, c.dataset.c); }
});
document.addEventListener("click", (e) => {
  const sg = $("#heroSug");
  if (sg && !e.target.closest(".searchbig")) sg.classList.remove("on");
  const a = e.target.closest('a[href^="#/research/"]');
  if (a) state.autoStart = digits(a.getAttribute("href"));
}, true);
const goHash = (h) => { const m = /^#\/research\/(\d{9})$/.exec(h); if (m) state.autoStart = m[1]; location.hash = h; };
window.addEventListener("hashchange", render);

(async function boot() {
  setTheme(localStorage.getItem("sp-theme") || "dark");
  $("#app").innerHTML = `<section class="hero"><div class="skel" style="height:30px;width:280px;margin:0 auto;border-radius:99px"></div><div class="skel" style="height:120px;max-width:780px;margin:26px auto 0"></div><div class="skel" style="height:64px;max-width:680px;margin:30px auto 0;border-radius:20px"></div></section>`;
  try { await loadIndex(); } catch (e) { $("#app").innerHTML = `<div class="glass card empty">Could not load the company library: ${esc(e.message)}</div>`; return; }
  render();
})();
