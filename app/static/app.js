"use strict";
// All user data is rendered with textContent (never innerHTML), so it cannot inject markup.
const $ = (sel) => document.querySelector(sel);
const state = { token: sessionStorage.getItem("cc_token"), me: null, view: "today" };

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null) el.append(kid.nodeType ? kid : document.createTextNode(kid));
  return el;
}
const say = (msg, error = false) => { const s = $("#status"); s.textContent = msg; s.className = "status" + (error ? " error" : ""); };

async function api(path, { method = "GET", body } = {}) {
  const res = await fetch("/api" + path, {
    method, headers: { ...(body ? { "Content-Type": "application/json" } : {}),
      ...(state.token ? { Authorization: "Bearer " + state.token } : {}) },
    body: body ? JSON.stringify(body) : undefined });
  if (res.status === 401 && state.token) { signOut("Please sign in again."); throw new Error("auth"); }
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Please check what you entered.");
  return data;
}
const guard = (fn) => async (e) => { e?.preventDefault?.(); try { await fn(e); } catch (err) { if (err.message !== "auth") say(err.message, true); } };

function field(label, name, attrs = {}, hint) {
  const id = "f_" + name, input = attrs.tag === "select" ? h("select", { id, name }, attrs.options.map((o) => h("option", { value: o[0] }, o[1])))
    : h("input", { id, name, autocomplete: "off", ...attrs });
  return h("div", {}, h("label", { for: id }, label), input, hint ? h("p", { class: "hint", id: id + "_h" }, hint) : null);
}
const val = (form, name) => form.elements[name].value.trim();
const fmtTime = (t) => { const [hh, mm] = t.split(":").map(Number); return new Date(2000, 0, 1, hh, mm).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }); };
const fmtWhen = (iso) => new Date(iso).toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit", timeZone: state.me.timezone });
const canWrite = () => state.me.role !== "family";
const isElder = () => state.me.role === "elder";

function secretCard(title, passcode, notice) {
  return h("div", { class: "card", role: "alert" }, h("h3", {}, title), h("p", { class: "secret" }, passcode), h("p", { class: "hint" }, notice));
}

// ---------- sign in / create ----------
function renderAuth() {
  $("#nav").hidden = true; $("#logout").hidden = true;
  const login = h("form", { class: "card", onsubmit: guard(async (e) => {
    const f = e.target, r = await api("/auth/login", { method: "POST", body: { profile_code: val(f, "code"), passcode: val(f, "pass") } });
    state.token = r.token; sessionStorage.setItem("cc_token", r.token); await start();
  }) }, h("h2", {}, "Sign in"),
    field("Profile code", "code", { required: true, maxlength: 12, autocapitalize: "characters" }, "8 letters and numbers, shared by your care circle."),
    field("Your passcode", "pass", { required: true, maxlength: 32, type: "password", autocapitalize: "characters" }, "Looks like ABCD-EFGHJKMNPQ."),
    h("p", {}, h("button", { class: "primary", type: "submit" }, "Sign in")));
  const create = h("form", { class: "card", onsubmit: guard(async (e) => {
    const f = e.target, email = val(f, "email");
    const r = await api("/profiles", { method: "POST", body: { elder_name: val(f, "name"), elder_email: email || null, timezone: val(f, "tz") } });
    f.replaceWith(h("div", { class: "card" }, h("h2", {}, "Care profile created"), h("p", {}, "Profile code:"), h("p", { class: "secret" }, r.profile_code),
      secretCard("Your passcode", r.passcode, r.notice), h("p", {}, "Write both down, then sign in on the left.")));
  }) }, h("h2", {}, "Start a new care profile"),
    field("Name of the person being cared for", "name", { required: true, maxlength: 100 }),
    field("Their email (for reminders, optional)", "email", { type: "email", maxlength: 254 }),
    field("Time zone", "tz", { value: Intl.DateTimeFormat().resolvedOptions().timeZone, required: true }),
    h("p", {}, h("button", { type: "submit" }, "Create profile")));
  $("#main").replaceChildren(h("div", { class: "grid" }, login, create));
}

// ---------- views ----------
const views = {
  async today() {
    const doses = await api("/doses/today");
    const speak = () => { speechSynthesis.cancel(); const pending = doses.filter((d) => d.status === "pending");
      speechSynthesis.speak(new SpeechSynthesisUtterance(pending.length ? "Still to take today: " + pending.map((d) => `${fmtTime(d.time)}, ${d.medication} ${d.dosage}`).join(". ") : "Nothing left to take today.")); };
    const act = (id, action) => guard(async () => { await api(`/doses/${id}/${action}`, { method: "POST" }); say(action === "take" ? "Marked as taken. Well done!" : "Marked as skipped."); await show("today"); });
    return h("section", {}, h("div", { class: "row" }, h("h2", {}, `Today for ${state.me.elder_name}`),
      "speechSynthesis" in window ? h("button", { type: "button", onclick: speak }, "Read aloud") : null),
      doses.length ? h("ul", { class: "plain" }, doses.map((d) => h("li", { class: "card" }, h("div", { class: "row" },
        h("div", {}, h("p", { class: "big" }, fmtTime(d.time)), h("h3", {}, `${d.medication} ${d.dosage}`), d.instructions ? h("p", { class: "hint" }, d.instructions) : null,
          h("span", { class: "badge " + d.status }, { taken: "✔ Taken", pending: "⏳ To take", missed: "✖ Missed", skipped: "⤼ Skipped" }[d.status]),
          d.resolved_by ? h("span", { class: "hint" }, ` by ${d.resolved_by}`) : null),
        canWrite() && d.status !== "taken" ? h("div", { class: "row" },
          h("button", { class: "primary", type: "button", onclick: act(d.id, "take"), "aria-label": `Mark ${d.medication} at ${fmtTime(d.time)} as taken` }, "I took it"),
          d.status === "pending" ? h("button", { type: "button", onclick: act(d.id, "skip"), "aria-label": `Skip ${d.medication} at ${fmtTime(d.time)}` }, "Skip") : null) : null))))
        : h("p", { class: "card" }, "No doses scheduled today."));
  },

  async medicines() {
    const meds = await api("/medications");
    const form = canWrite() ? h("form", { class: "card", onsubmit: guard(async (e) => {
      const f = e.target, times = val(f, "times").split(/[\s,]+/).filter(Boolean), pills = val(f, "pills");
      const r = await api("/medications", { method: "POST", body: { name: val(f, "name"), dosage: val(f, "dosage"), instructions: val(f, "ins"), times, pills_remaining: pills ? +pills : null } });
      say(r.warnings.length ? "Added. Please read the safety note below." : "Medicine added."); await show("medicines", r.warnings);
    }) }, h("h2", {}, "Add a medicine"), field("Name", "name", { required: true, maxlength: 100 }), field("Dose (e.g. 500 mg)", "dosage", { maxlength: 60 }),
      field("Times each day (24-hour)", "times", { required: true, placeholder: "08:00, 20:00" }, "Separate with commas, e.g. 08:00, 20:00"),
      field("Pills left (optional, enables refill alerts)", "pills", { type: "number", min: 0, max: 9999 }), field("Instructions (optional)", "ins", { maxlength: 300 }),
      h("p", {}, h("button", { class: "primary", type: "submit" }, "Add medicine"))) : null;
    return h("section", {}, h("h2", {}, "Medicines"), (views._warnings || []).map((w) => h("p", { class: "card missed", role: "alert" }, "⚠ " + w)),
      meds.length ? h("ul", { class: "plain" }, meds.map((m) => h("li", { class: "card row" }, h("div", {}, h("h3", {}, `${m.name} ${m.dosage}`),
        h("p", { class: "hint" }, "Daily at " + m.times.map(fmtTime).join(", ") + (m.pills_remaining != null ? ` · ${m.pills_remaining} pills left` : "")),
        m.instructions ? h("p", {}, m.instructions) : null),
        canWrite() ? h("button", { class: "danger", type: "button", "aria-label": `Stop ${m.name}`, onclick: guard(async () => {
          if (confirm(`Stop ${m.name}? Future reminders will be removed.`)) { await api("/medications/" + m.id, { method: "DELETE" }); say(m.name + " stopped."); await show("medicines"); } }) }, "Stop") : null)))
        : h("p", { class: "card" }, "No medicines yet."), form);
  },

  async appointments() {
    const appts = await api("/appointments");
    const form = canWrite() ? h("form", { class: "card", onsubmit: guard(async (e) => {
      const f = e.target; await api("/appointments", { method: "POST", body: { title: val(f, "title"), doctor: val(f, "doctor"), location: val(f, "loc"), starts_at_local: val(f, "when") } });
      say("Appointment added."); await show("appointments");
    }) }, h("h2", {}, "Add an appointment"), field("What is it for?", "title", { required: true, maxlength: 120 }), field("Doctor (optional)", "doctor", { maxlength: 100 }),
      field("Place (optional)", "loc", { maxlength: 200 }), field("Date and time", "when", { type: "datetime-local", required: true }, "In the time zone of " + state.me.timezone),
      h("p", {}, h("button", { class: "primary", type: "submit" }, "Add appointment"))) : null;
    return h("section", {}, h("h2", {}, "Appointments"), appts.length ? h("ul", { class: "plain" }, appts.map((a) => h("li", { class: "card row" },
      h("div", {}, h("h3", {}, a.title), h("p", {}, fmtWhen(a.starts_at)), h("p", { class: "hint" }, [a.doctor, a.location].filter(Boolean).join(" · "))),
      canWrite() ? h("button", { class: "danger", type: "button", "aria-label": "Remove " + a.title, onclick: guard(async () => { await api("/appointments/" + a.id, { method: "DELETE" }); await show("appointments"); }) }, "Remove") : null)))
      : h("p", { class: "card" }, "Nothing booked."), form);
  },

  async insights() {
    const i = await api("/insights"), pct = (v) => (v == null ? "-" : v + "%");
    const label = { good: "Going well", watch: "Needs a little attention", at_risk: "Needs support", unknown: "Not enough data yet" }[i.risk];
    return h("section", {}, h("h2", {}, "How is it going?"), h("div", { class: "grid" },
      h("div", { class: "card" }, h("p", { class: "hint" }, "Last 7 days"), h("p", { class: "big " + i.risk }, pct(i.adherence_7d)), h("p", { class: i.risk }, label)),
      h("div", { class: "card" }, h("p", { class: "hint" }, "Last 30 days"), h("p", { class: "big" }, pct(i.adherence_30d))),
      h("div", { class: "card" }, h("p", { class: "hint" }, "Perfect-day streak"), h("p", { class: "big" }, i.streak_days + (i.streak_days === 1 ? " day" : " days")))),
      i.weakest_slot ? h("p", { class: "card watch" }, `Doses at ${fmtTime(i.weakest_slot.time)} are taken only ${i.weakest_slot.rate}% of the time. A routine cue or a different time may help.`) : null,
      i.refill_alerts.map((r) => h("p", { class: "card missed" }, `Refill soon: ${r.medication} (about ${r.days_left} days left).`)),
      i.interaction_notes.map((n) => h("p", { class: "card missed", role: "note" }, "⚠ " + n)),
      i.by_medication.length ? h("div", { class: "card" }, h("h3", {}, "By medicine (7 days)"), h("ul", {}, i.by_medication.map((m) => h("li", {}, `${m.medication}: ${pct(m.rate)}`)))) : null);
  },

  async circle() {
    const members = await api("/members"), mine = state.me;
    const show2 = (title, r) => $("#main").prepend(secretCard(`${title}: ${r.name || ""} passcode`, r.passcode, r.notice));
    const invite = isElder() ? h("form", { class: "card", onsubmit: guard(async (e) => {
      const f = e.target, r = await api("/members", { method: "POST", body: { name: val(f, "name"), email: val(f, "email") || null, role: val(f, "role") } });
      await show("circle"); show2("Invitation created", r); say("Share the passcode privately with " + r.name + ".");
    }) }, h("h2", {}, "Invite someone to the circle"), field("Name", "name", { required: true, maxlength: 100 }), field("Email (for reminders)", "email", { type: "email", maxlength: 254 }),
      field("Their role", "role", { tag: "select", options: [["caregiver", "Caregiver - can manage medicines and appointments"], ["family", "Family - can view only"]] }),
      h("p", {}, h("button", { class: "primary", type: "submit" }, "Create passcode"))) : null;
    return h("section", {}, h("h2", {}, "Your care circle"), h("p", { class: "hint" }, `Profile code: ${mine.profile_code}`),
      h("ul", { class: "plain" }, members.map((m) => h("li", { class: "card row" }, h("div", {}, h("h3", {}, m.name), h("p", { class: "hint" }, m.role + (m.email ? " · " + m.email : ""))),
        isElder() ? h("div", { class: "row" },
          h("button", { type: "button", "aria-label": "New passcode for " + m.name, onclick: guard(async () => { const r = await api(`/members/${m.id}/rotate`, { method: "POST" }); await show("circle"); show2("New passcode", { ...r, name: m.name }); }) }, "New passcode"),
          m.role !== "elder" ? h("button", { class: "danger", type: "button", "aria-label": "Remove " + m.name, onclick: guard(async () => { if (confirm(`Remove ${m.name}?`)) { await api("/members/" + m.id, { method: "DELETE" }); await show("circle"); } }) }, "Remove") : null) : null))), invite);
  },

  async activity() {
    const log = await api("/audit");
    return h("section", {}, h("h2", {}, "Who changed what"), h("p", { class: "hint" }, "You always see every change made to your care plan."),
      log.length ? h("ul", { class: "plain" }, log.map((e) => h("li", { class: "card" }, h("strong", {}, e.actor), ` (${e.role}) · ${e.action.replaceAll("_", " ")}`, e.detail ? ` · ${e.detail}` : "", h("div", { class: "hint" }, fmtWhen(e.at))))) : h("p", { class: "card" }, "No activity yet."));
  },
};

async function show(view, warnings) {
  state.view = view; views._warnings = warnings;
  const tabs = [["today", "Today"], ["medicines", "Medicines"], ["appointments", "Appointments"], ["insights", "Insights"], ["circle", "Circle"], ...(isElder() ? [["activity", "Activity"]] : [])];
  $("#nav").replaceChildren(...tabs.map(([id, label]) => h("button", { type: "button", "aria-current": id === view ? "page" : false, onclick: () => { say(""); show(id); } }, label)));
  const node = await views[view]();
  $("#main").replaceChildren(node);
}

function signOut(msg = "") { state.token = null; state.me = null; sessionStorage.removeItem("cc_token"); renderAuth(); say(msg); }
async function start() {
  try { state.me = await api("/me"); } catch { return renderAuth(); }
  $("#nav").hidden = false; $("#logout").hidden = false; say(`Signed in as ${state.me.name} (${state.me.role}).`);
  try { await show("today"); } catch (err) { say(err.message, true); }
  $("#main").focus();
}

$("#logout").addEventListener("click", () => signOut("Signed out."));
$("#bigger").addEventListener("click", (e) => { const on = document.body.classList.toggle("large"); e.target.setAttribute("aria-pressed", on); localStorage.setItem("cc_large", on ? "1" : ""); });
$("#contrast").addEventListener("click", (e) => { const on = document.body.classList.toggle("contrast"); e.target.setAttribute("aria-pressed", on); localStorage.setItem("cc_contrast", on ? "1" : ""); });
for (const [key, cls, btn] of [["cc_large", "large", "#bigger"], ["cc_contrast", "contrast", "#contrast"]])
  if (localStorage.getItem(key) || (cls === "contrast" && matchMedia("(prefers-contrast: more)").matches)) { document.body.classList.add(cls); $(btn).setAttribute("aria-pressed", "true"); }
state.token ? start() : renderAuth();
