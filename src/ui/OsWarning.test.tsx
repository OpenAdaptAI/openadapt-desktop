import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { OsWarning, detectPlatform, type DesktopPlatform } from "./OsWarning";

afterEach(cleanup);

function text(platform: DesktopPlatform): string {
  return render(<OsWarning platform={platform} />).container.textContent ?? "";
}

describe("OsWarning", () => {
  it("never gives Windows steps on Linux", () => {
    const copy = text("linux");
    expect(copy).not.toMatch(/SmartScreen|Windows/);
    expect(copy).toMatch(/SHA256SUMS/);
  });

  it("gives the current macOS path, with the old one only as a note", () => {
    const copy = text("mac");
    expect(copy).toContain("Privacy & Security");
    expect(copy).toContain("Open Anyway");
    expect(copy).toMatch(/macOS 14 or earlier/);
    expect(copy).not.toMatch(/notarization shortly|rolling out/i);
  });

  it("keeps the SmartScreen steps on Windows", () => {
    const copy = text("windows");
    expect(copy).toContain("SmartScreen");
    expect(copy).not.toMatch(/roadmap/i);
  });

  it("never promises signing on any platform", () => {
    for (const platform of ["mac", "windows", "linux", "other"] as const) {
      expect(text(platform)).not.toMatch(/shortly|soon|roadmap/i);
      cleanup();
    }
  });

  it("shows nothing for a platform it has no steps for", () => {
    expect(text("other")).toBe("");
  });
});

describe("detectPlatform", () => {
  it.each([
    [{ platform: "MacIntel", userAgent: "" }, "mac"],
    [{ platform: "Win32", userAgent: "" }, "windows"],
    [{ platform: "Linux x86_64", userAgent: "" }, "linux"],
    [{ platform: "", userAgent: "Mozilla/5.0 (X11; Linux x86_64)" }, "linux"],
    [{ platform: "", userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64)" }, "windows"],
    [{ platform: "", userAgent: "" }, "other"],
  ])("reads %o as %s", (nav, expected) => {
    expect(detectPlatform(nav)).toBe(expected);
  });
});
