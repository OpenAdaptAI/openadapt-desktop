// The local task shell: pair this phone, show one decision, relay one answer.
//
// This file renders what the runner sends. It contains no decision model: the
// question text, the evidence rows, the expiry, and the action set all come
// from openadapt-flow's signed task projection and are displayed verbatim.
// ACTION_WIRE below is a name mapping between the portable task vocabulary and
// the engine's own action names -- Flow owns both, and this table mirrors its
// `_ACTION_MAP`. It never adds, removes, or reorders an allowed action.
//
// The operator copy in this file is the shell's own job. Flow deliberately
// sends closed enums and counts and no prose, because a free-text explanation
// field is how protected content escapes a closed contract. Turning those
// enums into sentences therefore happens here, on the phone, from values that
// cannot carry a patient's name.
//
// Every screen is the same card: what OpenAdapt is asking, why it stopped, the
// picture it kept, what happens next, and the finite answers at the end. The
// result replaces the card with a receipt. docs/DECISION_PORTAL.md describes
// the design and the rules each part keeps.

const ACTION_WIRE = {
  verify_and_resume: {
    wire: "continue",
    // Desktop's Needs you uses the same words for the same answer.
    label: "I fixed it. Check and continue",
    brief: "Checks the live screen first. This run only.",
    next: "OpenAdapt re-reads the live screen and continues only if it can",
    consequence:
      "OpenAdapt re-reads the live screen right now and continues only if its " +
      "checks pass. The saved workflow is not changed, so the same drift will " +
      "stop the next run too.",
  },
  skip: {
    wire: "skip",
    label: "Skip this step",
    brief: "Leaves this step undone. The workflow allows it.",
    next: "OpenAdapt leaves this step undone and continues only if it can",
    consequence:
      "The workflow continues without this step. Offered only when the " +
      "workflow itself declares the step skippable.",
  },
  reject: {
    wire: "reject",
    // The disagreement answer. It is about THIS RUN -- not about the saved
    // workflow, which is what "Show OpenAdapt the right way" is for.
    label: "Stop this run",
    brief: "Ends this run. Nothing else is touched.",
    consequence:
      "Ends this run now. Nothing in the application is touched and nothing " +
      "continues. Use it when you have looked at the live application and " +
      "OpenAdapt was right to stop. The saved workflow is not changed, and " +
      "this run cannot be resumed afterwards.",
  },
  teach: {
    wire: "teach",
    label: "Show OpenAdapt the right way",
    brief: "Changes future runs. This run stays paused.",
    consequence:
      "Records that this drift needs a corrective demonstration. Once someone " +
      "records it, future runs handle this on their own. Nothing continues " +
      "right now, and the run stays paused.",
  },
  escalate: {
    wire: "escalate",
    label: "Hand this to someone else",
    brief: "The run stays paused where it is.",
    consequence:
      "Passes the decision on. The run stays paused exactly where it is and " +
      "nothing in the application is touched.",
  },
  reconcile: {
    // Reconciliation is not a retry. The runner looks for the declared
    // persisted effect of the earlier delivery and reports what it finds. It
    // never dispatches that earlier action again from this decision path.
    wire: "reconcile",
    label: "Check what happened",
    brief: "Looks for the saved result. Never sends it again.",
    next: "OpenAdapt does not send the earlier action again. It checks the live application and continues only if it can",
    consequence:
      "OpenAdapt checks whether the earlier action already changed the system " +
      "of record. It does not send that action again. If it cannot prove the " +
      "result, the run stays paused for reconciliation.",
  },
};

const DISPOSITION = {
  continue: "completed_by_operator",
  skip: "not_applicable",
  reject: "rejected_by_operator",
  teach: "teach_requested",
  escalate: "needs_assistance",
  reconcile: "reconcile_requested",
};

// ------------------------------------------------------------- emphasis
//
// One answer may be drawn filled. Which one is a claim, so the rule is narrow
// and stated here rather than inherited from list order:
//
//   The emphasised answer is the one answer that lets the work go on, and only
//   when OpenAdapt re-checks the live application before anything happens.
//
// `continue`, `skip` (offered only when the workflow declares the step
// optional) and `reconcile` (which never resends) are those answers; each one
// stops again and changes nothing if its checks fail. Stop, teach and hand-on
// never continue anything, so they are never filled. Two rules turn emphasis
// off entirely:
//
//   * an irreversible step. A wrong "continue" there may be uncatchable, so the
//     page shows the stakes and no answer looks like the default;
//   * more than one continuing answer. The shell would be choosing between
//     them, which is not its job.
//
// This matches Desktop's Needs you, which marks "I fixed it. Check and
// continue" and "It saved. Mark it done" as primary. Earlier versions of this
// shell emphasised nothing; set EMPHASIS_ENABLED to false to return to that.
const EMPHASIS_ENABLED = true;
const CONTINUING = new Set(["verify_and_resume", "skip", "reconcile"]);

function primaryAction(task, actions) {
  if (!EMPHASIS_ENABLED || !task || task.risk_class === "irreversible") return null;
  const continuing = actions.filter((action) => CONTINUING.has(action));
  return continuing.length === 1 ? continuing[0] : null;
}

// -------------------------------------------------- the terminal receipt shape
//
// Flow returns a closed `HumanDecisionReceiptV1`: a `state` and a `reason_code`
// from fixed enums, and deliberately NO message -- prose from the runner could
// carry an operator name, an exception string, or an observed value. The phone
// therefore owns the wording, and this is the only place it is decided.
//
// Every (state, reason_code) pair Flow can emit needs an entry. A pair with no
// entry falls through to an explicitly unknown outcome rather than to a
// refusal: rendering a real `halted` as "that decision was refused" is exactly
// the defect this table exists to prevent.

const RECEIPT_COPY = {
  "completed/verified_and_resumed": {
    label: "Checks passed",
    title: "Checked and continued",
    tone: "success",
    text: "OpenAdapt re-read the live screen, its checks passed, and the workflow moved on.",
    terminal: true,
  },
  "completed/skipped_and_resumed": {
    label: "Step skipped",
    title: "Skipped and continued",
    tone: "neutral",
    text: "The step was left undone and the workflow continued from the next step.",
    terminal: true,
  },
  "completed/reconciled_and_resumed": {
    label: "Result proved",
    title: "It had already saved",
    tone: "success",
    text:
      "OpenAdapt proved the earlier action already changed the system of " +
      "record and continued. It did not send that action again.",
    terminal: true,
    reconciliation: true,
  },
  "halted/continuation_halted": {
    label: "Paused again",
    title: "It stopped again further on",
    tone: "halted",
    text:
      "OpenAdapt continued and then stopped at a later step. Nothing was " +
      "guessed. A new decision appears here if it needs you.",
    terminal: true,
  },
  "refused/revalidation_refused": {
    label: "Check did not pass",
    title: "The live screen still is not ready",
    tone: "halted",
    text:
      "OpenAdapt re-read the live screen and did nothing. Fix it in the " +
      "application, then answer again.",
    terminal: false,
  },
  "expired/expired": {
    label: "Expired",
    icon: "expired",
    title: "This request expired",
    tone: "neutral",
    text:
      "It expired before the computer acted on it. Nothing was done. If the " +
      "run is still paused, a new request appears here.",
    terminal: true,
  },
  "delivery_uncertain/delivery_uncertain": {
    label: "Result uncertain",
    title: "Do not answer again",
    tone: "uncertain",
    text:
      "This may already have been sent. Check the record and reconcile it on " +
      "the computer running OpenAdapt.",
    terminal: true,
  },
  "accepted_pending_runner/pending_runner": {
    label: "Checking",
    title: "Checking the live screen",
    tone: "pending",
    text: "Your answer reached the computer. It is checking the live screen now.",
    terminal: false,
    pending: true,
  },
  "demonstration_requested/demonstration_requested": {
    label: "Correction requested",
    title: "Saved as a correction to teach",
    tone: "teach",
    text:
      "The run stays paused until someone records the right way on the " +
      "computer. Future runs then handle this on their own.",
    terminal: true,
  },
  "escalated/escalation_recorded": {
    label: "Handed on",
    title: "Someone else will pick this up",
    tone: "teach",
    text: "The run stays paused exactly where it is. Nothing in the application was touched.",
    terminal: true,
  },
  // Deliberately not worded like the hand-on above. That one says someone
  // will pick this up; this one says nobody will, because there is no longer a
  // run to pick up. An operator told the wrong one of those acts on it.
  "rejected/rejected_by_operator": {
    label: "Run stopped",
    title: "This run is over",
    tone: "halted",
    text:
      "It cannot be resumed, and nothing in the application was touched. " +
      "Start it again on the computer if it should be attempted.",
    terminal: true,
  },
};

// Flow's own `_RECEIPT_STATE`: engine decision status -> receipt state. Kept so
// this shell renders a runner that has not yet adopted the closed receipt
// exactly as it renders one that has, instead of treating the older reply as a
// refusal.
const LEGACY_RECEIPT_STATE = {
  prepared: ["accepted_pending_runner", "pending_runner"],
  delivery_started: ["delivery_uncertain", "delivery_uncertain"],
  delivery_uncertain: ["delivery_uncertain", "delivery_uncertain"],
  completed: ["completed", "verified_and_resumed"],
  refused: ["refused", "revalidation_refused"],
  halted: ["halted", "continuation_halted"],
  needs_demonstration: ["demonstration_requested", "demonstration_requested"],
  escalated: ["escalated", "escalation_recorded"],
  rejected: ["rejected", "rejected_by_operator"],
};

// ------------------------------------------------- describing the halted step
//
// Flow sends `presentation.halt`: a typed category, the step's ordinal, its
// action kind, the target's role, and the target's label ONLY when the engine
// proved that label is static control chrome rather than record content. When
// it is withheld, these nouns still name the SHAPE, so "the field where you
// typed <value>" becomes "the text field" instead of leaking.

const ROLE_NOUN = {
  button: "button",
  checkbox: "checkbox",
  combobox: "dropdown",
  edit: "text field",
  hyperlink: "link",
  link: "link",
  listbox: "list",
  menubutton: "menu button",
  menuitem: "menu item",
  menuitemcheckbox: "menu item",
  menuitemradio: "menu item",
  radio: "option",
  radiobutton: "option",
  searchbox: "search box",
  slider: "slider",
  spinbutton: "number field",
  splitbutton: "button",
  switch: "switch",
  tab: "tab",
  tabitem: "tab",
  textbox: "text field",
  togglebutton: "switch",
  article: "panel",
  cell: "table cell",
  columnheader: "column heading",
  dataitem: "row",
  document: "page",
  grid: "table",
  gridcell: "table cell",
  group: "section",
  heading: "heading",
  image: "image",
  img: "image",
  list: "list",
  listitem: "list item",
  menu: "menu",
  option: "list option",
  region: "section",
  row: "row",
  rowheader: "row heading",
  table: "table",
  text: "text",
  tree: "tree",
  treeitem: "tree item",
};

const ACTION_VERB = {
  click: "click",
  double_click: "double-click",
  right_click: "right-click",
  drag: "drag",
  type: "type into",
  key: "press a key in",
  hotkey: "press a shortcut in",
  scroll: "scroll",
  wait: "wait for",
};

const RUNG_NOUN = {
  structural: "the page's own element identity",
  template: "the recorded picture of the target",
  template_global: "that picture anywhere on the screen",
  ocr: "the target's text label",
  geometry: "its position next to nearby text",
  grounder: "the configured fallback locator",
};

const RUNG_VERDICT = {
  resolved: "found it",
  failed: "did not find it",
  not_attempted: "nothing recorded to try",
  not_reached: "not needed",
  unavailable: "not available on this screen",
  unknown: "no result recorded",
};

const RECHECK_NOUN = {
  target_resolution: "find the target again on the live screen",
  record_identity: "confirm the record on screen is the intended one",
  postconditions: "confirm the screen reaches the expected state",
  system_of_record_effects: "confirm the result in the system of record",
  delivery_reconciliation: "reconcile an action that may already have been sent",
};

// ------------------------------------------------------------------- stakes
//
// Flow computes `risk_class` and seals it into the signed task. It is the one
// field that says what a wrong answer costs, so it goes above the question.
// Only `irreversible` and `consequential` are rendered. `unknown` is the
// majority case, and a row that says nothing on most halts is the mechanism by
// which its neighbours become ignorable too.

const RISK_COPY = {
  irreversible: {
    title: "This cannot be undone",
    body: "The next step writes something that cannot be taken back. If you are not certain, hand it on.",
  },
  consequential: {
    title: "This writes to the system of record",
    body: "The result is proved afterwards, not assumed.",
  },
};

const TASK_KIND_COPY = {
  identity: "Record identity",
  effect: "Saved-result check",
  ambiguity: "Target ambiguity",
  human_step: "Needs a person",
  delivery_uncertain: "May have been sent",
  halt: "Workflow pause",
  operator_review: "Operator review",
};

// The list row only carries Flow's closed category, so name it in words.
const CATEGORY_COPY = {
  identity: "Record identity",
  disambiguation: "Target ambiguity",
  resolution: "Target not found",
  ambiguity: "Target ambiguity",
  human_required: "Needs a person",
  human_step: "Needs a person",
  effect: "Saved-result check",
  delivery_uncertain: "May have been sent",
  halt: "Workflow pause",
};

// --------------------------------------------------- what the engine cannot do
//
// The engine can re-prove its own postconditions, identity contract, and effect
// contract. It cannot observe whether a person looked. The short form of this
// sits under the picture, on the reading path; the full form is in Details.

const LIMIT_COPY =
  "OpenAdapt re-checks what it can measure and stops again if that fails. It " +
  "cannot check whether you actually looked. Answer from the live " +
  "application, not from this page.";

const ICON = {
  success: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  halted: '<rect x="7" y="7" width="10" height="10" rx="1.5"/>',
  uncertain: '<path d="M12 6.5v7"/><path d="M12 17.2v.3"/>',
  teach: '<path d="M7 17L17 7"/><path d="M9 7h8v8"/>',
  neutral: '<path d="M6.5 12h11"/>',
  pending: '<circle cx="12" cy="12" r="6.5"/><path d="M12 8.5V12l2.5 1.5"/>',
  expired: '<circle cx="12" cy="12" r="6.5"/><path d="M12 8.5V12l2.5 1.5"/>',
  superseded: '<path d="M18 9a6.5 6.5 0 1 0 .5 5"/><path d="M18.5 5v4h-4"/>',
};

function icon(name) {
  return (
    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" ` +
    `stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">` +
    `${ICON[name] || ICON.neutral}</svg>`
  );
}

const main = document.getElementById("main");
const actionBar = document.getElementById("actions");
const deviceLabel = document.getElementById("device");
const linkStatus = document.getElementById("link");
const announcer = document.getElementById("announce");

// Session-scoped storage only: the credential dies with the tab rather than
// persisting on a personal phone.
const store = window.sessionStorage;

function esc(value) {
  return String(value == null ? "" : value).replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
}

function announce(text) {
  if (announcer) announcer.textContent = text;
}

function session() {
  return { token: store.getItem("portal_token"), csrf: store.getItem("portal_csrf") };
}

async function api(path, options = {}) {
  const { token, csrf } = session();
  const headers = Object.assign({ Accept: "application/json" }, options.headers || {});
  if (token) headers.Authorization = `Bearer ${token}`;
  if (options.method === "POST") {
    headers["Content-Type"] = "application/json";
    if (csrf) headers["X-OpenAdapt-Portal-CSRF"] = csrf;
  }
  let response;
  try {
    response = await fetch(path, {
      ...options,
      headers,
      cache: "no-store",
      credentials: "omit",
      redirect: "error",
    });
  } catch (error) {
    // The request left the phone and the answer did not come back. That is
    // status 0: uncertain, never failed and never retried on its own.
    return { status: 0, body: null };
  }
  let body = null;
  try {
    body = await response.json();
  } catch (error) {
    body = null;
  }
  return { status: response.status, body };
}

// ------------------------------------------------------------- connection
//
// Offline is shown, and it closes the answers: a tap that cannot reach the
// computer is not an answer. Nothing is queued to send later.

function isOffline() {
  return typeof navigator !== "undefined" && navigator.onLine === false;
}

function syncConnection() {
  const offline = isOffline();
  if (linkStatus) linkStatus.textContent = offline ? "Offline" : "";
  document.body.classList.toggle("offline", offline);
  actionBar.querySelectorAll("[data-action]").forEach((button) => {
    if (button.dataset.locked === "true") return;
    button.disabled = offline;
  });
  const note = document.getElementById("offline-note");
  if (note) note.hidden = !offline;
}

window.addEventListener("online", syncConnection);
window.addEventListener("offline", syncConnection);

// ------------------------------------------------------------------ pairing

function screen(html) {
  render(`<section class="center">${html}</section>`);
}

async function claimPairing(secret) {
  // Remove the secret from the address bar before anything else can read it
  // from history, a screenshot, or a shared tab.
  history.replaceState(null, "", "/");
  let response;
  try {
    response = await fetch("/api/portal/pair/claim", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ secret, device_label: "Phone" }),
      cache: "no-store",
      credentials: "omit",
      redirect: "error",
    });
  } catch (error) {
    response = null;
  }
  const body = response ? await response.json().catch(() => null) : null;
  if (!response || response.status !== 200 || !body || !body.session_token) {
    screen(
      `<h1 class="page-title" tabindex="-1">Pairing did not work</h1>
       <p class="lede">${esc((body && body.message) || "Show a new code on the computer running OpenAdapt, then scan it again.")}</p>`,
    );
    return;
  }
  store.setItem("portal_token", body.session_token);
  store.setItem("portal_csrf", body.csrf_token);
  screen(
    `<h1 class="page-title" tabindex="-1">Type this code on the computer</h1>
     <p class="lede">Only this phone can see it. Typing it there proves this is
     your phone.</p>
     <p class="code" aria-label="Code ${esc(String(body.confirm_code || "").split("").join(" "))}">${esc(body.confirm_code)}</p>
     <p class="waiting" id="wait">Waiting for the computer</p>`,
  );
  awaitApproval();
}

async function awaitApproval() {
  for (let attempt = 0; attempt < 150; attempt += 1) {
    const { status } = await api("/api/portal/session");
    if (status === 200) {
      route();
      return;
    }
    if (status !== 202 && status !== 0) break;
    await new Promise((resolve) => setTimeout(resolve, 2000));
  }
  screen(
    `<h1 class="page-title" tabindex="-1">Not approved</h1>
     <p class="lede">This phone was not approved in time. Show a new code on
     the computer running OpenAdapt and scan it again.</p>`,
  );
}

// -------------------------------------------------------------------- views

let frameUrl = null;

function releaseFrame() {
  // A protected crop is held only while its decision is on screen.
  if (frameUrl) URL.revokeObjectURL(frameUrl);
  frameUrl = null;
}

window.addEventListener("pagehide", releaseFrame);

// The first screen keeps the browser's own focus. Every later screen moves
// focus to its heading, so a screen reader starts at the new question.
let firstScreen = true;

function render(html) {
  releaseFrame();
  clearExpiryTimer();
  main.innerHTML = html;
  hideActions();
  const heading = main.querySelector("h1");
  if (heading && !firstScreen) heading.focus({ preventScroll: true });
  if (heading) announce(heading.textContent.trim());
  firstScreen = false;
}

function hideActions() {
  actionBar.hidden = true;
  actionBar.innerHTML = "";
}

function unavailable(body, status) {
  const offline = status === 0;
  render(
    `<section class="center">
       <h1 class="page-title" tabindex="-1">${offline ? "Cannot reach OpenAdapt" : "Not available right now"}</h1>
       <p class="lede">${
         offline
           ? "This phone cannot reach the computer running OpenAdapt. Check the connection. Nothing was sent."
           : esc((body && body.message) || "The local OpenAdapt decision service is not reachable.")
       }</p>
       <button class="quiet wide" id="retry" type="button">Try again</button>
     </section>`,
  );
  document.getElementById("retry").addEventListener("click", route);
}

let autoOpened = false;

async function showList() {
  const { status, body } = await api("/api/portal/tasks");
  if (status === 401 || status === 202) return startPairing();
  if (status !== 200 || !Array.isArray(body)) return unavailable(body, status);
  if (body.length === 0) {
    screen(`<h1 class="page-title" tabindex="-1">Nothing needs you</h1>
      <p class="lede">OpenAdapt has no open decisions on this computer. When a
      run stops and asks, the question appears here.</p>
      <button class="quiet wide" id="retry" type="button">Check again</button>`);
    document.getElementById("retry").addEventListener("click", showList);
    return;
  }
  // One open decision: go straight to it. The back link still reaches the list.
  if (body.length === 1 && !autoOpened) {
    autoOpened = true;
    return showTask(body[0].id);
  }
  autoOpened = true;
  const rows = body
    .map(
      (item) => `<li><button class="row" type="button" data-run="${esc(item.id)}">
        <span class="row-title">${esc(item.headline || "OpenAdapt needs a decision")}</span>
        <span class="row-meta">${esc(CATEGORY_COPY[item.category] || "Decision")}</span>
      </button></li>`,
    )
    .join("");
  render(`<h1 class="page-title" tabindex="-1">Needs you</h1>
    <p class="lede">${body.length} open decisions. Each one is answered on its own.</p>
    <ul class="rows">${rows}</ul>`);
  main.querySelectorAll("[data-run]").forEach((node) => {
    node.addEventListener("click", () => showTask(node.dataset.run));
  });
}

function evidenceRows(task) {
  if (!task || !task.evidence) return "";
  const e = task.evidence;
  const pairs = [
    ["Identity signals confirmed", e.identity_confirmed_count, e.identity_required_count],
    ["Result signals confirmed", e.effect_confirmed_count, e.effect_required_count],
    ["Result tier observed", e.observed_effect_tier, e.minimum_effect_tier],
  ];
  const rendered = pairs
    .filter(([, got, need]) => got != null || need != null)
    .map(
      ([label, got, need]) =>
        `<li><span>${esc(label)}</span><strong>${esc(got == null ? "—" : got)} of ${esc(
          need == null ? "—" : need,
        )}</strong></li>`,
    )
    .join("");
  return rendered ? `<h3>What OpenAdapt confirmed</h3><ul>${rendered}</ul>` : "";
}

function describeTarget(halt) {
  const noun = ROLE_NOUN[halt.target_role] || "target";
  // A label is present only when Flow proved it is the control's own static
  // label. Otherwise the noun alone is the whole description, on purpose.
  return halt.target_label ? `the ${noun} labelled “${halt.target_label}”` : `the ${noun}`;
}

function stepWhere(halt) {
  return halt && halt.step_ordinal && halt.step_count
    ? `Step ${halt.step_ordinal} of ${halt.step_count}`
    : "";
}

function describeStop(halt) {
  if (!halt) return "";
  const where = stepWhere(halt) || "This step";
  const target = describeTarget(halt);
  const verb = ACTION_VERB[halt.action_kind] || "act on";
  switch (halt.category) {
    case "resolution":
      return `${where} could not start: OpenAdapt could not find ${target} on the screen, so it did not ${verb} anything.`;
    case "disambiguation":
      return `${where} found more than one thing that looked like ${target}, so it refused to pick one.`;
    case "identity":
      return `${where} could not confirm that the record on screen is the intended one, so it did not ${verb} ${target}.`;
    case "human_required":
      return `${where} needs a person: complete the sign-in, challenge, or verification in the live application yourself.`;
    case "unmet_guard":
    case "postcondition":
      return `${where} did not leave the application in the state the workflow expects after it tried to ${verb} ${target}.`;
    case "effect_refuted":
    case "effect_indeterminate":
    case "effect_escalated":
    case "effect_unverifiable":
    case "placeholder_effect":
      return `${where} acted, but the result could not be confirmed in the system of record.`;
    default:
      return `${where} stopped instead of guessing.`;
  }
}

// The same facts as describeStop, without the step number: the eyebrow above
// the question already says which step it is.
function whyLine(halt) {
  if (!halt) return "";
  const target = describeTarget(halt);
  const verb = ACTION_VERB[halt.action_kind] || "act on";
  switch (halt.category) {
    case "resolution":
      return `OpenAdapt could not find ${target} on the screen, so it did not ${verb} anything.`;
    case "disambiguation":
      return `More than one thing looked like ${target}, so OpenAdapt did not pick one.`;
    case "identity":
      return `OpenAdapt could not confirm that the record on screen is the intended one, so it did not ${verb} ${target}.`;
    case "human_required":
      return "The application is asking for a person: a sign-in, a challenge, or a verification. Complete it in the live application yourself.";
    case "unmet_guard":
    case "postcondition":
      return `After it tried to ${verb} ${target}, the application was not in the state the workflow expects.`;
    case "effect_refuted":
    case "effect_indeterminate":
    case "effect_escalated":
    case "effect_unverifiable":
    case "placeholder_effect":
      return "OpenAdapt acted, but could not confirm the result in the system of record.";
    default:
      return "OpenAdapt stopped instead of guessing.";
  }
}

function ladderRows(halt) {
  const rows = (halt && halt.resolution_ladder) || [];
  const tried = rows.filter((row) => row.verdict !== "not_attempted");
  if (tried.length === 0) return "";
  const items = tried
    .map(
      (row) =>
        `<li><span>${esc(RUNG_NOUN[row.rung] || row.rung)}</span>
         <strong>${esc(RUNG_VERDICT[row.verdict] || row.verdict)}</strong></li>`,
    )
    .join("");
  return `<h3>What OpenAdapt tried</h3><ul>${items}</ul>`;
}

function recheckItems(halt) {
  const checks = (halt && halt.will_recheck) || [];
  return checks
    .map((row) => {
      const noun = RECHECK_NOUN[row.check];
      if (!noun) return "";
      const count = typeof row.count === "number" ? ` (${row.count})` : "";
      return `<li>${esc(noun)}${esc(count)}</li>`;
    })
    .filter(Boolean)
    .join("");
}

// What happens next, in one block. Every decision kind says it the same way:
// the answer it is about, what OpenAdapt will check, and what it does if a
// check fails.
function nextMarkup({ lead, items = "", after = "" }) {
  return `<section class="next" aria-labelledby="next-h">
      <h2 id="next-h">What happens next</h2>
      <p>${lead}</p>
      ${items ? `<ul>${items}</ul>` : ""}
      ${after ? `<p class="after">${after}</p>` : ""}
    </section>`;
}

// For a halt, the block is about the one answer that continues the work.
// `will_recheck` is the list the engine re-proves, so "I fixed it" is visibly
// not a repeat-the-step button.
function nextBlock(halt, task, actions) {
  const continuing = actions.filter((action) => CONTINUING.has(action));
  const lead = continuing.length === 1 ? ACTION_WIRE[continuing[0]] : null;
  const items = recheckItems(halt);
  if (!lead) {
    return nextMarkup({ lead: "Nothing continues until you answer. Each answer says what it does." });
  }
  const opening = `If you choose “${esc(lead.label)}”, ${esc(lead.next)}`;
  if (!items) {
    return nextMarkup({
      lead: `${opening} confirm the live state.`,
      after: "Otherwise it stops again and changes nothing.",
    });
  }
  return nextMarkup({
    lead: `${opening}:`,
    items,
    after: "If any of these fail, it stops again and changes nothing.",
  });
}

function stakesBlock(task) {
  const copy = RISK_COPY[task && task.risk_class];
  if (!copy) return "";
  return `<p class="stakes"><strong>${esc(copy.title)}</strong><span>${esc(copy.body)}</span></p>`;
}

// "about 14 minutes" / "about 3 hours" / "about 2 days". Deliberately coarse:
// the point is that the picture is old, not exactly how old, and a precise
// figure invites the reader to treat it as a live reading.
function describeAge(iso) {
  const then = Date.parse(iso || "");
  if (!Number.isFinite(then)) return "";
  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 0) return "";
  if (seconds < 90) return "less than a minute";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `about ${minutes} minutes`;
  const hours = Math.round(minutes / 60);
  if (hours < 36) return `about ${hours} hour${hours === 1 ? "" : "s"}`;
  return `about ${Math.round(hours / 24)} days`;
}

function clockTime(ms) {
  try {
    return new Date(ms).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  } catch (error) {
    return "";
  }
}

// Expiry is shown and enforced on the page as presentation only: the runner
// refuses an expired capability whatever this page does. Showing it here means
// the operator is told before they tap rather than after.
function expiryOf(task) {
  const at = Date.parse((task && task.expires_at) || "");
  return Number.isFinite(at) ? at : null;
}

function describeRemaining(at) {
  const minutes = Math.round((at - Date.now()) / 60000);
  if (minutes < 1) return "less than a minute";
  if (minutes < 90) return `${minutes} minute${minutes === 1 ? "" : "s"}`;
  const hours = Math.round(minutes / 60);
  if (hours < 36) return `${hours} hours`;
  return `${Math.round(hours / 24)} days`;
}

function expiryNote(task) {
  const at = expiryOf(task);
  return at == null ? "" : ` · closes in ${esc(describeRemaining(at))}`;
}

let expiryTimer = null;

function clearExpiryTimer() {
  if (expiryTimer) clearTimeout(expiryTimer);
  expiryTimer = null;
}

// The frame is a RE-ENTRY CUE, not evidence. This operator was not doing this
// task when it stopped, so the picture is the only orientation the interface
// offers -- which is why it stays. It is named as history, says how old it is,
// and points at the live application. There is deliberately NO refresh
// control: the runner does not re-observe on demand, and a refresh that
// re-requested this same stored artifact would manufacture an illusion of
// liveness.
function frameBlock(task, presentation) {
  const frameId = presentation.after_artifact_id || presentation.before_artifact_id;
  if (!frameId) return "";
  const age = describeAge(task && task.created_at);
  const when = age ? `${age} ago` : "when it stopped";
  return `<figure class="evidence" id="evidence">
      <button class="evidence-close" type="button" id="evidence-close">Close</button>
      <button class="evidence-open" type="button" id="evidence-open" aria-expanded="false"
              aria-label="Enlarge the screen OpenAdapt kept when it stopped">
        <img id="frame" alt="The screen OpenAdapt kept when it stopped, ${esc(when)}. Not live."
             data-artifact="${esc(frameId)}">
        <span class="evidence-hint" aria-hidden="true">Tap to zoom</span>
      </button>
      <figcaption>The screen when OpenAdapt stopped, ${esc(when)}. It is not
      live: answer from the application itself.</figcaption>
    </figure>`;
}

function wireEvidence() {
  const figure = document.getElementById("evidence");
  if (!figure) return;
  const open = document.getElementById("evidence-open");
  const close = document.getElementById("evidence-close");
  const set = (zoomed) => {
    figure.classList.toggle("zoomed", zoomed);
    open.setAttribute("aria-expanded", String(zoomed));
    open.setAttribute(
      "aria-label",
      zoomed ? "Shrink the screen OpenAdapt kept" : "Enlarge the screen OpenAdapt kept when it stopped",
    );
    (zoomed ? close : open).focus();
  };
  open.addEventListener("click", () => set(!figure.classList.contains("zoomed")));
  close.addEventListener("click", () => set(false));
  figure.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && figure.classList.contains("zoomed")) set(false);
  });
}

function consequenceBlock(actions) {
  const items = actions
    .map(
      (action) =>
        `<li><strong>${esc(ACTION_WIRE[action].label)}</strong>
         ${esc(ACTION_WIRE[action].consequence)}</li>`,
    )
    .join("");
  if (!items) return "";
  return `<h3>What each answer does</h3><ul class="effects">${items}</ul>`;
}

// Everything an operator may want and does not need in order to answer. It
// sits after the answers: each answer already carries its own consequence.
function detailsBlock(task, presentation, halt, actions, why) {
  const stopped = describeStop(halt);
  const expiresAt = expiryOf(task);
  return `<details class="more">
      <summary>Details</summary>
      ${stopped ? `<p>${esc(stopped)}</p>` : ""}
      ${presentation.explanation && presentation.explanation !== why ? `<p>${esc(presentation.explanation)}</p>` : ""}
      ${ladderRows(halt)}
      ${evidenceRows(task)}
      ${consequenceBlock(actions)}
      ${
        expiresAt != null
          ? `<h3>Expiry</h3><p>This request is valid until ${esc(clockTime(expiresAt))} (${esc(task.expires_at)}).</p>`
          : ""
      }
      <h3>What OpenAdapt cannot check</h3>
      <p class="limit">${esc(LIMIT_COPY)}</p>
    </details>`;
}

function actionIsValidForTask(action, task) {
  // Types v0.7 closes this too, but preserve the boundary in Desktop: an old
  // or malformed runner must not turn a definitely un-sent action into a
  // reconciliation UI. The signed task remains the source of authority; this
  // is a presentation refusal, never an addition to its action set.
  return (
    action !== "reconcile" ||
    (task.task_kind === "delivery_uncertain" &&
      ["delivered", "unknown"].includes(task.delivery_state))
  );
}

function permittedActions(task) {
  if (!task || !Array.isArray(task.allowed_actions)) return [];
  return task.allowed_actions.filter(
    (action) => ACTION_WIRE[action] && actionIsValidForTask(action, task),
  );
}

// The one card every decision uses: what is being asked, why, the picture,
// what happens next. The answers follow it (answerMarkup), and a result
// replaces it (resultMarkup).
function cardMarkup({ eyebrow = [], stakes = "", question, why, evidence = "", next = "" }) {
  const crumbs = eyebrow.filter(Boolean).map((part) => `<span>${esc(part)}</span>`).join("");
  return `
    <button class="back" id="back" type="button">← All decisions</button>
    <div id="decision">
      <p class="eyebrow">${crumbs}</p>
      ${stakes}
      <h1 class="question" tabindex="-1">${esc(question)}</h1>
      ${why ? `<p class="why">${esc(why)}</p>` : ""}
      ${evidence}
      ${next}
    </div>
    <section class="notice" id="outcome" role="status" hidden><strong></strong><span></span><small></small></section>`;
}

// `answers` is [{ attr, id, label, brief, primary }]. `attr` names what the
// button carries: a signed halt action (`data-action`) or a finite option.
function answerMarkup(answers, { heading = "Your answer", details = "" } = {}) {
  const buttons = answers
    .map(
      (answer) =>
        `<button type="button" class="answer${answer.primary ? " primary" : ""}" ${answer.attr}="${esc(answer.id)}">
           <span class="label">${esc(answer.label)}</span>
           ${answer.brief ? `<span class="brief">${esc(answer.brief)}</span>` : ""}
         </button>`,
    )
    .join("");
  return (
    `<h2 class="answers-label" id="answers-h">${heading}</h2>` +
    `<p class="notice pending" id="offline-note" hidden><strong>Offline</strong>` +
    `<span>Answers are off until this phone reconnects. Nothing is queued.</span></p>` +
    buttons +
    details
  );
}

// `rows` values are already-escaped HTML.
function resultMarkup({ tone = "neutral", mark, label, title, text, rows = [], done = "Back to decisions" }) {
  return `
    <span class="mark">${icon(mark || tone)}</span>
    <p class="result-kicker">${esc(label)}</p>
    <h1 tabindex="-1">${esc(title || label)}</h1>
    <p>${esc(text)}</p>
    ${
      rows.length
        ? `<dl class="receipt">${rows.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${v}</dd></div>`).join("")}</dl>`
        : ""
    }
    <button class="quiet wide" id="done" type="button">${esc(done)}</button>`;
}

let current = null;

async function showTask(runId) {
  const { status, body } = await api(`/api/portal/tasks/${encodeURIComponent(runId)}`);
  if (status === 401 || status === 202) return startPairing();
  if (status === 404) return showClosed();
  if (status !== 200 || !body) return unavailable(body, status);
  const task = body.task;
  const presentation = body.presentation || {};
  const halt = presentation.halt;
  const actions = permittedActions(task);
  const kind = TASK_KIND_COPY[task && task.task_kind] || "Decision";
  const where = stepWhere(halt);
  const why = whyLine(halt) || presentation.explanation || "OpenAdapt stopped instead of guessing.";
  const expiresAt = expiryOf(task);
  const expired = expiresAt != null && expiresAt <= Date.now();
  if (expired) return showExpiredRequest(task);
  current = { runId, detail: body, actions, details: detailsBlock(task, presentation, halt, actions, why) };
  render(
    cardMarkup({
      eyebrow: [kind, where],
      stakes: stakesBlock(task),
      question: presentation.question || "Review the live application.",
      why,
      evidence: frameBlock(task, presentation),
      next: nextBlock(halt, task, actions),
    }),
  );
  document.getElementById("back").addEventListener("click", showList);
  const frame = document.getElementById("frame");
  if (frame) loadFrame(runId, frame);
  wireEvidence();
  renderActions(runId, body, actions);
  if (expiresAt != null) {
    const wait = Math.min(expiresAt - Date.now(), 2147483000);
    expiryTimer = setTimeout(() => showExpiredRequest(task), wait);
  }
}

function showExpiredRequest(task) {
  const at = expiryOf(task);
  render(
    `<section class="result neutral terminal" id="outcome">${resultMarkup({
      mark: "expired",
      label: "Expired",
      title: "This request expired",
      text:
        `It closed${at ? ` at ${clockTime(at)}` : ""} and can no longer be answered. ` +
        "Nothing was sent. If the run is still paused, OpenAdapt issues a new request.",
      done: "Check for a new request",
    })}</section>`,
  );
  document.getElementById("done").addEventListener("click", () => {
    autoOpened = false;
    showList();
  });
}

async function loadFrame(runId, image) {
  // Held as a blob only in memory for this view; the response is no-store and
  // the service worker never sees a cacheable path.
  const { token } = session();
  let response;
  try {
    response = await fetch(
      `/api/portal/tasks/${encodeURIComponent(runId)}/evidence?id=${encodeURIComponent(
        image.dataset.artifact,
      )}`,
      { headers: { Authorization: `Bearer ${token}` }, cache: "no-store", credentials: "omit" },
    );
  } catch (error) {
    return;
  }
  if (!response.ok || !image.isConnected) return;
  releaseFrame();
  frameUrl = URL.createObjectURL(await response.blob());
  image.src = frameUrl;
}

// -------------------------------------------------------------- the answers
//
// The answers are in the document flow, after everything the engine had to
// say, so reaching them means passing the question, the picture and what
// happens next. On a short decision they are on screen at once; on a long one
// they are further down in proportion to the evidence. There is no shortcut
// past the content and no fixed bar covering it.

function renderActions(runId, detail, actions) {
  if (actions.length === 0) {
    hideActions();
    return;
  }
  const primary = primaryAction(detail.task, actions);
  // Each button carries its consequence, because the answers differ in
  // whether the saved workflow changes and in what becomes of the run, and
  // neither is inferable from a name alone.
  actionBar.innerHTML = answerMarkup(
    actions.map((action) => ({
      attr: "data-action",
      id: action,
      label: ACTION_WIRE[action].label,
      brief: ACTION_WIRE[action].brief,
      primary: action === primary,
    })),
    {
      heading: `Your answer${expiryNote(detail.task)}`,
      details: (current && current.details) || "",
    },
  );
  actionBar.setAttribute("aria-labelledby", "answers-h");
  actionBar.hidden = false;
  actionBar.querySelectorAll("[data-action]").forEach((node) => {
    node.addEventListener("click", () => decide(runId, detail, node.dataset.action));
  });
  syncConnection();
}

// Translate one reply into operator copy. Returns `terminal` (the decision is
// over -- take the buttons away) and `retry` (the runner refused without acting,
// so answering again after fixing the application is legitimate).
function hasReconciliationSuccessProof(body) {
  return (
    body &&
    body.report_success === true &&
    typeof body.transition_receipt_digest === "string" &&
    /^sha256:[0-9a-f]{64}$/.test(body.transition_receipt_digest)
  );
}

function interpretReply(status, body, portableAction) {
  if (status === 0 || status >= 500) {
    return {
      label: "Result uncertain",
      title: "Do not answer again",
      tone: "uncertain",
      text:
        "The connection dropped before the computer replied, so this phone " +
        "cannot tell whether the answer arrived. Check this decision on the " +
        "computer running OpenAdapt.",
      terminal: true,
    };
  }
  let state = body && body.state;
  let reason = body && body.reason_code;
  if (!state && body && body.status) {
    // A runner that still returns its own decision record rather than the
    // closed receipt. Map it through Flow's own status -> state table.
    const mapped = LEGACY_RECEIPT_STATE[body.status];
    if (mapped) {
      state = mapped[0];
      reason = mapped[1];
      if (state === "completed" && portableAction === "skip") {
        reason = "skipped_and_resumed";
      }
    }
  }
  if (state && reason) {
    const copy = RECEIPT_COPY[`${state}/${reason}`];
    if (copy) {
      // `reconciled_and_resumed` is a success-shaped outcome only when the
      // exact receipt commits to both a successful report and the transition
      // that consumed the pause. Do not translate a partial reply into proof.
      if (copy.reconciliation && !hasReconciliationSuccessProof(body)) {
        return {
          label: "Proof incomplete",
          title: "OpenAdapt did not prove it saved",
          tone: "uncertain",
          text:
            "The reconciliation receipt is incomplete, so it does not claim the " +
            "earlier action succeeded. Check this decision on the computer " +
            "running OpenAdapt.",
          terminal: true,
        };
      }
      if (
        portableAction === "reconcile" &&
        state === "refused" &&
        reason === "revalidation_refused"
      ) {
        return {
          label: "Result not proved yet",
          tone: "halted",
          text:
            "OpenAdapt could not yet prove the saved result. It did not send " +
            "the earlier action again. Review the live record, then check " +
            "again or hand this on.",
          terminal: false,
        };
      }
      return { ...copy };
    }
    // A state this build has no wording for is reported as exactly that. It is
    // NOT a refusal: showing a real terminal outcome as "refused" is the defect
    // this branch exists to prevent.
    return {
      label: "Unknown outcome",
      title: "Check this on the computer",
      tone: "uncertain",
      text:
        `OpenAdapt returned an outcome this phone has no wording for (${state}). ` +
        "Check this decision on the computer running OpenAdapt.",
      terminal: true,
    };
  }
  // No receipt at all: a pre-admission refusal (expired task, wrong binding,
  // action not allowed, another operator already answered). Nothing ran.
  return {
    label: "Not accepted",
    tone: "halted",
    text: "OpenAdapt did not accept that answer and did nothing. Review the live application and answer again.",
    // Flow's 4xx refusal is operator-actionable, so it is shown, as the
    // runner's own words, under the fixed sentence.
    detail: body && typeof body.detail === "string" ? body.detail : "",
    terminal: false,
    refused: true,
  };
}

function renderNotice(outcome, reply) {
  outcome.hidden = false;
  outcome.className = `notice ${reply.tone || "neutral"}`;
  outcome.querySelector("strong").textContent = reply.label || "Result";
  outcome.querySelector("span").textContent = reply.text;
  outcome.querySelector("small").textContent = reply.detail || "";
}

function renderResult(outcome, reply, portableAction) {
  const decision = document.getElementById("decision");
  if (decision) decision.hidden = true;
  const back = document.getElementById("back");
  if (back) back.hidden = true;
  const figure = document.getElementById("evidence");
  if (figure) figure.classList.remove("zoomed");
  releaseFrame();
  hideActions();
  const answered = portableAction && ACTION_WIRE[portableAction];
  const halt = current && current.detail.presentation && current.detail.presentation.halt;
  const where = stepWhere(halt);
  const rows = [
    answered ? ["Your answer", esc(answered.label)] : null,
    ["Answered", `On this phone · ${esc(clockTime(Date.now()))}`],
    where ? ["Step", esc(where.replace(/^Step /, ""))] : null,
    hasReconciliationSuccessProof(reply.body)
      ? ["Receipt", `<code>${esc(reply.body.transition_receipt_digest.slice(7, 19))}</code>`]
      : null,
  ].filter(Boolean);
  outcome.hidden = false;
  outcome.removeAttribute("role");
  outcome.className = `result ${reply.tone || "neutral"} terminal`;
  outcome.innerHTML = resultMarkup({
    tone: reply.tone,
    mark: reply.icon,
    label: reply.label,
    title: reply.title,
    text: reply.text,
    rows,
  });
  document.getElementById("done").addEventListener("click", () => {
    autoOpened = true;
    showList();
  });
  const scroller = document.scrollingElement || document.documentElement;
  if (scroller) scroller.scrollTop = 0;
  outcome.querySelector("h1").focus({ preventScroll: true });
  announce(`${reply.label}. ${reply.title || ""}`);
}

// The decision this phone was answering is no longer the open one.
function showClosed(changed) {
  render(
    `<section class="result neutral terminal" id="outcome">${resultMarkup({
      mark: "superseded",
      label: changed ? "Decision changed" : "No longer open",
      title: changed ? "There is a newer request" : "Someone else has this",
      text: changed
        ? "The run moved on or the request was renewed. Nothing was sent from this phone. Review the new request before answering."
        : "It was answered elsewhere, or the run moved on. Nothing was sent from this phone.",
      done: changed ? "Review the new request" : "Back to decisions",
    })}</section>`,
  );
  document.getElementById("done").addEventListener("click", () => {
    autoOpened = !changed;
    showList();
  });
}

async function decide(runId, detail, portableAction) {
  if (isOffline()) return syncConnection();
  const outcome = document.getElementById("outcome");
  const buttons = Array.from(actionBar.querySelectorAll("button"));
  const chosen = actionBar.querySelector(`[data-action="${portableAction}"]`);
  buttons.forEach((button) => {
    button.disabled = true;
    button.dataset.locked = "true";
  });
  if (chosen) {
    chosen.classList.add("chosen");
    chosen.setAttribute("aria-busy", "true");
    chosen.querySelector(".brief").innerHTML = `<span class="working">Sending to the computer…</span>`;
  }
  announce("Sending your answer to the computer.");
  const wire = ACTION_WIRE[portableAction].wire;
  const payload = {
    capability_digest: detail.task.capability_digest,
    task_digest: detail.task_digest,
    task_signature: detail.task.signature,
    idempotency_key: crypto.randomUUID().replaceAll("-", ""),
    action: wire,
    disposition: DISPOSITION[wire],
  };
  const { status, body } = await api(
    `/api/portal/tasks/${encodeURIComponent(runId)}/actions/${encodeURIComponent(wire)}`,
    { method: "POST", body: JSON.stringify(payload) },
  );
  // Never translate an accepted tap into success: the runner's own terminal
  // state decides what this says.
  const reply = interpretReply(status, body, portableAction);
  reply.body = body;
  if (reply.refused) {
    // A refusal with no receipt. If the open decision is no longer this one,
    // say so plainly instead of inviting a second answer to a stale request.
    const reread = await api(`/api/portal/tasks/${encodeURIComponent(runId)}`);
    if (reread.status === 404) return showClosed(false);
    if (reread.status === 200 && reread.body && reread.body.task_digest !== detail.task_digest) {
      return showClosed(true);
    }
  }
  if (reply.terminal) {
    renderResult(outcome, reply, portableAction);
    return;
  }
  renderNotice(outcome, reply);
  if (chosen) {
    chosen.classList.remove("chosen");
    chosen.removeAttribute("aria-busy");
    chosen.querySelector(".brief").textContent = ACTION_WIRE[portableAction].brief;
  }
  if (!reply.pending) {
    buttons.forEach((button) => {
      button.disabled = false;
      delete button.dataset.locked;
    });
    syncConnection();
  }
  announce(`${reply.label}. ${reply.text}`);
  if (typeof outcome.scrollIntoView === "function") {
    outcome.scrollIntoView({ block: "nearest" });
  }
}

// ------------------------------------------------------------------- routing

function startPairing() {
  store.removeItem("portal_token");
  store.removeItem("portal_csrf");
  screen(`<h1 class="page-title" tabindex="-1">Pair this phone</h1>
    <p class="lede">Decisions from OpenAdapt open here once this phone is paired
    with the computer running the workflow.</p>
    <ol class="steps">
      <li>On that computer, open OpenAdapt and choose Pair a phone.</li>
      <li>Scan the code it shows with this phone's camera.</li>
      <li>Type the code this phone then shows into the computer.</li>
    </ol>`);
}

async function route() {
  const hash = window.location.hash || "";
  if (hash.startsWith("#c=")) {
    await claimPairing(hash.slice(3));
    return;
  }
  const { token } = session();
  if (!token) return startPairing();
  const { status, body } = await api("/api/portal/session");
  if (status === 202) return awaitApproval();
  // Unreachable is not unpaired: keep the session and say what is wrong.
  if (status === 0 || status >= 500) return unavailable(body, status);
  if (status !== 200) return startPairing();
  deviceLabel.textContent = body && body.device_label ? body.device_label : "";
  await showList();
}

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

syncConnection();
route();
