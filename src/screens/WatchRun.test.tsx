import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CMD, engineInvoke, engineTry } from "../lib/engine";
import type {
  ExecutionResponse,
  QualificationProject,
  RunReport,
} from "../lib/types";
import { WatchRun } from "./WatchRun";

vi.mock("../lib/engine", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/engine")>();
  return {
    ...original,
    engineInvoke: vi.fn(),
    engineTry: vi.fn(),
    onEngineEvent: vi.fn(() => Promise.resolve(() => {})),
  };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function preciseReport(
  outcome: "VERIFIED" | "ROLLED_BACK",
): RunReport {
  const verified = outcome === "VERIFIED";
  return {
    ok: verified,
    outcome,
    pre_action_refusal: false,
    run_id: "run-1",
    workflow_id: "workflow-1",
    workflow_name: "Reference workflow",
    total_steps: 1,
    steps: [],
    outcome_details: {
      profile: "standard",
      production_eligible: verified,
      execution_completed: true,
      required_contracts: {
        authorization: 1,
        identity: 1,
        postcondition: 1,
        effect: 1,
      },
      passed_contracts: {
        authorization: 1,
        identity: 1,
        postcondition: 1,
        effect: verified ? 1 : 0,
      },
      evidence_classes: verified
        ? ["authorization", "identity", "effect_tier_1"]
        : ["authorization", "identity", "compensation"],
      model_calls: verified ? 0 : 2,
      external_network_calls: verified ? "none" : "observed",
      compensation_actions: verified ? 0 : 1,
    },
  };
}

it("renders the precise execution evidence returned by the sidecar", async () => {
  const report = preciseReport("ROLLED_BACK");
  vi.mocked(engineTry).mockResolvedValue(report);

  render(
    <WatchRun
      workflowId="workflow-1"
      initialTarget={{ backend: "web" }}
      onQualify={() => {}}
      onTeach={() => {}}
    />,
  );

  const modelCalls = await screen.findByText("Model calls");
  expect(modelCalls.parentElement?.textContent).toContain("2");
  expect(screen.getByText("External network").parentElement?.textContent).toContain(
    "observed",
  );
  expect(
    screen.getByText("Compensating actions").parentElement?.textContent,
  ).toContain("1");
  expect(screen.getByText("0/1")).toBeTruthy();
});

it("clears prior terminal evidence while a new run is in flight", async () => {
  const verified = preciseReport("VERIFIED");
  let finishRun!: (report: ExecutionResponse) => void;
  const pendingRun = new Promise<ExecutionResponse>((resolve) => {
    finishRun = resolve;
  });
  vi.mocked(engineTry).mockResolvedValue(verified);
  vi.mocked(engineInvoke).mockReturnValue(pendingRun);

  render(
    <WatchRun
      workflowId="workflow-1"
      initialTarget={{ backend: "web" }}
      onQualify={() => {}}
      onTeach={() => {}}
    />,
  );
  await screen.findByText("Outcome evidence");

  fireEvent.click(screen.getByRole("button", { name: "Run with safety gates" }));

  await waitFor(() =>
    expect(screen.queryByText("Outcome evidence")).toBeNull(),
  );

  await act(async () => {
    finishRun(verified);
    await pendingRun;
  });
  expect(await screen.findByText("Outcome evidence")).toBeTruthy();
});

it("keeps a report visible while local history is retried", async () => {
  const degraded = {
    ...preciseReport("VERIFIED"),
    persistence: {
      state: "degraded" as const,
      retryable: true,
      message: "The report is visible, but local history is not saved.",
    },
  };
  const persisted = {
    ...degraded,
    persistence: {
      state: "persisted" as const,
      retryable: false,
      message: "The report is saved in local history.",
    },
  };
  vi.mocked(engineTry).mockResolvedValue(degraded);
  vi.mocked(engineInvoke).mockResolvedValue({ ok: true, report: persisted });

  render(
    <WatchRun
      workflowId="workflow-1"
      initialTarget={{ backend: "web" }}
      onQualify={() => {}}
      onTeach={() => {}}
    />,
  );

  fireEvent.click(
    await screen.findByRole("button", { name: "Retry local history save" }),
  );
  await waitFor(() =>
    expect(engineInvoke).toHaveBeenCalledWith("retry_run_persistence", {
      workflow_id: "workflow-1",
      run_id: "run-1",
    }),
  );
  await waitFor(() =>
    expect(screen.queryByText("Local run history needs attention")).toBeNull(),
  );
  expect(screen.getByText("Outcome evidence")).toBeTruthy();
});

function firstWorkflowReview(): QualificationProject {
  return {
    ok: true,
    workflow_id: "workflow-1",
    graph: {
      bundle: {
        name: "Save a test note",
        action_count: 2,
        irreversible_count: 0,
        identity_armed_count: 0,
        identity_unarmed_count: 1,
        effect_count: 0,
        encrypted: false,
        provenance: {},
      },
      nodes: [
        {
          id: "step-1",
          index: 0,
          kind: "action",
          title: "Enter the test note",
          effects: [],
          postconditions: [],
          halts: [],
          badges: [],
        },
        {
          id: "step-2",
          index: 1,
          kind: "action",
          title: "Save the note",
          effects: [],
          postconditions: [],
          halts: [],
          badges: [],
        },
      ],
      edges: [],
    },
    controls: {
      parameters: [
        {
          name: "note_text",
          type: "string",
          secret: false,
          required: true,
          example: null,
          choices: [],
        },
      ],
      actions: {
        "step-1": {
          step_id: "step-1",
          execution_paths: ["gui"],
          classification: {
            step_id: "step-1",
            classification: "state_changing",
            explanation: "The action changes a field.",
            operator_confirmed: false,
          },
          identity: { can_arm: false, armed: false, sources: [] },
          effects: [],
        },
        "step-2": {
          step_id: "step-2",
          execution_paths: ["gui"],
          classification: {
            step_id: "step-2",
            classification: "consequential",
            explanation: "The action saves the note.",
            operator_confirmed: false,
          },
          identity: { can_arm: false, armed: false, sources: [] },
          effects: [],
        },
      },
      business_decisions: {
        available: false,
        required_flow_capability: "qualification.set_business_decision",
        graphs: [],
      },
      judgment_cases: {
        available: false,
        required_flow_capability: "qualification.set_judgment_cases",
        contexts: [],
        report: null,
      },
    },
  } as unknown as QualificationProject;
}

it("requires the first user to review the compiled workflow before a supervised run", async () => {
  const qualification = firstWorkflowReview();
  vi.mocked(engineTry).mockImplementation(async (command) => {
    if (command === CMD.GET_QUALIFICATION) return qualification;
    return null;
  });
  vi.mocked(engineInvoke).mockResolvedValue(preciseReport("VERIFIED"));
  const onQualify = vi.fn();

  render(
    <WatchRun
      workflowId="workflow-1"
      initialTarget={{ backend: "web", url: "https://example.test" }}
      firstWorkflow
      onQualify={onQualify}
      onTeach={() => {}}
    />,
  );

  expect(await screen.findByText("Enter the test note")).toBeTruthy();
  expect(screen.getByText("Save the note")).toBeTruthy();
  expect(screen.getByText("note_text")).toBeTruthy();
  expect(screen.getByText("state changing")).toBeTruthy();
  expect(screen.getByText("consequential")).toBeTruthy();

  const runButton = screen.getByRole("button", {
    name: "Run once while I watch",
  }) as HTMLButtonElement;
  expect(runButton.disabled).toBe(true);

  fireEvent.click(
    screen.getByRole("checkbox", {
      name: "I reviewed these steps and will keep the target app in view.",
    }),
  );
  expect(runButton.disabled).toBe(false);
  fireEvent.click(runButton);

  await waitFor(() =>
    expect(engineInvoke).toHaveBeenCalledWith("replay_workflow", {
      workflow_id: "workflow-1",
      target: { backend: "web", url: "https://example.test" },
    }),
  );
  fireEvent.click(
    await screen.findByRole("button", {
      name: "Review identity, effects, and policy",
    }),
  );
  expect(onQualify).toHaveBeenCalledWith("workflow-1");
});
