/* Swaraj Solar operations console — dependency-free client over the platform API. */

const state = {
  token: localStorage.getItem("swaraj_token"),
  user: null,
  page: localStorage.getItem("swaraj_page") || "dashboard",
};

const PAGES = [
  { id: "dashboard",  label: "Dashboard",   glyph: "◧", sub: "Today's calling activity and funnel" },
  { id: "leads",      label: "Leads",       glyph: "☰", sub: "Daily uploads, validation and lead records" },
  { id: "campaigns",  label: "Campaigns",   glyph: "◇", sub: "Calling windows, concurrency and retries" },
  { id: "myleads",    label: "My Leads",    glyph: "★", sub: "Your assigned queue, highest value first" },
  { id: "surveys",    label: "Site Surveys",glyph: "⊙", sub: "Survey requests and engineer visits" },
  { id: "calculator", label: "Calculator",  glyph: "∑", sub: "Approved solar sizing and ROI engine" },
];

/* ---------- utilities ---------- */

const el = (id) => document.getElementById(id);

/* Toggle a screen from script: an author `display` rule would otherwise
   outrank the browser's default [hidden] handling. */
function setVisible(node, visible) {
  node.hidden = !visible;
  node.style.display = visible ? "" : "none";
}

const esc = (v) =>
  String(v ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const num = (n) => (n == null ? "—" : Number(n).toLocaleString("en-IN"));
const money = (n) => (n == null ? "—" : "₹" + Number(n).toLocaleString("en-IN", { maximumFractionDigits: 0 }));
const ACRONYMS = new Set(["PM", "AI", "ROI", "EMI", "HT", "LT", "DG", "KW", "DNC", "CSV", "ID"]);
const title = (s) => String(s ?? "").replace(/_/g, " ").toLowerCase()
  .replace(/\b[a-z]+\b/g, (word) =>
    ACRONYMS.has(word.toUpperCase()) ? word.toUpperCase() : word[0].toUpperCase() + word.slice(1));

function toast(message, kind = "") {
  const node = document.createElement("div");
  node.className = "toast " + kind;
  node.textContent = message;
  el("toasts").append(node);
  setTimeout(() => node.remove(), 4200);
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (state.token) headers.Authorization = "Bearer " + state.token;
  if (options.body && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.body);
  }
  const response = await fetch(path, { ...options, headers });
  if (response.status === 401) {
    signOut();
    throw new Error("Your session expired. Please sign in again.");
  }
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = Array.isArray(body.detail)
        ? body.detail.map((d) => d.msg || d).join(", ")
        : body.detail || detail;
    } catch (_) { /* non-JSON error body */ }
    throw new Error(String(detail));
  }
  return response.status === 204 ? null : response.json();
}

/* ---------- building blocks ---------- */

function card(heading, body, hint = "") {
  return `<section class="card">
    <div class="card-head"><h2>${esc(heading)}</h2>${hint ? `<span class="hint">${esc(hint)}</span>` : ""}</div>
    <div class="card-body">${body}</div></section>`;
}

function plainCard(heading, body, hint = "") {
  return `<section class="card">
    <div class="card-head"><h2>${esc(heading)}</h2>${hint ? `<span class="hint">${esc(hint)}</span>` : ""}</div>
    ${body}</section>`;
}

function stat(label, value, { dot = "", foot = "" } = {}) {
  return `<div class="stat">
    <div class="label">${dot ? `<span class="dot ${dot}"></span>` : ""}${esc(label)}</div>
    <div class="value">${num(value)}</div>
    ${foot ? `<div class="foot">${esc(foot)}</div>` : ""}
  </div>`;
}

function emptyState(headline, hint) {
  return `<div class="empty"><strong>${esc(headline)}</strong>${esc(hint)}</div>`;
}

function table(columns, rows, renderRow, empty) {
  if (!rows.length) return empty;
  const head = columns.map((c) =>
    typeof c === "string" ? `<th>${esc(c)}</th>` : `<th class="num">${esc(c.label)}</th>`).join("");
  return `<div class="table-wrap"><table><thead><tr>${head}</tr></thead>
    <tbody>${rows.map(renderRow).join("")}</tbody></table></div>`;
}

/* Horizontal bars. One measure, so a single series colour; length carries
   magnitude and every bar is directly labelled. */
function bars(entries) {
  const peak = Math.max(1, ...entries.map(([, v]) => v));
  return `<div class="bars">${entries.map(([name, value]) => `
    <div class="bar-row" title="${esc(name)}: ${num(value)}">
      <span class="name">${esc(name)}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${Math.max(0.6, (value / peak) * 100)}%"></div></div>
      <span class="val">${num(value)}</span>
    </div>`).join("")}</div>`;
}

const TIER_CLASS = { HOT: "hot", WARM: "warm", COLD: "cold" };
function tierBadge(classification, score) {
  if (!classification) return "—";
  const cls = TIER_CLASS[classification] || "cold";
  return `<span class="badge tier"><span class="dot ${cls}"></span>${esc(classification)}${
    score != null ? " " + score : ""}</span>`;
}

/* ---------- auth ---------- */

el("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const error = el("login-error");
  const submit = el("login-submit");
  error.hidden = true;
  submit.disabled = true;
  submit.textContent = "Signing in…";
  try {
    const response = await fetch("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ username: el("email").value, password: el("password").value }),
    });
    if (!response.ok) {
      throw new Error(response.status === 423
        ? "This account is locked after repeated failed attempts. Try again in a few minutes."
        : "That email and password combination was not recognised.");
    }
    const data = await response.json();
    state.token = data.access_token;
    localStorage.setItem("swaraj_token", state.token);
    await start();
  } catch (exc) {
    error.textContent = exc.message;
    error.className = "form-msg err";
    error.hidden = false;
  } finally {
    submit.disabled = false;
    submit.textContent = "Sign in";
  }
});

function signOut() {
  state.token = null;
  state.user = null;
  localStorage.removeItem("swaraj_token");
  setVisible(el("app"), false);
  setVisible(el("login"), true);
}
el("logout").addEventListener("click", signOut);

el("nav-toggle").addEventListener("click", () => el("app").classList.toggle("nav-open"));

async function start() {
  try {
    state.user = await api("/auth/me");
  } catch (_) { signOut(); return; }

  setVisible(el("login"), false);
  setVisible(el("app"), true);

  el("user-name").textContent = state.user.full_name;
  el("user-role").textContent = title(state.user.role);
  el("avatar").textContent = state.user.full_name.split(/\s+/).map((w) => w[0])
    .slice(0, 2).join("").toUpperCase();

  el("nav").innerHTML = PAGES.map((p) =>
    `<button data-page="${p.id}"><span class="nav-glyph">${p.glyph}</span>${esc(p.label)}</button>`).join("");
  el("nav").querySelectorAll("button").forEach((button) =>
    button.addEventListener("click", () => render(button.dataset.page)));

  render(PAGES.some((p) => p.id === state.page) ? state.page : "dashboard");
}

/* ---------- router ---------- */

async function render(page) {
  state.page = page;
  localStorage.setItem("swaraj_page", page);
  el("app").classList.remove("nav-open");

  const meta = PAGES.find((p) => p.id === page);
  el("page-title").textContent = meta.label;
  el("page-sub").textContent = meta.sub;
  el("page-actions").innerHTML = "";
  el("nav").querySelectorAll("button").forEach((b) =>
    b.setAttribute("aria-current", String(b.dataset.page === page)));

  const view = el("view");
  view.innerHTML = `<div class="card"><div class="card-body skeleton">
    <i style="width:38%"></i><i style="width:88%"></i><i style="width:72%"></i><i style="width:80%"></i>
  </div></div>`;

  try {
    await VIEWS[page](view);
  } catch (exc) {
    view.innerHTML = card("Something went wrong",
      `<p class="form-msg err">${esc(exc.message)}</p>
       <p style="color:var(--ink-muted);font-size:12.5px;margin:8px 0 0">
         If this persists, check that the service is running.</p>`);
  }
}

const VIEWS = {};

/* ---------- dashboard ---------- */

VIEWS.dashboard = async (view) => {
  const [today, funnel, calls] = await Promise.all([
    api("/reports/dashboard"), api("/reports/funnel"), api("/reports/calls"),
  ]);

  el("page-actions").innerHTML = `<button class="btn btn-quiet btn-sm" id="refresh">Refresh</button>`;

  const connectRate = today.attempted ? Math.round((today.connected / today.attempted) * 100) : 0;

  const stageLabels = {
    lead: "Leads", called: "Called", connected: "Connected", qualified: "Qualified",
    site_survey: "Site survey", survey_completed: "Survey done",
    quotation: "Quotation", order: "Order",
  };
  const stageEntries = Object.entries(funnel.stages)
    .map(([key, value]) => [stageLabels[key] || title(key), value]);
  const serviceEntries = Object.entries(today.by_service)
    .map(([key, value]) => [title(key), value])
    .sort((a, b) => b[1] - a[1]);

  view.innerHTML = `
    <section class="card section">
      <div class="card-body">
        <div class="hero">
          <span class="figure">${num(today.qualified)}</span>
          <span class="caption">leads qualified today${
            today.connected ? ` · from ${num(today.connected)} connected calls` : ""}</span>
        </div>
      </div>
    </section>

    <div class="stats fixed-4 section">
      ${stat("Uploaded", today.uploaded, { foot: "leads received" })}
      ${stat("Eligible", today.eligible, { foot: "passed validation" })}
      ${stat("Attempted", today.attempted, { foot: "calls placed" })}
      ${stat("Connected", today.connected, { foot: `${connectRate}% of attempts` })}
      ${stat("Hot", today.hot, { dot: "hot", foot: "ready to buy" })}
      ${stat("Warm", today.warm, { dot: "warm", foot: "worth pursuing" })}
      ${stat("Cold", today.cold, { dot: "cold", foot: "long term" })}
      ${stat("Surveys", today.site_surveys, { foot: "booked today" })}
    </div>

    <div class="grid cols-2 section">
      ${card("Sales funnel", stageEntries.some(([, v]) => v)
        ? bars(stageEntries)
        : emptyState("No funnel activity yet", "Upload leads and start a campaign to see the funnel fill."),
        "leads reaching each stage")}

      ${plainCard("Conversion rates", `<div class="table-wrap"><table><tbody>
        ${Object.entries(funnel.rates).map(([name, value]) =>
          `<tr><td>${esc(title(name))}</td><td class="num strong">${value}%</td></tr>`).join("")}
      </tbody></table></div>`)}

      ${plainCard("Call activity", `<div class="table-wrap"><table><tbody>
        <tr><td>Total calls</td><td class="num strong">${num(calls.total_calls)}</td></tr>
        <tr><td>Total minutes</td><td class="num strong">${num(calls.total_minutes)}</td></tr>
        <tr><td>Average call length</td><td class="num strong">${num(calls.average_call_seconds)}s</td></tr>
        <tr><td>Calls in flight</td><td class="num strong">${num(calls.in_flight)}</td></tr>
        <tr><td>Human transfers</td><td class="num strong">${num(today.human_transfers)}</td></tr>
        <tr><td>Callbacks requested</td><td class="num strong">${num(today.callbacks)}</td></tr>
        <tr><td>Opted out</td><td class="num strong">${num(today.opt_outs)}</td></tr>
      </tbody></table></div>`)}

      ${card("Leads by service", serviceEntries.length
        ? bars(serviceEntries)
        : emptyState("No leads today", "Service breakdown appears once today's leads are uploaded."),
        "today")}
    </div>`;

  el("refresh").addEventListener("click", () => render("dashboard"));
};

/* ---------- leads ---------- */

VIEWS.leads = async (view) => {
  const [leads, uploads] = await Promise.all([
    api("/leads?limit=50"), api("/leads/uploads?limit=8").catch(() => []),
  ]);

  view.innerHTML = `
    ${card("Upload today's leads", `
      <form class="stack" id="upload-form">
        <div class="field-row">
          <label>CSV or Excel file
            <input type="file" id="file" accept=".csv,.xlsx" required></label>
          <button class="btn btn-primary" type="submit" id="upload-btn">Upload and validate</button>
          <button class="btn btn-quiet" type="button" id="template-btn">Get template</button>
        </div>
        <p class="form-msg" id="upload-msg" hidden></p>
      </form>`,
      "duplicates, invalid numbers and do-not-call are screened automatically")}

    <div class="section">${plainCard("Recent uploads", table(
      ["File", { label: "Rows" }, { label: "Ready" }, { label: "Rejected" }, "Status"],
      uploads,
      (u) => `<tr>
        <td>${esc(u.filename)}</td>
        <td class="num">${num(u.total_rows)}</td>
        <td class="num strong">${num(u.valid_count)}</td>
        <td class="num">${num(u.total_rows - u.valid_count)}</td>
        <td>${u.status === "COMPLETED"
          ? `<span class="badge on">Validated</span>`
          : `<span class="badge alert">Failed</span>`}</td></tr>`,
      emptyState("No uploads yet", "Your first upload will appear here with its validation summary.")
    ))}</div>

    <div class="section">${plainCard("Leads", table(
      ["Reference", "Customer", "Phone", "City", "Service", { label: "Monthly bill" }, "Status"],
      leads,
      (l) => `<tr>
        <td>${esc(l.lead_ref || "—")}</td>
        <td class="strong">${esc(l.customer_name)}</td>
        <td>${esc(l.customer_phone)}</td>
        <td>${esc(l.city || "—")}</td>
        <td>${esc(l.interested_service ? title(l.interested_service) : "—")}</td>
        <td class="num">${money(l.monthly_bill)}</td>
        <td><span class="badge">${esc(title(l.status))}</span></td></tr>`,
      emptyState("No leads yet", "Upload a file above to get started.")
    ), `${leads.length} shown`)}</div>`;

  el("template-btn").addEventListener("click", async () => {
    try {
      const response = await fetch("/leads/template", {
        headers: { Authorization: "Bearer " + state.token },
      });
      const url = URL.createObjectURL(await response.blob());
      const anchor = Object.assign(document.createElement("a"),
        { href: url, download: "swaraj_leads_template.csv" });
      anchor.click();
      URL.revokeObjectURL(url);
      toast("Template downloaded.", "ok");
    } catch (_) { toast("Could not download the template.", "err"); }
  });

  el("upload-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const file = el("file").files[0];
    if (!file) return;
    const message = el("upload-msg");
    const button = el("upload-btn");
    button.disabled = true;
    button.textContent = "Validating…";
    message.hidden = true;
    try {
      const form = new FormData();
      form.append("file", file);
      const r = await api("/leads/uploads", { method: "POST", body: form });
      if (r.status === "FAILED") {
        message.className = "form-msg err";
        message.textContent = r.error;
        message.hidden = false;
      } else {
        toast(`${r.valid_count} of ${r.total_rows} leads ready to call.`, "ok");
        message.className = "form-msg";
        message.innerHTML =
          `<strong>${num(r.valid_count)} ready.</strong> Rejected — duplicate ${num(r.duplicate_count)},
           invalid number ${num(r.invalid_phone_count)}, opted out ${num(r.opted_out_count)},
           consent ${num(r.consent_issue_count)}, missing fields ${num(r.missing_field_count)}.`;
        message.hidden = false;
        setTimeout(() => render("leads"), 1200);
      }
    } catch (exc) {
      message.className = "form-msg err";
      message.textContent = exc.message;
      message.hidden = false;
    } finally {
      button.disabled = false;
      button.textContent = "Upload and validate";
    }
  });
};

/* ---------- campaigns ---------- */

VIEWS.campaigns = async (view) => {
  const [campaigns, uploads] = await Promise.all([
    api("/campaigns?limit=25"), api("/leads/uploads?limit=10").catch(() => []),
  ]);
  const usable = uploads.filter((u) => u.valid_count > 0);

  view.innerHTML = `
    ${card("New campaign", `
      <form class="stack" id="campaign-form">
        <div class="field-row">
          <label>Campaign name
            <input id="c-name" placeholder="September residential leads" required></label>
          <label>Lead list
            <select id="c-upload">${usable.length
              ? usable.map((u) => `<option value="${u.id}">${esc(u.filename)} — ${u.valid_count} leads</option>`).join("")
              : `<option value="">No validated uploads yet</option>`}</select></label>
        </div>
        <div class="field-row">
          <label>Calling from<input type="time" id="c-start" value="10:00"></label>
          <label>Calling until<input type="time" id="c-end" value="18:00"></label>
          <label>Concurrent calls<input type="number" id="c-conc" value="5" min="1" max="200"></label>
          <label>Max attempts<input type="number" id="c-attempts" value="3" min="1" max="10"></label>
          <button class="btn btn-primary" type="submit" id="c-submit">Create campaign</button>
        </div>
        <p class="form-msg" id="campaign-msg" hidden></p>
      </form>`,
      "calls are placed only inside the window, never above the concurrency")}

    <div class="section">${plainCard("Campaigns", table(
      ["Campaign", "Status", "Window", { label: "Concurrency" }, { label: "Attempts" }, "Actions"],
      campaigns,
      (c) => `<tr>
        <td class="strong">${esc(c.name)}</td>
        <td><span class="badge ${c.status === "RUNNING" ? "on" : c.status === "STOPPED" ? "off" : ""}">${esc(title(c.status))}</span></td>
        <td>${esc(c.window_start.slice(0, 5))}–${esc(c.window_end.slice(0, 5))}</td>
        <td class="num">${c.concurrency}</td>
        <td class="num">${c.max_attempts}</td>
        <td><div style="display:flex;gap:6px;flex-wrap:wrap">
          ${["DRAFT", "PAUSED"].includes(c.status)
            ? `<button class="btn btn-quiet btn-sm" data-act="start" data-id="${c.id}">Start</button>` : ""}
          ${c.status === "RUNNING"
            ? `<button class="btn btn-quiet btn-sm" data-act="pause" data-id="${c.id}">Pause</button>
               <button class="btn btn-quiet btn-sm" data-act="dispatch" data-id="${c.id}">Dispatch now</button>` : ""}
          ${!["STOPPED", "COMPLETED"].includes(c.status)
            ? `<button class="btn btn-quiet btn-sm" data-act="stop" data-id="${c.id}">Stop</button>` : ""}
        </div></td></tr>`,
      emptyState("No campaigns yet", "Create one above to start calling a validated lead list.")
    ))}</div>`;

  el("campaign-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = el("campaign-msg");
    const button = el("c-submit");
    button.disabled = true;
    button.textContent = "Creating…";
    message.hidden = true;
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
      if (uploadId) {
        const queued = await api(`/campaigns/${campaign.id}/leads`, {
          method: "POST", body: { upload_id: Number(uploadId) },
        });
        toast(`Campaign created — ${queued.added} leads queued.`, "ok");
      } else {
        toast("Campaign created. Add a lead list before starting it.", "ok");
      }
      setTimeout(() => render("campaigns"), 900);
    } catch (exc) {
      message.className = "form-msg err";
      message.textContent = exc.message;
      message.hidden = false;
    } finally {
      button.disabled = false;
      button.textContent = "Create campaign";
    }
  });

  view.querySelectorAll("button[data-act]").forEach((button) =>
    button.addEventListener("click", async () => {
      button.disabled = true;
      const { act, id } = button.dataset;
      try {
        const result = await api(`/campaigns/${id}/${act}`, { method: "POST" });
        toast(act === "dispatch"
          ? `${result.calls_placed} call${result.calls_placed === 1 ? "" : "s"} placed.`
          : `Campaign ${act === "start" ? "started" : act + "d"}.`, "ok");
        render("campaigns");
      } catch (exc) {
        toast(exc.message, "err");
        button.disabled = false;
      }
    }));
};

/* ---------- my leads ---------- */

VIEWS.myleads = async (view) => {
  const leads = await api("/sales/my-leads");
  const hot = leads.filter((l) => l.classification === "HOT").length;

  view.innerHTML = `
    <div class="stats section">
      ${stat("Assigned to you", leads.length, { foot: "open opportunities" })}
      ${stat("Hot", hot, { dot: "hot", foot: "call these first" })}
      ${stat("Surveys pending", leads.filter((l) => l.stage === "SURVEY").length, { foot: "awaiting visit" })}
    </div>
    <div class="section">${plainCard("Your queue", table(
      ["Customer", "Phone", "Service", { label: "Monthly bill" }, "Score", "Stage"],
      leads,
      (l) => `<tr>
        <td class="strong">${esc(l.customer_name)}</td>
        <td>${esc(l.customer_phone)}</td>
        <td>${esc(l.service ? title(l.service) : "—")}</td>
        <td class="num">${money(l.monthly_bill)}</td>
        <td>${tierBadge(l.classification, l.score)}</td>
        <td><span class="badge">${esc(title(l.stage))}</span></td></tr>`,
      emptyState("Nothing assigned yet",
        "Qualified leads are assigned automatically once the AI finishes a call.")
    ), "highest value first")}</div>`;
};

/* ---------- surveys ---------- */

VIEWS.surveys = async (view) => {
  const surveys = await api("/surveys?limit=50");
  const counts = surveys.reduce((acc, s) => ({ ...acc, [s.status]: (acc[s.status] || 0) + 1 }), {});

  view.innerHTML = `
    <div class="stats section">
      ${stat("Requested", counts.REQUESTED || 0, { foot: "awaiting scheduling" })}
      ${stat("Scheduled", counts.SCHEDULED || 0, { foot: "date agreed" })}
      ${stat("Assigned", counts.ASSIGNED || 0, { foot: "engineer allocated" })}
      ${stat("Completed", counts.COMPLETED || 0, { foot: "visit done" })}
    </div>
    <div class="section">${plainCard("Site surveys", table(
      ["#", "Service", "Address", "Preferred", "Engineer", "Status"],
      surveys,
      (s) => `<tr>
        <td>${s.id}</td>
        <td>${esc(s.service ? title(s.service) : "—")}</td>
        <td>${esc(s.address || "—")}</td>
        <td>${esc(s.preferred_date || "—")}${s.preferred_time ? " · " + esc(s.preferred_time) : ""}</td>
        <td>${s.assigned_to_id ? "#" + s.assigned_to_id : "<span style='color:var(--ink-muted)'>Unassigned</span>"}</td>
        <td><span class="badge ${s.status === "COMPLETED" ? "on" : ""}">${esc(title(s.status))}</span></td></tr>`,
      emptyState("No surveys yet",
        "A survey is booked automatically whenever a customer asks for one during a call.")
    ))}</div>`;
};

/* ---------- calculator ---------- */

VIEWS.calculator = async (view) => {
  view.innerHTML = `
    <div class="notice">
      <b>Indicative only.</b>
      <span>Engineering constants and subsidy slabs must be signed off by Swaraj engineering
      before these figures are quoted to a customer.</span>
    </div>
    <div class="grid cols-2">
      ${card("Customer details", `
        <form class="stack" id="calc-form">
          <label>Monthly electricity bill (₹)
            <input type="number" id="k-bill" value="7500" min="0"></label>
          <label>Monthly units (optional — more accurate than the bill)
            <input type="number" id="k-units" min="0" placeholder="e.g. 620"></label>
          <label>Available roof area, sq ft (optional)
            <input type="number" id="k-roof" min="0" placeholder="e.g. 600"></label>
          <label>System type
            <select id="k-type">
              <option value="ON_GRID">On-grid</option>
              <option value="HYBRID">Hybrid</option>
              <option value="OFF_GRID">Off-grid</option>
            </select></label>
          <label style="flex-direction:row;align-items:center;gap:8px;display:flex">
            <input type="checkbox" id="k-subsidy" checked> Residential subsidy eligible</label>
          <button class="btn btn-primary" type="submit">Calculate</button>
        </form>`)}
      <section class="card"><div class="card-head"><h2>Estimate</h2></div>
        <div id="calc-out">${emptyState("No estimate yet",
          "Enter the customer's details and calculate.")}</div></section>
    </div>`;

  el("calc-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const output = el("calc-out");
    output.innerHTML = `<div class="card-body skeleton"><i style="width:60%"></i><i></i><i style="width:80%"></i></div>`;
    try {
      const body = {
        system_type: el("k-type").value,
        subsidy_eligible: el("k-subsidy").checked,
      };
      if (el("k-units").value) body.monthly_units = Number(el("k-units").value);
      else body.monthly_bill = Number(el("k-bill").value);
      if (el("k-roof").value) body.roof_area_sqft = Number(el("k-roof").value);

      const r = await api("/solar/estimate", { method: "POST", body });
      output.innerHTML = `
        <div class="card-body" style="border-bottom:1px solid var(--hairline)">
          <div class="hero">
            <span class="figure">${r.system_size_kw}</span>
            <span class="caption">kW recommended · about ${num(r.roof_area_required_sqft)} sq ft of roof</span>
          </div>
        </div>
        <div class="table-wrap"><table><tbody>
          <tr><td>Annual generation</td><td class="num strong">${num(r.annual_generation_kwh)} kWh</td></tr>
          <tr><td>Monthly savings</td><td class="num strong">${money(r.monthly_savings)}</td></tr>
          <tr><td>Project cost</td><td class="num strong">${money(r.project_cost_low)} – ${money(r.project_cost_high)}</td></tr>
          <tr><td>Subsidy</td><td class="num strong">${money(r.subsidy_amount)}</td></tr>
          <tr><td>Net investment</td><td class="num strong">${money(r.net_investment_low)} – ${money(r.net_investment_high)}</td></tr>
          <tr><td>Payback period</td><td class="num strong">${r.payback_years ?? "—"} years</td></tr>
        </tbody></table></div>
        <div class="card-body" style="color:var(--ink-muted);font-size:12px">
          ${r.notes.map((n) => esc(n)).join("<br>")}</div>`;
    } catch (exc) {
      output.innerHTML = `<div class="card-body"><p class="form-msg err">${esc(exc.message)}</p></div>`;
    }
  });
};

/* ---------- boot ---------- */

if (state.token) start(); else setVisible(el("login"), true);
