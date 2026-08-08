import { useMemo, useState } from "react";
import type {
  JudgmentCaseCaptureContextV1,
  JudgmentCaseV1,
  JudgmentDispositionV1,
  LocalEvidenceRefV1,
} from "../lib/types";
import { Button, Callout, Card, CardHead, Pill, SegControl } from "./primitives";

type FactValue = boolean | number | string;

const dispositionOptions: { value: JudgmentDispositionV1; label: string }[] = [
  { value: "automatic_rule", label: "Rule candidate" },
  { value: "human_node", label: "Keep human decision" },
  { value: "more_evidence_required", label: "More examples" },
];

function caseId(): string {
  return `judgment_case_${crypto.randomUUID()}`;
}

function initialFacts(context: JudgmentCaseCaptureContextV1): Record<string, FactValue> {
  return Object.fromEntries(
    Object.entries(context.fact_schema.fields).map(([name, field]) => [
      name,
      field.type === "boolean" ? false : field.type === "integer" || field.type === "number" ? 0 : "",
    ]),
  );
}

function factFingerprint(caseItem: JudgmentCaseV1): string {
  return JSON.stringify(
    Object.entries(caseItem.facts).sort(([left], [right]) => left.localeCompare(right)),
  );
}

function newRef(): LocalEvidenceRefV1 {
  return { relative_path: "", sha256: "", kind: "document" };
}

/**
 * Collect reviewed examples and counterfactuals for a single existing Flow
 * decision. This component has no runner, signer, or remote-delivery path.
 */
export function JudgmentCaseCapture({
  context,
  onCapture,
}: {
  context: JudgmentCaseCaptureContextV1;
  onCapture: (caseItem: JudgmentCaseV1) => void;
}) {
  const [source, setSource] = useState(context.allowed_sources[0] || "demonstration");
  const [facts, setFacts] = useState<Record<string, FactValue>>(() => initialFacts(context));
  const [optionId, setOptionId] = useState("");
  const [disposition, setDisposition] =
    useState<JudgmentDispositionV1>("human_node");
  const [evidence, setEvidence] = useState<LocalEvidenceRefV1[]>([]);
  const [note, setNote] = useState<LocalEvidenceRefV1 | null>(null);
  const [contrastCaseIds, setContrastCaseIds] = useState<string[]>([]);
  const [error, setError] = useState("");

  const conflicts = useMemo(() => {
    const groups = new Map<string, Set<string>>();
    for (const item of context.cases) {
      if (!item.option_id) continue;
      const options = groups.get(factFingerprint(item)) || new Set<string>();
      options.add(item.option_id);
      groups.set(factFingerprint(item), options);
    }
    return [...groups.values()].filter((options) => options.size > 1).length;
  }, [context.cases]);
  const missingContrast = useMemo(
    () => contrastCaseIds.filter((id) => !context.cases.some((item) => item.id === id)),
    [context.cases, contrastCaseIds],
  );

  function validateReference(reference: LocalEvidenceRefV1, label: string): string | null {
    if (!reference.relative_path.trim() || !reference.sha256.trim() || !reference.kind.trim()) {
      return `${label} needs a local path, SHA-256, and kind.`;
    }
    if (!/^[a-f0-9]{64}$/i.test(reference.sha256.trim())) {
      return `${label} SHA-256 must contain 64 hexadecimal characters.`;
    }
    return null;
  }

  function capture() {
    setError("");
    if (disposition !== "more_evidence_required" && !optionId) {
      setError("Select the branch that this reviewed case supports.");
      return;
    }
    for (const [index, reference] of evidence.entries()) {
      const invalid = validateReference(reference, `Evidence reference ${index + 1}`);
      if (invalid) return setError(invalid);
    }
    if (note) {
      const invalid = validateReference(note, "The local review note");
      if (invalid) return setError(invalid);
    }
    if (missingContrast.length) {
      setError("A contrast case must refer to a saved local case.");
      return;
    }
    onCapture({
      id: caseId(),
      decision: context.decision,
      fact_schema_sha256: context.fact_schema_sha256,
      facts,
      local_evidence: evidence,
      review_note_ref: note,
      provenance: {
        source,
        source_ref_sha256: context.decision.decision_contract_sha256,
        reviewer_role: context.reviewer.role,
        reviewer_principal_ref_sha256: context.reviewer.principal_ref_sha256,
      },
      disposition,
      option_id: optionId || null,
      contrast_case_ids: contrastCaseIds,
    });
    setFacts(initialFacts(context));
    setOptionId("");
    setEvidence([]);
    setNote(null);
    setContrastCaseIds([]);
  }

  return (
    <Card id="qualification-judgment-cases-section">
      <CardHead
        eyebrow="Judgment cases"
        title="Capture reviewed examples before you automate a choice"
        sub="Use this optional path when the choice depends on institutional policy, precedence, or local context. Direct branch authoring remains the fast path."
      />
      <Callout tone="info" title="An example is not a new policy.">
        A saved case can support a reviewed rule candidate. It cannot change a production
        branch. A qualified reviewer must approve the exact rule after coverage and fault
        checks pass. Otherwise, Flow keeps the human decision node.
      </Callout>

      <div className="grid grid-2" style={{ marginTop: "var(--space-4)" }}>
        <div className="field">
          <label>Case source</label>
          <SegControl
            value={source}
            onChange={setSource}
            options={context.allowed_sources.map((value) => ({
              value,
              label: value.replace(/_/g, " "),
            }))}
          />
        </div>
        <div className="field">
          <label htmlFor="judgment-option">Reviewed branch</label>
          <select
            id="judgment-option"
            className="input"
            value={optionId}
            onChange={(event) => setOptionId(event.target.value)}
            disabled={disposition === "more_evidence_required"}
          >
            <option value="">Select a qualified branch</option>
            {context.options.map((option) => (
              <option key={option.id} value={option.id}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      <details open>
        <summary>Reviewed typed facts</summary>
        <div className="grid grid-2" style={{ marginTop: "var(--space-3)" }}>
          {Object.entries(context.fact_schema.fields).map(([name, field]) => (
            <div className="field" key={name}>
              <label htmlFor={`judgment-fact-${name}`}>{name.replace(/_/g, " ")}</label>
              {field.type === "boolean" ? (
                <select
                  id={`judgment-fact-${name}`}
                  className="input"
                  value={String(facts[name])}
                  onChange={(event) =>
                    setFacts((current) => ({ ...current, [name]: event.target.value === "true" }))
                  }
                >
                  <option value="false">No</option>
                  <option value="true">Yes</option>
                </select>
              ) : field.type === "enum" ? (
                <select
                  id={`judgment-fact-${name}`}
                  className="input"
                  value={String(facts[name])}
                  onChange={(event) => setFacts((current) => ({ ...current, [name]: event.target.value }))}
                >
                  <option value="">Select a value</option>
                  {(field.allowed_values || []).map((value) => (
                    <option key={value} value={value}>{value}</option>
                  ))}
                </select>
              ) : (
                <input
                  id={`judgment-fact-${name}`}
                  className="input"
                  type={field.type === "integer" || field.type === "number" ? "number" : "text"}
                  value={String(facts[name])}
                  onChange={(event) =>
                    setFacts((current) => ({
                      ...current,
                      [name]: field.type === "integer" || field.type === "number"
                        ? Number(event.target.value)
                        : event.target.value,
                    }))
                  }
                />
              )}
              <span className="page-sub mono">{field.type}</span>
            </div>
          ))}
        </div>
      </details>

      <details>
        <summary>Local evidence and optional review note</summary>
        <p className="page-sub">
          Store raw screenshots, record values, and free text on this device. This form only
          sends local content references to Flow. It does not send the content to Cloud.
        </p>
        {evidence.map((reference, index) => (
          <EvidenceReference
            key={`evidence-${index}`}
            label={`Evidence reference ${index + 1}`}
            value={reference}
            onChange={(next) => setEvidence((current) => current.map((item, itemIndex) => itemIndex === index ? next : item))}
            onRemove={() => setEvidence((current) => current.filter((_, itemIndex) => itemIndex !== index))}
          />
        ))}
        <Button onClick={() => setEvidence((current) => [...current, newRef()])}>Add local evidence</Button>
        <div style={{ marginTop: "var(--space-3)" }}>
          {note ? (
            <EvidenceReference
              label="Optional local review note"
              value={note}
              onChange={setNote}
              onRemove={() => setNote(null)}
            />
          ) : (
            <Button onClick={() => setNote(newRef())}>Add local review note</Button>
          )}
        </div>
      </details>

      <details open>
        <summary>What should qualification do with this case?</summary>
        <div style={{ marginTop: "var(--space-3)" }}>
          <SegControl
            value={disposition}
            onChange={setDisposition}
            options={dispositionOptions}
          />
          <p className="page-sub">
            {disposition === "automatic_rule" && "This requests a rule candidate for review. It does not enable automatic execution."}
            {disposition === "human_node" && "This preserves a permanent human decision node for this case."}
            {disposition === "more_evidence_required" && "This records that the current facts do not justify a branch. Add a contrast case."}
          </p>
        </div>
        {context.cases.length > 0 && (
          <div className="field" style={{ marginTop: "var(--space-3)" }}>
            <label>Contrast cases</label>
            {context.cases.map((item) => (
              <label className="checkbox-row" key={item.id}>
                <input
                  type="checkbox"
                  checked={contrastCaseIds.includes(item.id)}
                  onChange={(event) => setContrastCaseIds((current) => event.target.checked ? [...current, item.id] : current.filter((id) => id !== item.id))}
                />
                {item.id}
              </label>
            ))}
          </div>
        )}
      </details>

      <div className="card-inset" style={{ marginTop: "var(--space-4)" }}>
        <div className="row">
          <strong>Coverage and conflicts</strong>
          <span className="spacer" />
          <Pill tone={conflicts ? "warn" : "ok"}>{conflicts ? `${conflicts} conflict${conflicts === 1 ? "" : "s"}` : "No conflicts"}</Pill>
        </div>
        <p className="page-sub">
          {context.cases.length} saved local case{context.cases.length === 1 ? "" : "s"}. A conflict means identical reviewed facts support different branches. Qualification must keep the decision human or request a missing fact.
        </p>
      </div>

      {error && <Callout tone="crit" title="Case not saved">{error}</Callout>}
      <div className="row" style={{ marginTop: "var(--space-4)" }}>
        <Button variant="primary" onClick={capture} data-testid="capture-judgment-case">
          Save reviewed case locally
        </Button>
        <span className="page-sub">Flow seals the case into the next qualification revision. This does not create a runtime task.</span>
      </div>
    </Card>
  );
}

function EvidenceReference({
  label,
  value,
  onChange,
  onRemove,
}: {
  label: string;
  value: LocalEvidenceRefV1;
  onChange: (value: LocalEvidenceRefV1) => void;
  onRemove: () => void;
}) {
  return (
    <div className="card-inset" style={{ marginBottom: "var(--space-3)" }}>
      <div className="grid grid-3">
        <div className="field">
          <label>Local path</label>
          <input className="input mono" aria-label={`${label} local path`} value={value.relative_path} onChange={(event) => onChange({ ...value, relative_path: event.target.value })} />
        </div>
        <div className="field">
          <label>SHA-256</label>
          <input className="input mono" aria-label={`${label} SHA-256`} value={value.sha256} onChange={(event) => onChange({ ...value, sha256: event.target.value })} />
        </div>
        <div className="field">
          <label>Kind</label>
          <input className="input" aria-label={`${label} kind`} value={value.kind} onChange={(event) => onChange({ ...value, kind: event.target.value })} />
        </div>
      </div>
      <Button onClick={onRemove}>Remove</Button>
    </div>
  );
}
