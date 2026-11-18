"""The browser experience: one shell, rendered by the client from the JSON API.

Every screen the spec names is served as this document; the script reads the
route, calls the same API the harness calls, and renders the result. Tokens live
in `localStorage`, so the API's bearer-token contract is exactly what the
browser uses -- no cookie session beside it.
"""
from __future__ import annotations

MARKUP = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dossier</title>
<style>
:root {
  --ink: #14202b; --muted: #5a6b7d; --line: #dfe6ee; --bg: #f4f7fa; --card: #ffffff;
  --accent: #1d4ed8; --accent-soft: #e8eefc; --known: #0b6b47; --known-soft: #e3f4ec;
  --contested: #9a4b06; --contested-soft: #fdeedb; --unknown: #55657a; --unknown-soft: #eef1f5;
  --building: #1d4ed8; --building-soft: #e5ecfd; --failed: #a3231b; --failed-soft: #fbe7e5;
  --radius: 14px; --gap: 20px;
  --font: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  --serif: ui-serif, Georgia, Cambria, "Times New Roman", serif;
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body { background: var(--bg); color: var(--ink); font-family: var(--font); line-height: 1.5;
  font-size: 16px; -webkit-font-smoothing: antialiased; }
a { color: var(--accent); text-decoration-thickness: 1px; text-underline-offset: 2px; }
a:hover { text-decoration-thickness: 2px; }
:focus-visible { outline: 3px solid #7aa2ff; outline-offset: 2px; border-radius: 6px; }
header.top { background: rgba(255,255,255,.94); border-bottom: 1px solid var(--line);
  position: sticky; top: 0; z-index: 20; backdrop-filter: blur(6px); }
.topbar { max-width: 1080px; margin: 0 auto; padding: 14px 20px; display: flex; gap: 18px;
  align-items: center; flex-wrap: wrap; }
.brand { font-family: var(--serif); font-size: 22px; font-weight: 700; letter-spacing: -.01em;
  color: var(--ink); text-decoration: none; }
.brand small { display: block; font-family: var(--font); font-size: 11px; letter-spacing: .12em;
  text-transform: uppercase; color: var(--muted); font-weight: 600; }
nav.main { display: flex; gap: 14px; margin-left: auto; align-items: center; flex-wrap: wrap; }
nav.main a { color: var(--muted); text-decoration: none; font-weight: 600; font-size: 14px;
  padding: 6px 2px; border-bottom: 2px solid transparent; }
nav.main a.on, nav.main a:hover { color: var(--ink); border-bottom-color: var(--accent); }
.user { display: flex; align-items: center; gap: 10px; padding-left: 6px; }
.chip { background: var(--accent-soft); color: #1b3b8f; border-radius: 999px; padding: 4px 12px;
  font-size: 13px; font-weight: 650; max-width: 220px; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }
main { max-width: 1080px; margin: 0 auto; padding: 28px 20px 64px; }
h1 { font-family: var(--serif); font-size: clamp(26px, 4vw, 34px); line-height: 1.15;
  margin: 0 0 6px; letter-spacing: -.015em; }
h2 { font-size: 17px; margin: 0 0 12px; letter-spacing: -.005em; }
.lede { color: var(--muted); margin: 0 0 24px; max-width: 62ch; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: var(--radius);
  padding: 20px; box-shadow: 0 1px 2px rgba(16,32,48,.04); }
.stack { display: grid; gap: var(--gap); }
.row { display: flex; gap: 12px; align-items: end; flex-wrap: wrap; }
label { display: block; font-size: 13px; font-weight: 650; color: var(--muted); margin-bottom: 6px; }
input[type=text], input[type=email], input[type=password], input[type=search] { width: 100%;
  padding: 11px 12px; border: 1px solid #c7d2de; border-radius: 10px; font: inherit;
  color: var(--ink); background: #fff; }
input::placeholder { color: #93a1b1; }
button { font: inherit; font-weight: 650; border-radius: 10px; cursor: pointer; border: 1px solid
  transparent; padding: 11px 18px; transition: background .12s, box-shadow .12s; }
button.primary { background: var(--accent); color: #fff; }
button.primary:hover { background: #1740b8; }
button.secondary { background: #fff; color: var(--ink); border-color: #c7d2de; }
button.secondary:hover { background: #f4f7fb; }
button[disabled] { opacity: .6; cursor: progress; }
.actions { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
.muted { color: var(--muted); }
.small { font-size: 13px; }
.pill { display: inline-flex; align-items: center; gap: 6px; border-radius: 999px;
  padding: 2px 10px; font-size: 12px; font-weight: 700; letter-spacing: .02em;
  text-transform: capitalize; }
.pill.known { background: var(--known-soft); color: var(--known); }
.pill.contested { background: var(--contested-soft); color: var(--contested); }
.pill.unknown { background: var(--unknown-soft); color: var(--unknown); }
.pill.building { background: var(--building-soft); color: var(--building); }
.pill.ready { background: var(--known-soft); color: var(--known); }
.pill.failed { background: var(--failed-soft); color: var(--failed); }
.pill.kind { background: #eef2f7; color: #44546a; text-transform: none; font-weight: 600; }
.alert { border-radius: 12px; padding: 12px 14px; font-size: 14px; border: 1px solid; }
.alert.error { background: var(--failed-soft); border-color: #f0c4bf; color: #7d1b14; }
.alert.uncertain { background: var(--contested-soft); border-color: #f2d3ab; color: #713a07; }
.alert.info { background: var(--accent-soft); border-color: #c9d7fb; color: #1b3b8f; }
.dossier-head { display: flex; gap: 18px; align-items: start; justify-content: space-between;
  flex-wrap: wrap; }
.meta { display: flex; gap: 18px; flex-wrap: wrap; color: var(--muted); font-size: 14px;
  margin-top: 8px; }
.meta strong { color: var(--ink); font-weight: 650; }
.fields { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }
.field { background: var(--card); border: 1px solid var(--line); border-radius: var(--radius);
  padding: 18px; display: flex; flex-direction: column; gap: 10px; }
.field[data-status=contested] { border-color: #eccb9d; }
.field-head { display: flex; align-items: center; gap: 10px; justify-content: space-between; }
.field-name { font-size: 13px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase;
  color: var(--muted); }
.value { font-size: 20px; font-weight: 650; letter-spacing: -.01em; word-break: break-word; }
.value.none { font-size: 15px; font-weight: 500; color: var(--muted); font-style: italic; }
blockquote.cite { margin: 0; padding: 10px 12px; background: #f8fafc; border-left: 3px solid #c7d6ea;
  border-radius: 0 8px 8px 0; font-family: var(--serif); font-size: 14.5px; color: #22303f; }
.cite-foot { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-top: 6px;
  font-size: 12px; color: var(--muted); }
.cites { display: grid; gap: 10px; }
.cand { border: 1px solid var(--line); border-radius: 12px; padding: 14px; background: #fff;
  display: grid; gap: 8px; }
.cand[data-selected=true] { border-color: #b9cdf3; box-shadow: inset 0 0 0 1px #dbe6fb; }
.cand-head { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.cand-value { font-weight: 650; font-size: 16px; }
.rank { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.evidence-grid { display: grid; gap: 18px; }
.list { display: grid; gap: 10px; padding: 0; margin: 0; list-style: none; }
.dossier-item { display: flex; gap: 14px; align-items: center; justify-content: space-between;
  border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px; background: #fff;
  flex-wrap: wrap; }
.dossier-item a.domain { font-weight: 650; font-size: 16px; text-decoration: none; color: var(--ink); }
.dossier-item a.domain:hover { color: var(--accent); }
.dossier-item .legal { color: var(--muted); font-size: 14px; }
.contested-item { border: 1px solid #eccb9d; background: #fffdf9; border-radius: 12px;
  padding: 14px 16px; display: grid; gap: 6px; }
.values { display: flex; gap: 8px; flex-wrap: wrap; }
.value-chip { background: #fff; border: 1px solid var(--line); border-radius: 999px;
  padding: 2px 10px; font-size: 13px; }
.empty { border: 1px dashed #c7d2de; border-radius: var(--radius); padding: 28px;
  text-align: center; color: var(--muted); background: #fbfdff; }
.skeleton { background: linear-gradient(100deg, #eef2f7 30%, #f7fafc 45%, #eef2f7 60%);
  background-size: 200% 100%; animation: shimmer 1.4s infinite; border-radius: 10px; height: 14px; }
@keyframes shimmer { from { background-position: 120% 0; } to { background-position: -20% 0; } }
.auth { max-width: 440px; margin: 8px auto 0; }
.auth form { display: grid; gap: 14px; }
.hint { font-size: 13px; color: var(--muted); }
footer.foot { max-width: 1080px; margin: 0 auto; padding: 0 20px 40px; color: var(--muted);
  font-size: 12px; }
@media (max-width: 640px) {
  .topbar { padding: 12px 16px; gap: 10px; }
  nav.main { width: 100%; margin-left: 0; gap: 12px; }
  main { padding: 20px 16px 48px; }
  .fields { grid-template-columns: 1fr; }
  .card { padding: 16px; }
  .field { padding: 16px; }
  .row { align-items: stretch; }
  .row > div { flex: 1 1 100%; }
  .row button { width: 100%; }
}
</style>
</head>
<body>
<header class="top">
  <div class="topbar">
    <a class="brand" href="/" data-testid="brand">Dossier<small>Company research</small></a>
    <nav class="main" id="nav"></nav>
    <div class="user" id="user"></div>
  </div>
</header>
<main id="app" aria-live="polite">
  <div class="stack"><div class="skeleton" style="width:38%"></div>
  <div class="skeleton" style="width:72%"></div><div class="skeleton" style="width:56%"></div></div>
</main>
<footer class="foot"><span id="foot">Six fields, every value with the reading it came from.</span></footer>
<script>
"use strict";
const FIELDS = ["legal_name", "hq_city", "ceo", "founded_year", "headcount_range", "funding_total"];
const LABELS = {legal_name: "Legal name", hq_city: "Headquarters city", ceo: "Chief executive",
  founded_year: "Founded", headcount_range: "Headcount", funding_total: "Total funding"};
const RANGES = {"1-10": "1\\u201310", "11-50": "11\\u201350", "51-200": "51\\u2013200",
  "201-500": "201\\u2013500", "501-1000": "501\\u20131,000", "1001-5000": "1,001\\u20135,000",
  "5001-10000": "5,001\\u201310,000", "10001+": "10,001+"};
const SESSION_KEY = "dossier.session";
const app = document.getElementById("app");
const POLL_MS = 1500;
let readSeq = 0;
let pollTimer = null;
let pendingResearch = null;
let pendingRefresh = null;

function session() {
  try { return JSON.parse(localStorage.getItem(SESSION_KEY) || "null"); } catch (e) { return null; }
}
function storeSession(value) {
  if (value) localStorage.setItem(SESSION_KEY, JSON.stringify(value));
  else localStorage.removeItem(SESSION_KEY);
}
function escapeHtml(text) {
  return String(text == null ? "" : text).replace(/[&<>"']/g, (c) => (
    {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
}
function newKey() {
  if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
  return "k" + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
}
function normalizeDomain(raw) {
  let domain = String(raw || "").trim().toLowerCase();
  if (domain.endsWith(".")) domain = domain.slice(0, -1);
  if (domain.startsWith("www.")) domain = domain.slice(4);
  return domain;
}
function token() { const s = session(); return s && s.token ? s.token : null; }
async function api(path, options) {
  const settings = Object.assign({headers: {}}, options || {});
  settings.headers = Object.assign({"Accept": "application/json"}, settings.headers);
  const bearer = token();
  if (bearer) settings.headers["Authorization"] = "Bearer " + bearer;
  if (settings.json !== undefined) {
    settings.headers["Content-Type"] = "application/json";
    settings.body = JSON.stringify(settings.json);
    delete settings.json;
  }
  const response = await fetch(path, settings);
  let body = null;
  try { body = await response.json(); } catch (e) { body = null; }
  return {status: response.status, ok: response.ok, body: body};
}
function errorText(result, fallback) {
  if (result && result.body && result.body.error && result.body.error.code) {
    const code = result.body.error.code;
    const words = {validation_failed: "That request was not valid",
      malformed_request: "That request could not be read",
      unauthenticated: "Please sign in again", not_found: "Not found",
      email_taken: "That email is already registered",
      idempotency_key_reuse: "That request clashed with an earlier one"};
    return words[code] || code.replace(/_/g, " ");
  }
  return fallback;
}
function fmtNumber(value) { return Number(value).toLocaleString("en-US"); }
function fmtValue(field, value) {
  if (value === null || value === undefined) return "";
  if (field === "funding_total") return "$" + fmtNumber(value);
  if (field === "headcount_range") return (RANGES[value] || value) + " people";
  if (field === "founded_year") return String(value);
  return String(value);
}
function fmtDate(iso) {
  if (!iso) return "";
  const when = new Date(iso);
  if (isNaN(when.getTime())) return String(iso);
  return when.toLocaleString("en-GB", {day: "numeric", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit", timeZone: "UTC", timeZoneName: "short"});
}
function units(value) { return fmtNumber(value) + (value === 1 ? " unit" : " units"); }
function kindLabel(kind) { return kind ? String(kind) : "imported"; }

function header(path) {
  const nav = document.getElementById("nav");
  const links = [["/", "Dossiers"], ["/contested", "Contested"]];
  nav.innerHTML = links.map(([href, label]) => {
    const on = href === "/" ? (path === "/") : path.startsWith(href);
    return `<a href="${href}" class="${on ? "on" : ""}">${label}</a>`;
  }).join("");
  const box = document.getElementById("user");
  const who = session();
  if (who && who.display_name) {
    box.innerHTML = `<span class="chip" data-testid="current-user">${escapeHtml(who.display_name)}</span>
      <button class="secondary small" type="button" data-testid="logout-button" id="logout">Sign out</button>`;
    document.getElementById("logout").addEventListener("click", () => {
      storeSession(null);
      location.assign("/login");
    });
  } else {
    box.innerHTML = `<a href="/login">Sign in</a> <a href="/signup">Create account</a>`;
  }
}

function requireSession() {
  if (token()) return true;
  location.replace("/login?next=" + encodeURIComponent(location.pathname + location.search));
  return false;
}

function notice(id, text, kind) {
  return `<div class="alert ${kind}" data-testid="${id}" role="status">${escapeHtml(text)}</div>`;
}

/* ---- the dossier list (/) ------------------------------------------------ */
async function renderList() {
  const seq = ++readSeq;
  clearTimeout(pollTimer);
  let result;
  try { result = await api("/dossiers?limit=200"); }
  catch (e) {
    if (seq === readSeq) {
      app.innerHTML = `<h1>Dossiers</h1>` + notice("list-error", "The list could not be read.", "error");
    }
    return;
  }
  if (seq !== readSeq) return;
  if (result.status === 401) { requireSession(); return; }
  const entries = (result.body && result.body.dossiers) || [];
  app.innerHTML = `
    <h1>Dossiers</h1>
    <p class="lede">Research a company by its web domain. Every field keeps the readings behind it.</p>
    <div class="stack">
      <section class="card" aria-labelledby="research-title">
        <h2 id="research-title">Research a company</h2>
        <form id="research-form" class="row" novalidate>
          <div style="flex:1 1 280px">
            <label for="research-domain">Company domain</label>
            <input id="research-domain" data-testid="research-domain" name="domain" type="text"
              placeholder="acme-robotics.com" autocomplete="off" autocapitalize="off" spellcheck="false">
          </div>
          <button class="primary" type="submit" data-testid="research-submit">Research</button>
        </form>
        <div id="research-notes" class="stack" style="margin-top:12px"></div>
      </section>
      <section class="card" aria-labelledby="list-title">
        <div class="row" style="justify-content:space-between">
          <h2 id="list-title">Companies</h2>
          <div style="flex: 0 1 320px">
            <label for="dossier-search">Filter by domain or legal name</label>
            <input id="dossier-search" data-testid="dossier-search" type="search" autocomplete="off"
              placeholder="Search">
          </div>
        </div>
        <div id="list-slot" style="margin-top:14px"></div>
      </section>
    </div>`;
  const notes = document.getElementById("research-notes");
  if (pendingResearch && pendingResearch.uncertain) {
    notes.innerHTML = notice("research-uncertain",
      "We lost the outcome of that request. It is safe to try again.", "uncertain");
  }
  document.getElementById("research-form").addEventListener("submit", (event) => {
    event.preventDefault();
    research(notes);
  });
  const search = document.getElementById("dossier-search");
  search.addEventListener("input", () => paintList(entries, search.value));
  paintList(entries, "");
}

function paintList(entries, query) {
  const slot = document.getElementById("list-slot");
  const needle = String(query || "").trim().toLowerCase();
  const shown = entries.filter((entry) => !needle ||
    entry.domain.toLowerCase().includes(needle) ||
    (entry.legal_name || "").toLowerCase().includes(needle));
  if (!shown.length) {
    slot.innerHTML = `<div class="empty" data-testid="empty-dossiers">${
      entries.length ? "No dossier matches that search." : "No dossiers yet. Research a domain above."}</div>`;
    return;
  }
  slot.innerHTML = `<ul class="list" data-testid="dossier-list">` + shown.map((entry) => `
    <li class="dossier-item" data-testid="dossier-item-${escapeHtml(entry.domain)}"
        data-state="${escapeHtml(entry.state)}">
      <div>
        <a class="domain" href="/dossiers/${encodeURIComponent(entry.domain)}">${escapeHtml(entry.domain)}</a>
        ${entry.legal_name ? `<div class="legal">${escapeHtml(entry.legal_name)}</div>` : ""}
      </div>
      <div class="actions small">
        <span class="pill ${escapeHtml(entry.state)}">${escapeHtml(entry.state)}</span>
        <span class="muted">${escapeHtml(units(entry.spent_units))}</span>
      </div>
    </li>`).join("") + `</ul>`;
}

async function research(notes) {
  const input = document.getElementById("research-domain");
  const submit = document.querySelector("[data-testid=research-submit]");
  const typed = input.value.trim();
  const domain = normalizeDomain(typed);
  // One key per intended request: retrying after a lost response reuses it, but a
  // different typed domain is a different request and needs a key of its own.
  if (!pendingResearch || pendingResearch.typed !== typed) {
    pendingResearch = {typed: typed, key: newKey(), body: {domain: typed}};
  }
  submit.disabled = true;
  notes.innerHTML = notice("research-loading", "Researching\\u2026", "info");
  let result;
  try {
    result = await api("/dossiers", {method: "POST", json: pendingResearch.body,
      headers: {"Idempotency-Key": pendingResearch.key}});
  } catch (error) {
    pendingResearch.uncertain = true;
    submit.disabled = false;
    notes.innerHTML = notice("research-uncertain",
      "We lost the outcome of that request. It is safe to try again.", "uncertain");
    return;
  }
  submit.disabled = false;
  if (result.ok) {
    pendingResearch = null;
    const opened = (result.body && result.body.domain) || domain;
    location.assign("/dossiers/" + encodeURIComponent(opened));
    return;
  }
  if (result.status === 401) { requireSession(); return; }
  if (result.status >= 500) {
    pendingResearch.uncertain = true;
    notes.innerHTML = notice("research-uncertain",
      "The service did not confirm that request. It is safe to try again.", "uncertain");
    return;
  }
  notes.innerHTML = notice("research-error", errorText(result, "That domain was refused."), "error");
}

/* ---- one dossier (/dossiers/{domain}) ----------------------------------- */
function citationsHtml(field, name) {
  const cites = field.citations || [];
  const body = cites.map((cite, index) => `
    <blockquote class="cite" data-testid="citation-${name}-${index + 1}"
        data-provider="${escapeHtml(cite.provider)}">
      ${escapeHtml(cite.excerpt)}
      <div class="cite-foot">
        <span class="pill kind">${escapeHtml(cite.provider)}</span>
        <a href="${escapeHtml(cite.url)}" rel="noopener noreferrer" target="_blank">Open source</a>
      </div>
    </blockquote>`).join("");
  return `<div class="cites" data-testid="field-citations-${name}">${body}</div>`;
}

function fieldsHtml(body) {
  return `<div class="fields">` + FIELDS.map((name) => {
    const field = (body.fields || {})[name] || {value: null, status: "unknown", citations: []};
    const contested = field.status === "contested"
      ? `<span class="pill contested" data-testid="field-contested-${name}">sources disagree</span>`
      : "";
    const value = field.value === null || field.value === undefined
      ? `<div class="value none">Not established by the evidence</div>`
      : `<div class="value" data-testid="field-value-${name}"
             data-value="${escapeHtml(String(field.value))}">${escapeHtml(fmtValue(name, field.value))}</div>`;
    return `<section class="field" data-testid="field-${name}"
        data-status="${escapeHtml(field.status)}" aria-label="${LABELS[name]}">
      <div class="field-head">
        <span class="field-name">${LABELS[name]}</span>
        <span class="pill ${escapeHtml(field.status)}">${escapeHtml(field.status)}</span>
        ${contested}
      </div>
      ${value}
      ${citationsHtml(field, name)}
    </section>`;
  }).join("") + `</div>`;
}

async function renderDossier(domain) {
  const seq = ++readSeq;
  clearTimeout(pollTimer);
  if (seq === 1) app.innerHTML = loadingDossier(domain);
  let result;
  try { result = await api("/dossiers/" + encodeURIComponent(domain)); }
  catch (error) {
    if (seq === readSeq) app.innerHTML = `<div class="alert error" data-testid="dossier-error">
      The dossier could not be read. <button class="secondary" id="retry">Try again</button></div>`;
    if (seq === readSeq) document.getElementById("retry").addEventListener("click", () => renderDossier(domain));
    return;
  }
  if (seq !== readSeq) return;
  if (result.status === 401) { requireSession(); return; }
  if (result.status === 404) {
    app.innerHTML = `<h1>Dossier</h1>
      <div class="empty" data-testid="dossier-not-found">
        No dossier exists for <strong>${escapeHtml(domain)}</strong>.
        <div class="actions" style="justify-content:center;margin-top:14px">
          <a class="muted" href="/">Back to dossiers</a></div></div>`;
    return;
  }
  if (!result.ok) {
    app.innerHTML = `<h1>Dossier</h1>` + notice("dossier-error",
      errorText(result, "The dossier could not be read."), "error");
    return;
  }
  const body = result.body;
  app.innerHTML = `
    <div class="dossier-head">
      <div>
        <h1 data-testid="dossier-domain">${escapeHtml(body.domain)}</h1>
        <div class="meta">
          <span>State <span class="pill ${escapeHtml(body.state)}"
            data-testid="dossier-state">${escapeHtml(body.state)}</span></span>
          <span>Spent <strong data-testid="dossier-spent"
            data-units="${escapeHtml(String(body.spent_units))}">${escapeHtml(units(body.spent_units))}</strong></span>
          ${body.researched_at ? `<span>Researched <strong data-testid="dossier-researched-at"
            title="${escapeHtml(body.researched_at)}">${escapeHtml(fmtDate(body.researched_at))}</strong></span>` : ""}
        </div>
      </div>
      <div class="actions">
        <button class="primary" data-testid="dossier-refresh" id="refresh">Refresh research</button>
        <button class="secondary" data-testid="dossier-reload" id="reload">Reload</button>
        <a class="secondary" style="padding:11px 18px;border:1px solid #c7d2de;border-radius:10px;
          text-decoration:none;color:var(--ink);font-weight:650"
          data-testid="dossier-evidence-link"
          href="/dossiers/${encodeURIComponent(body.domain)}/evidence">See all evidence</a>
      </div>
    </div>
    <div id="dossier-notes" class="stack" style="margin:16px 0"></div>
    <div id="dossier-body">${body.state === "building" ? buildingNotice(body) : fieldsHtml(body)}</div>`;
  const notes = document.getElementById("dossier-notes");
  if (pendingRefresh && pendingRefresh.uncertain && pendingRefresh.domain === body.domain) {
    notes.innerHTML = notice("refresh-uncertain",
      "We lost the outcome of that refresh. It is safe to try again.", "uncertain");
  }
  document.getElementById("reload").addEventListener("click", () => renderDossier(body.domain));
  document.getElementById("refresh").addEventListener("click", () => refresh(body.domain, notes));
  if (body.state === "building" && seq === readSeq) {
    pollTimer = setTimeout(() => renderDossier(body.domain), POLL_MS);
  }
}

function loadingDossier(domain) {
  return `<h1>${escapeHtml(domain)}</h1>
    <div class="stack"><div class="skeleton" style="width:30%"></div>
    <div class="skeleton" style="width:64%"></div>
    <div class="fields">${FIELDS.map(() => `<div class="field"><div class="skeleton" style="width:40%"></div>
      <div class="skeleton" style="width:70%"></div></div>`).join("")}</div></div>`;
}

function buildingNotice(body) {
  return `<div class="alert info" data-testid="building-notice">
    Research is running. This page checks again every ${POLL_MS / 1000} seconds.
  </div>
  <div style="margin-top:16px">${fieldsHtml(body)}</div>`;
}

async function refresh(domain, notes) {
  const button = document.getElementById("refresh");
  if (!pendingRefresh || pendingRefresh.domain !== domain) {
    pendingRefresh = {domain: domain, key: newKey()};
  }
  button.disabled = true;
  notes.innerHTML = notice("refresh-loading", "Starting a fresh build\\u2026", "info");
  let result;
  try {
    result = await api("/dossiers/" + encodeURIComponent(domain) + "/refresh",
      {method: "POST", headers: {"Idempotency-Key": pendingRefresh.key}});
  } catch (error) {
    pendingRefresh.uncertain = true;
    button.disabled = false;
    notes.innerHTML = notice("refresh-uncertain",
      "We lost the outcome of that refresh. It is safe to try again.", "uncertain");
    return;
  }
  button.disabled = false;
  if (result.ok) {
    pendingRefresh = null;
    await renderDossier(domain);
    return;
  }
  if (result.status === 401) { requireSession(); return; }
  if (result.status >= 500) {
    pendingRefresh.uncertain = true;
    notes.innerHTML = notice("refresh-uncertain",
      "The service did not confirm that refresh. It is safe to try again.", "uncertain");
    return;
  }
  notes.innerHTML = notice("refresh-error", errorText(result, "That refresh was refused."), "error");
}

/* ---- evidence (/dossiers/{domain}/evidence) ----------------------------- */
async function renderEvidence(domain) {
  const seq = ++readSeq;
  clearTimeout(pollTimer);
  let result;
  try { result = await api("/dossiers/" + encodeURIComponent(domain)); }
  catch (error) {
    app.innerHTML = `<h1>Evidence</h1>` + notice("evidence-error", "The evidence could not be read.", "error");
    return;
  }
  if (seq !== readSeq) return;
  if (result.status === 401) { requireSession(); return; }
  if (result.status === 404) {
    app.innerHTML = `<h1>Evidence</h1>
      <div class="empty" data-testid="dossier-not-found">No dossier exists for
      <strong>${escapeHtml(domain)}</strong>.</div>`;
    return;
  }
  if (!result.ok) {
    app.innerHTML = `<h1>Evidence</h1>` + notice("evidence-error",
      errorText(result, "The evidence could not be read."), "error");
    return;
  }
  const body = result.body;
  app.innerHTML = `
    <p class="small"><a href="/dossiers/${encodeURIComponent(body.domain)}">&larr; ${escapeHtml(body.domain)}</a></p>
    <h1>Evidence</h1>
    <p class="lede">Every reading behind every field of
      <strong>${escapeHtml(body.domain)}</strong>, ranked by the selection rule.</p>
    <div class="evidence-grid">` + FIELDS.map((name) => {
      const field = (body.fields || {})[name] || {candidates: []};
      const list = field.candidates || [];
      const inner = list.length ? list.map((cand, index) => `
        <article class="cand" data-testid="candidate-${name}-${index + 1}"
          data-value="${escapeHtml(String(cand.value))}"
          data-provider="${escapeHtml(cand.provider)}"
          data-kind="${escapeHtml(cand.kind === null ? "" : cand.kind)}"
          data-url="${escapeHtml(cand.url)}"
          data-published-at="${escapeHtml(cand.published_at || "")}"
          data-selected="${cand.selected ? "true" : "false"}">
          <div class="cand-head">
            <span class="cand-value">${escapeHtml(fmtValue(name, cand.value))}</span>
            ${cand.selected ? `<span class="pill known">selected</span>` : `<span class="rank">rank ${index + 1}</span>`}
            <span class="pill kind">${escapeHtml(kindLabel(cand.kind))}</span>
            <span class="pill kind">${escapeHtml(cand.provider)}</span>
          </div>
          <blockquote class="cite">${escapeHtml(cand.excerpt)}</blockquote>
          <div class="cite-foot">
            <a href="${escapeHtml(cand.url)}" rel="noopener noreferrer" target="_blank">Open source</a>
            <span>${cand.published_at ? "Published " + escapeHtml(fmtDate(cand.published_at)) : "No publication date"}</span>
            ${cand.retrieved_at ? `<span>Read ${escapeHtml(fmtDate(cand.retrieved_at))}</span>` : ""}
          </div>
        </article>`).join("")
        : `<div class="empty" data-testid="evidence-empty-${name}">No source supports this field yet.</div>`;
      return `<section class="card" data-testid="evidence-${name}">
        <div class="field-head"><h2 style="margin:0">${LABELS[name]}</h2>
          <span class="pill ${escapeHtml(field.status || "unknown")}">${escapeHtml(field.status || "unknown")}</span></div>
        <div class="stack" style="margin-top:12px">${inner}</div>
      </section>`;
    }).join("") + `</div>`;
}

/* ---- contested (/contested) --------------------------------------------- */
async function renderContested() {
  const seq = ++readSeq;
  clearTimeout(pollTimer);
  let listed;
  try { listed = await api("/dossiers?limit=200"); }
  catch (error) {
    app.innerHTML = `<h1>Contested fields</h1>` + notice("contested-error",
      "The contested fields could not be read.", "error");
    return;
  }
  if (seq !== readSeq) return;
  if (listed.status === 401) { requireSession(); return; }
  const entries = (listed.body && listed.body.dossiers) || [];
  const bodies = [];
  const queue = entries.slice();
  async function worker() {
    while (queue.length) {
      const entry = queue.shift();
      try {
        const one = await api("/dossiers/" + encodeURIComponent(entry.domain));
        if (one.ok && one.body) bodies.push(one.body);
      } catch (error) { /* one dossier unreadable must not blank the screen */ }
    }
  }
  await Promise.all(Array.from({length: Math.min(6, entries.length)}, worker));
  if (seq !== readSeq) return;
  const rows = [];
  bodies.sort((a, b) => a.domain < b.domain ? -1 : a.domain > b.domain ? 1 : 0)
    .forEach((body) => FIELDS.forEach((name) => {
      const field = (body.fields || {})[name];
      if (field && field.status === "contested") rows.push({body: body, name: name, field: field});
    }));
  app.innerHTML = `<h1>Contested fields</h1>
    <p class="lede">Fields whose sources disagree. The selected value follows the ranking rule.</p>` +
    (rows.length ? `<div class="stack" data-testid="contested-list">` + rows.map(({body, name, field}) => {
      const values = [];
      (field.candidates || []).forEach((cand) => {
        if (!values.some((value) => String(value) === String(cand.value))) values.push(cand.value);
      });
      return `<a class="contested-item" style="text-decoration:none;color:inherit"
        data-testid="contested-item-${escapeHtml(body.domain)}-${name}"
        data-value="${escapeHtml(String(field.value))}"
        href="/dossiers/${encodeURIComponent(body.domain)}/evidence">
        <div class="cand-head">
          <strong>${escapeHtml(body.domain)}</strong>
          <span class="muted small">${LABELS[name]} &middot; ${name}</span>
          <span class="pill contested">contested</span>
        </div>
        <div class="values">${values.map((value) => `<span class="value-chip">${
          escapeHtml(fmtValue(name, value))}</span>`).join("")}</div>
        <div class="small muted">Selected: ${escapeHtml(fmtValue(name, field.value))} &middot; see all evidence</div>
      </a>`;
    }).join("") + `</div>`
    : `<div class="empty" data-testid="empty-contested">No field is contested right now.</div>`);
}

/* ---- signup and login --------------------------------------------------- */
function renderAuth(which, next) {
  clearTimeout(pollTimer);
  const signup = which === "signup";
  app.innerHTML = `<div class="auth">
    <h1>${signup ? "Create your account" : "Sign in"}</h1>
    <p class="lede">${signup ? "One account is enough to research and read every dossier."
      : "Research dossiers are shared by everyone signed in."}</p>
    <section class="card">
      <form id="auth-form" novalidate>
        ${signup ? `<div><label for="signup-display-name">Display name</label>
          <input id="signup-display-name" data-testid="signup-display-name" type="text"
            autocomplete="name" required></div>` : ""}
        <div><label for="${signup ? "signup" : "login"}-email">Email</label>
          <input id="${signup ? "signup" : "login"}-email"
            data-testid="${signup ? "signup" : "login"}-email" type="email"
            autocomplete="email" required></div>
        <div><label for="${signup ? "signup" : "login"}-password">Password</label>
          <input id="${signup ? "signup" : "login"}-password"
            data-testid="${signup ? "signup" : "login"}-password" type="password"
            autocomplete="${signup ? "new-password" : "current-password"}" required></div>
        <div id="auth-notes"></div>
        <button class="primary" type="submit" data-testid="${signup ? "signup" : "login"}-submit">
          ${signup ? "Create account" : "Sign in"}</button>
      </form>
    </section>
    <p class="hint">${signup ? `Already have an account? <a href="/login">Sign in</a>.`
      : `New here? <a href="/signup">Create an account</a>.`}</p>
  </div>`;
  document.getElementById("auth-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const notes = document.getElementById("auth-notes");
    const button = document.querySelector("[data-testid$=-submit]");
    const email = document.getElementById((signup ? "signup" : "login") + "-email").value;
    const password = document.getElementById((signup ? "signup" : "login") + "-password").value;
    const body = signup
      ? {email: email, password: password,
         display_name: document.getElementById("signup-display-name").value}
      : {email: email, password: password};
    button.disabled = true;
    notes.innerHTML = "";
    let result;
    try { result = await api(signup ? "/auth/signup" : "/auth/login", {method: "POST", json: body}); }
    catch (error) {
      button.disabled = false;
      notes.innerHTML = notice("auth-error", "We lost the outcome of that request.", "error");
      return;
    }
    button.disabled = false;
    if (!result.ok) {
      notes.innerHTML = notice("auth-error",
        errorText(result, signup ? "That account could not be created." : "That sign-in was refused."),
        "error");
      return;
    }
    storeSession({token: result.body.token, display_name: result.body.display_name,
      user_id: result.body.user_id});
    location.assign(next || "/");
  });
}

/* ---- boot --------------------------------------------------------------- */
function boot() {
  const path = location.pathname.replace(/\\/+$/, "") || "/";
  header(path);
  const params = new URLSearchParams(location.search);
  if (path === "/login") return renderAuth("login", params.get("next"));
  if (path === "/signup") return renderAuth("signup", params.get("next"));
  if (!requireSession()) return;
  if (path === "/") return renderList();
  if (path === "/contested") return renderContested();
  const evidence = path.match(/^\\/dossiers\\/([^/]+)\\/evidence$/);
  if (evidence) return renderEvidence(normalizeDomain(decodeURIComponent(evidence[1])));
  const one = path.match(/^\\/dossiers\\/([^/]+)$/);
  if (one) return renderDossier(normalizeDomain(decodeURIComponent(one[1])));
  app.innerHTML = `<h1>Not found</h1><div class="empty">There is nothing at this address.
    <div style="margin-top:12px"><a href="/">Back to dossiers</a></div></div>`;
}
boot();
</script>
</body>
</html>
"""


def page() -> str:
    """The single document every browser route serves."""
    return MARKUP
