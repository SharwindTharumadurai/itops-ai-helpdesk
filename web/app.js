// ITOps web chat: Microsoft sign-in (MSAL, auth code + PKCE) and calls to the ITOps API.
// All server data is rendered with textContent (never innerHTML) so ticket text can't inject markup.
import { PublicClientApplication, InteractionRequiredAuthError } from "https://cdn.jsdelivr.net/npm/@azure/msal-browser@5.23.0/+esm";
import { CONFIG } from "./config.js";

const SCOPES = [`api://${CONFIG.clientId}/access_as_user`];
const TERMINAL = new Set(["SUCCESS", "FAILED", "REJECTED", "NEEDS_HUMAN", "NEEDS_INFO", "BLOCKED"]);
const STATUS_TEXT = {
  DISPATCHED: "Running on device", APPROVED: "Approved", SUCCESS: "Done", FAILED: "Failed",
  PENDING_APPROVAL: "Waiting for IT approval", NEEDS_HUMAN: "With IT technician", NEEDS_INFO: "Needs more info",
  REJECTED: "Rejected", BLOCKED: "Blocked",
};

const $ = (id) => document.getElementById(id);
let msal, account, isAdmin = false, catalog = {};

// ------------------------------------------------------------------ DOM helpers
function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null) node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return node;
}
const badge = (status) => el("span", { class: `badge ${status}`, text: STATUS_TEXT[status] || status });
const actionTitle = (id) => (id ? catalog[id]?.title || id : "No automated action");
const ticketAction = (t) => (t.kind === "generated_script" ? "AI-generated script" : actionTitle(t.action_id));
const when = (iso) => (iso ? new Date(iso).toLocaleString() : "");

function showBanner(message) {
  $("banner").textContent = message;
  $("banner").hidden = !message;
}

// ------------------------------------------------------------------ auth
async function initAuth() {
  const here = window.location.origin + window.location.pathname;
  msal = new PublicClientApplication({
    auth: {
      clientId: CONFIG.clientId,
      authority: `https://login.microsoftonline.com/${CONFIG.tenantId}`,
      redirectUri: here,
      postLogoutRedirectUri: here,
    },
    cache: { cacheLocation: "sessionStorage" }, // tokens vanish when the tab closes
  });
  await msal.initialize();                      // required before any other MSAL call (v3+)
  const result = await msal.handleRedirectPromise();
  account = result?.account || msal.getActiveAccount() || msal.getAllAccounts()[0] || null;
  if (account) msal.setActiveAccount(account);
}

async function getToken() {
  try {
    return (await msal.acquireTokenSilent({ scopes: SCOPES, account })).accessToken;
  } catch (e) {
    if (e instanceof InteractionRequiredAuthError) {
      await msal.acquireTokenRedirect({ scopes: SCOPES, account });
      return new Promise(() => {}); // page is navigating away
    }
    throw e;
  }
}

async function api(method, path, body) {
  const res = await fetch(CONFIG.apiBase + path, {
    method,
    headers: { authorization: `Bearer ${await getToken()}`, "content-type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = {};
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) {
    const reason = data.error || data.message || res.statusText;
    const hint = res.status === 401 ? " (sign-in expired or not assigned to the app)"
      : res.status === 403 ? " (not allowed)" : "";
    throw new Error(`${reason}${hint}`);
  }
  return data;
}

// ------------------------------------------------------------------ chat
function addMessage(who, text, extraClass = "") {
  const msg = el("div", { class: `msg ${who} ${extraClass}` }, el("div", { class: "bubble", text }));
  $("messages").append(msg);
  $("messages").scrollTop = $("messages").scrollHeight;
  return msg;
}

function ticketCard(t) {
  const statusSlot = el("span", {}, badge(t.status));
  const message = el("div", { class: "meta", text: t.status_message || "" });
  const output = el("div");
  const card = el("div", { class: "ticket-card" },
    el("div", { class: "row" }, el("strong", { text: actionTitle(t.proposed_action) }), statusSlot),
    message,
    t.note ? el("div", { class: "meta", text: t.note }) : null,
    el("div", { class: "mono", text: `${t.ticket_id} · ${t.category} · ${t.priority}` }),
    output,
  );
  return { card, statusSlot, message, output };
}

function renderOutput(target, executions) {
  target.replaceChildren();
  for (const ex of executions || []) {
    const text = [ex.stdout, ex.stderr && `--- errors ---\n${ex.stderr}`].filter(Boolean).join("\n");
    if (!text) continue;
    target.append(el("details", {}, el("summary", { text: `Output (exit ${ex.response_code ?? "?"})` }), el("pre", { text })));
  }
}

async function pollTicket(ticketId, ui) {
  for (let i = 0; i < 45; i++) {                 // ~3 minutes
    await new Promise((r) => setTimeout(r, 4000));
    let t;
    try { t = await api("GET", `/tickets/${encodeURIComponent(ticketId)}`); } catch { continue; }
    ui.statusSlot.replaceChildren(badge(t.status));
    if (TERMINAL.has(t.status)) {
      ui.message.textContent = t.status === "SUCCESS" ? "Done - the fix ran on your device." : (t.history?.at(-1)?.note || "");
      renderOutput(ui.output, t.executions);
      return;
    }
  }
  ui.message.textContent = "Still running - check My tickets later.";
}

async function sendChat(event) {
  event.preventDefault();
  const input = $("chat-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  $("btn-send").disabled = true;
  addMessage("user", text);
  const thinking = addMessage("bot", "Looking into it…", "thinking");
  try {
    const t = await api("POST", "/chat", { message: text, device_id: $("device-select").value || undefined });
    thinking.remove();
    const msg = addMessage("bot", t.reply || "Your request has been logged.");
    const ui = ticketCard(t);
    msg.append(ui.card);
    $("messages").scrollTop = $("messages").scrollHeight;
    if (t.status === "DISPATCHED" || t.status === "APPROVED") pollTicket(t.ticket_id, ui);
    if (isAdmin) refreshApprovalCount();
  } catch (e) {
    thinking.remove();
    addMessage("bot", `Sorry, something went wrong: ${e.message}`);
  } finally {
    $("btn-send").disabled = false;
    input.focus();
  }
}

async function loadDevices() {
  try {
    const { devices } = await api("GET", "/devices");
    const label = (d) => `${d.computer_name} (${d.platform}${d.ping_status === "Online" ? "" : ", offline"})` +
      (isAdmin && d.owner ? ` - ${d.owner}` : "");
    for (const d of devices) {
      $("device-select").append(el("option", { value: d.instance_id, text: label(d) }));
      $("draft-device").append(el("option", { value: d.instance_id, text: label(d) }));
    }
  } catch (e) { showBanner(`Could not load devices: ${e.message}`); }
}

async function draftScript(event) {
  event.preventDefault();
  const button = $("btn-draft");
  button.disabled = true;
  $("draft-result").textContent = "Asking the AI and scanning the script… (up to ~25 s)";
  try {
    const r = await api("POST", "/scripts/generate", { device_id: $("draft-device").value, goal: $("draft-goal").value.trim() });
    $("draft-result").replaceChildren(badge(r.status), r.status === "BLOCKED"
      ? " The script tripped the safety scanner - see the Blocked list."
      : " Ready for review. A different IT admin must approve it.");
    $("draft-goal").value = "";
    $("queue-status").value = r.status;          // show the new ticket
    loadQueue();
  } catch (e) {
    $("draft-result").textContent = e.message;
  } finally {
    button.disabled = false;
  }
}

// ------------------------------------------------------------------ my tickets
async function loadTickets() {
  const list = $("ticket-list");
  list.replaceChildren(el("div", { class: "meta", text: "Loading…" }));
  try {
    const { tickets } = await api("GET", "/tickets");
    list.replaceChildren();
    if (!tickets.length) list.append(el("div", { class: "empty-state", text: "No tickets yet." }));
    for (const t of tickets) {
      const item = el("button", { class: "list-item", "data-ticket": t.ticket_id, onclick: () => {
        list.querySelectorAll(".selected").forEach((n) => n.classList.remove("selected"));
        item.classList.add("selected");
        showTicket(t.ticket_id);
      } },
        el("div", { class: "row" }, badge(t.status), el("span", { class: "mono", text: when(t.created_at) })),
        el("div", { class: "title", text: t.summary || t.request_text }),
        el("div", { class: "meta", text: ticketAction(t) }));
      list.append(item);
    }
  } catch (e) { list.replaceChildren(el("div", { class: "banner", text: e.message })); }
}

let detailTimer = null;

async function showTicket(id, quiet = false) {
  clearTimeout(detailTimer);
  detailTimer = null;
  const detail = $("ticket-detail");
  detail.className = "detail";
  if (!quiet) detail.replaceChildren(el("div", { class: "meta", text: "Loading…" }));
  try {
    const t = await api("GET", `/tickets/${encodeURIComponent(id)}`);
    // While a fix is still running, keep this view (and the list badge) up to date on its own.
    // The SSM agent can take a minute or so to pick up a command.
    if (!TERMINAL.has(t.status) && t.status !== "PENDING_APPROVAL") {
      detailTimer = setTimeout(() => { if (!$("view-tickets").hidden) showTicket(id, true); }, 5000);
    }
    const listBadge = document.querySelector(`[data-ticket="${CSS.escape(id)}"] .badge`);
    if (listBadge) listBadge.replaceWith(badge(t.status));
    const output = el("div");
    renderOutput(output, t.executions);
    detail.replaceChildren(
      el("div", { class: "row" }, el("h2", { text: t.summary || "Ticket" }), badge(t.status),
        detailTimer ? el("span", { class: "meta", text: "updating automatically…" }) : null),
      el("dl", { class: "kv" },
        ...[["Ticket", t.ticket_id], ["Created", when(t.created_at)], ["Request", t.request_text],
            ["Category", `${t.category} · ${t.priority}`], ["Action", ticketAction(t)],
            ["Device", t.target_instance], ["Account", t.target_user], ["AI", t.ai_provider && (t.kind === "generated_script" ? `script written by ${t.ai_provider}`
              : `${t.ai_provider}${t.ai_confidence != null ? ` (confidence ${t.ai_confidence})` : ""}`)]]
          .filter(([, v]) => v)
          .flatMap(([k, v]) => [el("dt", { text: k }), el("dd", { text: String(v) })])),
      el("h3", { text: "History" }),
      el("ol", { class: "history" }, (t.history || []).map((h) =>
        el("li", { text: `${when(h.at)} · ${STATUS_TEXT[h.to] || h.to} · ${h.by}${h.note ? ` · ${h.note}` : ""}` }))),
      output,
    );
  } catch (e) { detail.replaceChildren(el("div", { class: "banner", text: e.message })); }
}

// ------------------------------------------------------------------ admin queue
async function refreshApprovalCount() {
  try {
    const { tickets } = await api("GET", "/tickets?status=PENDING_APPROVAL");
    $("approval-count").textContent = tickets.length;
    $("approval-count").hidden = tickets.length === 0;
  } catch { /* badge is best-effort */ }
}

function showSecret(value) {
  $("secret-value").textContent = value;
  $("secret-dialog").showModal();
}

async function loadQueue() {
  const queue = $("queue");
  const status = $("queue-status").value;
  queue.replaceChildren(el("div", { class: "meta", text: "Loading…" }));
  try {
    const { tickets } = await api("GET", `/tickets?status=${status}`);
    queue.replaceChildren();
    if (!tickets.length) queue.append(el("div", { class: "empty-state", text: "Nothing here." }));
    for (const t of tickets) queue.append(queueCard(t));
    if (status === "PENDING_APPROVAL") {
      $("approval-count").textContent = tickets.length;
      $("approval-count").hidden = tickets.length === 0;
    }
  } catch (e) { queue.replaceChildren(el("div", { class: "banner", text: e.message })); }
}

function queueCard(t) {
  const reason = el("input", { type: "text", placeholder: "Reason (for rejecting)", maxlength: "300" });
  const result = el("div", { class: "meta" });
  const buttons = [];
  const params = Object.entries(t.parameters || {}).map(([k, v]) => `${k}=${v}`).join(", ");

  if (t.status === "PENDING_APPROVAL") {
    buttons.push(el("button", { class: "primary", text: "Approve", onclick: async (ev) => {
      const label = t.kind === "generated_script" ? "this AI-generated script" : actionTitle(t.action_id);
      if (!confirm(`Approve and run ${label}?\n\nRequested by ${t.requester}`)) return;
      ev.target.disabled = true;
      try {
        const body = t.kind === "generated_script" ? { sha256: t.script_sha256 } : {};
        const r = await api("POST", `/tickets/${encodeURIComponent(t.ticket_id)}/approve`, body);
        result.replaceChildren(badge(r.status), ` ${r.result?.message || r.error || ""}`);
        if (r.temporary_password) showSecret(r.temporary_password);
        refreshApprovalCount();
      } catch (e) { result.textContent = e.message; ev.target.disabled = false; }
    } }));
  }
  if (["PENDING_APPROVAL", "NEEDS_HUMAN", "NEEDS_INFO", "BLOCKED"].includes(t.status)) {
    buttons.push(el("button", { class: "danger", text: "Reject / close", onclick: async (ev) => {
      ev.target.disabled = true;
      try {
        const r = await api("POST", `/tickets/${encodeURIComponent(t.ticket_id)}/reject`, { reason: reason.value });
        result.replaceChildren(badge(r.status));
        refreshApprovalCount();
      } catch (e) { result.textContent = e.message; ev.target.disabled = false; }
    } }), reason);
  }

  return el("div", { class: "queue-card" },
    el("div", { class: "row" }, el("strong", { text: t.kind === "generated_script" ? "AI-generated script" : actionTitle(t.action_id) }),
      badge(t.status), el("span", { class: "mono", text: `${t.ticket_id} · ${when(t.created_at)}` })),
    el("div", { class: "meta", text: `From ${t.requester} · ${t.category} · ${t.priority}` +
      (t.ai_provider ? ` · ${t.kind === "generated_script" ? "written" : "triaged"} by ${t.ai_provider}` : "") }),
    el("div", { class: "request", text: t.request_text }),
    params ? el("div", { class: "meta", text: `Parameters: ${params}` }) : null,
    t.target_instance ? el("div", { class: "meta", text: `Device: ${t.target_instance}` }) : null,
    t.target_user ? el("div", { class: "meta", text: `Account: ${t.target_user}` }) : null,
    t.note ? el("div", { class: "meta", text: `Note: ${t.note}` }) : null,
    t.script ? el("details", { open: true }, el("summary", { text: `Script (sha256 ${t.script_sha256?.slice(0, 12)}…) - read it fully before approving` }), el("pre", { text: t.script })) : null,
    t.violations ? el("ul", { class: "violations" }, t.violations.map((v) => el("li", { text: v }))) : null,
    el("div", { class: "actions" }, buttons, result),
  );
}

// ------------------------------------------------------------------ wiring
function switchTab(name) {
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  for (const v of ["chat", "tickets", "approvals"]) $(`view-${v}`).hidden = v !== name;
  if (name === "tickets") loadTickets();
  if (name === "approvals") loadQueue();
}

async function main() {
  try {
    await initAuth();
  } catch (e) {
    $("signed-out").hidden = false;
    showBanner(`Sign-in setup failed: ${e.message}`);
    $("signed-out").prepend($("banner"));
    return;
  }
  $("btn-login").addEventListener("click", () => msal.loginRedirect({ scopes: SCOPES, prompt: "select_account" }));
  if (!account) { $("signed-out").hidden = false; return; }

  const roles = account.idTokenClaims?.roles || [];
  isAdmin = roles.includes(CONFIG.adminRole);   // UI only - the API enforces roles itself
  $("user-name").textContent = account.name || account.username;
  $("user-role").hidden = !isAdmin;
  $("tab-approvals").hidden = !isAdmin;
  $("signed-in").hidden = false;

  $("btn-logout").addEventListener("click", () => msal.logoutRedirect({ account }));
  document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));
  $("chat-form").addEventListener("submit", sendChat);
  $("chat-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("chat-form").requestSubmit(); }
  });
  $("btn-refresh-tickets").addEventListener("click", loadTickets);
  $("btn-refresh-queue").addEventListener("click", loadQueue);
  $("queue-status").addEventListener("change", loadQueue);
  $("draft-form").addEventListener("submit", draftScript);
  $("btn-copy-secret").addEventListener("click", () => navigator.clipboard.writeText($("secret-value").textContent));
  $("btn-close-secret").addEventListener("click", () => {
    $("secret-value").textContent = "";              // don't leave it in the DOM
    $("secret-dialog").close();
  });

  api("GET", "/catalog").then(({ actions }) => { catalog = Object.fromEntries(actions.map((a) => [a.id, a])); }).catch(() => {});
  loadDevices();
  if (isAdmin) refreshApprovalCount();
  $("chat-input").focus();
}

main();
