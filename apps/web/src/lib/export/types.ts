import type { Column } from "../datasets";

export type Cell = string | number | null;
export type ExportRow = Record<string, Cell>;

export type ExportInput = {
  columns: Column[];
  rows: ExportRow[];
  meta: {
    downloadId: string;
    dataset: string;
    title: string;
    generatedAt: string; // ISO timestamp, UTC
    filters: string; // human readable
    organization: string;
    sampleData: boolean;
  };
};

export const NOTICE =
  "Licensed to the organization named above for its internal use under its subscription agreement. Do not redistribute.";
export const SAMPLE_NOTICE = "SAMPLE DATA: values are synthetic test data, not real company figures.";
