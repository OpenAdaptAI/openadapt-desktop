/**
 * Behavioural tests for the runner-local decision portal shell.
 *
 * The shell was previously covered only by source-text assertions, and every
 * one of them passed while a real `halted` outcome rendered on a phone as
 * "That decision was refused". So this drives the actual script in a DOM:
 * stubbed transport in, rendered text out.
 *
 * These tests protect the operator and runner contracts. They do not pin
 * ordinary wording, layout, or visual styling.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// The shipped shell sources, exactly as the portal serves them.
// (The stylesheet's own rules are asserted in tests/test_portal/test_shell.py;
// vitest does not process CSS, so a `?raw` import of it would be empty.)
import APP from "../openadapt_desktop/portal/shell/app.js?raw";

const RESOLUTION_HALT = {
  task: {
    capability_digest: "sha256:" + "a".repeat(64),
    signature: "hmac-sha256:" + "b".repeat(64),
    delivery_state: "not_delivered",
    risk_class: "unknown",
    created_at: "2026-07-27T21:00:00+00:00",
    expires_at: "2026-07-27T22:00:00+00:00",
    allowed_actions: ["verify_and_resume", "reject", "teach", "escalate"],
    evidence: {},
  },
  task_digest: "sha256:" + "c".repeat(64),
  presentation: {
    question: "Can you prepare one unambiguous target in the live application?",
    explanation: "The compiled target could not be resolved uniquely.",
    assurance: "Your answer does not mark the run verified.",
    after_artifact_id: "artifact-1",
    halt: {
      category: "resolution",
      step_ordinal: 1,
      step_count: 6,
      action_kind: "click",
      target_role: "button",
      target_label: "Open",
      target_label_withheld: false,
      resolution_ladder: [
        { rung: "structural", evidence: "recorded", verdict: "failed" },
        { rung: "template", evidence: "recorded", verdict: "failed" },
        { rung: "template_global", evidence: "recorded", verdict: "failed" },
        { rung: "ocr", evidence: "recorded", verdict: "failed" },
        { rung: "geometry", evidence: "absent", verdict: "not_attempted" },
        { rung: "grounder", evidence: "unknown", verdict: "unknown" },
      ],
      will_recheck: [
        { check: "target_resolution", count: null },
        { check: "record_identity", count: 5 },
        { check: "postconditions", count: 2 },
      ],
    },
  },
};

/** The exact leak the projection is built to avoid ever carrying. */
const PROTECTED_VALUE = "Marta Quilligan 1974-03-08 MRN 40182";

const TYPED_HALT = {
  ...RESOLUTION_HALT,
  presentation: {
    ...RESOLUTION_HALT.presentation,
    halt: {
      ...RESOLUTION_HALT.presentation.halt,
      action_kind: "type",
      target_role: "textbox",
      target_label: null,
      target_label_withheld: true,
    },
  },
};

const RECONCILIATION_HALT = {
  ...RESOLUTION_HALT,
  task: {
    ...RESOLUTION_HALT.task,
    task_kind: "delivery_uncertain",
    delivery_state: "unknown",
    allowed_actions: ["reconcile", "teach", "escalate"],
  },
  presentation: {
    ...RESOLUTION_HALT.presentation,
    question: "Is the live destination ready for OpenAdapt to reconcile the uncertain action?",
    halt: {
      ...RESOLUTION_HALT.presentation.halt,
      category: "effect_indeterminate",
      will_recheck: [{ check: "delivery_reconciliation", count: null }],
    },
  },
};

type Reply = { status: number; body: unknown };

let decisionReply: Reply = { status: 200, body: null };
let detail: unknown = RESOLUTION_HALT;

// A save waiting for approval, exactly as Flow's console serves it.
const SAVE_ID = "5a7e".repeat(8);
const SAVE_APPROVAL = {
  task: {
    task_kind: "save_approval",
    capability_digest: "sha256:" + "e".repeat(64),
    signature: "hmac-sha256:" + "f".repeat(64),
    delivery_state: "not_delivered",
    risk_class: "consequential",
    question: { template: "approve_save", safe_slots: {} },
    created_at: "2026-07-27T21:13:00+00:00",
    expires_at: "2026-07-27T22:00:00+00:00",
    allowed_actions: ["verify_and_resume", "reject"],
    evidence: { frame_available_locally: true, sensitive_evidence_local_only: true },
  },
  task_digest: "sha256:" + "9".repeat(64),
  presentation: {
    question: "Save this?",
    step_intent: "Save the new patient registration",
    step_index: 4,
    step_class: "consequential",
    explanation: "This is exactly what OpenAdapt will save.",
    decline_reasons: ["wrong_values", "wrong_record", "screen_problem"],
    expires_at: "2026-07-27T22:00:00+00:00",
  },
};
let saves: unknown[] | null = null;
let saveDetail: Reply = { status: 200, body: SAVE_APPROVAL };
let saveFrame = true;
let saveReply: Reply = { status: 200, body: null };

function stubTransport() {
  const fetchStub = vi.fn(async (url: string) => {
    let reply: Reply = { status: 404, body: null };
    if (url === "/api/portal/session") {
      reply = { status: 200, body: { device_label: "Phone" } };
    } else if (url === "/api/portal/tasks") {
      reply = {
        status: 200,
        body: saves
          ? []
          : [{ id: "run1", headline: "The compiled target could not be resolved uniquely.", category: "resolution" }],
      };
    } else if (url === "/api/portal/tasks/run1") {
      reply = { status: 200, body: detail };
    } else if (url === "/api/portal/save-approvals") {
      reply = saves ? { status: 200, body: saves } : { status: 404, body: { detail: "Not Found" } };
    } else if (url === `/api/portal/save-approvals/${SAVE_ID}/frame`) {
      return saveFrame
        ? {
            status: 200,
            ok: true,
            headers: { get: () => "image/png" },
            blob: async () => new Blob(["png"], { type: "image/png" }),
          }
        : { status: 404, ok: false, headers: { get: () => "application/json" }, json: async () => ({}) };
    } else if (url.startsWith(`/api/portal/save-approvals/${SAVE_ID}/actions/`)) {
      reply = saveReply;
    } else if (url === `/api/portal/save-approvals/${SAVE_ID}`) {
      reply = saveDetail;
    } else if (url.includes("/actions/")) {
      reply = decisionReply;
    } else if (url === "/api/portal/tasks/none") {
      reply = { status: 404, body: null };
    }
    return {
      status: reply.status,
      ok: reply.status < 400,
      json: async () => reply.body,
    };
  });
  vi.stubGlobal("fetch", fetchStub);
}

async function flush(times = 12) {
  for (let i = 0; i < times; i += 1) {
    await new Promise((r) => setTimeout(r, 0));
  }
}

async function boot() {
  // The shipped index.html body, minus the script tag.
  document.body.innerHTML = `
    <header class="bar"><span class="brand">OpenAdapt</span>
      <span class="bar-right"><span id="device" class="device"></span><span id="link" class="link" role="status"></span></span></header>
    <main id="main" class="main" tabindex="-1">Loading…</main>
    <footer id="actions" class="answers" hidden></footer>
    <p id="announce" aria-live="assertive"></p>`;
  window.sessionStorage.setItem("portal_token", "token");
  window.sessionStorage.setItem("portal_csrf", "csrf");
  new Function(APP)();
  await flush();
}

/** One open decision opens by itself; a list needs a tap. */
async function openTask() {
  const row = document.querySelector("[data-run]") as HTMLButtonElement | null;
  if (row) row.click();
  await flush();
}

function posts() {
  return (fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls.filter(
    (call) => (call[1] as { method?: string } | undefined)?.method === "POST",
  );
}

async function answer(action = "verify_and_resume") {
  const button = document.querySelector(`[data-action="${action}"]`) as HTMLButtonElement;
  button.click();
  await flush();
}

function outcomeText() {
  return document.getElementById("outcome")?.textContent ?? "";
}

// The fixtures expire at 22:00 on their own day, so the page is read as if it
// were shortly after they were issued. Only Date is faked; timers stay real.
const NOW = new Date("2026-07-27T21:14:00Z");

afterEach(() => {
  vi.useRealTimers();
});

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
  Object.defineProperty(window.navigator, "onLine", { configurable: true, get: () => true });
  // The gate tests replace `IntersectionObserver`; drop every stub so one test
  // cannot decide whether the next one is gated.
  vi.unstubAllGlobals();
  detail = RESOLUTION_HALT;
  decisionReply = { status: 200, body: null };
  saves = null;
  saveDetail = { status: 200, body: SAVE_APPROVAL };
  saveFrame = true;
  saveReply = { status: 200, body: null };
  URL.createObjectURL = vi.fn(() => "blob:frame");
  URL.revokeObjectURL = vi.fn();
  window.sessionStorage.clear();
  if (!globalThis.crypto?.randomUUID) {
    vi.stubGlobal("crypto", { randomUUID: () => "00000000-0000-4000-8000-000000000000" });
  }
  stubTransport();
});

describe("the decision view preserves the data boundary", () => {
  it("degrades a withheld label to the field's shape and shows no value", async () => {
    detail = TYPED_HALT;
    await boot();
    await openTask();
    const text = document.getElementById("main")!.textContent ?? "";
    expect(text).not.toContain(PROTECTED_VALUE);
    expect(text).not.toContain("Marta");
  });
});

describe("the shell sends only signed task actions", () => {
  it("sends the closed reject disposition and never any free text", async () => {
    await boot();
    await openTask();
    await answer("reject");
    const body = JSON.parse(
      (fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls
        .map((call) => call[1] as { body?: string } | undefined)
        .filter((init) => init?.body)
        .pop()!.body!,
    );
    expect(body.action).toBe("reject");
    expect(body.disposition).toBe("rejected_by_operator");
    // No field on the wire could carry a reason an operator typed.
    expect(Object.keys(body).sort()).toEqual([
      "action",
      "capability_digest",
      "disposition",
      "idempotency_key",
      "task_digest",
      "task_signature",
    ]);
  });
});

describe("reconciliation never turns an uncertain delivery into a retry", () => {
  it("shows it only for a delivery-uncertain task that may have crossed delivery", async () => {
    detail = RECONCILIATION_HALT;
    await boot();
    await openTask();
    const reconcile = document.querySelector('[data-action="reconcile"]')!;
    expect(reconcile).toBeInstanceOf(HTMLButtonElement);
  });

  it("does not render reconcile when the action was not delivered", async () => {
    detail = {
      ...RECONCILIATION_HALT,
      task: { ...RECONCILIATION_HALT.task, delivery_state: "not_delivered" },
    };
    await boot();
    await openTask();
    expect(document.querySelector('[data-action="reconcile"]')).toBeNull();
  });

  it("sends a closed reconciliation request, not continue", async () => {
    detail = RECONCILIATION_HALT;
    decisionReply = {
      status: 200,
      body: {
        state: "completed",
        reason_code: "reconciled_and_resumed",
        action: "reconcile",
        report_success: true,
        transition_receipt_digest: "sha256:" + "d".repeat(64),
      },
    };
    await boot();
    await openTask();
    await answer("reconcile");
    const body = JSON.parse(
      (fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls
        .map((call) => call[1] as { body?: string } | undefined)
        .filter((init) => init?.body)
        .pop()!.body!,
    );
    expect(body.action).toBe("reconcile");
    expect(body.disposition).toBe("reconcile_requested");
    const outcome = document.getElementById("outcome")!;
    expect(outcome.classList).toContain("success");
    expect(outcome.classList).toContain("terminal");
  });

  it("does not claim a reconciliation succeeded without the exact receipt proof", async () => {
    detail = RECONCILIATION_HALT;
    decisionReply = {
      status: 200,
      body: {
        state: "completed",
        reason_code: "reconciled_and_resumed",
        action: "reconcile",
        report_success: true,
      },
    };
    await boot();
    await openTask();
    await answer("reconcile");
    const outcome = document.getElementById("outcome")!;
    expect(outcome.classList).not.toContain("success");
    expect(outcome.classList).toContain("terminal");
  });

  it("keeps an unproven reconciliation answerable without offering a resend", async () => {
    detail = RECONCILIATION_HALT;
    decisionReply = {
      status: 200,
      body: {
        state: "refused",
        reason_code: "revalidation_refused",
        action: "reconcile",
      },
    };
    await boot();
    await openTask();
    await answer("reconcile");
    expect(document.querySelector('[data-action="reconcile"]')).not.toBeNull();
    expect(document.querySelector('[data-action="continue"]')).toBeNull();
  });
});

describe("terminal outcomes close the action set", () => {
  it("closes the run after rejection", async () => {
    decisionReply = {
      status: 200,
      body: {
        schema_version: "openadapt.human-decision-receipt/v1",
        action: "reject",
        state: "rejected",
        reason_code: "rejected_by_operator",
        report_success: null,
      },
    };
    await boot();
    await openTask();
    await answer("reject");
    expect(document.getElementById("outcome")!.classList).toContain("terminal");
    expect(document.getElementById("actions")!.hidden).toBe(true);
  });

  it("renders a runner that still returns the older decision record", async () => {
    decisionReply = { status: 200, body: { status: "rejected" } };
    await boot();
    await openTask();
    await answer("reject");
    expect(document.getElementById("outcome")!.classList).toContain("terminal");
  });
});

describe("the terminal receipt contract", () => {
  it("renders a real halt as a halt, not as a refusal", async () => {
    decisionReply = {
      status: 200,
      body: {
        schema_version: "openadapt.human-decision-receipt/v1",
        action: "verify_and_resume",
        state: "halted",
        reason_code: "continuation_halted",
      },
    };
    await boot();
    await openTask();
    await answer();
    expect(document.getElementById("outcome")!.classList).toContain("halted");
    expect((document.getElementById("actions") as HTMLElement).hidden).toBe(true);
  });

  it("renders a verified continuation as success", async () => {
    decisionReply = {
      status: 200,
      body: { state: "completed", reason_code: "verified_and_resumed", action: "verify_and_resume" },
    };
    await boot();
    await openTask();
    await answer();
    expect((document.getElementById("decision") as HTMLElement).hidden).toBe(true);
    expect(document.getElementById("outcome")!.classList).toContain("terminal");
    expect(document.getElementById("outcome")!.classList).toContain("success");
    expect((document.getElementById("actions") as HTMLElement).hidden).toBe(true);
  });

  it("keeps a live revalidation refusal answerable again", async () => {
    decisionReply = {
      status: 200,
      body: { state: "refused", reason_code: "revalidation_refused", action: "verify_and_resume" },
    };
    await boot();
    await openTask();
    await answer();
    expect((document.getElementById("decision") as HTMLElement).hidden).toBe(false);
    expect(document.getElementById("outcome")!.classList).not.toContain("terminal");
    const button = document.querySelector('[data-action="verify_and_resume"]') as HTMLButtonElement;
    expect(button.disabled).toBe(false);
  });

  it("reports an uncertain delivery without ever inviting a retry", async () => {
    decisionReply = {
      status: 202,
      body: { state: "delivery_uncertain", reason_code: "delivery_uncertain", action: "verify_and_resume" },
    };
    await boot();
    await openTask();
    await answer();
    const outcome = document.getElementById("outcome")!;
    expect(outcome.classList).toContain("uncertain");
    expect(outcome.classList).toContain("terminal");
  });

  it("reports an outcome it has no wording for as unknown, not as a refusal", async () => {
    decisionReply = {
      status: 200,
      body: { state: "some_future_state", reason_code: "some_future_reason", action: "verify_and_resume" },
    };
    await boot();
    await openTask();
    await answer();
    const outcome = document.getElementById("outcome")!;
    expect(outcome.classList).toContain("uncertain");
    expect(outcome.classList).toContain("terminal");
  });

  it("still renders a runner that returns the older decision record", async () => {
    decisionReply = {
      status: 200,
      body: {
        action: "continue",
        status: "halted",
        message: "front-desk: the live session died at step_003",
        report_success: false,
      },
    };
    await boot();
    await openTask();
    await answer();
    expect(document.getElementById("outcome")!.classList).toContain("halted");
    expect(outcomeText()).not.toContain("front-desk");
  });

  it("shows a pre-admission refusal without claiming the engine acted", async () => {
    decisionReply = {
      status: 409,
      body: { detail: "the human decision task changed; reload the current pause" },
    };
    await boot();
    await openTask();
    await answer();
    const button = document.querySelector('[data-action="verify_and_resume"]') as HTMLButtonElement;
    expect(button.disabled).toBe(false);
  });

  it("treats a lost network as uncertain rather than failed", async () => {
    decisionReply = { status: 503, body: null };
    await boot();
    await openTask();
    await answer();
    expect(document.getElementById("outcome")!.classList).toContain("uncertain");
  });

  it("treats a request that never came back as uncertain, not as pending forever", async () => {
    await boot();
    await openTask();
    vi.stubGlobal("fetch", async () => {
      throw new TypeError("Failed to fetch");
    });
    await answer();
    const outcome = document.getElementById("outcome")!;
    expect(outcome.classList).toContain("uncertain");
    expect(outcome.classList).toContain("terminal");
  });
});

describe("the signed task controls the action set", () => {
  it("does not add a local action", async () => {
    await boot();
    await openTask();
    const buttons = Array.from(
      document.querySelectorAll("#actions [data-action]"),
    ) as HTMLButtonElement[];
    expect(buttons.map((button) => button.dataset.action)).toEqual(
      RESOLUTION_HALT.task.allowed_actions,
    );
  });
});

describe("the retained frame is named as history, not as the live screen", () => {
  it("shows a retained figure with age and a no-live-state warning", async () => {
    detail = {
      ...RESOLUTION_HALT,
      task: {
        ...RESOLUTION_HALT.task,
        created_at: new Date(NOW.getTime() - 14 * 60 * 1000).toISOString(),
      },
    };
    await boot();
    await openTask();
    const figure = document.querySelector("figure.evidence")!;
    expect(figure.querySelector("img[data-artifact]")).not.toBeNull();
    // Said twice: on the picture, and to a screen reader in its description.
    expect(figure.textContent).toContain("Kept 14 minutes ago · not live");
    const alt = figure.querySelector("img")!.getAttribute("alt") ?? "";
    expect(alt).toContain("about 14 minutes ago");
    expect(alt).toContain("Not live");
  });

  it("zooms the same picture without asking the runner for it again", async () => {
    await boot();
    await openTask();
    const evidenceCalls = () =>
      (fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls.filter((call) =>
        String(call[0]).includes("/evidence?"),
      ).length;
    const before = evidenceCalls();
    const open = document.getElementById("evidence-open") as HTMLButtonElement;
    expect(open.getAttribute("aria-expanded")).toBe("false");
    open.click();
    expect(document.getElementById("evidence")!.classList).toContain("zoomed");
    expect(open.getAttribute("aria-expanded")).toBe("true");
    (document.getElementById("evidence-close") as HTMLButtonElement).click();
    expect(document.getElementById("evidence")!.classList).not.toContain("zoomed");
    // There is no refresh: the runner does not re-observe on demand, and
    // re-requesting the stored crop would pretend it had.
    expect(evidenceCalls()).toBe(before);
  });
});

describe("stakes are stated above the question", () => {
  it("shows an irreversible step above the question", async () => {
    detail = {
      ...RESOLUTION_HALT,
      task: { ...RESOLUTION_HALT.task, risk_class: "irreversible" },
    };
    await boot();
    await openTask();
    const stakes = document.querySelector("#decision .stakes")!;
    expect(stakes.textContent).toContain("cannot be undone");
    const question = document.querySelector("#decision h1")!;
    expect(stakes.compareDocumentPosition(question) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("does not invent a risk claim when the engine does not know", async () => {
    await boot();
    await openTask();
    expect(document.querySelector(".stakes")).toBeNull();
  });
});

describe("one answer is emphasised only when it continues under a live re-check", () => {
  function primaries() {
    return Array.from(document.querySelectorAll("#actions .answer.primary")).map(
      (node) => (node as HTMLElement).dataset.action,
    );
  }

  it("emphasises check-and-continue for an ordinary halt", async () => {
    await boot();
    await openTask();
    expect(primaries()).toEqual(["verify_and_resume"]);
  });

  it("emphasises nothing before a step that cannot be undone", async () => {
    detail = { ...RESOLUTION_HALT, task: { ...RESOLUTION_HALT.task, risk_class: "irreversible" } };
    await boot();
    await openTask();
    expect(primaries()).toEqual([]);
  });

  it("emphasises the reconciliation check, which never resends", async () => {
    detail = RECONCILIATION_HALT;
    await boot();
    await openTask();
    expect(primaries()).toEqual(["reconcile"]);
  });

  it("emphasises nothing when two answers would continue", async () => {
    detail = {
      ...RESOLUTION_HALT,
      task: { ...RESOLUTION_HALT.task, allowed_actions: ["verify_and_resume", "skip", "escalate"] },
    };
    await boot();
    await openTask();
    expect(primaries()).toEqual([]);
  });

  it("never emphasises stop, teach or hand-on", async () => {
    detail = {
      ...RESOLUTION_HALT,
      task: { ...RESOLUTION_HALT.task, allowed_actions: ["reject", "teach", "escalate"] },
    };
    await boot();
    await openTask();
    expect(primaries()).toEqual([]);
  });
});

describe("the answers are at the end of the reading path", () => {
  it("puts the answers after the question, the picture and what happens next", async () => {
    await boot();
    await openTask();
    const answers = document.getElementById("actions")!;
    for (const selector of ["#decision h1", "figure.evidence", ".next"]) {
      const node = document.querySelector(selector)!;
      expect(node.compareDocumentPosition(answers) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
    expect(answers.querySelectorAll("[data-action]")).toHaveLength(4);
    expect(answers.getAttribute("aria-labelledby")).toBe("answers-h");
  });

  it("states what the continuing answer re-checks", async () => {
    await boot();
    await openTask();
    const next = document.querySelector(".next")!.textContent ?? "";
    expect(next).toContain("Check and continue");
    expect(next).toContain("find the target again on the live screen");
    expect(next).toContain("it stops again and changes nothing");
  });
});

describe("expiry is shown before a tap, not discovered after it", () => {
  it("opens an expired request as expired, with no answers", async () => {
    detail = {
      ...RESOLUTION_HALT,
      task: { ...RESOLUTION_HALT.task, expires_at: "2026-07-27T21:00:00+00:00" },
    };
    await boot();
    await openTask();
    expect(document.querySelectorAll("[data-action]")).toHaveLength(0);
    expect(document.getElementById("main")!.textContent).toContain("This request expired");
  });

  it("closes the answers when the request expires while it is open", async () => {
    detail = {
      ...RESOLUTION_HALT,
      task: { ...RESOLUTION_HALT.task, expires_at: new Date(NOW.getTime() + 1500).toISOString() },
    };
    await boot();
    await openTask();
    expect(document.querySelectorAll("[data-action]")).toHaveLength(4);
    // The close is a real timer for the time left; the clock moves with it.
    vi.setSystemTime(new Date(NOW.getTime() + 60_000));
    await new Promise((r) => setTimeout(r, 1700));
    expect(document.querySelectorAll("[data-action]")).toHaveLength(0);
  });

  it("says how long is left next to the answers", async () => {
    await boot();
    await openTask();
    expect(document.getElementById("answers-h")!.textContent).toContain("closes in 46 minutes");
  });
});

describe("a decision answered elsewhere is reported as gone, not as refused", () => {
  it("reports the decision closed when the runner no longer has it", async () => {
    decisionReply = { status: 409, body: { detail: "the attention item is no longer current" } };
    await boot();
    await openTask();
    // After the refusal, the open decision is gone.
    const stub = fetch as unknown as { mockImplementation: (f: unknown) => void };
    stub.mockImplementation(async (url: string) => {
      if (url.includes("/actions/")) return { status: 409, ok: false, json: async () => decisionReply.body };
      if (url === "/api/portal/tasks/run1") return { status: 404, ok: false, json: async () => ({}) };
      return { status: 200, ok: true, json: async () => [] };
    });
    await answer();
    expect(document.querySelectorAll("[data-action]")).toHaveLength(0);
    expect(document.getElementById("main")!.textContent).toContain("Nothing was sent from this phone");
  });

  it("reports a newer request when the task digest changed", async () => {
    decisionReply = { status: 409, body: { detail: "the human decision task changed" } };
    await boot();
    await openTask();
    const stub = fetch as unknown as { mockImplementation: (f: unknown) => void };
    stub.mockImplementation(async (url: string) => {
      if (url.includes("/actions/")) return { status: 409, ok: false, json: async () => decisionReply.body };
      if (url === "/api/portal/tasks/run1") {
        return {
          status: 200,
          ok: true,
          json: async () => ({ ...RESOLUTION_HALT, task_digest: "sha256:" + "e".repeat(64) }),
        };
      }
      return { status: 200, ok: true, json: async () => [] };
    });
    await answer();
    expect(document.getElementById("main")!.textContent).toContain("There is a newer request");
  });
});

describe("offline closes the answers and queues nothing", () => {
  it("disables every answer while offline and sends nothing", async () => {
    await boot();
    await openTask();
    Object.defineProperty(window.navigator, "onLine", { configurable: true, get: () => false });
    window.dispatchEvent(new Event("offline"));
    const buttons = Array.from(document.querySelectorAll("[data-action]")) as HTMLButtonElement[];
    expect(buttons.every((button) => button.disabled)).toBe(true);
    expect((document.getElementById("offline-note") as HTMLElement).hidden).toBe(false);
    expect(document.getElementById("link")!.textContent).toBe("Offline");
    buttons[0].click();
    await flush();
    expect(posts()).toHaveLength(0);
    Object.defineProperty(window.navigator, "onLine", { configurable: true, get: () => true });
    window.dispatchEvent(new Event("online"));
    expect(buttons.every((button) => !button.disabled)).toBe(true);
  });

  it("keeps the pairing when the computer cannot be reached", async () => {
    vi.stubGlobal("fetch", async () => {
      throw new TypeError("Failed to fetch");
    });
    await boot();
    expect(window.sessionStorage.getItem("portal_token")).toBe("token");
    expect(document.getElementById("main")!.textContent).toContain("Cannot reach OpenAdapt");
    expect(document.getElementById("main")!.textContent).not.toContain("Pair this phone");
  });
});

describe("the result is a receipt", () => {
  it("names the answer that was sent and where it was answered", async () => {
    decisionReply = {
      status: 200,
      body: { state: "completed", reason_code: "verified_and_resumed", action: "verify_and_resume" },
    };
    await boot();
    await openTask();
    await answer();
    const receipt = document.querySelector("#outcome .receipt")!.textContent ?? "";
    expect(receipt).toContain("I fixed it. Check and continue");
    expect(receipt).toContain("On this phone");
    expect(document.activeElement).toBe(document.querySelector("#outcome h1"));
  });

  it("keeps the answers locked while the runner is still checking", async () => {
    decisionReply = {
      status: 200,
      body: { state: "accepted_pending_runner", reason_code: "pending_runner", action: "verify_and_resume" },
    };
    await boot();
    await openTask();
    await answer();
    const buttons = Array.from(document.querySelectorAll("[data-action]")) as HTMLButtonElement[];
    expect(buttons.length).toBeGreaterThan(0);
    expect(buttons.every((button) => button.disabled)).toBe(true);
    expect(document.getElementById("outcome")!.classList).toContain("pending");
  });
});

describe("navigation", () => {
  it("opens a single open decision directly and lists several", async () => {
    await boot();
    expect(document.querySelector("#decision h1")).not.toBeNull();
    (document.getElementById("back") as HTMLButtonElement).click();
    await flush();
    expect(document.querySelectorAll("[data-run]")).toHaveLength(1);
  });
});

describe("a save waiting for approval", () => {
  const WAITING = [{ request_id: SAVE_ID, step_class: "consequential", created_at: "", expires_at: "2026-07-27T22:00:00+00:00" }];

  function lastPost() {
    const call = posts().pop()!;
    return { url: call[0] as string, body: JSON.parse((call[1] as { body: string }).body) };
  }

  async function tap(selector: string) {
    (document.querySelector(selector) as HTMLButtonElement).click();
    await flush();
  }

  it("opens by itself and asks one question with the exact form", async () => {
    saves = WAITING;
    await boot();
    expect(document.querySelector("#decision h1")!.textContent).toBe("Save this form?");
    expect((document.getElementById("frame") as HTMLImageElement).src).toBe("blob:frame");
    expect(document.getElementById("main")!.textContent).toContain("Save the new patient registration");
    expect(document.querySelector(".stakes")!.textContent).toContain("system of record");
  });

  it("approves with one tap, sending only the task it showed", async () => {
    saves = WAITING;
    saveReply = {
      status: 200,
      body: { state: "accepted_pending_runner", reason_code: "pending_runner", action: "verify_and_resume", decision_digest: "sha256:" + "7".repeat(64) },
    };
    await boot();
    const approve = document.querySelector('[data-save-action="verify_and_resume"]')!;
    expect(approve.classList).toContain("primary");
    await tap('[data-save-action="verify_and_resume"]');
    const { url, body } = lastPost();
    expect(url).toBe(`/api/portal/save-approvals/${SAVE_ID}/actions/verify_and_resume`);
    // Flow forbids extra fields, and the phone sends none.
    expect(body).toEqual({
      action: "verify_and_resume",
      task_digest: SAVE_APPROVAL.task_digest,
      task_signature: SAVE_APPROVAL.task.signature,
    });
    expect(outcomeText()).toContain("Approved");
    expect(outcomeText()).toContain("Saving after one more check");
    expect(outcomeText()).toContain("777777777777");
  });

  it("declines only with one closed reason, and says nothing was saved", async () => {
    saves = WAITING;
    saveReply = { status: 200, body: { state: "rejected", reason_code: "rejected_by_operator", action: "reject" } };
    await boot();
    await tap('[data-save-action="reject"]');
    expect(posts()).toHaveLength(0);
    const reasons = Array.from(document.querySelectorAll("[data-decline]")).map((b) => (b as HTMLElement).dataset.decline);
    expect(reasons).toEqual(["wrong_values", "wrong_record", "screen_problem"]);
    await tap('[data-decline="wrong_record"]');
    const { body } = lastPost();
    expect(body).toEqual({
      action: "reject",
      task_digest: SAVE_APPROVAL.task_digest,
      task_signature: SAVE_APPROVAL.task.signature,
      decline_reason: "wrong_record",
    });
    expect(outcomeText()).toContain("Nothing was saved");
    expect(outcomeText()).toContain("It's the wrong record");
  });

  it("offers no reason it cannot name", async () => {
    saves = WAITING;
    saveDetail = {
      status: 200,
      body: { ...SAVE_APPROVAL, presentation: { ...SAVE_APPROVAL.presentation, decline_reasons: ["wrong_values", "free_text"] } },
    };
    await boot();
    await tap('[data-save-action="reject"]');
    expect(Array.from(document.querySelectorAll("[data-decline]")).map((b) => (b as HTMLElement).dataset.decline)).toEqual([
      "wrong_values",
    ]);
  });

  it("cannot approve a save whose picture did not load", async () => {
    saves = WAITING;
    saveFrame = false;
    await boot();
    expect(document.querySelector('[data-save-action="verify_and_resume"]')).toBeNull();
    expect(document.querySelector('[data-save-action="reject"]')).not.toBeNull();
    expect(document.getElementById("evidence")).toBeNull();
    expect(outcomeText()).toContain("cannot approve");
  });

  it("gives two equal answers when the save cannot be undone", async () => {
    saves = WAITING;
    saveDetail = { status: 200, body: { ...SAVE_APPROVAL, task: { ...SAVE_APPROVAL.task, risk_class: "irreversible" } } };
    await boot();
    expect(document.querySelectorAll(".answer.primary")).toHaveLength(0);
    expect(document.querySelector(".stakes")!.textContent).toContain("cannot be undone");
  });

  it("says a save answered elsewhere is no longer waiting", async () => {
    saves = WAITING;
    saveReply = { status: 409, body: { detail: "request_not_waiting" } };
    await boot();
    await tap('[data-save-action="verify_and_resume"]');
    expect(document.getElementById("main")!.textContent).toContain("This save is no longer waiting");
  });

  it("asks to look again when the save changed", async () => {
    saves = WAITING;
    saveReply = { status: 409, body: { detail: "task_changed" } };
    await boot();
    await tap('[data-save-action="verify_and_resume"]');
    expect(document.getElementById("main")!.textContent).toContain("This save changed");
    await tap("#done");
    expect(document.querySelector("#decision h1")!.textContent).toBe("Save this form?");
  });

  it("opens an expired save as expired, with no answers", async () => {
    saves = WAITING;
    saveDetail = { status: 200, body: { ...SAVE_APPROVAL, task: { ...SAVE_APPROVAL.task, expires_at: "2026-07-27T21:00:00+00:00" } } };
    await boot();
    expect(document.querySelectorAll("[data-save-action]")).toHaveLength(0);
    expect(document.getElementById("main")!.textContent).toContain("This request expired");
  });

  it("sends nothing while offline", async () => {
    saves = WAITING;
    await boot();
    Object.defineProperty(window.navigator, "onLine", { configurable: true, get: () => false });
    window.dispatchEvent(new Event("offline"));
    const approve = document.querySelector('[data-save-action="verify_and_resume"]') as HTMLButtonElement;
    expect(approve.disabled).toBe(true);
    approve.click();
    await flush();
    expect(posts()).toHaveLength(0);
  });

  it("does not show a task that is not a save approval as one", async () => {
    saves = WAITING;
    saveDetail = { status: 200, body: { ...SAVE_APPROVAL, task: { ...SAVE_APPROVAL.task, task_kind: "identity" } } };
    await boot();
    expect(document.querySelectorAll("[data-save-action]")).toHaveLength(0);
  });

  it("lists saves with paused runs, and an older runner without saves still lists runs", async () => {
    await boot();
    // No save route (404): the paused run still opens.
    expect(document.querySelector("#decision h1")!.textContent).toContain("unambiguous target");
  });
});
