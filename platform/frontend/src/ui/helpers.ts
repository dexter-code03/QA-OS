import { useCallback, useState } from "react";
import type { Build, ModuleDef, Project, Run, SuiteDef, TestDef } from "./api";

/* ── Types ──────────────────────────────────────────── */
export type Page = "dashboard" | "execution" | "library" | "reports" | "builds" | "settings";

export type ExecutionSetupPreset = {
  testId: number;
  buildId: number | null;
  platform: Run["platform"];
  deviceTarget: string;
};

/* ── Toast system ───────────────────────────────────── */
export type ToastType = "info" | "error" | "success";
type Toast = { id: number; msg: string; type: ToastType };
let _tid = 0;
let _addToast: (m: string, t: ToastType) => void = () => {};

export function toast(msg: string, type: ToastType = "info") { _addToast(msg, type); }

export function useToasts() {
  const [ts, setTs] = useState<Toast[]>([]);
  _addToast = useCallback((msg: string, type: ToastType) => {
    const id = ++_tid;
    setTs(p => [...p, { id, msg, type }]);
    setTimeout(() => setTs(p => p.filter(x => x.id !== id)), 4000);
  }, []);
  return ts;
}

/* ── UI helpers ─────────────────────────────────────── */
export function guessModule(n: string) { const p = n.split(/[_\s-]+/); return p.length >= 2 ? p[0][0].toUpperCase() + p[0].slice(1) : "General"; }
export function statusDot(s: string) { return s === "passed" ? "dot-green" : s === "failed" || s === "error" ? "dot-danger" : s === "running" ? "dot-warn" : "dot-gray"; }
export function statusIcon(s: string) { return s === "passed" ? "si-pass" : s === "failed" || s === "error" ? "si-fail" : s === "running" ? "si-run" : "si-skip"; }

export function pickScreenRecorderMime(): { mime?: string; ext: string } {
  if (typeof MediaRecorder === "undefined") return { ext: "webm" };
  const candidates: { mime: string; ext: string }[] = [
    { mime: "video/webm;codecs=vp9", ext: "webm" },
    { mime: "video/webm;codecs=vp8", ext: "webm" },
    { mime: "video/webm", ext: "webm" },
    { mime: "video/mp4", ext: "mp4" },
    { mime: "video/mp4; codecs=avc1.42E01E", ext: "mp4" },
  ];
  for (const c of candidates) {
    if (MediaRecorder.isTypeSupported(c.mime)) return c;
  }
  return { ext: "webm" };
}

export function replayExtFromRecorderMime(mime: string): string {
  const m = mime.toLowerCase();
  if (m.includes("mp4")) return "mp4";
  if (m.includes("webm")) return "webm";
  return "webm";
}

export function parseApiDate(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const t = String(iso).trim();
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(t) && !/[zZ]$/.test(t) && !/[+-]\d{2}:?\d{2}$/.test(t)) {
    return new Date(t + "Z");
  }
  const d = new Date(t);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function ago(d: string | null) {
  const dt = parseApiDate(d);
  if (!dt) return "—";
  const sec = Math.floor((Date.now() - dt.getTime()) / 1000);
  if (sec < 45) return "just now";
  if (sec < 0) return "just now";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  if (day < 14) return day === 1 ? "1 day ago" : `${day} days ago`;
  return dt.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

export function stepsForPlatform(test: TestDef | undefined | null, pf: Run["platform"] | "android" | "ios_sim"): any[] {
  if (!test) return [];
  const ps = test.platform_steps;
  const android = (ps?.android?.length ? ps.android : test.steps) ?? [];
  const ios = ps?.ios_sim ?? [];
  if (pf === "ios_sim") return ios.length ? ios : android;
  return android;
}

export function buildDailyDigestText(
  project: Project,
  tests: TestDef[],
  runs: Run[],
  builds: Build[],
  modules: ModuleDef[],
  suites: SuiteDef[],
  hierarchy: any | null,
): string {
  const lines: string[] = [];
  const now = new Date();
  const dateStr = now.toLocaleString(undefined, { dateStyle: "full", timeStyle: "short" });
  lines.push(`Subject: [QA Daily Status] ${project.name} — ${now.toLocaleDateString()}`);
  lines.push("");
  lines.push("DAILY TESTING STATUS");
  lines.push(`Project: ${project.name}`);
  lines.push(`Generated: ${dateStr}`);
  lines.push("");

  const latestByTest = new Map<number, Run>();
  for (const r of runs) {
    if (r.test_id == null) continue;
    if (!latestByTest.has(r.test_id)) latestByTest.set(r.test_id, r);
  }
  let passedTc = 0;
  let failedTc = 0;
  let otherTc = 0;
  for (const t of tests) {
    const lr = latestByTest.get(t.id);
    if (!lr) otherTc++;
    else if (lr.status === "passed") passedTc++;
    else if (lr.status === "failed" || lr.status === "error") failedTc++;
    else otherTc++;
  }
  lines.push("— Test cases (latest result per test) —");
  lines.push(`Total definitions: ${tests.length}`);
  lines.push(`Last run passed: ${passedTc} · last run failed/error: ${failedTc} · never run / queued / cancelled / running: ${otherTc}`);
  lines.push("");

  const pRuns = runs.filter(r => r.status === "passed").length;
  const fRuns = runs.filter(r => r.status === "failed" || r.status === "error").length;
  lines.push("— All executions (run count) —");
  lines.push(`Total runs: ${runs.length} · passed: ${pRuns} · failed/error: ${fRuns} · other: ${runs.length - pRuns - fRuns}`);
  lines.push("");

  lines.push("— Builds in use —");
  if (builds.length === 0) lines.push("(none uploaded)");
  else {
    builds.slice(0, 12).forEach(b => {
      const meta = (b.metadata || {}) as Record<string, unknown>;
      const vn = meta.version_name != null ? String(meta.version_name) : "";
      lines.push(`· ${b.file_name} (${b.platform})${vn ? ` v${vn}` : ""}`);
    });
  }
  lines.push("");

  lines.push("— Bugs / failures (recent failed or error runs) —");
  const fails = runs.filter(r => r.status === "failed" || r.status === "error").slice(0, 30);
  if (fails.length === 0) lines.push("(none in current history)");
  else {
    fails.forEach(r => {
      const tn = tests.find(te => te.id === r.test_id)?.name ?? `Test #${r.test_id}`;
      const err = (r.error_message || "").replace(/\s+/g, " ").trim().slice(0, 800);
      lines.push(`• Run #${r.id} · ${tn}`);
      lines.push(`  Platform: ${r.platform} · Device: ${r.device_target || "—"} · Status: ${r.status}`);
      if (err) lines.push(`  Detail: ${err}`);
      const sum = r.summary as Record<string, unknown> | undefined;
      const sr = sum?.stepResults as Array<{ status?: string; details?: unknown }> | undefined;
      if (Array.isArray(sr)) {
        const failedStep = sr.find(s => s?.status === "failed");
        if (failedStep?.details != null && typeof failedStep.details === "object") {
          const det = (failedStep.details as { error?: string }).error;
          if (det) lines.push(`  Step failure: ${String(det).slice(0, 400)}`);
        }
      }
      lines.push("");
    });
  }

  if (hierarchy?.summary && typeof hierarchy.summary === "object") {
    const s = hierarchy.summary as Record<string, number>;
    lines.push("— Reports summary (library hierarchy) —");
    lines.push(`Pass rate: ${s.pass_rate ?? "—"}% · Failed tests (latest): ${s.failed ?? "—"} · Not run: ${s.not_run ?? "—"} · Total: ${s.total_tests ?? "—"}`);
    lines.push("");
  }

  lines.push("— Structure: collections & suites —");
  for (const m of modules) {
    const ms = suites.filter(x => x.module_id === m.id);
    lines.push(`▸ ${m.name} — ${ms.length} suite(s)`);
    for (const su of ms) {
      const tc = tests.filter(t => t.suite_id === su.id);
      lines.push(`   · ${su.name}: ${tc.length} test case(s)`);
    }
  }
  lines.push("");
  lines.push("—");
  lines.push("Tip: Copy everything after the Subject line into your email body, or paste Subject into your mail client's subject field.");
  return lines.join("\n");
}
