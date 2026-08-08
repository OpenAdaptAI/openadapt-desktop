import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { JudgmentCaseCaptureContextV1 } from "../lib/types";
import { JudgmentCaseCapture } from "./JudgmentCaseCapture";

const digest = "a".repeat(64);

function context(): JudgmentCaseCaptureContextV1 {
  return {
    decision: {
      graph_id: "main",
      state_id: "routing_review",
      workflow_contract_sha256: digest,
      decision_contract_sha256: "b".repeat(64),
    },
    fact_schema: {
      fields: {
        service_level: { type: "enum", allowed_values: ["standard", "urgent"] },
        reserved_capacity_available: { type: "boolean" },
        days_waiting: { type: "integer" },
      },
    },
    fact_schema_sha256: "c".repeat(64),
    options: [
      { id: "priority_review", label: "Priority review" },
      { id: "standard_review", label: "Standard review" },
      { id: "supervisor", label: "Supervisor" },
    ],
    reviewer: { role: "scheduling_lead", principal_ref_sha256: "d".repeat(64) },
    allowed_sources: ["historical_case", "counterfactual"],
    cases: [],
  };
}

afterEach(cleanup);

describe("JudgmentCaseCapture", () => {
  it("captures reviewed typed facts and keeps the optional note as a local evidence reference", () => {
    const onCapture = vi.fn();
    render(<JudgmentCaseCapture context={context()} onCapture={onCapture} />);

    fireEvent.change(screen.getByLabelText("Reviewed branch"), {
      target: { value: "priority_review" },
    });
    fireEvent.change(screen.getByLabelText("service level"), {
      target: { value: "urgent" },
    });
    fireEvent.change(screen.getByLabelText("reserved capacity available"), {
      target: { value: "true" },
    });
    fireEvent.change(screen.getByLabelText("days waiting"), { target: { value: "4" } });
    fireEvent.click(screen.getByRole("button", { name: "Add local review note" }));
    fireEvent.change(screen.getByLabelText("Optional local review note local path"), {
      target: { value: "evidence/review-note.txt" },
    });
    fireEvent.change(screen.getByLabelText("Optional local review note SHA-256"), {
      target: { value: "e".repeat(64) },
    });
    fireEvent.click(screen.getByTestId("capture-judgment-case"));

    expect(onCapture).toHaveBeenCalledWith(
      expect.objectContaining({
        decision: expect.objectContaining({ state_id: "routing_review" }),
        fact_schema_sha256: "c".repeat(64),
        facts: {
          service_level: "urgent",
          reserved_capacity_available: true,
          days_waiting: 4,
        },
        option_id: "priority_review",
        disposition: "human_node",
        review_note_ref: expect.objectContaining({
          relative_path: "evidence/review-note.txt",
          sha256: "e".repeat(64),
        }),
        provenance: {
          source: "historical_case",
          source_ref_sha256: "b".repeat(64),
          reviewer_role: "scheduling_lead",
          reviewer_principal_ref_sha256: "d".repeat(64),
        },
      }),
    );
  });

  it("does not allow an automatic rule candidate without a selected qualified branch", () => {
    const onCapture = vi.fn();
    render(<JudgmentCaseCapture context={context()} onCapture={onCapture} />);

    fireEvent.click(screen.getByRole("button", { name: "Rule candidate" }));
    fireEvent.click(screen.getByTestId("capture-judgment-case"));

    expect(onCapture).not.toHaveBeenCalled();
    expect(screen.getByText("Select the branch that this reviewed case supports.")).toBeTruthy();
  });
});
