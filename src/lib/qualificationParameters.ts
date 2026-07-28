import type { QualificationParameter } from "./types";

export type QualificationParameterValues = Record<string, string>;

export type QualificationParameterSerialization =
  | { ok: true; json: string }
  | { ok: false; error: string };

/** Convert the default case form to the exact JSON wire format used by Flow. */
export function serializeQualificationParameters(
  parameters: QualificationParameter[],
  values: QualificationParameterValues,
): QualificationParameterSerialization {
  const payload: Record<string, string | number> = {};
  for (const parameter of parameters) {
    if (parameter.secret) continue;
    const value = values[parameter.name] ?? "";
    if (value === "") continue;
    if (parameter.type === "number") {
      const parsed = Number(value);
      if (!Number.isFinite(parsed)) {
        return {
          ok: false,
          error: `${parameter.name} must be a valid number.`,
        };
      }
      payload[parameter.name] = parsed;
    } else {
      payload[parameter.name] = value;
    }
  }
  return { ok: true, json: JSON.stringify(payload) };
}
