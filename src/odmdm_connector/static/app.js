"use strict";
// The page: asks the connector for its state and draws it. Setting up is a row of cards, one thing
// each, like a setup wizard; the daily work is one view of the phones with a few dialogs. Every
// sentence shown comes from the connector; this file adds labels, headings and the sentences for a
// connector that refuses this browser or does not answer. Data never becomes markup: the page is
// built with createElement and text nodes. The browser keeps one thing, the page's key, and nothing else.

const view = document.getElementById("view");
const dialog = document.getElementById("dialog");
const menu = document.getElementById("menu");
// After naming or asking again, the table waits for the listing that follows
const SETTLING = ["name", "ask again", "phones"];

let S = null; // the last state
let screen = ""; // a card's name, or "app" for the phones
let problem = ""; // the last refusal, shown in the view
let notice = ""; // the last answer's sentence, shown on the next card
let timer = 0;
let selected = new Set(); // the phones ticked in the table, by id
let settling = false; // the table is from before a naming or an asking again
let submit = null; // what the main button of the form being drawn does
let arriving = null; // a settings line Deploy App's page sent, {line, from}, until it is read
const flow = {}; // what the cards carry from one to the next, in memory only

// Building blocks

function el(tag, props, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === false || value === null || value === undefined) continue;
    if (key === "class") node.className = value;
    else if (key === "value" || key === "checked" || key === "indeterminate") node[key] = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  // A string becomes a text node, never markup
  for (const child of children.flat(Infinity)) if (child !== null && child !== undefined && child !== "") node.append(child);
  return node;
}

const h1 = (text) => el("h1", {}, text);
const lead = (text) => (text ? el("p", { class: "lead" }, text) : null);
// A button is known by its text, so that it keeps the focus when the view is drawn again
const button = (props, text) => {
  const node = el("button", props, text);
  node.dataset.key = text;
  return node;
};
// The form's main button submits it, so Enter in a field presses it
const main = (text, action, disabled = false, tone = "") => {
  submit = action;
  return button({ class: `main ${tone}`, type: "submit", disabled }, text);
};
const second = (text, onclick, disabled = false) => button({ class: "second", type: "button", onclick, disabled }, text);
const tool = (text, onclick, disabled = false, tone = "") => button({ class: `tool ${tone}`, type: "button", onclick, disabled }, text);
const link = (text, onclick) => el("button", { class: "back", type: "button", onclick }, text);
const choice = (title, sub, onclick, tone = "") =>
  el("button", { class: "choice", type: "button", onclick }, el("strong", {}, title), sub ? el("span", { class: tone }, sub) : null);
const count = (n, noun) => `${n} ${noun}${n === 1 ? "" : "s"}`;
// Text a password manager must not take: masked by the style, never a password field
const typed = (props) => el("input", { type: "text", autocomplete: "off", spellcheck: "false", ...props });
// Where a phone's certificate is, as a dot's colour
const tone = (row) => (!row ? "" : row.failed ? "bad" : row.waiting ? "none" : row.ready ? "ok" : "on");

function field(id, label, input) {
  input.id = id;
  input.dataset.key = id;
  return [el("label", { for: id }, label), input];
}

function check(label, checked, onchange) {
  return el(
    "label",
    { class: "check" },
    el("input", { type: "checkbox", checked, onchange: (event) => onchange(event.target.checked) }),
    label,
  );
}

function copy(text) {
  navigator.clipboard?.writeText(text).catch(() => {});
}

// A text to copy, shown in a field of its own that keeps a long one on one line
function copyable(text, label, masked = false) {
  const value = typed({ class: masked ? "secret" : null, value: text, readonly: true, "aria-label": label });
  value.dataset.key = label;
  return value;
}

// The page's key, which the connector printed in the address's fragment: no server is sent a
// fragment, so it reaches this page alone. It is kept in this browser's storage for this port, which
// another port cannot read, so that every tab of the page has it; without storage, in this tab only.
const KEPT = "odmdm-key";
let key = "";
try {
  key = localStorage.getItem(KEPT) || "";
} catch {}

// Take the key from the address, if it holds one, and take it out of the address; whether it did.
// Any web page can send the browser here with a key of its own, so a key is kept, and so passed to the
// other tabs, only once the connector accepts it; another one leaves the kept key as it is.
async function takeKey() {
  const found = new URLSearchParams(location.hash.slice(1)).get("key");
  if (!found) return false;
  history.replaceState(null, "", location.pathname);
  // The answer is read to its end, so that the request is not left open
  const accepted = await fetch("/api/state", { cache: "no-store", headers: { "X-ODMDM-Key": found } }).then(
    (response) => response.text().then(() => response.ok),
    () => false,
  );
  if (!accepted) return false;
  key = found;
  try {
    localStorage.setItem(KEPT, found);
  } catch {}
  return true;
}

// Talking to the connector

async function call(path, body) {
  const headers = { "X-ODMDM-Key": key };
  const options =
    body === undefined
      ? { cache: "no-store", headers }
      : { method: "POST", headers: { ...headers, "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const response = await fetch(path, options);
  const json = (response.headers.get("Content-Type") || "").startsWith("application/json");
  const answer = json ? await response.json() : { error: await response.text() };
  if (!response.ok) throw Object.assign(new Error(answer.error || response.statusText), { status: response.status });
  return answer;
}

// Ask, take the state that follows, then go on; a refusal is said by fail, in the view by default
async function act(path, body, then, fail = say) {
  problem = "";
  let answer;
  try {
    answer = await call(path, body);
  } catch (error) {
    if (error.status && error.status !== 403) fail(error.message);
    else away(error);
    return;
  }
  notice = answer.said || "";
  if (await refresh(false)) then(answer);
}

function say(text) {
  problem = text;
  draw(false);
}

const overlaid = () => dialog.open || menu.matches(":popover-open");

function closeOverlays() {
  if (menu.matches(":popover-open")) menu.hidePopover();
  if (dialog.open) dialog.close();
}

// The cards that hold nothing typed yet, which follow another tab's adding, opening or connecting
const FOLLOWING = ["add", "choose", "mdm"];

// Whether the state came; the views that show it are drawn again, and follow it where it moved on
async function refresh(redraw = true) {
  const before = S;
  try {
    S = await call("/api/state");
  } catch (error) {
    away(error);
    return false;
  }
  const moved =
    before && (S.active !== before.active || S.connected !== before.connected || S.connections.length > before.connections.length);
  if (settling && !S.busy.some((job) => SETTLING.includes(job))) settling = false;
  if (!screen || screen === "away") begin();
  else if (!redraw) return true;
  else if (screen === "app" && !S.connected) {
    closeOverlays();
    home();
  } else if (screen === "connecting") checklist();
  else if (screen === "app" && !overlaid()) draw(false);
  else if (FOLLOWING.includes(screen) && moved) begin();
  return true;
}

// The connector refuses this browser, having no key yet or a new one since a restart, or does not answer
function away(error) {
  closeOverlays();
  flow.away = { status: error.status || 0, text: error.message };
  screen = "away";
  problem = "";
  draw(false);
}

function schedule() {
  clearTimeout(timer);
  timer = setTimeout(tick, S && S.busy.length ? 1000 : 3000);
}

async function tick() {
  if (document.visibilityState === "visible") await refresh();
  schedule();
}

// Moving between views

function show(name) {
  screen = name;
  problem = "";
  draw(true);
}

function go(name) {
  notice = "";
  show(name);
}

function draw(fresh) {
  const focused = document.activeElement?.dataset?.key;
  const scroller = view.querySelector(".table");
  const at = scroller ? [scroller.scrollTop, scroller.scrollLeft] : null;
  submit = null;
  const built = screen === "app" ? phones() : frame(CARDS[screen]());
  document.body.className = screen === "app" ? "app" : "cards";
  view.replaceChildren(...built);
  const table = view.querySelector(".table");
  if (table && at && !fresh) [table.scrollTop, table.scrollLeft] = at;
  const again = fresh ? null : [...view.querySelectorAll("[data-key]")].find((node) => node.dataset.key === focused);
  const card = view.querySelector(".card");
  const first = card ? starting(card) : null;
  (again || ((fresh || document.activeElement === document.body) && first) || null)?.focus();
}

// Where the focus starts: a button marked to start on, else the first field shown, else the main
// button, so that typing or Enter works at once
function starting(root) {
  const fields = root.querySelectorAll("input:not([type=checkbox]):not([readonly]), textarea");
  return root.querySelector("[autofocus]") || [...fields].find((node) => !node.closest("[hidden]")) || root.querySelector("button.main:enabled");
}

// Cancel, to start on where a stray Enter must not remove something or take something in
const cancel = (onclick) => {
  const node = second("Cancel", onclick);
  node.autofocus = true;
  return node;
};

// The form of a view: its main button submits it
function form(parts, cls) {
  const action = submit;
  return el(
    "form",
    {
      class: cls,
      onsubmit: (event) => {
        event.preventDefault();
        action?.();
      },
    },
    parts,
  );
}

// A card: the brand line, a link back and a corner, how far a first set-up is, then the card's own parts
function frame({ parts, step = null, back = null, corner = "", host = false }) {
  // The MDM's own title once its fields are asked for; a plugin is not imported before it is approved
  const chosen = screen === "field" || screen === "connecting" ? flow.form.title : "MDM";
  const names = ["Deploy App", chosen, "Connect"];
  const bar =
    step === null
      ? null
      : el(
          "ol",
          { class: "progress", "aria-label": "Set-up" },
          names.map((name, index) =>
            el("li", { class: index < step ? "done" : index === step ? "now" : "", "aria-current": index === step ? "step" : null }, name),
          ),
        );
  const top = back || corner ? el("div", { class: "topline" }, back ? link(`‹ ${back.text}`, back.action) : el("span"), corner) : null;
  const body = parts.flat(Infinity).filter(Boolean);
  // A refusal goes under the heading and the lead, where it is read first
  let at = body.findIndex((node) => node.tagName === "H1") + 1;
  while (body[at]?.matches?.("p.lead, p.muted")) at += 1;
  if (problem) body.splice(at, 0, el("p", { class: "error", role: "alert" }, problem));
  return [
    el("header", { class: "brand" }, host && S?.active ? `ODMDM connector · ${S.active}` : "ODMDM connector"),
    el("main", { class: "card" }, form([top, bar, body])),
  ];
}

function begin() {
  problem = notice = "";
  if (!arriving) return home();
  closeOverlays();
  const { line, from } = arriving;
  arriving = null;
  // Nothing else moves the view on while the line is read: it goes to the confirming card, or to Add
  screen = "reading";
  readLine(line, from);
}

// Where a Deploy App's work goes on: its phones, else what its MDM still needs, and why. The last
// answer's sentence stays, for a card to say.
function home() {
  flow.renew = flow.change = flow.again = false;
  flow.back = null;
  if (!S.active) show(S.connections.length ? "choose" : "add");
  else if (S.connected) show("app");
  else if (S.needs === "secret") chooseMdm(S.mdm, S.status, true);
  else if (S.mdms.length === 1) chooseMdm(S.mdms[0].name);
  else go("mdm");
}

// Add a Deploy App, or new settings for the active one; Back returns to the phones
function adding(renew) {
  closeOverlays();
  flow.renew = renew;
  flow.back = () => go("app");
  go("add");
}

// from is the origin of the page that sent the line, empty for one typed or pasted here
function readLine(line, from) {
  act(
    "/api/read",
    { line, from },
    (reading) => {
      flow.line = { line, from };
      flow.reading = reading;
      show("confirm");
    },
    (text) => {
      if (screen !== "add") show("add");
      say(text);
    },
  );
}

// A secret kept goes only with the settings it was kept for: it stands in while the rest are as loaded
const unchanged = () => flow.form.fields.every((each) => each.secret || (flow.values[each.key] || "") === (flow.loaded[each.key] || ""));
const held = (each) => each.secret && each.kept && unchanged();
const filled = (each) => Boolean((flow.values[each.key] || "").trim() || held(each));

// The MDM's fields one at a time; with only, just the first it lacks. why is said above that field.
function chooseMdm(name, why = "", only = false) {
  act("/api/form", { mdm: name }, (answer) => {
    if (answer.approval) {
      flow.approval = { name, text: answer.approval };
      show("approve");
      return;
    }
    flow.form = answer;
    flow.values = Object.fromEntries(answer.fields.map((each) => [each.key, each.value]));
    flow.loaded = { ...flow.values };
    flow.remember = true;
    flow.why = why;
    const lacking = only ? answer.fields.findIndex((each) => !each.advanced && (each.required || each.secret) && !filled(each)) : -1;
    flow.only = lacking >= 0;
    flow.index = flow.only ? answer.fields.filter((each) => !each.advanced).indexOf(answer.fields[lacking]) : 0;
    show("field");
  });
}

// After Connect: the setup's steps while it runs or leaves something to say, else the phones. With
// the token typed again, a setup that ends with nothing to say goes straight on to the phones.
function checklist() {
  const busy = S.busy.includes("setup");
  const said = S.setup_error || S.challenge || S.steps.some((step) => !step.done) || (S.steps.length && !flow.again);
  if (!busy && (!S.connected || !said)) home();
  else if (screen === "connecting") draw(false);
  else show("connecting");
}

// The challenge, for an MDM that is given it by hand: masked until shown
function challenge() {
  return el(
    "div",
    { class: "copyrow" },
    copyable(S.challenge, "Challenge", !flow.showChallenge),
    tool(flow.showChallenge ? "Hide" : "Show", () => {
      flow.showChallenge = !flow.showChallenge;
      draw(false);
    }),
    tool("Copy", () => copy(S.challenge)),
  );
}

// The cards of a first set-up, and of the few things that interrupt the work

const CARDS = {
  away() {
    const { status, text } = flow.away;
    const kept = arriving ? el("p", { class: "muted" }, "The settings from Deploy App are kept here until then.") : null;
    if (status === 403)
      return {
        parts: [
          h1("Open the connector's address"),
          lead(text),
          el("div", { class: "box" }, "It looks like this:", el("br"), `http://127.0.0.1:${location.port}/#key=…`),
          kept,
        ],
      };
    if (status) return { parts: [h1("The connector failed"), el("p", { class: "error" }, text)] };
    return {
      parts: [h1("The connector has stopped"), lead("Start it again on this computer, then open the address it prints."), kept],
    };
  },

  add() {
    const renew = flow.renew && S.active;
    const input = typed({ class: "secret", placeholder: "ODMDM1 …" });
    let button;
    input.addEventListener("input", () => {
      button.disabled = !input.value.trim();
    });
    input.addEventListener("paste", (event) => {
      event.preventDefault();
      readLine(event.clipboardData.getData("text"), "");
    });
    button = main("Continue", () => readLine(input.value, ""), true);
    return {
      step: renew ? null : 0,
      back: S.connections.length ? { text: "Back", action: flow.back || home } : null,
      parts: [
        h1(renew ? `New settings for Deploy App ${S.active}` : "Add your Deploy App"),
        notice ? el("p", { class: "muted" }, notice) : null,
        lead("On Deploy App's MDM page, press this button:"),
        // A picture of Deploy App's button, which the sentence above names: nothing to press here
        el("div", { class: "lookfor", "aria-hidden": "true" }, el("small", {}, "on Deploy App"), el("span", {}, "Open in the MDM connector")),
        // Only a tab that Deploy App's button opened waits for its message; the button opens a new tab otherwise
        window.opener
          ? el("p", { class: "waiting" }, el("span", { class: "pulse" }), "Waiting for Deploy App…")
          : el("p", { class: "waiting" }, "It opens the connector in a new tab."),
        el("div", { class: "or" }, "OR"),
        field("line", "Paste its settings line:", input),
        button,
        S.connections.length
          ? null
          : el(
              "div",
              { class: "athand" },
              el("strong", {}, "Have at hand:"),
              el(
                "ul",
                {},
                ["Deploy App's MDM page, open as an admin", "your MDM's address", "an API token for your MDM"].map((each) => el("li", {}, each)),
              ),
            ),
      ],
    };
  },

  confirm() {
    const { host, until, known } = flow.reading;
    const leave = () => {
      flow.line = null;
      home();
    };
    const take = () =>
      act("/api/add", flow.line, () => {
        flow.line = null;
        home();
      });
    return {
      step: known ? null : 0,
      parts: [
        h1(known ? `New settings for Deploy App ${host}` : `Add Deploy App ${host}?`),
        lead(until),
        main(known ? "Use the new settings" : `Add ${host}`, take),
        // Any web page can bring this card up for a Deploy App of its own
        known ? second("Cancel", leave) : cancel(leave),
      ],
    };
  },

  choose() {
    return {
      parts: [
        h1("Which Deploy App?"),
        notice ? lead(notice) : null,
        S.connections.map((each) =>
          choice(each.host, each.short, () => act("/api/open", { host: each.host }, home), each.level),
        ),
        second("Add a Deploy App", () => {
          flow.renew = false;
          flow.back = () => go("choose");
          go("add");
        }),
      ],
    };
  },

  mdm() {
    return {
      step: 1,
      host: true,
      back: S.connections.length > 1 ? { text: "Other Deploy Apps", action: () => go("choose") } : null,
      parts: [
        h1("Which MDM?"),
        S.mdms.map((each) =>
          choice(each.name, each.built_in ? "Comes with the connector" : `Plugin: ${each.package} ${each.version}`, () =>
            chooseMdm(each.name),
          ),
        ),
      ],
    };
  },

  approve() {
    const { name, text } = flow.approval;
    return {
      step: 1,
      host: true,
      back: { text: "Back", action: () => go("mdm") },
      parts: [
        h1("Use this plugin?"),
        lead(text),
        main("Use it", () => act("/api/approve", { mdm: name }, () => chooseMdm(name))),
      ],
    };
  },

  field() {
    const form = flow.form;
    const steps = form.fields.filter((each) => !each.advanced);
    const shown = steps[flow.index];
    const last = flow.only || flow.index >= steps.length - 1;
    const advanced = flow.only ? [] : form.fields.filter((each) => each.advanced);
    // A required field left empty holds the button back; a secret kept from before counts as filled
    const ready = () => (last ? [shown, ...advanced] : [shown]).every((each) => !each || !each.required || filled(each));
    let button;
    const input = (each) => {
      const kept = each.remembered ? "Kept on this computer" : "Kept while the connector runs";
      const node = typed({
        class: each.secret ? "secret" : null,
        value: flow.values[each.key],
        placeholder: held(each) ? kept : each.default,
        oninput: (event) => {
          flow.values[each.key] = event.target.value;
          button.disabled = !ready();
        },
      });
      return field(`field-${each.key}`, each.label, node);
    };
    // The card's title is the field's label, so the input is named by it rather than shown it again
    const alone = (each) => {
      const node = input(each)[1];
      node.setAttribute("aria-label", each.label);
      return node;
    };
    const connect = () =>
      act(
        "/api/connect",
        { mdm: form.name, values: flow.values, remember: S.keeps && flow.remember },
        () => {
          flow.values = {};
          flow.again = flow.only;
          checklist();
        },
        // A kept secret left empty after the settings changed is asked for again on its own card
        (text) => {
          const again = steps.findIndex((each) => each.secret && each.kept && !unchanged() && !(flow.values[each.key] || "").trim());
          if (again >= 0 && !flow.only) {
            flow.index = again;
            show("field");
          }
          say(text);
        },
      );
    const next = () => {
      flow.index += 1;
      show("field");
    };
    const back = () => {
      if (flow.index > 0 && !flow.only) {
        flow.index -= 1;
        show("field");
      } else if (flow.change) go("app");
      else go(flow.only ? "choose" : "mdm");
    };
    const backward = flow.change || (flow.only ? S.connections.length > 1 : flow.index > 0 || S.mdms.length > 1);
    const backText = flow.only ? "Other Deploy Apps" : flow.index > 0 ? "Back" : flow.change ? "Back to the phones" : "Back";
    button = main(last ? "Connect" : "Next", last ? connect : next, !ready());
    return {
      step: flow.only ? null : 1,
      corner: flow.only ? "" : `${flow.index + 1} of ${steps.length}`,
      back: backward ? { text: backText, action: back } : null,
      host: true,
      parts: [
        h1(shown.label),
        lead(shown.help),
        flow.why ? el("p", { class: "note" }, flow.why) : null,
        alone(shown),
        // Said once: without a keychain the reason above already says the token is not kept
        shown.secret
          ? S.keeps
            ? check("Remember on this computer", flow.remember, (on) => {
                flow.remember = on;
              })
            : flow.why
              ? null
              : el("p", { class: "muted hint" }, "Kept only until the connector stops.")
          : null,
        last && advanced.length
          ? el(
              "details",
              { class: "more" },
              el("summary", {}, "More settings"),
              advanced.map((each) => [input(each), each.help ? el("p", { class: "muted hint" }, each.help) : null]),
            )
          : null,
        button,
      ],
    };
  },

  connecting() {
    const title = flow.form.title;
    const busy = S.busy.includes("setup");
    const done = S.steps.filter((step) => step.done);
    const todo = S.steps.filter((step) => !step.done);
    const item = (cls, step) => el("li", { class: cls }, step.label, step.message ? el("span", {}, step.message) : null);
    // Steps left to the operator are what the card is about once the setup has run
    const hand = !busy && !S.setup_error && (todo.length || S.challenge);
    const heading = busy ? `Connecting ${title}` : S.setup_error ? `${title} is not set up` : hand ? "Do these by hand" : `${title} is connected`;
    return {
      step: flow.again ? null : 2,
      host: true,
      parts: [
        h1(heading),
        // The connector's sentence only where it says more than the heading
        hand ? lead(S.status) : null,
        busy ? S.lines.map((line) => el("p", { class: "note" }, line)) : null,
        hand
          ? el(
              "ul",
              { class: "run" },
              todo.map((step) => item("hand", step)),
              S.challenge ? el("li", { class: "hand" }, S.challenge_note, challenge()) : null,
            )
          : null,
        el(
          "ul",
          { class: "run" },
          done.map((step) => item("done", step)),
          busy ? el("li", { class: "now" }, "…") : null,
        ),
        S.setup_error ? el("p", { class: "error" }, S.setup_error) : null,
        S.setup_error
          ? main("Change the settings", () => {
              flow.change = false;
              chooseMdm(S.mdm);
            })
          : main("Show the phones", home, busy),
      ],
    };
  },
};

// The phones: the window's line, one row of buttons, the selection, the table and what was said last

function phones() {
  const rows = S.rows;
  const ids = new Set(rows.map((row) => row.id));
  selected = new Set([...selected].filter((id) => ids.has(id)));
  const picked = rows.filter((row) => selected.has(row.id));
  const pick = (test) => {
    selected = new Set(rows.filter(test).map((row) => row.id));
    draw(false);
  };
  const flip = (id) => {
    if (selected.has(id)) selected.delete(id);
    else selected.add(id);
    draw(false);
  };
  const waiting = rows.some((row) => row.waiting);
  const askable = selected.size ? picked.some((row) => row.askable) : rows.some((row) => row.askable && row.failed);
  const level = S.banner.level;
  const renew = level === "closed" || level === "none";

  const picker = el(
    "button",
    { class: "picker", type: "button", popovertarget: "menu", "aria-haspopup": "true" },
    el("small", {}, "Deploy App"),
    el("strong", {}, S.active),
    " ▾",
  );
  picker.dataset.key = "picker";
  const all = el("input", {
    type: "checkbox",
    "aria-label": "Select all",
    checked: rows.length > 0 && selected.size === rows.length,
    indeterminate: selected.size > 0 && selected.size < rows.length,
    disabled: !rows.length,
    onchange: (event) => pick(() => event.target.checked),
  });
  all.dataset.key = "all";
  const body = rows.map((row) => {
    const box = el("input", {
      type: "checkbox",
      checked: selected.has(row.id),
      "aria-label": `Select ${row.callsign || row.id}`,
      onchange: () => flip(row.id),
    });
    box.dataset.key = `pick-${row.id}`;
    const line = el(
      "tr",
      {
        class: `${row.failed ? "bad" : ""} ${selected.has(row.id) ? "sel" : ""}`,
        tabindex: "0",
        onclick: (event) => {
          if (!event.target.closest(".pick")) flip(row.id);
        },
        ondblclick: () => detail(row.id),
        onkeydown: (event) => {
          if (event.target !== event.currentTarget) return;
          if (event.key === "Enter") {
            event.preventDefault();
            detail(row.id);
          } else if (event.key === " ") {
            event.preventDefault();
            flip(row.id);
          }
        },
      },
      el("td", { class: "pick" }, box),
      el("td", {}, row.id),
      el("td", { class: row.callsign ? "call" : "call nocall" }, row.callsign || "—"),
      el("td", { class: "phone" }, row.phone),
      el("td", { class: "joined" }, row.joined),
      el("td", { class: "cert" }, el("span", { class: `dot ${tone(row)}` }), row.certificate),
    );
    line.dataset.key = `row-${row.id}`;
    return line;
  });
  const empty = el("tr", {}, el("td", { class: "empty", colspan: "6" }, S.summary || "…"));

  return [
    el("header", { class: "top" }, el("span", { class: "name" }, "ODMDM connector"), picker),
    el(
      "main",
      { class: "work" },
      form(
        [
          el(
            "div",
            { class: `window ${level}` },
            el("span", { class: "said" }, el("span", { class: "dot" }), S.banner.text),
            renew && S.mdm_page
              ? el("a", { class: "out", href: S.mdm_page, target: "_blank", rel: "noopener noreferrer" }, "Open Deploy App ↗")
              : null,
          ),
          S.challenge_note
            ? el("div", { class: "note" }, el("p", {}, S.challenge_note), S.challenge ? challenge() : null)
            : null,
          el(
            "div",
            { class: "tools" },
            main("Give callsigns", () => giveCallsigns([...selected]), settling || !(selected.size || waiting), "tool go"),
            tool("Ask again", () => askAgain([...selected]), settling || !askable),
            tool("Details", () => detail([...selected][0]), settling || selected.size !== 1),
            tool("Refresh", () => act("/api/refresh", {}, () => draw(false))),
            tool("New settings", () => adding(true)),
            tool("Settings", settings, false, "push"),
          ),
          el(
            "div",
            { class: "select" },
            el("span", { class: "muted" }, "Select:"),
            tool("Waiting for a name", () => pick((row) => row.waiting), !waiting, "small"),
            tool("Refused or failed", () => pick((row) => row.failed), !rows.some((row) => row.failed), "small"),
            selected.size ? [el("span", { class: "muted" }, `${selected.size} selected`), link("Clear", () => pick(() => false))] : null,
            rows.length ? el("span", { class: "count" }, S.summary) : null,
          ),
          el(
            "div",
            { class: settling ? "table settling" : "table", "aria-busy": settling ? "true" : null },
            el(
              "table",
              {},
              el(
                "thead",
                {},
                el(
                  "tr",
                  {},
                  el("th", { class: "pick" }, all),
                  el("th", {}, "ID"),
                  el("th", {}, "Callsign"),
                  el("th", { class: "phone" }, "Phone"),
                  el("th", { class: "joined" }, "Joined"),
                  el("th", { class: "cert" }, "Certificate"),
                ),
              ),
              el("tbody", {}, rows.length ? body : empty),
            ),
          ),
        ],
        "app-form",
      ),
    ),
    el(
      "footer",
      { class: "status" },
      el(
        "div",
        { "aria-live": "polite" },
        problem ? el("p", { class: "error", role: "alert" }, problem) : el("p", {}, S.status),
        S.lines.length ? el("ul", {}, S.lines.map((line) => el("li", {}, line))) : null,
      ),
      S.updated ? el("span", { class: "updated" }, `Updated ${S.updated}`) : null,
    ),
  ];
}

function askAgain(ids) {
  settling = true;
  act("/api/again", { ids }, () => draw(false), (text) => {
    settling = false;
    say(text);
  });
}

// The Deploy Apps, in the menu under the picker: open one, add one, or remove the one open

menu.addEventListener("beforetoggle", (event) => {
  if (event.newState !== "open") return;
  const pick = (action) => () => {
    menu.hidePopover();
    action();
  };
  menu.replaceChildren(
    ...S.connections.map((each) =>
      el(
        "button",
        {
          class: each.active ? "item now" : "item",
          type: "button",
          "aria-current": each.active ? "true" : null,
          onclick: pick(() => (each.active ? null : act("/api/open", { host: each.host }, home))),
        },
        el("span", { class: "mark" }, each.active ? "●" : ""),
        el("span", {}, el("strong", {}, each.host), el("small", { class: each.level }, each.short)),
      ),
    ),
    el("hr"),
    el("button", { class: "item act", type: "button", onclick: pick(() => adding(false)) }, "+ Add a Deploy App"),
    el("button", { class: "item act danger", type: "button", onclick: pick(remove) }, `Remove ${S.active}…`),
  );
});

menu.addEventListener("toggle", (event) => {
  if (event.newState === "closed" && screen === "app" && !dialog.open) draw(false);
});

// The dialogs: the browser's own, which bring the backdrop, Escape and the keyboard kept inside. Each
// is a form; parts is what it holds, and its main button submits it.

function open(title, parts, foot) {
  const opener = document.activeElement?.dataset?.key;
  dialog.dataset.opener = opener || "";
  const say = el("p", { class: "error", role: "alert" });
  const built = form(
    [
      el("header", {}, el("h2", { id: "dialog-title" }, title), el("button", { class: "x", type: "button", "aria-label": "Close", onclick: () => dialog.close() }, "×")),
      el("div", { class: "body" }, parts, say),
      el("footer", {}, foot),
    ],
    "",
  );
  dialog.setAttribute("aria-labelledby", "dialog-title");
  dialog.replaceChildren(built);
  if (!dialog.open) dialog.showModal();
  // Never on the close mark, which the browser would choose
  (starting(built.querySelector(".body")) || starting(built.querySelector("footer")))?.focus();
  return (text) => {
    say.textContent = text;
  };
}

dialog.addEventListener("close", () => {
  // The event comes a moment later: a dialog opened in its place since then stays
  if (dialog.open) return;
  dialog.replaceChildren();
  if (screen !== "app") return;
  draw(false);
  const opener = [...view.querySelectorAll("[data-key]")].find((node) => node.dataset.key === dialog.dataset.opener);
  opener?.focus();
});

// A click on the backdrop, outside the dialog's box, closes it
dialog.addEventListener("click", (event) => {
  if (event.target === dialog) dialog.close();
});

function detail(id) {
  act("/api/detail", { id }, (phone) => {
    const row = S.rows.find((each) => each.id === phone.id);
    const pair = (term, value, cls) => (value ? [el("dt", {}, term), el("dd", { class: cls }, value)] : null);
    submit = null;
    const give = phone.callsign ? "Give another callsign" : "Give a callsign";
    const giving = () => {
      dialog.close();
      giveCallsigns([phone.id]);
    };
    const foot = phone.can_ask_again
      ? [
          second(give, giving),
          main("Ask again", () => {
            dialog.close();
            askAgain([phone.id]);
          }),
        ]
      : [main(give, giving)];
    open(
      phone.callsign || `Phone ${phone.id}`,
      [
        el(
          "dl",
          { class: "facts" },
          pair("Phone", phone.phone ? `${phone.phone}, ID ${phone.id}` : `ID ${phone.id}`),
          pair("Joined", phone.joined),
          [el("dt", {}, "Certificate"), el("dd", { class: tone(row) }, el("span", { class: `dot ${tone(row)}` }), phone.state)],
          pair("The MDM says", phone.detail),
        ),
        el("p", { class: "muted" }, phone.window),
      ],
      foot,
    );
  });
}

// One dialog for naming: numbered or from a list, and the preview the connector makes of it, asked a
// moment after the typing stops. One preview is asked at a time and the last one asked is the one
// shown, as the connector names only its last; Name waits for it, and names exactly what it showed.
function giveCallsigns(ids) {
  const n = ids.length || S.rows.filter((row) => row.waiting).length;
  // The rest of a list kept from last time is where the next callsigns come from
  const naming = { how: S.callsigns.length ? "list" : "numbered", prefix: S.prefix, first: "", list: S.callsigns.join("\n"), save: true };
  let found = null;
  let refused = "";
  let asking = false;
  let again = false;
  let wait = 0;
  const later = (key) => (event) => {
    naming[key] = event.target.value;
    tell("");
    clearTimeout(wait);
    wait = setTimeout(ask, 400);
    shown();
  };
  const prefix = typed({ value: naming.prefix, placeholder: "OTTER", oninput: later("prefix") });
  const first = typed({ inputmode: "numeric", value: "", oninput: later("first") });
  const list = el("textarea", { spellcheck: "false", rows: "6", oninput: later("list") });
  list.value = naming.list;
  const numbered = el("div", { class: "inline" }, el("div", { class: "grow" }, field("prefix", "Prefix", prefix)), el("div", { class: "num" }, field("first", "First number", first)));
  const listed = el("div", {}, field("list", "One callsign per line", list));
  const keep = check("Keep the rest of the list for next time", naming.save, (on) => {
    naming.save = on;
  });
  const switches = ["numbered", "list"].map((how) =>
    el(
      "button",
      {
        type: "button",
        "aria-pressed": "false",
        onclick: () => {
          naming.how = how;
          tell("");
          ask();
        },
      },
      how === "numbered" ? "Numbered" : "From a list",
    ),
  );
  const preview = el("div", { class: "preview-part" });
  const name = () => {
    const names = Object.fromEntries(found.rows.map((row) => [row.id, row.callsign]));
    const { how, list: text, save } = naming;
    act(
      "/api/name",
      { names, how, prefix: naming.prefix, list: text, save },
      () => {
        selected = new Set();
        settling = true;
        dialog.close();
      },
      // The connector may hold another preview, from another tab: ask again, so the dialog shows what it would
      // name; the reason stays until the operator changes something
      (text) => {
        tell(text);
        ask();
      },
    );
  };
  submit = null;
  const button = main("Name", name, true);
  shown();
  const tell = open(
    `Give callsigns to ${count(n, "phone")}`,
    [el("div", { class: "mode", role: "group", "aria-label": "How" }, switches), numbered, listed, preview],
    [keep, second("Cancel", () => dialog.close()), button],
  );

  // What the dialog shows now: the way chosen, and the preview with the button, held back while one is asked
  function shown() {
    const list = naming.how === "list";
    switches.forEach((each, index) => each.setAttribute("aria-pressed", String(index === (list ? 1 : 0))));
    numbered.hidden = list;
    listed.hidden = !list;
    keep.hidden = !list;
    if (found) first.placeholder = String(found.first || "");
    const busy = asking || again || Boolean(wait);
    const rows = found ? found.rows : [];
    const problems = rows.some((row) => row.refusal);
    const cell = (cls, text) => el("td", { class: cls }, text);
    const parts = [
      found || refused ? el("h3", {}, "Preview") : null,
      found
        ? [
            el(
              "div",
              { class: busy ? "table preview settling" : "table preview", "aria-busy": busy ? "true" : null },
              el(
                "table",
                {},
                el(
                  "thead",
                  {},
                  el(
                    "tr",
                    {},
                    el("th", {}, "ID"),
                    el("th", { class: "phone" }, "Phone"),
                    el("th", { class: "joined" }, "Joined"),
                    el("th", {}, "Callsign"),
                    problems ? el("th", { class: "why" }, "Problem") : null,
                  ),
                ),
                el(
                  "tbody",
                  {},
                  rows.map((row) =>
                    el(
                      "tr",
                      { class: row.refusal ? "bad" : "" },
                      cell("", row.id),
                      cell("phone", row.phone),
                      cell("joined", row.joined),
                      cell("will", row.callsign || "—"),
                      problems ? cell("why", row.refusal) : null,
                    ),
                  ),
                ),
              ),
            ),
            found.refusal ? el("p", { class: "error" }, found.refusal) : null,
            found.warning ? el("p", { class: "warn" }, found.warning) : null,
          ]
        : null,
      refused ? el("p", { class: "error" }, refused) : null,
      !found && !refused && busy ? el("p", { class: "muted" }, "…") : null,
    ];
    preview.replaceChildren(...parts.flat(Infinity).filter(Boolean));
    button.textContent = found ? found.button : "Name";
    button.disabled = busy || refused !== "" || !found?.ok;
  }

  async function ask() {
    clearTimeout(wait);
    wait = 0;
    if (!preview.isConnected) return;
    // A numbered batch with neither a prefix nor a first number has nothing typed to preview yet
    if (naming.how === "numbered" && !naming.prefix.trim() && !naming.first.trim()) {
      found = null;
      refused = "";
      shown();
      return;
    }
    if (asking) {
      again = true;
      return;
    }
    asking = true;
    again = false;
    shown();
    try {
      found = await call("/api/preview", { ids, ...naming });
      refused = "";
    } catch (error) {
      if (!error.status || error.status === 403) {
        away(error);
        return;
      }
      found = null;
      refused = error.message;
    } finally {
      asking = false;
    }
    if (again) ask();
    else shown();
  }

  ask();
}

function settings() {
  act("/api/form", { mdm: S.mdm }, (answer) => {
    const said = (each) =>
      each.secret
        ? each.remembered
          ? "kept on this computer"
          : each.kept
            ? "kept until the connector stops"
            : "not given"
        : each.value;
    const change = () => {
      dialog.close();
      flow.change = true;
      chooseMdm(S.mdm);
    };
    submit = null;
    const close = main("Close", () => dialog.close(), false, "plain");
    open(
      "Settings",
      [
        el("h3", { class: "sect" }, `${answer.title} for ${S.active}`),
        el(
          "dl",
          { class: "facts" },
          answer.fields.filter((each) => said(each)).map((each) => [el("dt", {}, each.label), el("dd", {}, said(each))]),
        ),
        second(`Change the ${answer.title} settings`, change),
        el("h3", { class: "sect" }, "Log"),
        el("div", { class: "copyrow" }, copyable(S.log, "Log"), tool("Copy", () => copy(S.log))),
        el("p", { class: "muted" }, `odmdm-connector ${S.version}`),
      ],
      [close],
    );
  });
}

function remove() {
  const host = S.active;
  submit = null;
  const removing = () =>
    act(
      "/api/remove",
      { host },
      () => {
        dialog.close();
        home();
      },
      (text) => tell(text),
    );
  const button = main("Remove", removing, false, "danger");
  const tell = open(
    `Remove Deploy App ${host}?`,
    [
      el(
        "p",
        {},
        "The connector forgets this Deploy App and its MDM token on this computer. Nothing changes in Deploy App, the MDM or the phones.",
      ),
    ],
    [cancel(() => dialog.close()), button],
  );
}

// A settings line from Deploy App's page arrives by message, only from the page that opened this one.
// Once it has, this page lets go of that one: holding it would let this page send it elsewhere.
window.addEventListener("message", (event) => {
  if (!window.opener || event.source !== window.opener || typeof event.data?.add !== "string") return;
  window.opener = null;
  arriving = { line: event.data.add, from: event.origin };
  if (screen === "away") draw(false);
  else if (S && screen) begin();
});

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") tick();
});

// The address opened in a tab that shows the page already changes only its fragment, and reloads nothing
window.addEventListener("hashchange", async () => {
  if (await takeKey()) tick();
});

// Another tab of the page took a key: a tab that waits for one goes on by itself
window.addEventListener("storage", (event) => {
  if (event.key !== KEPT || !event.newValue) return;
  key = event.newValue;
  tick();
});

// The word is no secret: it only tells Deploy App's page that this one listens
window.opener?.postMessage("odmdm-ready", "*");
takeKey().then(tick);
