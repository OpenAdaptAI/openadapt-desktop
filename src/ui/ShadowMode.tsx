// Shadow mode UI: the notice a person must acknowledge, the Settings card, the
// header indicator, and the prompt banner. Shadow mode is off by default and
// runs only on this computer. It offers drafts; it never runs anything.
import { useCallback, useEffect, useState } from "react";
import {
  CMD,
  EVT,
  engineInvoke,
  engineTry,
  onEngineEvent,
  setShadowTrayStatus,
} from "../lib/engine";
import {
  SHADOW_OFF,
  formatWhen,
  isPrompt,
  parseDomains,
  readShadowStatus,
  type ShadowDecision,
  type ShadowNotice,
  type ShadowPrompt,
  type ShadowStatus,
} from "../lib/shadowMode";
import { Button, Callout, Card, CardHead, Field, Pill, StatusDot } from "./primitives";

/** Shadow mode's status, kept current from engine events. */
export function useShadowMode({ syncTray = false }: { syncTray?: boolean } = {}) {
  const [status, setStatus] = useState<ShadowStatus>(SHADOW_OFF);

  const refresh = useCallback(async () => {
    setStatus(readShadowStatus(await engineTry(CMD.SHADOW_STATUS, {}, null)));
  }, []);

  useEffect(() => {
    void refresh();
    // Outside the desktop shell there are no events; the status read above
    // still answers, so a failed subscription is not an error.
    const quiet = (p: Promise<() => void>) => p.catch(() => () => {});
    const unlisteners = [
      quiet(onEngineEvent(EVT.SHADOW_STATE, (payload) => setStatus(readShadowStatus(payload)))),
      quiet(
        onEngineEvent(EVT.SHADOW_PROMPT, (payload) => {
          if (!isPrompt(payload)) return;
          setStatus((s) => ({
            ...s,
            pending: [...s.pending.filter((p) => p.motif_id !== payload.motif_id), payload],
          }));
        }),
      ),
    ];
    return () => {
      for (const u of unlisteners) void u.then((fn) => fn());
    };
  }, [refresh]);

  useEffect(() => {
    if (syncTray) void setShadowTrayStatus(status.active);
  }, [syncTray, status.active]);

  return { status, setStatus, refresh };
}

/** The header indicator. Shows only while shadow mode is on. */
export function ShadowModeIndicator({ status }: { status: ShadowStatus }) {
  if (!status.active) return null;
  return (
    <span
      className="shadow-mode-on"
      role="status"
      title="Shadow mode is on. It keeps an encrypted record of the last 24 hours on this computer only."
    >
      <StatusDot tone="run" />
      <strong>Shadow mode on</strong>
    </span>
  );
}

/** One offer per repeated task: Review draft or Ignore. Generic text only. */
export function ShadowPromptBanner({
  status,
  onDecided,
}: {
  status: ShadowStatus;
  onDecided?: (decision: ShadowDecision) => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (!status.active || (status.pending.length === 0 && !note && !error)) return null;

  async function decide(prompt: ShadowPrompt, action: string) {
    setBusy(prompt.motif_id);
    setError(null);
    try {
      const result = await engineInvoke<ShadowDecision>(CMD.SHADOW_DECIDE, {
        motif_id: prompt.motif_id,
        action,
      });
      setNote(action === "review_draft" ? (result.next ?? "The draft is saved.") : null);
      onDecided?.(result);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="shadow-prompts" aria-label="Repeated tasks">
      {status.pending.map((prompt) => (
        <Callout key={prompt.motif_id} title={prompt.title}>
          <p>{prompt.body}</p>
          <div className="row">
            {prompt.actions.map((a) => (
              <Button
                key={a.id}
                size="sm"
                variant={a.id === "review_draft" ? "primary" : "ghost"}
                disabled={busy === prompt.motif_id}
                onClick={() => void decide(prompt, a.id)}
              >
                {a.label}
              </Button>
            ))}
          </div>
        </Callout>
      ))}
      {note && (
        <Callout title="Draft saved on this computer">
          <p>{note}</p>
        </Callout>
      )}
      {error && (
        <Callout tone="warn" title="Shadow mode couldn't save that answer">
          {error}
        </Callout>
      )}
    </div>
  );
}

/** The notice. Shadow mode turns on only from its acknowledge button. */
export function ShadowNoticeDialog({
  notice,
  busy,
  onAcknowledge,
  onDecline,
}: {
  notice: ShadowNotice;
  busy?: boolean;
  onAcknowledge: () => void;
  onDecline: () => void;
}) {
  return (
    <div role="dialog" aria-modal="true" aria-labelledby="shadow-notice-title" className="shadow-notice">
      <h3 id="shadow-notice-title">{notice.title}</h3>
      <p>{notice.intro}</p>
      {notice.sections.map((section) => (
        <section key={section.heading}>
          <h4>{section.heading}</h4>
          <ul>
            {section.items.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </section>
      ))}
      <p className="hint">Notice version {notice.version}</p>
      <div className="row">
        <Button variant="primary" disabled={busy} onClick={onAcknowledge}>
          {notice.acknowledge_label}
        </Button>
        <Button variant="ghost" disabled={busy} onClick={onDecline}>
          {notice.decline_label}
        </Button>
      </div>
    </div>
  );
}

/** The Settings card: status, the notice, work domains, and delete-and-off. */
export function ShadowModeCard() {
  const { status, setStatus } = useShadowMode();
  const [notice, setNotice] = useState<ShadowNotice | null>(null);
  const [domains, setDomains] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const savedDomains = status.work_domains.join("\n");
  useEffect(() => {
    setDomains(savedDomains);
  }, [savedDomains]);

  async function run(action: () => Promise<unknown>, done?: string) {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const result = await action();
      if (result && typeof result === "object" && "available" in result) {
        setStatus(readShadowStatus(result));
      }
      if (done) setMessage(done);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const openNotice = () =>
    run(async () => setNotice(await engineInvoke<ShadowNotice>(CMD.SHADOW_NOTICE)));

  const acknowledge = () =>
    run(async () => {
      if (!notice) return null;
      const result = await engineInvoke(CMD.SHADOW_ENABLE, {
        notice_sha256: notice.sha256,
        acknowledged: true,
      });
      setNotice(null);
      return result;
    });

  const purge = () =>
    run(
      () => engineInvoke(CMD.SHADOW_PURGE_AND_DISABLE),
      "Shadow mode is off. Everything it kept on this computer is deleted.",
    );

  const saveDomains = () =>
    run(
      () => engineInvoke(CMD.SHADOW_SET_WORK_DOMAINS, { domains: parseDomains(domains) }),
      "Work sites saved.",
    );

  return (
    <Card>
      <CardHead
        title="Shadow mode"
        sub="Off unless you turn it on. When on, OpenAdapt notices a task you repeat and offers to make a draft. It keeps an encrypted record of the last 24 hours on this computer only, and it never runs anything."
      />
      {!status.available ? (
        <Callout title="Not available in this version">
          This version of the OpenAdapt engine doesn't include shadow mode.
        </Callout>
      ) : (
        <>
          <div className="row">
            <Pill tone={status.active ? "run" : "neutral"}>
              {status.active ? "On" : "Off"}
            </Pill>
            {status.active && (
              <span className="page-sub">
                Turned on {formatWhen(status.acknowledged_at)}. Watches your recordings in
                Desktop. Keeps the last {status.buffer_hours} hours, encrypted. The key is{" "}
                {status.keys_in_keychain ? "in your system keychain" : "in memory only"} and is
                replaced, with everything recorded, by {formatWhen(status.epoch_ends_at)}.
              </span>
            )}
          </div>

          {notice ? (
            <ShadowNoticeDialog
              notice={notice}
              busy={busy}
              onAcknowledge={() => void acknowledge()}
              onDecline={() => setNotice(null)}
            />
          ) : status.active ? (
            <div className="row">
              <Button variant="danger" disabled={busy} onClick={() => void purge()}>
                Delete everything and turn off
              </Button>
            </div>
          ) : (
            <div className="row">
              <Button variant="ghost" disabled={busy} onClick={() => void openNotice()}>
                Read the notice to turn on
              </Button>
            </div>
          )}

          <Field
            label="Work sites"
            htmlFor="shadow-work-domains"
            hint="Shadow mode keeps browser steps only on these sites, one per line, such as erp.example.com. On any other site it notes only that a browser was in use."
          >
            <textarea
              id="shadow-work-domains"
              rows={3}
              value={domains}
              onChange={(e) => setDomains(e.target.value)}
            />
          </Field>
          <div className="row">
            <Button variant="ghost" size="sm" disabled={busy} onClick={() => void saveDomains()}>
              Save work sites
            </Button>
          </div>
        </>
      )}
      {message && <p className="page-sub">{message}</p>}
      {error && (
        <Callout tone="warn" title="Shadow mode didn't change">
          {error}
        </Callout>
      )}
    </Card>
  );
}
