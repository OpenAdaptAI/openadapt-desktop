// Watch-it-run — trigger a replay and watch the compile/replay rail + live log.
// Consumes replay_progress / log_line events; falls back to get_run_report.
import { useEffect, useId, useRef, useState } from "react";
import { CMD, engineInvoke, engineTry, onEngineEvent, EVT } from "../lib/engine";
import type {
  BrowserRuntimeStatus,
  ExecutionResponse,
  ExecutionTarget,
  ReplayProgress,
  RunReport,
  RunStep,
  TargetBackend,
} from "../lib/types";
import {
  Button,
  Card,
  CardHead,
  Callout,
  Field,
  Pill,
  SegControl,
} from "../ui/primitives";
import { ReplayMonitor } from "../ui/ReplayMonitor";

const TARGETS: Record<
  TargetBackend,
  { label: string; maturity: string; description: string }
> = {
  web: {
    label: "Browser",
    maturity: "Beta",
    description: "A web page opened and driven by OpenAdapt.",
  },
  windows: {
    label: "Windows",
    maturity: "Early access",
    description: "A native Windows desktop connected through the local WAA agent.",
  },
  macos: {
    label: "macOS",
    maturity: "Early access",
    description: "One native Mac application window.",
  },
  linux: {
    label: "Linux",
    maturity: "Qualification-specific",
    description:
      "One exact Linux application window through AT-SPI; support remains workflow-specific.",
  },
  rdp: {
    label: "RDP",
    maturity: "Early access",
    description: "A network RDP session or a local remote-desktop client window.",
  },
  citrix: {
    label: "Citrix",
    maturity: "Exploratory",
    description: "The local Citrix Workspace or Viewer session window.",
  },
};

type ExecuteMode = "replay" | "run";
type RdpMode = "network" | "window";
type RunIssue = { message: string; preActionRefusal: boolean };

export function WatchRun({
  workflowId,
  onTeach,
}: {
  workflowId: string;
  onTeach: (id: string) => void;
}) {
  const [report, setReport] = useState<RunReport | null>(null);
  const [running, setRunning] = useState(false);
  const [runtime, setRuntime] = useState<BrowserRuntimeStatus | null>(null);
  const [runIssue, setRunIssue] = useState<RunIssue | null>(null);
  const [target, setTarget] = useState<ExecutionTarget>({ backend: "web" });
  const [rdpMode, setRdpMode] = useState<RdpMode>("network");
  const [deploymentConfig, setDeploymentConfig] = useState("");
  const stepsRef = useRef<RunStep[]>([]);
  const fieldPrefix = useId();

  async function load() {
    const r = await engineTry<RunReport | null>(
      CMD.GET_RUN_REPORT,
      { workflow_id: workflowId },
      null,
    );
    if (r) {
      setReport(r);
      stepsRef.current = r.steps ?? [];
    }
  }

  useEffect(() => {
    void load();
    const unsubs = [
      onEngineEvent(EVT.LOG_LINE, (step: RunStep | { line: string }) => {
        if (!("index" in step)) return;
        const next = [...stepsRef.current.filter((s) => s.index !== step.index), step];
        next.sort((a, b) => a.index - b.index);
        stepsRef.current = next;
        setReport((r) => (r ? { ...r, steps: next } : r));
      }),
      onEngineEvent(EVT.REPLAY_PROGRESS, (progress: ReplayProgress) => {
        if (progress.workflow_id !== workflowId) return;
        setRunning(progress.state === "running");
      }),
      onEngineEvent(EVT.BROWSER_RUNTIME, (status: BrowserRuntimeStatus) => {
        if (status.workflow_id === workflowId) setRuntime(status);
      }),
    ];
    return () => unsubs.forEach((p) => p.then((u) => u()).catch(() => {}));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflowId]);

  function selectBackend(backend: TargetBackend) {
    // Reset on substrate changes so fields for the previous backend can never
    // leak into a new dispatch. The engine independently enforces this.
    setTarget({ backend });
    setRuntime(null);
    if (backend === "rdp") setRdpMode("network");
  }

  function setTargetField<K extends keyof ExecutionTarget>(
    key: K,
    value: ExecutionTarget[K],
  ) {
    setTarget((current) => ({ ...current, [key]: value || undefined }));
  }

  function switchRdpMode(mode: RdpMode) {
    setRdpMode(mode);
    setTarget((current) =>
      mode === "network"
        ? { backend: "rdp", rdp_host: current.rdp_host }
        : { backend: "rdp", rdp_window: current.rdp_window },
    );
  }

  async function execute(mode: ExecuteMode) {
    setRunning(true);
    setRunIssue(null);
    stepsRef.current = [];
    setReport((r) => (r ? { ...r, steps: [] } : r));
    try {
      const r = await engineInvoke<ExecutionResponse>(
        mode === "run" ? CMD.RUN_WORKFLOW : CMD.REPLAY_WORKFLOW,
        {
          workflow_id: workflowId,
          target,
          ...(deploymentConfig.trim()
            ? { deployment_config: deploymentConfig.trim() }
            : {}),
        },
      );
      if (r.outcome === "refused") {
        setRunIssue({
          message: r.error,
          preActionRefusal: r.pre_action_refusal,
        });
      } else {
        setReport(r);
        stepsRef.current = r.steps ?? [];
        if (r.outcome === "unknown") {
          setRunIssue({
            message:
              r.error || "Desktop could not classify the execution outcome.",
            preActionRefusal: false,
          });
        }
      }
    } catch (error) {
      setRunIssue({
        message:
          error instanceof Error
            ? error.message
            : "The engine connection ended unexpectedly.",
        preActionRefusal: false,
      });
    } finally {
      setRunning(false);
    }
  }

  const total = report?.total_steps ?? 0;
  const steps = report?.steps ?? [];
  const selected = TARGETS[target.backend];

  return (
    <div className="content">
      <div className="page-head">
        <div className="titles">
          <p className="eyebrow">Execute</p>
          <h1>{report?.workflow_name ?? "Watch it run"}</h1>
        </div>
        <div className="row">
          <Button disabled={running} onClick={() => execute("replay")}>
            {running ? "Running…" : "Replay"}
          </Button>
          <Button
            variant="primary"
            disabled={running}
            onClick={() => execute("run")}
          >
            Run with safety gates
          </Button>
        </div>
      </div>

      <Card>
        <CardHead
          eyebrow="Target"
          title="Where should this workflow run?"
          sub="Choose the application surface. OpenAdapt uses the same compiled workflow and fail-closed verification on every target."
        />
        <Field label="Application surface" htmlFor={`${fieldPrefix}-backend`}>
          <select
            id={`${fieldPrefix}-backend`}
            className="input"
            value={target.backend}
            aria-describedby={`${fieldPrefix}-backend-description`}
            onChange={(event) =>
              selectBackend(event.target.value as TargetBackend)
            }
          >
            {(Object.keys(TARGETS) as TargetBackend[]).map((backend) => (
              <option key={backend} value={backend}>
                {TARGETS[backend].label}
              </option>
            ))}
          </select>
        </Field>
        <div
          className="row target-summary"
          id={`${fieldPrefix}-backend-description`}
        >
          <Pill tone={target.backend === "citrix" ? "warn" : "neutral"}>
            {selected.maturity}
          </Pill>
          <span className="page-sub">{selected.description}</span>
        </div>

        <TargetFields
          target={target}
          rdpMode={rdpMode}
          setField={setTargetField}
          switchRdpMode={switchRdpMode}
          idPrefix={fieldPrefix}
        />

        <details className="advanced-target">
          <summary>Advanced deployment details</summary>
          <div className="advanced-target-body">
            <Field
              label="Deployment config"
              hint="Optional local YAML/JSON file for policy, effect verification, credentials, and other advanced Flow settings."
              htmlFor={`${fieldPrefix}-deployment-config`}
              hintId={`${fieldPrefix}-deployment-config-hint`}
            >
              <input
                id={`${fieldPrefix}-deployment-config`}
                className="input mono"
                value={deploymentConfig}
                aria-describedby={`${fieldPrefix}-deployment-config-hint ${fieldPrefix}-deployment-config-note`}
                onChange={(event) => setDeploymentConfig(event.target.value)}
                placeholder="/path/to/deployment.yaml"
                spellCheck={false}
              />
            </Field>
            <p
              className="page-sub"
              id={`${fieldPrefix}-deployment-config-note`}
            >
              Desktop merges target details into a short-lived private config.
              Secret and selector values stay off the process command line and
              out of Desktop logs.
            </p>
          </div>
        </details>
      </Card>

      <Card>
        {target.backend === "web" && runtime && runtime.state !== "ready" && (
          <Callout
            tone={runtime.state === "error" ? "warn" : "info"}
            title={
              runtime.state === "installing"
                ? "Preparing the browser"
                : runtime.state === "error"
                  ? "Browser setup needs attention"
                  : "Checking the browser"
            }
          >
            {runtime.detail}
          </Callout>
        )}
        {runIssue && (
          <Callout
            tone="warn"
            title={
              runIssue.preActionRefusal
                ? "Execution was refused before action"
                : "Execution outcome needs verification"
            }
          >
            {runIssue.message}{" "}
            {runIssue.preActionRefusal
              ? "Correct the configuration before trying again; Flow was not invoked."
              : "Do not retry until you verify the target application or system of record and inspect the retained run evidence; the prior dispatch may have delivered an action."}
          </Callout>
        )}
        <ReplayMonitor
          workflowName={report?.workflow_name ?? workflowId}
          steps={steps}
          total={total || steps.length}
        />
      </Card>

      {report?.halt && (
        <Card>
          <CardHead eyebrow="Halted" title="This run stopped safely" />
          <Callout tone="warn" title={report.halt.step_intent}>
            {report.halt.reason}
            {report.halt.resolver_rung
              ? ` (resolver: ${report.halt.resolver_rung})`
              : ""}
          </Callout>
          <div className="row" style={{ marginTop: "var(--space-4)" }}>
            <Button variant="primary" onClick={() => onTeach(workflowId)}>
              Teach the fix
            </Button>
          </div>
        </Card>
      )}

      {report?.metrics && (
        <Card>
          <CardHead eyebrow="Run report" title="Metrics" />
          <div className="metrics">
            <div className="metric">
              <span className="label">Steps</span>
              <span className="metric-value tnum">{report.total_steps}</span>
            </div>
            {report.metrics.duration_s != null && (
              <div className="metric">
                <span className="label">Duration</span>
                <span className="metric-value tnum">
                  {report.metrics.duration_s.toFixed(1)}s
                </span>
              </div>
            )}
            {report.metrics.cost_usd != null && (
              <div className="metric">
                <span className="label">Cost</span>
                <span className="metric-value tnum">
                  ${report.metrics.cost_usd.toFixed(3)}
                </span>
              </div>
            )}
          </div>
        </Card>
      )}
    </div>
  );
}

function TargetFields({
  target,
  rdpMode,
  setField,
  switchRdpMode,
  idPrefix,
}: {
  target: ExecutionTarget;
  rdpMode: RdpMode;
  setField: <K extends keyof ExecutionTarget>(
    key: K,
    value: ExecutionTarget[K],
  ) => void;
  switchRdpMode: (mode: RdpMode) => void;
  idPrefix: string;
}) {
  switch (target.backend) {
    case "web":
      return (
        <Field
          label="Page URL"
          hint="Leave blank only to use Flow's bundled local demonstration."
          htmlFor={`${idPrefix}-web-url`}
          hintId={`${idPrefix}-web-url-hint`}
        >
          <input
            id={`${idPrefix}-web-url`}
            className="input"
            type="url"
            value={target.url ?? ""}
            aria-describedby={`${idPrefix}-web-url-hint`}
            onChange={(event) => setField("url", event.target.value)}
            placeholder="https://your-app.example"
            spellCheck={false}
          />
        </Field>
      );
    case "windows":
      return (
        <Field
          label="Windows connection"
          hint="The WAA agent URL on this machine or through your approved tunnel."
          htmlFor={`${idPrefix}-windows-agent`}
          hintId={`${idPrefix}-windows-agent-hint`}
        >
          <input
            id={`${idPrefix}-windows-agent`}
            className="input"
            type="url"
            value={target.agent_url ?? ""}
            aria-describedby={`${idPrefix}-windows-agent-hint`}
            onChange={(event) => setField("agent_url", event.target.value)}
            placeholder="http://localhost:5001"
            spellCheck={false}
          />
        </Field>
      );
    case "macos":
      return (
        <>
          <Field
            label="Mac application"
            hint="The owner application, for example TextEdit."
            htmlFor={`${idPrefix}-macos-app`}
            hintId={`${idPrefix}-macos-app-hint`}
          >
            <input
              id={`${idPrefix}-macos-app`}
              className="input"
              value={target.macos_app ?? ""}
              aria-describedby={`${idPrefix}-macos-app-hint`}
              onChange={(event) => setField("macos_app", event.target.value)}
              placeholder="TextEdit"
            />
          </Field>
          <Field
            label="Window title"
            hint="Optional title substring; ambiguous matches stop safely."
            htmlFor={`${idPrefix}-macos-window-title`}
            hintId={`${idPrefix}-macos-window-title-hint`}
          >
            <input
              id={`${idPrefix}-macos-window-title`}
              className="input"
              value={target.macos_window_title ?? ""}
              aria-describedby={`${idPrefix}-macos-window-title-hint`}
              onChange={(event) =>
                setField("macos_window_title", event.target.value)
              }
            />
          </Field>
        </>
      );
    case "linux":
      return (
        <>
          <Field
            label="Linux application"
            hint="Exact AT-SPI application name."
            htmlFor={`${idPrefix}-linux-app`}
            hintId={`${idPrefix}-linux-app-hint`}
          >
            <input
              id={`${idPrefix}-linux-app`}
              className="input"
              value={target.linux_app ?? ""}
              aria-describedby={`${idPrefix}-linux-app-hint`}
              onChange={(event) => setField("linux_app", event.target.value)}
              placeholder="gedit"
            />
          </Field>
          <Field
            label="Window title"
            hint="Exact top-level title; zero or multiple matches stop safely."
            htmlFor={`${idPrefix}-linux-window-title`}
            hintId={`${idPrefix}-linux-window-title-hint`}
          >
            <input
              id={`${idPrefix}-linux-window-title`}
              className="input"
              value={target.linux_window_title ?? ""}
              aria-describedby={`${idPrefix}-linux-window-title-hint`}
              onChange={(event) =>
                setField("linux_window_title", event.target.value)
              }
            />
          </Field>
          <label
            className="check-row"
            htmlFor={`${idPrefix}-linux-physical-input`}
          >
            <input
              id={`${idPrefix}-linux-physical-input`}
              type="checkbox"
              checked={Boolean(target.linux_allow_physical_input)}
              onChange={(event) =>
                setField("linux_allow_physical_input", event.target.checked)
              }
            />
            <span>
              Allow window-bound physical input if native AT-SPI action is
              unavailable
            </span>
          </label>
        </>
      );
    case "rdp":
      return (
        <>
          <fieldset className="field target-fieldset">
            <legend>RDP connection</legend>
            <SegControl<RdpMode>
              value={rdpMode}
              onChange={switchRdpMode}
              options={[
                { value: "network", label: "Network host" },
                { value: "window", label: "Local client window" },
              ]}
            />
          </fieldset>
          {rdpMode === "network" ? (
            <Field
              label="RDP host"
              hint="Host name or IP only. Credentials stay in the deployment config."
              htmlFor={`${idPrefix}-rdp-host`}
              hintId={`${idPrefix}-rdp-host-hint`}
            >
              <input
                id={`${idPrefix}-rdp-host`}
                className="input"
                value={target.rdp_host ?? ""}
                aria-describedby={`${idPrefix}-rdp-host-hint`}
                onChange={(event) => setField("rdp_host", event.target.value)}
                placeholder="10.0.0.5"
                spellCheck={false}
              />
            </Field>
          ) : (
            <Field
              label="Client window owner"
              hint="Exact local app/process that paints the remote session."
              htmlFor={`${idPrefix}-rdp-window`}
              hintId={`${idPrefix}-rdp-window-hint`}
            >
              <input
                id={`${idPrefix}-rdp-window`}
                className="input"
                value={target.rdp_window ?? ""}
                aria-describedby={`${idPrefix}-rdp-window-hint`}
                onChange={(event) => setField("rdp_window", event.target.value)}
                placeholder="Microsoft Remote Desktop"
              />
            </Field>
          )}
          <RemoteWindowFields
            target={target}
            setField={setField}
            idPrefix={`${idPrefix}-rdp`}
          />
        </>
      );
    case "citrix":
      return (
        <>
          <Callout tone="info" title="Open the session first">
            OpenAdapt targets the Citrix Workspace or Viewer window already
            running on this computer. The default owner is selected for your
            operating system.
          </Callout>
          <Field
            label="Ready-screen text"
            hint="Stable text that confirms the intended app is open before input. Required by governed Citrix runs."
            htmlFor={`${idPrefix}-citrix-readiness`}
            hintId={`${idPrefix}-citrix-readiness-hint`}
          >
            <input
              id={`${idPrefix}-citrix-readiness`}
              className="input"
              value={target.rdp_readiness_text ?? ""}
              aria-describedby={`${idPrefix}-citrix-readiness-hint`}
              onChange={(event) =>
                setField("rdp_readiness_text", event.target.value)
              }
              placeholder="Patient Search"
            />
          </Field>
          <RemoteWindowFields
            target={target}
            setField={setField}
            idPrefix={`${idPrefix}-citrix`}
            citrix
          />
        </>
      );
  }
}

function RemoteWindowFields({
  target,
  setField,
  idPrefix,
  citrix = false,
}: {
  target: ExecutionTarget;
  setField: <K extends keyof ExecutionTarget>(
    key: K,
    value: ExecutionTarget[K],
  ) => void;
  idPrefix: string;
  citrix?: boolean;
}) {
  return (
    <>
      {citrix && (
        <Field
          label="Workspace window owner"
          hint="Optional override for a nonstandard Citrix client name."
          htmlFor={`${idPrefix}-window`}
          hintId={`${idPrefix}-window-hint`}
        >
          <input
            id={`${idPrefix}-window`}
            className="input"
            value={target.rdp_window ?? ""}
            aria-describedby={`${idPrefix}-window-hint`}
            onChange={(event) => setField("rdp_window", event.target.value)}
            placeholder="Citrix Viewer"
          />
        </Field>
      )}
      <Field
        label="Session window title"
        hint="Optional exact title to disambiguate multiple remote sessions."
        htmlFor={`${idPrefix}-window-title`}
        hintId={`${idPrefix}-window-title-hint`}
      >
        <input
          id={`${idPrefix}-window-title`}
          className="input"
          value={target.rdp_window_title ?? ""}
          aria-describedby={`${idPrefix}-window-title-hint`}
          onChange={(event) =>
            setField("rdp_window_title", event.target.value)
          }
        />
      </Field>
      {!citrix && (
        <Field
          label="Ready-screen text"
          hint="Optional stable text checked on the current remote frame before input."
          htmlFor={`${idPrefix}-readiness`}
          hintId={`${idPrefix}-readiness-hint`}
        >
          <input
            id={`${idPrefix}-readiness`}
            className="input"
            value={target.rdp_readiness_text ?? ""}
            aria-describedby={`${idPrefix}-readiness-hint`}
            onChange={(event) =>
              setField("rdp_readiness_text", event.target.value)
            }
          />
        </Field>
      )}
    </>
  );
}
