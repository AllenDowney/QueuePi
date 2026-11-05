// QueuePi staff pages: register, kitchen, or both (chosen by the URL path).
//
// The page polls /api/board every two seconds and redraws when the board's
// version changes. Every change names the status this device last saw, so if
// another device got there first the server says 409 and we just refresh.
"use strict";

const VIEWS = { "/register": "register", "/kitchen": "kitchen", "/staff": "both" };
const VIEW = VIEWS[location.pathname] || "both";
const POLL_MS = 2000;
const TOAST_MS = 8000;
// Moves that reverse an earlier one (shown as "back to ..." in Recent changes).
const UNDO_MOVES = new Set(["ready>waiting", "picked_up>ready", "cancelled>waiting"]);
const LABELS = { register: "Register", kitchen: "Kitchen", both: "Register + Kitchen" };

// Older phone browsers lack replaceChildren and ?., so avoid newer syntax
// and DOM methods here; test on the shop's actual devices.
const $ = (id) => document.getElementById(id);

function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
}

function fill(el, children) {
  clear(el);
  for (const c of children) el.appendChild(c);
}

let board = null;
let clockOffset = 0; // server time minus device time, ms
let digits = "";
let toastTimer = null;
let pending = false; // a write is in flight

document.body.dataset.view = VIEW;
$("title").textContent = "QueuePi · " + LABELS[VIEW];
try { localStorage.setItem("queuepi.view", location.pathname); } catch (e) { /* private mode */ }

// --- talking to the server ---

async function api(method, path, body) {
  const options = { method, cache: "no-store", headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch (e) {
    setOffline(true);
    return { ok: false, status: 0, data: { detail: "Can't reach QueuePi. Check the Wi-Fi." } };
  }
  setOffline(false);
  let data = {};
  try { data = await response.json(); } catch (e) { /* empty body */ }
  if (response.status === 401 && path !== "/api/login") showLogin();
  return { ok: response.ok, status: response.status, data };
}

function setOffline(offline) {
  $("offline").hidden = !offline;
}

async function refresh(force = false) {
  const r = await api("GET", "/api/board");
  if (!r.ok) return;
  clockOffset = Date.parse(r.data.server_time) - Date.now();
  const changed = !board || board.version !== r.data.version;
  board = r.data;
  if (changed || force) render();
  else renderAges();
  if ($("recent").open && changed) loadRecent();
}

// A write: send it, show the outcome, refresh. `undoText` labels the toast.
async function act(path, body, okText) {
  if (pending) return null;
  pending = true;
  document.body.classList.add("busy");
  try {
    const r = await api("POST", path, body);
    if (r.ok) {
      toast(okText(r.data), r.data.last_event_id);
    } else if (r.status !== 401) {
      toast(r.status === 409 && r.data.code === "conflict"
        ? "Already changed on another device"
        : (r.data.detail || "Something went wrong"), null, true);
    }
    await refresh(true);
    return r;
  } finally {
    pending = false;
    document.body.classList.remove("busy");
  }
}

// --- toast with undo ---

function toast(text, undoEventId = null, error = false) {
  const el = $("toast");
  $("toast-text").textContent = text;
  el.classList.toggle("error", error);
  const undo = $("toast-undo");
  undo.hidden = undoEventId == null;
  undo.onclick = () => {
    hideToast();
    act(`/api/events/${undoEventId}/undo`, undefined, (o) => `Undone: ${o.number} is ${describe(o.status)}`);
  };
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(hideToast, TOAST_MS);
}

function hideToast() {
  $("toast").hidden = true;
}

function describe(status) {
  return { waiting: "waiting", ready: "ready", picked_up: "picked up", cancelled: "removed", cleared: "cleared" }[status] || status;
}

// --- register ---

function press(key) {
  if (key === "back") digits = digits.slice(0, -1);
  else if (key === "add") return addOrder();
  else if (digits.length < 2) digits += key;
  showDigits();
}

function showDigits() {
  const el = $("readout");
  el.textContent = digits === "" ? "––" : digits;
  el.classList.toggle("empty", digits === "");
}

async function addOrder() {
  if (digits === "") return;
  const number = digits;
  digits = "";
  showDigits();
  await act("/api/orders", { number }, (o) => `Added ${o.number}`);
}

$("keypad").addEventListener("click", (e) => {
  const button = e.target.closest("button");
  if (button && button.dataset.key) press(button.dataset.key);
});

// A hardware keyboard works too (desktop testing, or a tablet with a keyboard).
document.addEventListener("keydown", (e) => {
  if (VIEW === "kitchen" || !$("login").hidden || e.target.tagName === "INPUT") return;
  if (/^[0-9]$/.test(e.key)) press(e.key);
  else if (e.key === "Backspace") press("back");
  else if (e.key === "Enter") { e.preventDefault(); press("add"); }
  else return;
  e.preventDefault();
});

function renderAdded() {
  const list = $("added");
  clear(list);
  const active = [...board.waiting, ...board.ready]
    .sort((a, b) => b.created_at.localeCompare(a.created_at));
  for (const o of active) {
    const li = document.createElement("li");
    li.className = o.status;
    li.append(o.number);
    if (o.status === "waiting") {
      const x = document.createElement("button");
      x.type = "button";
      x.textContent = "✕";
      x.setAttribute("aria-label", `Remove ${o.number}`);
      x.onclick = () => transition(o, "cancelled", `Removed ${o.number}`);
      li.append(x);
    }
    list.append(li);
  }
}

// --- kitchen ---

function transition(order, to, text) {
  return act(`/api/orders/${order.id}/transition`, { to, expect: order.status }, () => text);
}

function orderButton(o, onTap) {
  const b = document.createElement("button");
  b.type = "button";
  b.dataset.id = o.id;
  const num = document.createElement("span");
  num.className = "num";
  num.textContent = o.number;
  const age = document.createElement("span");
  age.className = "age";
  age.dataset.since = o.status === "ready" ? o.ready_at : o.created_at;
  b.append(num, age);
  b.onclick = onTap;
  return b;
}

function renderKitchen() {
  fill($("waiting"), board.waiting.map((o) =>
    orderButton(o, () => transition(o, "ready", `${o.number} is ready`))));
  fill($("ready"), board.ready.map((o) =>
    orderButton(o, () => transition(o, "picked_up", `${o.number} picked up`))));
  renderAges();
}

function renderAges() {
  const now = Date.now() + clockOffset;
  for (const el of document.querySelectorAll(".age")) {
    const minutes = Math.floor((now - Date.parse(el.dataset.since)) / 60000);
    el.textContent = minutes < 1 ? "just now" : `${minutes} min`;
  }
}

async function loadRecent() {
  const r = await api("GET", "/api/recent?limit=15");
  if (!r.ok) return;
  const list = $("recent-list");
  clear(list);
  for (const e of r.data) {
    const li = document.createElement("li");
    const what = document.createElement("span");
    what.className = "what";
    const when = new Date(Date.parse(e.at) - clockOffset)
      .toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    const back = UNDO_MOVES.has(`${e.from_status}>${e.to_status}`) ? "back to " : "";
    what.textContent = `${e.number} ${e.from_status ? back + describe(e.to_status) : "added"} · ${when}`;
    li.append(what);
    if (e.undoable) {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = "Undo";
      b.onclick = () => act(`/api/events/${e.id}/undo`, undefined, (o) => `Undone: ${o.number} is ${describe(o.status)}`);
      li.append(b);
    }
    list.append(li);
  }
}

$("recent").addEventListener("toggle", () => { if ($("recent").open) loadRecent(); });

// --- both ---

function render() {
  if (!board) return;
  if (VIEW !== "kitchen") renderAdded();
  if (VIEW !== "register") renderKitchen();
}

// --- login, menu ---

async function checkLogin() {
  const r = await api("GET", "/api/me");
  if (r.ok && !r.data.staff) showLogin();
}

function showLogin() {
  $("login").hidden = false;
  $("pin").value = "";
  $("pin").focus();
}

$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const r = await api("POST", "/api/login", { pin: $("pin").value });
  $("login-error").hidden = r.ok;
  if (r.ok) {
    $("login").hidden = true;
    refresh(true);
  }
});

$("logout").addEventListener("click", async () => {
  await api("POST", "/api/logout");
  $("menu").open = false;
  showLogin();
});

$("reset").addEventListener("click", async () => {
  $("menu").open = false;
  const n = board ? board.waiting.length + board.ready.length : 0;
  if (!confirm(`End of day: clear all ${n} orders from the board? This can't be undone.`)) return;
  const r = await api("POST", "/api/reset", { confirm: true });
  if (r.ok) toast(`Cleared ${r.data.cleared} orders`);
  refresh(true);
});

// Close the menu when tapping elsewhere.
document.addEventListener("click", (e) => {
  if ($("menu").open && !$("menu").contains(e.target)) $("menu").open = false;
});

// --- start ---

showDigits();
checkLogin();
refresh(true);
setInterval(refresh, POLL_MS);
// Phones pause background tabs; catch up as soon as the page is visible again.
document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(true); });
