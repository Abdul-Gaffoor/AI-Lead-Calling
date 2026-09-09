/* Swaraj Solar operations console.
   A dependency-free client over the platform API. */

const state = { token: localStorage.getItem("swaraj_token"), user: null, page: "dashboard" };

const PAGES = [
  { id: "dashboard", label: "Dashboard" },
  { id: "leads", label: "Leads" },
  { id: "campaigns", label: "Campaigns" },
  { id: "myleads", label: "My Leads" },
  { id: "surveys", label: "Site Surveys" },
  { id: "calculator", label: "Solar Calculator" },
];

/* ---------- helpers ---------- */

const el = (id) => document.getElementById(id);
const esc = (v) =>
  String(v ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (n) => (n == null ? "—" : "₹" + Number(n).toLocaleString("en-IN"));
const num = (n) => (n == null ? "—" : Number(n).toLocaleString("en-IN"));

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (state.token) headers.Authorization = "Bearer " + state.token;
  if (options.body && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.body);
  }
  const response = await fetch(path, { ...options, headers });
  if (response.status === 401) { signOut(); throw new Error("Session expired — please sign in again"); }
  if (!response.ok) {
    let detail = response.statusText;
    try { const body = await response.json(); detail = body.detail || detail; } catch (_) {}
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return response.status === 204 ? null : response.json();
}

function card(title, inner) {
  return `<section class="card"><h2>${esc(title)}</h2>${inner}</section>`;
}

function table(columns, rows, renderRow) {
  if (!rows.length) return `<div class="empty">Nothing here yet.</div>`;
  return `<div class="table-wrap"><table><thead><tr>${columns
    .map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>${rows
    .map(renderRow).join("")}</tbody></table></div>`;
}

/* ---------- auth ---------- */

el("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const error = el("login-error");
  error.hidden = true;
  const body = new URLSearchParams({ username: el("email").value, password: el("password").value });
  try {
    const response = await fetch("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
    });
    if (!response.ok) {
      const detail = response.status === 423
        ? "Account locked after repeated failed attempts. Try again shortly."
        : "Incorrect email or password.";
      throw new Error(detail);
    }
    const data = await response.json();
    state.token = data.access_token;
    localStorage.setItem("swaraj_token", state.token);
    await start();
  } catch (exc) {
    error.textContent = exc.message;
    error.hidden = false;
  }
});

function signOut() {
  state.token = null;
  state.user = null;
  localStorage.removeItem("swaraj_token");
  el("app").hidden = true;
  el("login").hidden = false;
}
el("logout").addEventListener("click", signOut);

async function start() {
  try {
    state.user = await api("/auth/me");
  } catch (_) { signOut(); return; }
  el("login").hidden = true;
  el("app").hidden = false;
  el("whoami").textContent = `${state.user.full_name} · ${state.user.role.replace(/_/g, " ")}`;
  el("nav").innerHTML = PAGES.map(
    (p) => `<button data-page="${p.id}">${esc(p.label)}</button>`).join("");
  el("nav").querySelectorAll("button").forEach((button) =>
    button.addEventListener("click", () => render(button.dataset.page)));
  render(state.page);
}

/* ---------- router ---------- */

async function render(page) {
  state.page = page;
  el("nav").querySelectorAll("button").forEach((b) =>
    b.setAttribute("aria-current", String(b.dataset.page === page)));
  const view = el("view");
  view.innerHTML = `<p class="muted">Loading…</p>`;
  try {
    await VIEWS[page](view);
  } catch (exc) {
    view.innerHTML = `<div class="card"><p class="error">${esc(exc.message)}</p></div>`;
  }
}

const VIEWS = {};

/* ---------- dashboard ---------- */

VIEWS.dashboard = async (view) => {
  const [today, funnel, calls] = await Promise.all([
    api("/reports/dashboard"), api("/reports/funnel"), api("/reports/calls"),
  ]);

  const stats = [
    ["Uploaded", today.uploaded], ["Eligible", today.eligible],
    ["Attempted", today.attempted], ["Connected", today.connected],
    ["Qualified", today.qualified],
    ["Hot", today.hot, "hot"], ["Warm", today.warm, "warm"], ["Cold", today.cold, "cold"],
    ["Site surveys", today.site_surveys], ["Human transfers", today.human_transfers],
    ["Callbacks", today.callbacks], ["Opt-outs", today.opt_outs],
  ].map(([label, value, cls]) =>
    `<div class="stat ${cls || ""}"><div class="label">${esc(label)}</div>
     <div class="value">${num(value)}</div></div>`).join("");

  const stages = Object.entries(funnel.stages);
  const peak = Math.max(1, ...stages.map(([, v]) => v));
  const funnelRows = stages.map(([name, value]) =>
    `<div class="funnel-row"><span>${esc(name.replace(/_/g, " "))}</span>
     <div class="funnel-track"><div class="funnel-bar" style="width:${(value / peak) * 100}%"></div></div>
     <strong style="text-align:right">${num(value)}</strong></div>`).join("");

  const rates = Object.entries(funnel.rates).map(([name, value]) =>
    `<tr><td>${esc(name.replace(/_/g, " "))}</td><td class="num">${value}%</td></tr>`).join("");

  view.innerHTML = `
    <div class="page-head"><h1>Today — ${esc(today.date)}</h1>
      <p class="muted">Live figures from the calling platform.</p></div>
    <div class="grid stats" style="margin-bottom:18px">${stats}</div>
    <div class="grid cols-2">
      ${card("Funnel", funnelRows || `<div class="empty">No funnel data yet.</div>`)}
      ${card("Conversion rates", `<table><tbody>${rates}</tbody></table>`)}
      ${card("Calls", `<table><tbody>
        <tr><td>Total calls</td><td class="num">${num(calls.total_calls)}</td></tr>
        <tr><td>Total minutes</td><td class="num">${num(calls.total_minutes)}</td></tr>
        <tr><td>Average call</td><td class="num">${num(calls.average_call_seconds)}s</td></tr>
        <tr><td>In flight</td><td class="num">${num(calls.in_flight)}</td></tr>
      </tbody></table>`)}
      ${card("By service", Object.keys(today.by_service).length
        ? `<table><tbody>${Object.entries(today.by_service).map(([service, count]) =>
            `<tr><td>${esc(service.replace(/_/g, " "))}</td><td class="num">${num(count)}</td></tr>`
          ).join("")}</tbody></table>`
        : `<div class="empty">No leads today.</div>`)}
    </div>`;
};

/* ---------- leads ---------- */

VIEWS.leads = async (view) => {
  const [leads, uploads] = await Promise.all([
    api("/leads?limit=50"), api("/leads/uploads?limit=10").catch(() => []),
  ]);

  view.innerHTML = `
    <div class="page-head"><h1>Leads</h1>
      <p class="muted">Upload the daily file — validation, duplicate and do-not-call checks run automatically.</p></div>
    <div class="grid cols-2" style="margin-bottom:18px">
      ${card("Upload daily leads", `
        <form class="stack" id="upload-form">
          <label>CSV or Excel file<input type="file" id="file" accept=".csv,.xlsx" required></label>
          <div class="row">
            <button class="primary" type="submit">Upload &amp; validate</button>
            <a class="ghost" href="/leads/template" id="template-link" style="text-decoration:none;padding:7px 12px">Download template</a>
          </div>
          <p id="upload-msg" hidden></p>
        </form>`)}
      ${card("Recent uploads", table(
        ["File", "Rows", "Valid", "Rejected"], uploads,
        (u) => `<tr><td>${esc(u.filename)}</td><td class="num">${num(u.total_rows)}</td>
          <td class="num">${num(u.valid_count)}</td>
          <td class="num">${num(u.total_rows - u.valid_count)}</td></tr>`))}
    </div>
    ${card("Latest leads", table(
      ["Ref", "Customer", "Phone", "City", "Service", "Bill", "Status"], leads,
      (l) => `<tr><td>${esc(l.lead_ref || "—")}</td><td>${esc(l.customer_name)}</td>
        <td>${esc(l.customer_phone)}</td><td>${esc(l.city || "—")}</td>
        <td>${esc((l.interested_service || "—").replace(/_/g, " "))}</td>
        <td class="num">${money(l.monthly_bill)}</td>
        <td><span class="pill">${esc(l.status)}</span></td></tr>`))}`;

  // The template endpoint needs the auth header, so fetch and save it.
  el("template-link").addEventListener("click", async (event) => {
    event.preventDefault();
    const response = await fetch("/leads/template", { headers: { Authorization: "Bearer " + state.token } });
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "swaraj_leads_template.csv";
    anchor.click();
    URL.revokeObjectURL(url);
  });

  el("upload-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = el("upload-msg");
    const file = el("file").files[0];
    if (!file) return;
    message.hidden = false;
    message.className = "muted";
    message.textContent = "Validating…";
    try {
      const form = new FormData();
      form.append("file", file);
      const result = await api("/leads/uploads", { method: "POST", body: form });
      if (result.status === "FAILED") {
        message.className = "error";
        message.textContent = result.error;
      } else {
        message.className = "success";
        message.textContent =
          `${result.valid_count} of ${result.total_rows} ready. ` +
          `Duplicate ${result.duplicate_count}, invalid phone ${result.invalid_phone_count}, ` +
          `opted out ${result.opted_out_count}, consent ${result.consent_issue_count}.`;
        setTimeout(() => render("leads"), 1400);
      }
    } catch (exc) {
      message.className = "error";
      message.textContent = exc.message;
    }
  });
};

/* ---------- campaigns ---------- */

VIEWS.campaigns = async (view) => {
  const [campaigns, uploads] = await Promise.all([
    api("/campaigns?limit=25"), api("/leads/uploads?limit=10").catch(() => []),
  ]);

  view.innerHTML = `
    <div class="page-head"><h1>Campaigns</h1>
      <p class="muted">The dispatcher calls only inside the calling window and never exceeds the concurrency you set.</p></div>
    ${card("New campaign", `
      <form class="stack" id="campaign-form">
        <div class="row">
          <label>Name<input id="c-name" placeholder="September Residential Leads" required></label>
          <label>Lead list<select id="c-upload">${
            uploads.map((u) => `<option value="${u.id}">${esc(u.filename)} (${u.valid_count} leads)</option>`).join("")
            || `<option value="">No uploads yet</option>`}</select></label>
        </div>
        <div class="row">
          <label>Calling from<input type="time" id="c-start" value="10:00"></label>
          <label>Calling until<input type="time" id="c-end" value="18:00"></label>
          <label>Concurrent calls<input type="number" id="c-conc" value="5" min="1" max="200"></label>
          <label>Max attempts<input type="number" id="c-attempts" value="3" min="1" max="10"></label>
          <button class="primary" type="submit">Create</button>
        </div>
        <p id="campaign-msg" hidden></p>
      </form>`)}
    <div style="height:18px"></div>
    ${card("All campaigns", table(
      ["Name", "Status", "Window", "Concurrency", "Attempts", "Actions"], campaigns,
      (c) => `<tr><td>${esc(c.name)}</td>
        <td><span class="pill ${esc(c.status)}">${esc(c.status)}</span></td>
        <td>${esc(c.window_start)}–${esc(c.window_end)}</td>
        <td class="num">${c.concurrency}</td><td class="num">${c.max_attempts}</td>
        <td><div class="row" style="gap:6px">
          ${c.status === "DRAFT" || c.status === "PAUSED"
            ? `<button class="ghost" data-act="start" data-id="${c.id}">Start</button>` : ""}
          ${c.status === "RUNNING"
            ? `<button class="ghost" data-act="pause" data-id="${c.id}">Pause</button>
               <button class="ghost" data-act="dispatch" data-id="${c.id}">Dispatch now</button>` : ""}
          ${c.status !== "STOPPED" && c.status !== "COMPLETED"
            ? `<button class="ghost" data-act="stop" data-id="${c.id}">Stop</button>` : ""}
        </div></td></tr>`))}`;

  el("campaign-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = el("campaign-msg");
    message.hidden = false;
    message.className = "muted";
    message.textContent = "Creating…";
    try {
      const campaign = await api("/campaigns", {
        method: "POST",
        body: {
          name: el("c-name").value,
          window_start: el("c-start").value + ":00",
          window_end: el("c-end").value + ":00",
          concurrency: Number(el("c-conc").value),
          max_attempts: Number(el("c-attempts").value),
        },
      });
      const uploadId = el("c-upload").value;
      let queued = "";
      if (uploadId) {
        const result = await api(`/campaigns/${campaign.id}/leads`, {
          method: "POST", body: { upload_id: Number(uploadId) },
        });
        queued = ` ${result.added} leads queued (${result.suppressed} suppressed).`;
      }
      message.className = "success";
      message.textContent = `Campaign created.${queued}`;
      setTimeout(() => render("campaigns"), 1400);
    } catch (exc) {
      message.className = "error";
      message.textContent = exc.message;
    }
  });

  view.querySelectorAll("button[data-act]").forEach((button) =>
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        await api(`/campaigns/${button.dataset.id}/${button.dataset.act}`, { method: "POST" });
        render("campaigns");
      } catch (exc) {
        alert(exc.message);
        button.disabled = false;
      }
    }));
};

/* ---------- my leads ---------- */

VIEWS.myleads = async (view) => {
  const leads = await api("/sales/my-leads");
  view.innerHTML = `
    <div class="page-head"><h1>My priority leads</h1>
      <p class="muted">Assigned to you, hottest first — not a raw list of phone numbers.</p></div>
    ${card("Queue", table(
      ["Customer", "Phone", "Service", "Bill", "Score", "Stage"], leads,
      (l) => `<tr><td>${esc(l.customer_name)}</td><td>${esc(l.customer_phone)}</td>
        <td>${esc((l.service || "—").replace(/_/g, " "))}</td>
        <td class="num">${money(l.monthly_bill)}</td>
        <td>${l.classification ? `<span class="pill ${esc(l.classification)}">${esc(l.classification)} ${l.score ?? ""}</span>` : "—"}</td>
        <td>${esc(l.stage)}</td></tr>`))}`;
};

/* ---------- surveys ---------- */

VIEWS.surveys = async (view) => {
  const surveys = await api("/surveys?limit=50");
  view.innerHTML = `
    <div class="page-head"><h1>Site surveys</h1>
      <p class="muted">Booked automatically when a customer asks for one during a call.</p></div>
    ${card("Surveys", table(
      ["#", "Customer", "Service", "Address", "Preferred", "Status"], surveys,
      (s) => `<tr><td>${s.id}</td><td>${s.customer_id}</td>
        <td>${esc((s.service || "—").replace(/_/g, " "))}</td>
        <td>${esc(s.address || "—")}</td>
        <td>${esc(s.preferred_date || "—")} ${esc(s.preferred_time || "")}</td>
        <td><span class="pill">${esc(s.status)}</span></td></tr>`))}`;
};

/* ---------- calculator ---------- */

VIEWS.calculator = async (view) => {
  view.innerHTML = `
    <div class="page-head"><h1>Solar calculator</h1>
      <p class="muted">The single approved engine used by the website, the AI agent and sales.</p></div>
    <div class="notice">Indicative figures only. Constants must be signed off by Swaraj engineering, and
      subsidy values confirmed against the current scheme, before customer-facing use.</div>
    <div class="grid cols-2">
      ${card("Inputs", `
        <form class="stack" id="calc-form">
          <label>Monthly bill (₹)<input type="number" id="k-bill" value="7500" min="0"></label>
          <label>Monthly units (optional, more accurate)<input type="number" id="k-units" min="0"></label>
          <label>Roof area, sq ft (optional)<input type="number" id="k-roof" min="0"></label>
          <label>System type<select id="k-type">
            <option value="ON_GRID">On-grid</option><option value="HYBRID">Hybrid</option>
            <option value="OFF_GRID">Off-grid</option></select></label>
          <label style="display:flex;align-items:center;gap:8px;font-weight:600">
            <input type="checkbox" id="k-subsidy" checked style="width:auto"> Residential subsidy eligible</label>
          <button class="primary" type="submit">Calculate</button>
        </form>`)}
      <section class="card"><h2>Estimate</h2><div id="calc-out"><div class="empty">Enter details and calculate.</div></div></section>
    </div>`;

  el("calc-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const output = el("calc-out");
    output.innerHTML = `<p class="muted">Calculating…</p>`;
    try {
      const body = { system_type: el("k-type").value, subsidy_eligible: el("k-subsidy").checked };
      if (el("k-units").value) body.monthly_units = Number(el("k-units").value);
      else body.monthly_bill = Number(el("k-bill").value);
      if (el("k-roof").value) body.roof_area_sqft = Number(el("k-roof").value);

      const r = await api("/solar/estimate", { method: "POST", body });
      output.innerHTML = `
        <table><tbody>
          <tr><td>System size</td><td class="num"><strong>${r.system_size_kw} kW</strong></td></tr>
          <tr><td>Roof area needed</td><td class="num">${num(r.roof_area_required_sqft)} sq ft</td></tr>
          <tr><td>Annual generation</td><td class="num">${num(r.annual_generation_kwh)} kWh</td></tr>
          <tr><td>Monthly savings</td><td class="num">${money(r.monthly_savings)}</td></tr>
          <tr><td>Project cost</td><td class="num">${money(r.project_cost_low)} – ${money(r.project_cost_high)}</td></tr>
          <tr><td>Subsidy</td><td class="num">${money(r.subsidy_amount)}</td></tr>
          <tr><td>Net investment</td><td class="num">${money(r.net_investment_low)} – ${money(r.net_investment_high)}</td></tr>
          <tr><td>Payback</td><td class="num"><strong>${r.payback_years ?? "—"} years</strong></td></tr>
        </tbody></table>
        <p class="muted" style="margin-top:12px">${r.notes.map(esc).join("<br>")}</p>`;
    } catch (exc) {
      output.innerHTML = `<p class="error">${esc(exc.message)}</p>`;
    }
  });
};

/* ---------- boot ---------- */

if (state.token) start(); else el("login").hidden = false;
