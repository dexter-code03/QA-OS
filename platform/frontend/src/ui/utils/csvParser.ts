/**
 * Browser-side CSV parser for manual test case import.
 * No network calls — parsing runs entirely in the browser.
 */

export interface ColumnMapping {
  name: string;
  steps: string;
  expected: string | null;
  priority: string | null;
}

export interface ManualTestCase {
  name: string;
  steps: string[];
  expected: string;
  priority: string | null;
}

const NAME_SIGNALS = ["name", "title", "test", "case", "scenario", "id", "tc", "summary"];
const STEPS_SIGNALS = ["step", "action", "description", "procedure", "how", "instruction"];
const EXPECTED_SIGNALS = ["expected", "result", "outcome", "verify", "assert", "pass", "criteria"];
const PRIORITY_SIGNALS = ["priority", "severity", "p1", "p2", "p3", "critical", "high", "medium"];

function bestMatch(
  headers: string[],
  signals: string[],
  fallbackIndex: number,
): string | null {
  for (const header of headers) {
    const h = header.toLowerCase();
    if (signals.some((s) => h.includes(s))) return header;
  }
  return headers[fallbackIndex] ?? null;
}

export function detectColumns(headers: string[]): ColumnMapping {
  return {
    name: bestMatch(headers, NAME_SIGNALS, 0) ?? headers[0] ?? "",
    steps: bestMatch(headers, STEPS_SIGNALS, 1) ?? headers[1] ?? "",
    expected: bestMatch(headers, EXPECTED_SIGNALS, 2),
    priority: bestMatch(headers, PRIORITY_SIGNALS, -1),
  };
}

/** True when auto-detection is confident (both required fields matched a signal). */
export function isDetectionConfident(headers: string[], mapping: ColumnMapping): boolean {
  const nameMatch = headers.some((h) => NAME_SIGNALS.some((s) => h.toLowerCase().includes(s)));
  const stepsMatch = headers.some((h) => STEPS_SIGNALS.some((s) => h.toLowerCase().includes(s)));
  return nameMatch && stepsMatch;
}

/**
 * Split a single text block of manual steps into individual step strings.
 * Tries newline, numbered prefix, semicolons, then pipes — in priority order.
 */
export function splitSteps(raw: string): string[] {
  if (!raw.trim()) return [raw.trim()].filter(Boolean);

  // Try newline first (most common in Google Sheets multi-line cells)
  if (raw.includes("\n")) {
    const parts = raw
      .split("\n")
      .map((s) => s.replace(/^\d+[.)]\s*/, "").trim())
      .filter(Boolean);
    if (parts.length > 1) return parts;
  }

  // Numbered prefix: "1. step  2. step" or "1) step  2) step"
  const numbered = raw.split(/(?=\d+[.)]\s)/).map((s) => s.replace(/^\d+[.)]\s*/, "").trim()).filter(Boolean);
  if (numbered.length > 1) return numbered;

  // Semicolons
  if (raw.includes(";")) {
    const parts = raw.split(";").map((s) => s.trim()).filter(Boolean);
    if (parts.length > 1) return parts;
  }

  // Pipes
  if (raw.includes("|")) {
    const parts = raw.split("|").map((s) => s.trim()).filter(Boolean);
    if (parts.length > 1) return parts;
  }

  return [raw.trim()];
}

/**
 * Parse raw CSV text with RFC 4180-aware field splitting.
 * Handles quoted fields containing commas, newlines, and escaped quotes.
 */
function parseRow(line: string): string[] {
  const fields: string[] = [];
  let current = "";
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (inQuotes) {
      if (ch === '"' && line[i + 1] === '"') {
        current += '"';
        i++;
      } else if (ch === '"') {
        inQuotes = false;
      } else {
        current += ch;
      }
    } else {
      if (ch === '"') {
        inQuotes = true;
      } else if (ch === ",") {
        fields.push(current.trim());
        current = "";
      } else {
        current += ch;
      }
    }
  }
  fields.push(current.trim());
  return fields;
}

/**
 * Main entry point: parse CSV text into ManualTestCase[].
 * If no mapping is provided, auto-detects columns.
 */
export function parseCSV(csvText: string, mapping?: ColumnMapping): ManualTestCase[] {
  const lines = csvText.trim().split(/\r?\n/);
  if (lines.length < 2) return [];

  const headers = parseRow(lines[0]);
  const colMap = mapping ?? detectColumns(headers);

  return lines
    .slice(1)
    .map((line) => {
      const values = parseRow(line);
      const row: Record<string, string> = {};
      headers.forEach((h, i) => { row[h] = values[i] ?? ""; });

      const rawSteps = row[colMap.steps] ?? "";
      const steps = splitSteps(rawSteps);

      return {
        name: (row[colMap.name] ?? "").trim(),
        steps: steps.length ? steps : [rawSteps.trim()].filter(Boolean),
        expected: (colMap.expected ? row[colMap.expected] : "") ?? "",
        priority: colMap.priority ? (row[colMap.priority] ?? null) : null,
      };
    })
    .filter((t) => t.name.trim() !== "");
}
