import { z } from "zod";
import { DATASETS, FORMATS } from "./entitlements";

const isoDate = z
  .string()
  .regex(/^\d{4}-\d{2}-\d{2}$/, "Use YYYY-MM-DD")
  .refine((s) => !Number.isNaN(Date.parse(`${s}T00:00:00Z`)) && new Date(`${s}T00:00:00Z`).toISOString().startsWith(s), "Not a real date");

/** "PLD, o spg" -> ["PLD","O","SPG"]; empty -> [] */
const list = (re: RegExp, max: number, upper = true) =>
  z
    .string()
    .max(10_000)
    .optional()
    .transform((s) =>
      [...new Set((s ?? "").split(/[\s,;]+/).map((x) => (upper ? x.trim().toUpperCase() : x.trim())).filter(Boolean))],
    )
    .refine((xs) => xs.length <= max, `At most ${max} values`)
    .refine((xs) => xs.every((x) => re.test(x)), "Contains a value that isn't allowed");

const blankToUndefined = (v: unknown) => (v === "" || v === null ? undefined : v);

export const downloadRequest = z
  .object({
    dataset: z.enum(DATASETS),
    format: z.enum(FORMATS),
    tickers: list(/^[A-Z][A-Z0-9.-]{0,15}$/, 500),
    metrics: list(/^[A-Z0-9_]{1,48}$/, 200),
    periodTypes: list(/^(Q|YTD|FY|TTM|INSTANT)$/, 5),
    forms: list(/^[A-Z0-9/-]{1,32}$/, 50),
    from: z.preprocess(blankToUndefined, isoDate.optional()),
    to: z.preprocess(blankToUndefined, isoDate.optional()),
    asOf: z.preprocess(blankToUndefined, isoDate.optional()),
  })
  .refine((r) => !r.from || !r.to || r.from <= r.to, { message: "From must be on or before To", path: ["from"] });

export type DownloadRequest = z.infer<typeof downloadRequest>;

export const email = z.string().trim().toLowerCase().pipe(z.email("Enter a valid email address").max(254));

export function formToObject(form: FormData): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of form.entries()) if (typeof v === "string") out[k] = v;
  return out;
}
