// Shadow mode: the engine's local, off-by-default watcher that notices a task
// done many times and offers to turn it into a draft. These types mirror
// openadapt_desktop/shadow_mode.py. Nothing here leaves the computer.

export interface ShadowStatus {
  available: boolean;
  active: boolean;
  acknowledged_at: number | null;
  buffer_hours: number;
  work_domains: string[];
  keys_in_keychain: boolean;
  epoch_ends_at: number | null;
  watches: "recordings" | null;
  pending: ShadowPrompt[];
}

export interface ShadowPromptAction {
  id: "review_draft" | "ignore";
  label: string;
}

/** Generic text only: never an app, a page, a field, or a value. */
export interface ShadowPrompt {
  motif_id: string;
  count: number;
  title: string;
  body: string;
  actions: ShadowPromptAction[];
}

export interface ShadowNotice {
  version: string;
  sha256: string;
  title: string;
  intro: string;
  sections: { heading: string; items: string[] }[];
  acknowledge_label: string;
  decline_label: string;
  purge_and_disable_label: string;
  text: string;
}

export interface ShadowDecision {
  motif_id: string;
  action: string;
  draft_path?: string;
  episodes?: number;
  next?: string;
}

export const SHADOW_OFF: ShadowStatus = {
  available: false,
  active: false,
  acknowledged_at: null,
  buffer_hours: 24,
  work_domains: [],
  keys_in_keychain: false,
  epoch_ends_at: null,
  watches: null,
  pending: [],
};

/** Read a status payload defensively: anything unclear reads as off. */
export function readShadowStatus(raw: unknown): ShadowStatus {
  if (!raw || typeof raw !== "object") return SHADOW_OFF;
  const r = raw as Partial<ShadowStatus>;
  return {
    available: r.available === true,
    active: r.available === true && r.active === true,
    acknowledged_at: typeof r.acknowledged_at === "number" ? r.acknowledged_at : null,
    buffer_hours: typeof r.buffer_hours === "number" ? r.buffer_hours : 24,
    work_domains: Array.isArray(r.work_domains) ? r.work_domains.map(String) : [],
    keys_in_keychain: r.keys_in_keychain === true,
    epoch_ends_at: typeof r.epoch_ends_at === "number" ? r.epoch_ends_at : null,
    watches: r.watches === "recordings" ? "recordings" : null,
    pending: Array.isArray(r.pending) ? r.pending.filter(isPrompt) : [],
  };
}

export function isPrompt(raw: unknown): raw is ShadowPrompt {
  if (!raw || typeof raw !== "object") return false;
  const p = raw as Partial<ShadowPrompt>;
  return (
    typeof p.motif_id === "string" &&
    typeof p.count === "number" &&
    typeof p.title === "string" &&
    typeof p.body === "string" &&
    Array.isArray(p.actions)
  );
}

/** Split what a person typed into domain names: commas, spaces, or lines. */
export function parseDomains(text: string): string[] {
  const seen = new Set<string>();
  for (const part of text.split(/[\s,]+/)) {
    const d = part.trim().toLowerCase().replace(/^https?:\/\//, "").replace(/\/.*$/, "");
    if (d) seen.add(d);
  }
  return [...seen];
}

/** A local date and time for an epoch-seconds value. */
export function formatWhen(epochSeconds: number | null): string {
  if (epochSeconds == null) return "not recorded";
  return new Date(epochSeconds * 1000).toLocaleString();
}
