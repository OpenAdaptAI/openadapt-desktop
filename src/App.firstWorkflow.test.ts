import { expect, it } from "vitest";
import type { FirstWorkflowState } from "./lib/types";
import { routeForFirstWorkflow } from "./App";

function state(
  stage: FirstWorkflowState["stage"],
): FirstWorkflowState {
  return {
    stage,
    capture_id: "capture-1",
    workflow_id: "workflow-1",
    target: { backend: "web", url: "https://example.test" },
    task: "Read one test record",
    updated_at: "2026-08-26T00:00:00Z",
  };
}

it("resumes the durable first workflow at its exact user stage", () => {
  expect(routeForFirstWorkflow(state("record"))).toEqual({
    name: "record",
    firstWorkflow: true,
    target: { backend: "web", url: "https://example.test" },
    task: "Read one test record",
  });
  expect(routeForFirstWorkflow(state("review"))).toEqual({
    name: "watch",
    id: "workflow-1",
    target: { backend: "web", url: "https://example.test" },
    firstWorkflow: true,
    firstRunComplete: false,
  });
  expect(routeForFirstWorkflow(state("result"))).toEqual({
    name: "watch",
    id: "workflow-1",
    target: { backend: "web", url: "https://example.test" },
    firstWorkflow: true,
    firstRunComplete: true,
  });
  expect(routeForFirstWorkflow(state("qualification"))).toEqual({
    name: "qualify",
    id: "workflow-1",
    firstWorkflow: true,
  });
  expect(routeForFirstWorkflow(state("complete"))).toBeNull();
});
