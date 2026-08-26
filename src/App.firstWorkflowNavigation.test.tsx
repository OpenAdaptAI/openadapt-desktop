import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

vi.mock("./lib/engine", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./lib/engine")>();
  return {
    ...actual,
    engineTry: vi.fn(),
    onEngineEvent: vi.fn(async () => () => {}),
    sidecarRunning: vi.fn(async () => true),
  };
});

vi.mock("./screens/Onboarding", () => ({
  Onboarding: ({ onStart }: { onStart: () => void }) => (
    <button type="button" onClick={onStart}>
      Start first workflow
    </button>
  ),
}));

vi.mock("./screens/RecordReview", () => ({
  RecordReview: () => <div>First workflow recording</div>,
}));

import App from "./App";
import { CMD, engineTry } from "./lib/engine";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it("stays in onboarding when its durable stage cannot be saved", async () => {
  let stageAttempts = 0;
  vi.mocked(engineTry).mockImplementation(async (command) => {
    if (command === CMD.GET_AUTH_STATUS) return { authenticated: true };
    if (command === CMD.GET_WORKFLOWS) return [];
    if (command === CMD.GET_FIRST_WORKFLOW_STATE) {
      return { ok: true, state: null };
    }
    if (command === CMD.GET_NEEDS_ATTENTION) {
      return { count: 0, open_halts: 0, failed_runs: 0 };
    }
    if (command === CMD.GET_SYNC_STATE) return { state: "synced", queued: 0 };
    if (command === CMD.SET_FIRST_WORKFLOW_STAGE) {
      stageAttempts += 1;
      return { ok: stageAttempts > 1 };
    }
    return null;
  });

  render(<App />);
  const start = await screen.findByRole("button", {
    name: "Start first workflow",
  });
  fireEvent.click(start);

  const alert = await screen.findByRole("alert");
  expect(alert.textContent).toContain(
    "Desktop couldn't save your place in setup. Check the local engine, then try again.",
  );
  expect(screen.getByRole("button", { name: "Start first workflow" })).toBeTruthy();
  expect(screen.queryByText("First workflow recording")).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "Start first workflow" }));
  await waitFor(() =>
    expect(screen.getByText("First workflow recording")).toBeTruthy(),
  );
});
