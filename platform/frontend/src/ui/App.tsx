import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { DndContext, closestCenter, DragEndEvent, KeyboardSensor, PointerSensor, useSensor, useSensors } from "@dnd-kit/core";
import { arrayMove, SortableContext, sortableKeyboardCoordinates, useSortable, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { api, BatchRun, BatchRunChild, Build, DeviceList, ModuleDef, Project, Run, SuiteDef, TapDiagnosisOut, TestDef, SuiteHealthResponse, TestHealthRow, SuiteTrendItem, StepCoverageItem, TriageResponse, CollectionHealthResponse, BlockerItem, ScreenEntry, ScreenEntryFull, ScreenFolder } from "./api";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, PieChart, Pie, Cell, LineChart, Line, Legend } from "recharts";
import { XmlElementTree, simplifyXmlForAI } from "./XmlElementTree";

/* ── Toast ─────────────────────────────────────────── */
type Toast = { id: number; msg: string; type: "info" | "error" | "success" };
let _tid = 0;
let _addToast: (m: string, t: Toast["type"]) => void = () => {};
function toast(msg: string, type: Toast["type"] = "info") { _addToast(msg, type); }

function Toasts() {
  const [ts, setTs] = useState<Toast[]>([]);
  _addToast = useCallback((msg: string, type: Toast["type"]) => {
    const id = ++_tid;
    setTs(p => [...p, { id, msg, type }]);
    setTimeout(() => setTs(p => p.filter(x => x.id !== id)), 4000);
  }, []);
  return (
    <div className="toast-container">
      {ts.map(t => <div key={t.id} className={`toast toast-${t.type}`}>{t.msg}</div>)}
    </div>
  );
}

/* ── Helpers ────────────────────────────────────────── */
type Page = "dashboard" | "execution" | "library" | "reports" | "builds" | "settings";

/** One-shot preset for Live Run setup (e.g. re-run prerequisite with same build/device). */
type ExecutionSetupPreset = {
  testId: number;
  buildId: number | null;
  platform: Run["platform"];
  deviceTarget: string;
};
function guessModule(n: string) { const p = n.split(/[_\s-]+/); return p.length >= 2 ? p[0][0].toUpperCase() + p[0].slice(1) : "General"; }
function statusDot(s: string) { return s === "passed" ? "dot-green" : s === "failed" || s === "error" ? "dot-danger" : s === "running" ? "dot-warn" : "dot-gray"; }
function statusIcon(s: string) { return s === "passed" ? "si-pass" : s === "failed" || s === "error" ? "si-fail" : s === "running" ? "si-run" : "si-skip"; }

/** MediaRecorder: Chrome favors WebM; Safari often supports MP4 — pick first supported for stitched replay video. */
function pickScreenRecorderMime(): { mime?: string; ext: string } {
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

function replayExtFromRecorderMime(mime: string): string {
  const m = mime.toLowerCase();
  if (m.includes("mp4")) return "mp4";
  if (m.includes("webm")) return "webm";
  return "webm";
}
/** Backend sends naive UTC ISO timestamps; parse as UTC so relative times match server clock. */
function parseApiDate(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const t = String(iso).trim();
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(t) && !/[zZ]$/.test(t) && !/[+-]\d{2}:?\d{2}$/.test(t)) {
    return new Date(t + "Z");
  }
  const d = new Date(t);
  return Number.isNaN(d.getTime()) ? null : d;
}

function ago(d: string | null) {
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

function buildDailyDigestText(
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

/** Steps for a run / platform: iOS list falls back to Android until populated. */
function stepsForPlatform(test: TestDef | undefined | null, pf: Run["platform"] | "android" | "ios_sim"): any[] {
  if (!test) return [];
  const ps = test.platform_steps;
  const android = (ps?.android?.length ? ps.android : test.steps) ?? [];
  const ios = ps?.ios_sim ?? [];
  if (pf === "ios_sim") return ios.length ? ios : android;
  return android;
}

/* ── App ───────────────────────────────────────────── */
export function App() {
  const [ok, setOk] = useState<boolean | null>(null);
  const [onboarded, setOnboarded] = useState<boolean | null>(null);
  const [project, setProject] = useState<Project | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [page, setPage] = useState<Page>("dashboard");
  const [builds, setBuilds] = useState<Build[]>([]);
  const [tests, setTests] = useState<TestDef[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [modules, setModules] = useState<ModuleDef[]>([]);
  const [suites, setSuites] = useState<SuiteDef[]>([]);
  const [devices, setDevices] = useState<DeviceList>({ android: [], ios_simulators: [] });
  const [activeRunId, setActiveRunId] = useState<number | null>(null);
  const [activeBatchId, setActiveBatchId] = useState<number | null>(null);
  const [batchRuns, setBatchRuns] = useState<BatchRun[]>([]);
  const [libraryOpenTestId, setLibraryOpenTestId] = useState<number | null>(null);
  const [executionSetupPreset, setExecutionSetupPreset] = useState<ExecutionSetupPreset | null>(null);

  const clearExecutionSetupPreset = useCallback(() => setExecutionSetupPreset(null), []);
  const clearLibraryOpenTest = useCallback(() => setLibraryOpenTestId(null), []);

  const refresh = useCallback(async () => {
    if (!project) return;
    const pid = project.id;
    const [b, t, r, m, br] = await Promise.all([
      api.listBuilds(pid).catch(() => null),
      api.listTests(pid).catch(() => null),
      api.listRuns(pid).catch(() => null),
      api.listModules(pid).catch(() => null),
      api.listBatchRuns(pid).catch(() => null),
    ]);
    if (b) setBuilds(b);
    if (t) setTests(t);
    if (r) setRuns(r);
    if (m) setModules(m);
    if (br) setBatchRuns(br as BatchRun[]);
    const mods = m || modules;
    const allSuites: SuiteDef[] = [];
    for (const mod of mods) { try { const s = await api.listSuites(mod.id); allSuites.push(...s); } catch {} }
    setSuites(allSuites);
  }, [project]);

  useEffect(() => {
    api.health().then(() => { setOk(true); return api.getOnboarding(); }).then(o => setOnboarded(o.completed)).catch(() => setOk(false));
  }, []);

  useEffect(() => { if (onboarded) api.listProjects().then(ps => { setProjects(ps); if (ps.length > 0 && !project) setProject(ps[0]); }); }, [onboarded]);
  const loadDevices = useCallback(() => { api.listDevices().then(setDevices).catch(() => {}); }, []);
  useEffect(() => { if (project) { refresh(); loadDevices(); } }, [project, refresh, loadDevices]);

  const runningRuns = runs.filter(r => r.status === "running");
  const runningCount = runningRuns.length;

  if (ok === null) return <><Toasts /><div className="center-screen"><div className="spinner" /><p style={{ color: "var(--muted)", marginTop: 12 }}>Connecting...</p></div></>;
  if (!ok) return <><Toasts /><div className="center-screen"><h2 style={{ fontFamily: "var(--sans)" }}>Backend Unreachable</h2><p style={{ color: "var(--muted)", marginTop: 8 }}>Start: <code style={{ color: "var(--accent)" }}>cd platform/backend && uvicorn app.main:app --port 9001 --reload</code></p><button className="btn-primary" style={{ marginTop: 16 }} onClick={() => location.reload()}>Retry</button></div></>;
  if (onboarded === null) return <><Toasts /><div className="center-screen"><div className="spinner" /><p style={{ color: "var(--muted)", marginTop: 12 }}>Loading...</p></div></>;

  if (!onboarded) return <><Toasts /><Onboarding onDone={p => { setProject(p); setOnboarded(true); }} /></>;
  if (!project) return <><Toasts /><div className="center-screen"><div className="spinner" /><p style={{ color: "var(--muted)", marginTop: 12 }}>Loading project...</p></div></>;

  const latestBuild = builds[0];

  return (
    <>
      <Toasts />
      {/* ── TOPBAR ── */}
      <div className="topbar">
        <div className="logo"><div className="logo-dot" />QA·OS</div>
        <nav className="nav-tabs">
          {([["dashboard", "Dashboard"], ["execution", "Live Run"], ["library", "Test Library"], ["reports", "Reports"], ["builds", "Builds"], ["settings", "Settings"]] as [Page, string][]).map(([k, label]) => (
            <button key={k} className={`nav-tab${page === k ? " active" : ""}`} onClick={() => setPage(k)}>
              {label}
              {k === "execution" && runningCount > 0 && <span className="badge warn">{runningCount > 9 ? "9+" : runningCount}</span>}
            </button>
          ))}
        </nav>
        <div className="topbar-right">
          <select className="project-sel" value={project.id} onChange={e => { const p = projects.find(x => x.id === Number(e.target.value)); if (p) setProject(p); }}>
            {projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          <button className="btn-ghost btn-sm" style={{ fontSize: 10, padding: "4px 8px" }} onClick={() => { setOnboarded(false); }}>+ New Project</button>
          {latestBuild && (latestBuild.metadata as any)?.display_name && (
            <div style={{ fontSize: 11, fontFamily: "var(--mono)", color: "var(--accent2)" }}>{(latestBuild.metadata as any).display_name}{(latestBuild.metadata as any).version_name ? ` v${(latestBuild.metadata as any).version_name}` : ""}</div>
          )}
          {runningCount > 0 && (
            <div className="status-pill warn" title={runningRuns.map(r => `#${r.id}`).join(", ")}>
              <div className="live-dot" />
              {runningCount === 1 ? "Run in progress" : `${runningCount} runs in progress`}
            </div>
          )}
          {runningCount === 0 && runs.length > 0 && <div className="status-pill"><div className="live-dot" />Ready</div>}
        </div>
      </div>

      <div className="layout">
        {/* ── SIDEBAR ── */}
        <div className="sidebar">
          <div className="sidebar-section">
            <div className="sidebar-label">Recent Runs</div>
            {batchRuns.slice(0, 5).map(br => (
              <div key={`batch-${br.id}`} className="sidebar-run-row" style={{ display: "flex", alignItems: "center", minWidth: 0 }}>
                <button type="button" className={`sidebar-item${activeBatchId === br.id ? " active" : ""}`} style={{ flex: 1, minWidth: 0 }} onClick={() => { setActiveBatchId(br.id); setActiveRunId(null); setPage("execution"); }}>
                  <div className={`dot ${statusDot(br.status)}`} style={{ flexShrink: 0 }} />
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", minWidth: 0, fontSize: 11 }}>
                    {br.source_name} <span style={{ color: "var(--muted)", fontSize: 10 }}>({br.children.filter(c => c.status === "passed").length}/{br.total})</span>
                  </span>
                </button>
              </div>
            ))}
            {runs.filter(r => !r.batch_run_id).slice(0, 5).map(r => (
              <div key={r.id} className="sidebar-run-row" style={{ display: "flex", alignItems: "center", minWidth: 0 }}>
                <button
                  type="button"
                  className={`sidebar-item${activeRunId === r.id && !activeBatchId ? " active" : ""}`}
                  style={{ flex: 1, minWidth: 0 }}
                  onClick={() => { setActiveRunId(r.id); setActiveBatchId(null); setPage("execution"); }}
                >
                  <div className={`dot ${statusDot(r.status)}`} style={{ flexShrink: 0 }} />
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", minWidth: 0 }}>
                    {tests.find(t => t.id === r.test_id)?.name || `Run #${r.id}`}{r.status === "running" ? " · Live" : ""}
                  </span>
                </button>
                <button
                  type="button"
                  className="sidebar-delete-btn"
                  style={{
                    background: "none",
                    border: "none",
                    color: r.status === "running" || r.status === "queued" ? "var(--warn)" : "var(--muted)",
                    fontSize: 10,
                    cursor: "pointer",
                    padding: "2px 8px",
                    flexShrink: 0,
                  }}
                  title={r.status === "running" || r.status === "queued" ? "Remove stuck or abandoned run (still marked in progress)" : "Delete run"}
                  onClick={async (e) => {
                    e.stopPropagation();
                    const inFlight = r.status === "running" || r.status === "queued";
                    const msg = inFlight
                      ? `Remove run #${r.id}? It is still “${r.status}”. Use this if the run is stuck (e.g. after a server restart) or you want to discard it.`
                      : `Delete run #${r.id}?`;
                    if (!confirm(msg)) return;
                    try {
                      if (inFlight) {
                        try {
                          await api.cancelRun(r.id);
                        } catch {
                          /* engine may be gone for stuck runs */
                        }
                      }
                      await api.deleteRun(r.id);
                      if (activeRunId === r.id) setActiveRunId(null);
                      refresh();
                      toast("Run removed", "info");
                    } catch (err: any) {
                      toast(err.message, "error");
                    }
                  }}
                >✕</button>
              </div>
            ))}
            {runs.length === 0 && batchRuns.length === 0 && <div style={{ padding: "4px 16px", fontSize: 11, color: "var(--muted)" }}>No runs yet</div>}
          </div>

          {latestBuild && (
            <div className="sidebar-section">
              <div className="sidebar-label">Active Build</div>
              <div className="build-card-sidebar">
                <div className="build-name">{latestBuild.file_name}</div>
                <div className="build-meta">Uploaded {ago(latestBuild.created_at)}</div>
                <div className="build-badge">{latestBuild.platform === "android" ? "Android" : "iOS"}</div>
              </div>
            </div>
          )}

          <div className="sidebar-section">
            <div className="sidebar-label" style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              Devices
              <button className="btn-ghost btn-sm" style={{ fontSize: 9, padding: "2px 6px" }} onClick={loadDevices} title="Rescan devices">↻</button>
            </div>
            {devices.android.map(d => (
              <div key={d.serial} className="sidebar-item"><div className="dot dot-green" />{d.serial}</div>
            ))}
            {devices.ios_simulators.map(d => (
              <div key={d.udid} className="sidebar-item">
                <div className={`dot ${d.state === "Booted" ? "dot-green" : "dot-gray"}`} />{d.name}
              </div>
            ))}
            {devices.android.length === 0 && devices.ios_simulators.length === 0 && (
              <div style={{ padding: "4px 16px", fontSize: 11, color: "var(--muted)" }}>No devices detected. Click ↻ to rescan.</div>
            )}
          </div>

          {modules.length > 0 && (
            <div className="sidebar-section">
              <div className="sidebar-label">Test Suite Collections</div>
              {modules.map(m => {
                const mSuites = suites.filter(s => s.module_id === m.id);
                return (
                  <div key={m.id}>
                    <div className="sidebar-item" style={{ fontWeight: 600, fontSize: 12, display: "flex", alignItems: "center", minWidth: 0 }}>
                      <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", minWidth: 0 }}>
                        📁 {m.name}
                      </span>
                      <span style={{ display: "flex", gap: 2, flexShrink: 0, marginLeft: 4 }}>
                        <button type="button" style={{ background: "none", border: "none", color: "var(--muted)", fontSize: 9, cursor: "pointer" }} title="Rename collection" onClick={async () => { const n = prompt("Rename collection:", m.name); if (n && n.trim()) { try { await api.renameModule(m.id, n.trim()); refresh(); toast("Renamed", "success"); } catch (e: any) { toast(e.message, "error"); } } }}>✏️</button>
                        <button type="button" style={{ background: "none", border: "none", color: "var(--muted)", fontSize: 9, cursor: "pointer" }} title="Delete collection" onClick={async () => { if (confirm(`Delete collection "${m.name}" and all its suites?`)) { try { await api.deleteModule(m.id); refresh(); toast("Deleted", "info"); } catch (e: any) { toast(e.message, "error"); } } }}>🗑</button>
                      </span>
                    </div>
                    {mSuites.map(s => {
                      const sTests = tests.filter(t => t.suite_id === s.id);
                      return (
                        <div key={s.id} style={{ paddingLeft: 16, display: "flex", alignItems: "center", minWidth: 0 }}>
                          <div className="sidebar-item" style={{ fontSize: 11, color: "var(--accent2)", flex: 1, minWidth: 0, display: "flex", alignItems: "center", gap: 6 }}>
                            <span style={{ flexShrink: 0 }}>📋</span>
                            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", minWidth: 0 }}>
                              {s.name} ({sTests.length})
                            </span>
                          </div>
                          <span style={{ display: "flex", gap: 2, flexShrink: 0 }}>
                            <button type="button" style={{ background: "none", border: "none", color: "var(--muted)", fontSize: 9, cursor: "pointer" }} title="Rename Test Suite" onClick={async () => { const n = prompt("Rename suite:", s.name); if (n && n.trim()) { try { await api.renameSuite(s.id, n.trim()); refresh(); toast("Renamed", "success"); } catch (e: any) { toast(e.message, "error"); } } }}>✏️</button>
                            <button type="button" style={{ background: "none", border: "none", color: "var(--muted)", fontSize: 9, cursor: "pointer" }} title="Delete suite" onClick={async () => { if (confirm(`Delete suite "${s.name}"?`)) { try { await api.deleteSuite(s.id); refresh(); toast("Deleted", "info"); } catch (e: any) { toast(e.message, "error"); } } }}>🗑</button>
                          </span>
                        </div>
                      );
                    })}
                  </div>
                );
              })}
            </div>
          )}
          {tests.filter(t => !t.suite_id).length > 0 && modules.length > 0 && (
            <div className="sidebar-section">
              <div className="sidebar-label">Unassigned ({tests.filter(t => !t.suite_id).length})</div>
            </div>
          )}
        </div>

        {/* ── MAIN ── */}
        <div className="main-content">
          {page === "dashboard" && (
            <DashboardView
              project={project}
              tests={tests}
              runs={runs}
              builds={builds}
              modules={modules}
              suites={suites}
              onNav={setPage}
              onOpenRun={id => { setActiveRunId(id); setPage("execution"); }}
            />
          )}
          {page === "execution" && activeBatchId && (
            <BatchRunView
              batchId={activeBatchId}
              onBack={() => setActiveBatchId(null)}
              onDrillIn={(runId) => { setActiveRunId(runId); setActiveBatchId(null); }}
              onRefresh={refresh}
              onBatchCreated={(id) => { setActiveBatchId(id); setActiveRunId(null); }}
            />
          )}
          {page === "execution" && !activeBatchId && (
            <ExecutionView
              project={project}
              tests={tests}
              builds={builds}
              runs={runs}
              devices={devices}
              modules={modules}
              suites={suites}
              activeRunId={activeRunId}
              executionSetupPreset={executionSetupPreset}
              onExecutionSetupPresetConsumed={clearExecutionSetupPreset}
              onClearActiveRun={() => setActiveRunId(null)}
              onOpenTestInLibrary={id => {
                setLibraryOpenTestId(id);
                setPage("library");
              }}
              onOpenPrereqRun={id => setActiveRunId(id)}
              onPreparePrerequisiteRun={(prereqTestId, fromRun) => {
                setActiveRunId(null);
                setExecutionSetupPreset({
                  testId: prereqTestId,
                  buildId: fromRun.build_id ?? null,
                  platform: fromRun.platform,
                  deviceTarget: fromRun.device_target || "",
                });
              }}
              onRunCreated={id => {
                setActiveRunId(id);
                // Light sidebar update: only fetch runs (1 request) instead of full refresh (5+ requests)
                if (project) api.listRuns(project.id).then(setRuns).catch(() => {});
              }}
              onBatchCreated={id => {
                setActiveBatchId(id);
                setActiveRunId(null);
                // Light sidebar update: only fetch batch runs (1 request) instead of full refresh (5+ requests)
                if (project) api.listBatchRuns(project.id).then(br => setBatchRuns(br as BatchRun[])).catch(() => {});
              }}
              onRefresh={refresh}
            />
          )}
          {page === "library" && (
            <LibraryView
              project={project}
              tests={tests}
              runs={runs}
              modules={modules}
              suites={suites}
              devices={devices}
              openTestId={libraryOpenTestId}
              onOpenTestConsumed={clearLibraryOpenTest}
              onRefresh={refresh}
            />
          )}
          {page === "reports" && <ReportsView project={project} runs={runs} tests={tests} modules={modules} suites={suites} onRefresh={refresh} />}
          {page === "builds" && <BuildsView project={project} builds={builds} runs={runs} onRefresh={refresh} onRunTest={(b) => {
                setExecutionSetupPreset({ testId: 0, buildId: b.id, platform: b.platform as Run["platform"], deviceTarget: "" });
                setActiveRunId(null); setActiveBatchId(null); setPage("execution");
              }} />}
          {page === "settings" && <SettingsView />}
        </div>
      </div>
    </>
  );
}

/* ── Onboarding ────────────────────────────────────── */
function Onboarding({ onDone }: { onDone: (p: Project) => void }) {
  const [step, setStep] = useState(0);
  const [busy, setBusy] = useState(false);
  const stepLabels = ["Create Project", "Connect Tools", "Device Setup", "Upload Build", "Ready"];

  // Step 1 — Create Project
  const [projName, setProjName] = useState("");
  const [projPlatform, setProjPlatform] = useState<"android" | "ios_sim" | "both">("android");
  const [createdProject, setCreatedProject] = useState<Project | null>(null);

  // Step 2 — Connect Tools
  const [aiKey, setAiKey] = useState("");
  const [aiModel, setAiModel] = useState("gemini-2.5-flash");
  const [aiOk, setAiOk] = useState<boolean | null>(null);
  const [aiMsg, setAiMsg] = useState("");
  const [confUrl, setConfUrl] = useState("");
  const [confToken, setConfToken] = useState("");
  const [confOk, setConfOk] = useState<boolean | null>(null);
  const [confMsg, setConfMsg] = useState("");
  const [figmaToken, setFigmaToken] = useState("");

  // Step 3 — Device Setup
  const [devices, setDevices] = useState<DeviceList>({ android: [], ios_simulators: [] });
  const [selectedDevice, setSelectedDevice] = useState("");
  const [devicesLoaded, setDevicesLoaded] = useState(false);

  // Step 4 — Upload Build
  const [uploadedBuild, setUploadedBuild] = useState<Build | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const testAi = async () => {
    if (!aiKey.trim()) { toast("Enter an API key", "error"); return; }
    setBusy(true); setAiMsg("Testing...");
    try {
      await api.saveSettings({ ai_api_key: aiKey, ai_model: aiModel });
      const r = await api.testAI();
      setAiOk(r.ok); setAiMsg(r.message);
    } catch (e: any) { setAiOk(false); setAiMsg(e.message); }
    finally { setBusy(false); }
  };

  const testConf = async () => {
    if (!confUrl.trim()) { toast("Enter Confluence URL", "error"); return; }
    setBusy(true); setConfMsg("Testing...");
    try {
      await api.saveSettings({ confluence_url: confUrl, confluence_token: confToken });
      const r = await api.testConfluence();
      setConfOk(r.ok); setConfMsg(r.message);
    } catch (e: any) { setConfOk(false); setConfMsg(e.message); }
    finally { setBusy(false); }
  };

  const loadDevices = async () => {
    setDevicesLoaded(false);
    try { const d = await api.listDevices(); setDevices(d); } catch {}
    setDevicesLoaded(true);
  };

  const uploadBuild = async (file: File) => {
    if (!createdProject) return;
    setBusy(true);
    try {
      const b = await api.uploadBuild(createdProject.id, projPlatform === "both" ? "android" : projPlatform, file);
      setUploadedBuild(b);
      toast("Build uploaded & manifest parsed", "success");
    } catch (e: any) { toast(e.message, "error"); }
    finally { setBusy(false); }
  };

  const canProceed = () => {
    if (step === 0) return !!projName.trim();
    if (step === 1) return aiOk === true;
    if (step === 2) return true;
    if (step === 3) return true;
    return true;
  };

  const next = async () => {
    if (step === 0) {
      if (!projName.trim()) { toast("Enter a project name", "error"); return; }
      if (createdProject) { setStep(1); return; }
      setBusy(true);
      try {
        const p = await api.createProject(projName.trim());
        setCreatedProject(p);
        toast("Project created!", "success");
        setStep(1);
      } catch (e: any) { toast(e.message, "error"); }
      finally { setBusy(false); }
      return;
    }
    if (step === 1) {
      if (aiOk !== true) { toast("Test your AI key first — must show ✅ to proceed", "error"); return; }
      await api.saveSettings({ ai_api_key: aiKey, ai_model: aiModel, confluence_url: confUrl, confluence_token: confToken, figma_token: figmaToken });
      loadDevices();
      setStep(2);
      return;
    }
    if (step === 2) { setStep(3); return; }
    if (step === 3) { setStep(4); return; }
    if (step === 4) {
      setBusy(true);
      try {
        await api.completeOnboarding();
        onDone(createdProject!);
      } catch (e: any) { toast(e.message, "error"); }
      finally { setBusy(false); }
    }
  };

  const allDevices = [...devices.android.map(d => ({ id: d.serial, name: d.serial || d.model || "Android Device", type: "android" as const })), ...devices.ios_simulators.map(d => ({ id: d.udid, name: `${d.name} (${d.runtime})`, type: "ios" as const, state: d.state }))];

  return (
    <div className="center-screen">
      <div className="onboarding-card" style={{ maxWidth: 580 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 20 }}>
          <div className="logo-dot" />
          <h2 style={{ margin: 0, fontFamily: "var(--sans)", fontSize: 20 }}>QA·OS Setup</h2>
        </div>
        <div className="onboarding-steps">{stepLabels.map((l, i) => (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span className={`onboarding-dot${i === step ? " active" : i < step ? " done" : ""}`} />
            <span style={{ fontSize: 10, color: i === step ? "var(--accent)" : i < step ? "var(--text)" : "var(--muted)", fontFamily: "var(--mono)" }}>{l}</span>
          </div>
        ))}</div>

        {/* Step 1: Create Project */}
        {step === 0 && (
          <div className="onboarding-body">
            <h3>Create Project</h3>
            <div style={{ marginBottom: 12 }}>
              <div className="form-label">Project Name</div>
              <input value={projName} onChange={e => setProjName(e.target.value)} placeholder="e.g. My Mobile App" autoFocus className="form-input" style={{ width: "100%" }} />
            </div>
            <div>
              <div className="form-label">Platform</div>
              <div style={{ display: "flex", gap: 8 }}>
                {(["android", "ios_sim", "both"] as const).map(p => (
                  <button key={p} className={`btn-ghost btn-sm${projPlatform === p ? " active" : ""}`} style={projPlatform === p ? { borderColor: "var(--accent)", color: "var(--accent)" } : {}} onClick={() => setProjPlatform(p)}>
                    {p === "android" ? "Android" : p === "ios_sim" ? "iOS" : "Both"}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* Step 2: Connect Tools */}
        {step === 1 && (
          <div className="onboarding-body">
            <h3>Connect Your Tools</h3>
            <div className="form-section" style={{ marginBottom: 16, border: "1px solid var(--border)", borderRadius: 8, padding: 14 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
                <div className="form-section-title" style={{ margin: 0 }}>AI (Gemini)</div>
                {aiOk === true && <span style={{ color: "var(--accent)", fontSize: 14 }}>✅</span>}
                {aiOk === false && <span style={{ color: "var(--danger)", fontSize: 14 }}>❌</span>}
              </div>
              <div style={{ display: "flex", gap: 8, marginBottom: 6 }}>
                <input className="form-input" style={{ flex: 1 }} type="password" value={aiKey} onChange={e => { setAiKey(e.target.value); setAiOk(null); }} placeholder="API Key" />
                <input className="form-input" style={{ width: 160 }} value={aiModel} onChange={e => setAiModel(e.target.value)} placeholder="Model" />
                <button className="btn-primary btn-sm" onClick={testAi} disabled={busy}>Test</button>
              </div>
              {aiMsg && <div style={{ fontSize: 11, color: aiOk ? "var(--accent)" : "var(--danger)" }}>{aiMsg}</div>}
            </div>

            <div className="form-section" style={{ marginBottom: 16, border: "1px solid var(--border)", borderRadius: 8, padding: 14 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
                <div className="form-section-title" style={{ margin: 0 }}>Confluence</div>
                {confOk === true && <span style={{ color: "var(--accent)", fontSize: 14 }}>✅</span>}
                {confOk === false && <span style={{ color: "var(--danger)", fontSize: 14 }}>❌</span>}
              </div>
              <div style={{ display: "flex", gap: 8, marginBottom: 6 }}>
                <input className="form-input" style={{ flex: 1 }} value={confUrl} onChange={e => { setConfUrl(e.target.value); setConfOk(null); }} placeholder="https://your-domain.atlassian.net/wiki" />
                <input className="form-input" style={{ flex: 1 }} type="password" value={confToken} onChange={e => setConfToken(e.target.value)} placeholder="API Token" />
                <button className="btn-ghost btn-sm" onClick={testConf} disabled={busy}>Test</button>
              </div>
              {confMsg && <div style={{ fontSize: 11, color: confOk ? "var(--accent)" : "var(--danger)" }}>{confMsg}</div>}
            </div>

            <div className="form-section" style={{ border: "1px solid var(--border)", borderRadius: 8, padding: 14 }}>
              <div className="form-section-title" style={{ margin: 0, marginBottom: 10 }}>Figma (optional — can skip)</div>
              <input className="form-input" style={{ width: "100%" }} type="password" value={figmaToken} onChange={e => setFigmaToken(e.target.value)} placeholder="figd_..." />
            </div>
          </div>
        )}

        {/* Step 3: Device Setup */}
        {step === 2 && (
          <div className="onboarding-body">
            <h3>Device Setup</h3>
            {!devicesLoaded ? (
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
                <div className="spinner" style={{ width: 18, height: 18 }} />
                <span style={{ color: "var(--muted)", fontSize: 12 }}>Scanning for devices...</span>
              </div>
            ) : allDevices.length === 0 ? (
              <div style={{ padding: 16, border: "1px solid var(--border)", borderRadius: 8, marginBottom: 12 }}>
                <div style={{ color: "var(--warn)", fontWeight: 600, marginBottom: 8 }}>No devices detected</div>
                <div style={{ fontSize: 12, color: "var(--muted)", lineHeight: 1.6 }}>
                  <strong>Android:</strong> Start an emulator from Android Studio → AVD Manager, or connect a physical device with USB debugging.<br />
                  <strong>iOS:</strong> Open Xcode → Window → Devices and Simulators → Boot a simulator.<br /><br />
                  Then click Rescan below.
                </div>
                <button className="btn-ghost btn-sm" style={{ marginTop: 10 }} onClick={loadDevices}>↻ Rescan</button>
              </div>
            ) : (
              <>
                <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 12 }}>Click a device to select it as default:</div>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 12 }}>
                  {allDevices.map(d => (
                    <div key={d.id} onClick={() => setSelectedDevice(d.id)} style={{ cursor: "pointer", padding: "12px 14px", border: `1px solid ${selectedDevice === d.id ? "var(--accent)" : "var(--border)"}`, borderRadius: 8, background: selectedDevice === d.id ? "rgba(0,229,160,.08)" : "var(--card)" }}>
                      <div style={{ fontFamily: "var(--sans)", fontWeight: 600, fontSize: 13 }}>{d.name}</div>
                      <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4 }}>{d.type === "android" ? "Android" : "iOS Simulator"}{(d as any).state ? ` · ${(d as any).state}` : ""}</div>
                    </div>
                  ))}
                </div>
                <button className="btn-ghost btn-sm" onClick={loadDevices}>↻ Rescan</button>
              </>
            )}
          </div>
        )}

        {/* Step 4: Upload Build */}
        {step === 3 && (
          <div className="onboarding-body">
            <h3>Upload Your First Build</h3>
            {!uploadedBuild ? (
              <>
                <div className="upload-zone" onClick={() => fileRef.current?.click()} style={{ marginBottom: 12, cursor: "pointer" }}
                  onDragOver={e => e.preventDefault()}
                  onDrop={e => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f) uploadBuild(f); }}>
                  <div className="upload-icon">⬆</div>
                  <div className="upload-text">Drop your APK or IPA here</div>
                  <div className="upload-sub">Auto-reads manifest on upload</div>
                </div>
                <input ref={fileRef} type="file" hidden accept=".apk,.ipa,.app,.zip" onChange={e => { if (e.target.files?.[0]) uploadBuild(e.target.files[0]); }} />
                <div style={{ fontSize: 11, color: "var(--muted)", textAlign: "center" }}>Or skip this step and upload later from Builds page</div>
              </>
            ) : (
              <div style={{ padding: 16, border: "1px solid var(--accent)", borderRadius: 8, background: "rgba(0,229,160,.06)" }}>
                <div style={{ fontFamily: "var(--sans)", fontWeight: 700, fontSize: 15, color: "var(--accent)", marginBottom: 8 }}>✅ Build parsed successfully</div>
                <table style={{ fontSize: 12, width: "100%" }}>
                  <tbody>
                    {(uploadedBuild.metadata as any)?.display_name && <tr><td style={{ color: "var(--muted)", padding: "3px 10px 3px 0" }}>App Name</td><td>{(uploadedBuild.metadata as any).display_name}</td></tr>}
                    {(uploadedBuild.metadata as any)?.package && <tr><td style={{ color: "var(--muted)", padding: "3px 10px 3px 0" }}>Package</td><td style={{ fontFamily: "var(--mono)", color: "var(--accent2)" }}>{(uploadedBuild.metadata as any).package}</td></tr>}
                    {(uploadedBuild.metadata as any)?.version_name && <tr><td style={{ color: "var(--muted)", padding: "3px 10px 3px 0" }}>Version</td><td>{(uploadedBuild.metadata as any).version_name}</td></tr>}
                    {(uploadedBuild.metadata as any)?.main_activity && <tr><td style={{ color: "var(--muted)", padding: "3px 10px 3px 0" }}>Activity</td><td style={{ fontFamily: "var(--mono)", fontSize: 11 }}>{(uploadedBuild.metadata as any).main_activity}</td></tr>}
                    <tr><td style={{ color: "var(--muted)", padding: "3px 10px 3px 0" }}>File</td><td>{uploadedBuild.file_name}</td></tr>
                    <tr><td style={{ color: "var(--muted)", padding: "3px 10px 3px 0" }}>Platform</td><td>{uploadedBuild.platform === "android" ? "Android" : "iOS"}</td></tr>
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* Step 5: Ready */}
        {step === 4 && (
          <div className="onboarding-body">
            <h3 style={{ color: "var(--accent)" }}>You're Ready!</h3>
            <div style={{ fontSize: 12, lineHeight: 1.8, marginBottom: 12 }}>
              <div>✅ Project: <strong>{projName}</strong></div>
              <div>✅ AI: {aiOk ? <span style={{ color: "var(--accent)" }}>Connected ({aiModel})</span> : <span style={{ color: "var(--warn)" }}>Not configured</span>}</div>
              <div>{confOk ? "✅" : "⬜"} Confluence: {confOk ? <span style={{ color: "var(--accent)" }}>Connected</span> : <span style={{ color: "var(--muted)" }}>Skipped</span>}</div>
              <div>{selectedDevice ? "✅" : "⬜"} Device: {selectedDevice ? <span style={{ color: "var(--accent)" }}>{selectedDevice}</span> : <span style={{ color: "var(--muted)" }}>Default</span>}</div>
              <div>{uploadedBuild ? "✅" : "⬜"} Build: {uploadedBuild ? <span style={{ color: "var(--accent)" }}>{uploadedBuild.file_name}</span> : <span style={{ color: "var(--muted)" }}>Not uploaded</span>}</div>
            </div>
          </div>
        )}

        <div className="onboarding-actions">
          {step > 0 && <button className="btn-ghost" onClick={() => setStep(s => s - 1)}>Back</button>}
          <button className="btn-primary" onClick={next} disabled={busy || (step === 0 && !projName.trim()) || (step === 1 && aiOk !== true)}>
            {step === 4 ? "Run your first test →" : "Next"}
          </button>
          {step === 3 && !uploadedBuild && <button className="btn-ghost" onClick={() => setStep(4)}>Skip</button>}
        </div>
      </div>
    </div>
  );
}

/* ── Dashboard ─────────────────────────────────────── */
function DashboardView({
  project,
  tests,
  runs,
  builds,
  modules,
  suites,
  onNav,
  onOpenRun,
}: {
  project: Project;
  tests: TestDef[];
  runs: Run[];
  builds: Build[];
  modules: ModuleDef[];
  suites: SuiteDef[];
  onNav: (p: Page) => void;
  onOpenRun: (id: number) => void;
}) {
  const [digestBusy, setDigestBusy] = useState(false);

  const runsPassed = runs.filter(r => r.status === "passed").length;
  const runsFailed = runs.filter(r => r.status === "failed" || r.status === "error").length;
  const runsRunning = runs.filter(r => r.status === "running").length;
  const totalRuns = runs.length;
  const runPassRate = totalRuns > 0 ? ((runsPassed / totalRuns) * 100).toFixed(1) : "0";

  const latestByTest = new Map<number, Run>();
  for (const r of runs) {
    if (r.test_id == null) continue;
    if (!latestByTest.has(r.test_id)) latestByTest.set(r.test_id, r);
  }
  let tcLatestPassed = 0;
  let tcLatestFailed = 0;
  let tcPending = 0;
  for (const t of tests) {
    const lr = latestByTest.get(t.id);
    if (!lr) tcPending++;
    else if (lr.status === "passed") tcLatestPassed++;
    else if (lr.status === "failed" || lr.status === "error") tcLatestFailed++;
    else tcPending++;
  }
  const tcHealthRate = tests.length > 0 ? ((tcLatestPassed / tests.length) * 100).toFixed(1) : "0";

  const collectionStats: Array<{ id: number | string; name: string; rate: number; latestPass: number; latestFail: number; pending: number }> = modules.map(m => {
    const suiteIds = new Set(suites.filter(s => s.module_id === m.id).map(s => s.id));
    const mTests = tests.filter(t => t.suite_id != null && suiteIds.has(t.suite_id));
    let lp = 0;
    let lf = 0;
    let pend = 0;
    for (const t of mTests) {
      const lr = latestByTest.get(t.id);
      if (!lr) pend++;
      else if (lr.status === "passed") lp++;
      else if (lr.status === "failed" || lr.status === "error") lf++;
      else pend++;
    }
    const n = mTests.length;
    const rate = n > 0 ? Math.round((lp / n) * 100) : 0;
    return { id: m.id, name: m.name, latestPass: lp, latestFail: lf, pending: pend, rate };
  });
  const unassignedTests = tests.filter(t => !t.suite_id);
  if (unassignedTests.length > 0) {
    let lp = 0;
    let lf = 0;
    let pend = 0;
    for (const t of unassignedTests) {
      const lr = latestByTest.get(t.id);
      if (!lr) pend++;
      else if (lr.status === "passed") lp++;
      else if (lr.status === "failed" || lr.status === "error") lf++;
      else pend++;
    }
    const n = unassignedTests.length;
    collectionStats.push({
      id: "unassigned",
      name: "Unassigned",
      rate: n > 0 ? Math.round((lp / n) * 100) : 0,
      latestPass: lp,
      latestFail: lf,
      pending: pend,
    });
  }

  const downloadDailyDraft = async () => {
    setDigestBusy(true);
    try {
      const hier = await api.getReportsHierarchy(project.id).catch(() => null);
      const text = buildDailyDigestText(project, tests, runs, builds, modules, suites, hier);
      const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
      const a = document.createElement("a");
      const safe = project.name.replace(/[^\w\-]+/g, "_").slice(0, 60) || "qa_project";
      a.href = URL.createObjectURL(blob);
      a.download = `${safe}_daily_status_${new Date().toISOString().slice(0, 10)}.txt`;
      a.click();
      URL.revokeObjectURL(a.href);
      toast("Daily status draft downloaded — open in a text editor and paste into email", "success");
    } catch (e: any) {
      toast(e.message || String(e), "error");
    } finally {
      setDigestBusy(false);
    }
  };

  const failingTests = [...latestByTest.entries()]
    .filter(([, r]) => r.status === "failed" || r.status === "error")
    .slice(0, 6)
    .map(([tid, r]) => ({ test: tests.find(t => t.id === tid), run: r }));

  return (
    <>
        <div className="section-head">
        <div>
          <div className="section-title">Dashboard</div>
          <div className="section-sub">{project.name} · test cases, runs, and collections</div>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button type="button" className="btn-ghost btn-sm" disabled={digestBusy} onClick={downloadDailyDraft} title="Plain-text draft with runs, failures, and structure">
            {digestBusy ? "…" : "📧 Daily status draft"}
          </button>
          <button type="button" className="run-now-btn" onClick={() => onNav("execution")}>
            ▶ Run Tests
          </button>
        </div>
      </div>

      <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 10 }}>Test case row = latest result per definition. Run row = every execution.</div>

      <div className="metrics-row">
        <div className="metric-card">
          <div className="metric-val">{tcHealthRate}%</div>
          <div className="metric-label">Cases last passed</div>
          <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 4 }}>{tcLatestPassed}/{tests.length} tests</div>
        </div>
        <div className="metric-card danger">
          <div className="metric-val">{tcLatestFailed}</div>
          <div className="metric-label">Cases last failed</div>
          <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 4 }}>latest run error</div>
        </div>
        <div className="metric-card warn">
          <div className="metric-val">{tcPending}</div>
          <div className="metric-label">No pass / not run</div>
          <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 4 }}>queued · running · none</div>
        </div>
        <div className="metric-card">
          <div className="metric-val">{runPassRate}%</div>
          <div className="metric-label">Run pass rate</div>
          <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 4 }}>all executions</div>
        </div>
        <div className="metric-card blue">
          <div className="metric-val">{totalRuns}</div>
          <div className="metric-label">Total runs</div>
          <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 4 }}>{runsFailed} fail · {runsRunning} live</div>
        </div>
        <div className="metric-card purple">
          <div className="metric-val">{builds.length}</div>
          <div className="metric-label">Builds</div>
        </div>
      </div>

      <div className="runs-grid">
        <div className="panel">
          <div className="panel-header">
            <div className="panel-title">Recent runs</div>
            <div style={{ fontSize: 11, color: "var(--muted)", cursor: "pointer" }} onClick={() => onNav("execution")}>
              View all →
            </div>
          </div>
          <div className="panel-body">
            {runs.slice(0, 5).map(r => (
              <div key={r.id} className="run-row" onClick={() => onOpenRun(r.id)}>
                <div className={`status-icon ${statusIcon(r.status)}`} />
                <div>
                  <div className="run-name">{tests.find(t => t.id === r.test_id)?.name || `Run #${r.id}`}</div>
                  <div className="run-meta">
                    {r.device_target || "default"} · {r.platform}
                  </div>
                </div>
                <div className={`run-pct ${r.status === "passed" ? "pct-pass" : "pct-fail"}`}>{r.status}</div>
                <div className="run-tag">{r.platform}</div>
                <div className="run-dur">{ago(r.finished_at || r.started_at)}</div>
              </div>
            ))}
            {runs.length === 0 && (
              <div style={{ padding: 18, color: "var(--muted)", fontSize: 12 }}>No runs yet. Create a test and start a run.</div>
            )}
          </div>
        </div>

        <div className="panel">
          <div className="panel-header">
            <div className="panel-title">Collections — test case health</div>
          </div>
          <div className="chart-bar-group" style={{ paddingBottom: 8 }}>
            {collectionStats.map(ms => (
              <div key={ms.id} className="bar-row">
                <div className="bar-label" title={`${ms.latestPass} pass · ${ms.latestFail} fail · ${ms.pending} other`}>
                  {ms.name}
                  <span style={{ fontSize: 9, color: "var(--muted)", display: "block" }}>
                    {ms.latestPass}✓ {ms.latestFail}✗ {ms.pending}···
                  </span>
                </div>
                <div className="bar-track">
                  <div
                    className="bar-fill"
                    style={{
                      width: `${ms.rate}%`,
                      background: ms.rate >= 90 ? "var(--accent)" : ms.rate >= 70 ? "var(--warn)" : "var(--danger)",
                    }}
                  />
                </div>
                <div className="bar-val">{ms.rate}%</div>
              </div>
            ))}
            {collectionStats.length === 0 && (
              <div style={{ color: "var(--muted)", fontSize: 12 }}>Add collections in Test Library to group tests</div>
            )}
          </div>
        </div>
      </div>

      {failingTests.length > 0 && (
        <div className="panel" style={{ marginTop: 14 }}>
          <div className="panel-header">
            <div className="panel-title">Open issues — latest failing test cases</div>
            <div style={{ fontSize: 11, color: "var(--muted)", cursor: "pointer" }} onClick={() => onNav("reports")}>
              Reports →
            </div>
          </div>
          <div className="panel-body">
            {failingTests.map(({ test, run: r }) => (
              <div
                key={`${r.id}-${test?.id}`}
                className="run-row"
                onClick={() => onOpenRun(r.id)}
                style={{ cursor: "pointer" }}
              >
                <div className={`status-icon ${statusIcon(r.status)}`} />
                <div style={{ flex: 1 }}>
                  <div className="run-name">{test?.name || `Test #${r.test_id}`}</div>
                  <div className="run-meta" style={{ fontSize: 10, color: "var(--danger)", maxHeight: 36, overflow: "hidden" }}>
                    {(r.error_message || "").slice(0, 180) || "See run for details"}
                  </div>
                </div>
                <div className="run-dur">{ago(r.finished_at || r.started_at)}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  );
}

/* ── Batch Run View ────────────────────────────────── */
const BATCH_TERMINAL = ["passed", "failed", "partial", "cancelled"];

function BatchRunView({ batchId, onBack, onDrillIn, onRefresh, onBatchCreated }: { batchId: number; onBack: () => void; onDrillIn: (runId: number) => void; onRefresh: () => void; onBatchCreated: (id: number) => void }) {
  const [batch, setBatch] = useState<BatchRun | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [rerunning, setRerunning] = useState(false);
  const onRefreshRef = useRef(onRefresh);
  onRefreshRef.current = onRefresh;

  const didRefreshOnTerminal = useRef(false);
  const loadBatch = useCallback(async () => {
    try {
      const b = await api.getBatchRun(batchId);
      setBatch(b);
      setError(null);
      if (BATCH_TERMINAL.includes(b.status) && !didRefreshOnTerminal.current) {
        didRefreshOnTerminal.current = true;
        try { onRefreshRef.current(); } catch {}
      }
    } catch (e: any) {
      setError(e.message || "Failed to load batch run");
    }
    setLoading(false);
  }, [batchId]);

  useEffect(() => {
    setLoading(true);
    setBatch(null);
    setError(null);
    didRefreshOnTerminal.current = false;
    loadBatch();
  }, [batchId, loadBatch]);

  useEffect(() => {
    if (!batch || BATCH_TERMINAL.includes(batch.status)) return;
    const iv = setInterval(loadBatch, 3000);
    return () => clearInterval(iv);
  }, [batch?.status, loadBatch]);

  if (error && !batch) {
    return (
      <div className="main-content">
        <div className="panel" style={{ padding: 28, textAlign: "center" }}>
          <div style={{ fontSize: 13, color: "var(--danger)", marginBottom: 12 }}>{error}</div>
          <button className="btn-ghost btn-sm" onClick={() => { setError(null); setLoading(true); loadBatch(); }}>Retry</button>
        </div>
      </div>
    );
  }

  if ((loading && !batch) || !batch) {
    return (
      <div className="main-content">
        <div className="panel" style={{ padding: 28, textAlign: "center" }}>
          <div className="spinner" style={{ margin: "0 auto 12px" }} />
          <div style={{ fontSize: 13, color: "var(--muted)" }}>Loading batch run...</div>
        </div>
      </div>
    );
  }

  // Derive counts from children (source of truth) rather than batch.passed/failed
  // which may be stale or unsynced.
  const childPassed = batch.children.filter(c => c.status === "passed").length;
  const childFailed = batch.children.filter(c => c.status === "failed" || c.status === "error").length;
  const childCancelled = batch.children.filter(c => c.status === "cancelled").length;
  const childRunning = batch.children.filter(c => c.status === "running").length;
  const childQueued = batch.children.filter(c => c.status === "queued").length;
  const executed = childPassed + childFailed;
  const notRun = childCancelled + childQueued;
  const passedPct = batch.total > 0 ? (childPassed / batch.total) * 100 : 0;
  const failedPct = batch.total > 0 ? (childFailed / batch.total) * 100 : 0;
  const executedPct = batch.total > 0 ? Math.round((executed / batch.total) * 100) : 0;
  const isActive = batch.status === "running" || batch.status === "queued";
  const isTerminal = BATCH_TERMINAL.includes(batch.status);

  const handleCancel = async () => {
    setCancelling(true);
    try {
      await api.cancelBatchRun(batch.id);
      toast("Stopping — waiting for running test to finish...", "info");
      for (let i = 0; i < 30; i++) {
        await new Promise(r => setTimeout(r, 1000));
        try {
          const b = await api.getBatchRun(batchId);
          setBatch(b);
          if (BATCH_TERMINAL.includes(b.status)) break;
        } catch { break; }
      }
    } catch (e: any) {
      toast(e.message || "Cancel failed", "error");
    }
    setCancelling(false);
    // Refresh sidebar + top bar badge after cancel settles
    try { onRefreshRef.current(); } catch {}
  };

  const handleRerun = async () => {
    setRerunning(true);
    try {
      const newBatch = await api.createBatchRun({
        project_id: batch.project_id,
        build_id: batch.build_id,
        mode: batch.mode,
        source_id: batch.source_id,
        platform: batch.platform as "android" | "ios_sim",
        device_target: batch.device_target || undefined,
      });
      toast(`Rerun: ${newBatch.source_name} — ${newBatch.total} tests queued`, "success");
      onBatchCreated(newBatch.id);
    } catch (e: any) {
      toast(e.message || "Rerun failed", "error");
    }
    setRerunning(false);
  };

  const totalDuration = (() => {
    if (!batch.started_at) return null;
    const start = new Date(batch.started_at + "Z").getTime();
    const end = batch.finished_at ? new Date(batch.finished_at + "Z").getTime() : Date.now();
    const secs = (end - start) / 1000;
    if (secs < 60) return `${secs.toFixed(1)}s`;
    return `${Math.floor(secs / 60)}m ${Math.round(secs % 60)}s`;
  })();

  return (
    <div className="main-content">
      <div className="section-head">
        <div>
          <div className="section-title" style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <button className="btn-ghost btn-sm" onClick={onBack} style={{ fontSize: 11, padding: "4px 10px" }}>← Back</button>
            {batch.mode === "suite" ? "Suite" : "Collection"}: {batch.source_name}
          </div>
          <div className="section-sub">{batch.platform === "android" ? "Android" : "iOS"} · {batch.total} test{batch.total !== 1 ? "s" : ""}{totalDuration ? ` · ${totalDuration}` : ""}</div>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <span style={{ fontSize: 12, fontWeight: 700, color: batch.status === "passed" ? "#00e5a0" : batch.status === "failed" ? "#ff3b5c" : batch.status === "partial" ? "#ffb020" : "var(--muted)" }}>
            {batch.status.toUpperCase()}
          </span>
          {isActive && (
            <button className="btn-ghost btn-sm" style={{ color: "var(--danger)", borderColor: "rgba(255,59,92,.4)" }} onClick={handleCancel} disabled={cancelling}>
              {cancelling ? "⏳ Stopping..." : "⏹ Stop"}
            </button>
          )}
          {isTerminal && (
            <button className="run-now-btn" style={{ fontSize: 11, padding: "7px 14px" }} onClick={handleRerun} disabled={rerunning}>
              {rerunning ? "⏳ Starting..." : "▶ Rerun"}
            </button>
          )}
        </div>
      </div>

      {/* Summary report — always visible, acts as the immediate report on stop */}
      <div className="panel" style={{ padding: 16, marginBottom: 16 }}>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, marginBottom: 8 }}>
          <span>{executed} of {batch.total} executed</span>
          <span style={{ fontWeight: 600 }}>{executedPct}%</span>
        </div>
        <div style={{ height: 8, borderRadius: 4, background: "var(--bg3)", overflow: "hidden", display: "flex" }}>
          <div style={{ height: "100%", width: `${passedPct}%`, background: "#00e5a0", transition: "width .3s" }} />
          <div style={{ height: "100%", width: `${failedPct}%`, background: "#ff3b5c", transition: "width .3s" }} />
        </div>
        <div style={{ display: "flex", gap: 16, marginTop: 10, fontSize: 11 }}>
          <span style={{ color: "#00e5a0", fontWeight: 600 }}>✓ {childPassed} passed</span>
          <span style={{ color: "#ff3b5c", fontWeight: 600 }}>✗ {childFailed} failed</span>
          {childRunning > 0 && <span style={{ color: "#ffb020", fontWeight: 600 }}>◎ {childRunning} running</span>}
          {childQueued > 0 && <span style={{ color: "var(--muted)" }}>○ {childQueued} queued</span>}
          {notRun > 0 && isTerminal && <span style={{ color: "var(--muted)" }}>— {notRun} not run</span>}
        </div>
      </div>

      {/* Terminal report banner */}
      {isTerminal && (
        <div className="panel" style={{ padding: 14, marginBottom: 16, border: batch.status === "passed" ? "1px solid rgba(0,229,160,.3)" : batch.status === "cancelled" ? "1px solid rgba(255,255,255,.1)" : "1px solid rgba(255,59,92,.3)", background: batch.status === "passed" ? "rgba(0,229,160,.04)" : batch.status === "cancelled" ? "rgba(255,255,255,.02)" : "rgba(255,59,92,.04)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
            <span style={{ fontSize: 14 }}>{batch.status === "passed" ? "✅" : batch.status === "cancelled" ? "⏹" : "📋"}</span>
            <span style={{ fontSize: 13, fontWeight: 700, color: batch.status === "passed" ? "#00e5a0" : batch.status === "cancelled" ? "var(--text)" : "#ff3b5c" }}>
              {batch.status === "passed" ? "All tests passed" : batch.status === "cancelled" ? "Execution stopped" : "Execution Report"}
            </span>
          </div>
          <div style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.6 }}>
            {childPassed > 0 && <div>{childPassed} test{childPassed !== 1 ? "s" : ""} passed</div>}
            {childFailed > 0 && <div>{childFailed} test{childFailed !== 1 ? "s" : ""} failed</div>}
            {notRun > 0 && <div>{notRun} test{notRun !== 1 ? "s" : ""} not executed</div>}
            {totalDuration && <div>Total duration: {totalDuration}</div>}
          </div>
        </div>
      )}

      {/* Test case table */}
      <div className="panel" style={{ padding: 0, overflow: "hidden" }}>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 100px 90px 80px", gap: 8, padding: "10px 16px", fontSize: 10, color: "var(--muted)", textTransform: "uppercase" as const, borderBottom: "1px solid var(--border)", fontWeight: 600 }}>
          <div>Test Case</div>
          <div>Status</div>
          <div>Duration</div>
          <div></div>
        </div>
        {batch.children.map(child => {
          let dur = "—";
          if (child.started_at && child.finished_at) {
            const secs = (new Date(child.finished_at + "Z").getTime() - new Date(child.started_at + "Z").getTime()) / 1000;
            dur = secs >= 0 ? `${secs.toFixed(1)}s` : "—";
          } else if (child.status === "running" && child.started_at) {
            const secs = (Date.now() - new Date(child.started_at + "Z").getTime()) / 1000;
            dur = secs >= 0 ? `${secs.toFixed(0)}s...` : "...";
          }
          const isOrphaned = isTerminal && (child.status === "running" || child.status === "queued");
          const statusLabel = child.status === "cancelled" || isOrphaned ? "NOT RUN" : child.status.toUpperCase();
          return (
            <div key={child.run_id} style={{ display: "grid", gridTemplateColumns: "1fr 100px 90px 80px", gap: 8, padding: "12px 16px", borderBottom: "1px solid var(--border)", fontSize: 12, alignItems: "center", opacity: child.status === "cancelled" || isOrphaned ? 0.5 : 1 }}>
              <div style={{ fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{child.test_name}</div>
              <div>
                <span style={{
                  fontSize: 10, fontWeight: 700, padding: "2px 8px", borderRadius: 12,
                  background: isOrphaned ? "var(--bg3)" : child.status === "passed" ? "rgba(0,229,160,.12)" : child.status === "failed" || child.status === "error" ? "rgba(255,59,92,.12)" : child.status === "running" ? "rgba(255,176,32,.12)" : "var(--bg3)",
                  color: isOrphaned ? "var(--muted)" : child.status === "passed" ? "#00e5a0" : child.status === "failed" || child.status === "error" ? "#ff3b5c" : child.status === "running" ? "#ffb020" : "var(--muted)",
                }}>{statusLabel}</span>
              </div>
              <div style={{ fontSize: 11, color: "var(--muted)" }}>{dur}</div>
              <div>
                {(child.status === "passed" || child.status === "failed" || child.status === "error") && (
                  <button className="btn-ghost btn-sm" style={{ fontSize: 10, padding: "3px 8px" }} onClick={() => onDrillIn(child.run_id)}>View →</button>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {batch.started_at && (
        <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 12, textAlign: "right" }}>
          Started {ago(batch.started_at)}{batch.finished_at ? ` · Finished ${ago(batch.finished_at)}` : ""}
        </div>
      )}
    </div>
  );
}

/* ── Execution ─────────────────────────────────────── */
function ExecutionView({
  project,
  tests,
  builds,
  runs,
  devices,
  modules,
  suites,
  activeRunId,
  executionSetupPreset,
  onExecutionSetupPresetConsumed,
  onClearActiveRun,
  onOpenTestInLibrary,
  onOpenPrereqRun,
  onPreparePrerequisiteRun,
  onRunCreated,
  onBatchCreated,
  onRefresh,
}: {
  project: Project;
  tests: TestDef[];
  builds: Build[];
  runs: Run[];
  devices: DeviceList;
  modules: ModuleDef[];
  suites: SuiteDef[];
  activeRunId: number | null;
  executionSetupPreset: ExecutionSetupPreset | null;
  onExecutionSetupPresetConsumed: () => void;
  onClearActiveRun: () => void;
  onOpenTestInLibrary: (testId: number) => void;
  onOpenPrereqRun: (runId: number) => void;
  onPreparePrerequisiteRun: (prereqTestId: number, fromRun: Run) => void;
  onRunCreated: (id: number) => void;
  onBatchCreated: (id: number) => void;
  onRefresh: () => void;
}) {
  const [platform, setPlatform] = useState<Run["platform"]>("android");
  const [buildId, setBuildId] = useState<number | null>(null);
  const [testId, setTestId] = useState<number | null>(null);
  const [runMode, setRunMode] = useState<"single" | "suite" | "collection">("single");
  const [selectedSuiteId, setSelectedSuiteId] = useState<number | null>(null);
  const [selectedCollectionId, setSelectedCollectionId] = useState<number | null>(null);
  const [deviceTarget, setDeviceTarget] = useState("");
  const [agentMode, setAgentMode] = useState(false);
  const [agentMaxIterations] = useState(5);
  const [agentRunning, setAgentRunning] = useState(false);
  const [agentStatus, setAgentStatus] = useState("");
  const [agentProgressLog, setAgentProgressLog] = useState<string[]>([]);
  const agentPausedRef = useRef(false);
  const [busy, setBusy] = useState(false);
  const [run, setRun] = useState<Run | null>(null);
  const [stepResults, setStepResults] = useState<any[]>([]);
  const [selShot, setSelShot] = useState<string | null>(null);
  const [liveXml, setLiveXml] = useState<string | null>(null);
  const [liveXmlName, setLiveXmlName] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const wsReconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const wsReconnectAttemptRef = useRef(0);
  const [wsLive, setWsLive] = useState(true);

  const bfp = useMemo(() => builds.filter(b => b.platform === platform), [builds, platform]);
  const devList = platform === "android" ? devices.android : devices.ios_simulators;

  const testsInSuite = selectedSuiteId ? tests.filter(t => t.suite_id === selectedSuiteId) : [];
  const testsInCollection = selectedCollectionId ? tests.filter(t => { const s = suites.find(x => x.id === t.suite_id); return s && s.module_id === selectedCollectionId; }) : [];
  const batchTests = runMode === "suite" ? testsInSuite : runMode === "collection" ? testsInCollection : [];

  useEffect(() => {
    if (!executionSetupPreset) return;
    const p = executionSetupPreset;
    setRunMode("single");
    if (p.testId) setTestId(p.testId);
    setBuildId(p.buildId);
    setPlatform(p.platform);
    setDeviceTarget(p.deviceTarget ?? "");
    setSelectedSuiteId(null);
    setSelectedCollectionId(null);
    onExecutionSetupPresetConsumed();
  }, [executionSetupPreset, onExecutionSetupPresetConsumed]);

  const loadRun = useCallback(async (id: number) => {
    const r = await api.getRun(id);
    setRun(r);
    const saved = (r.summary as any)?.stepResults;
    if (Array.isArray(saved) && saved.length) { setStepResults(saved); const shots = (r.artifacts as any)?.screenshots || []; if (shots.length) setSelShot(shots[shots.length - 1]); }
    return r;
  }, []);

  useEffect(() => {
    if (!activeRunId) {
      setRun(null);
      setStepResults([]);
      setSelShot(null);
      setLiveXml(null);
      setLiveXmlName(null);
      setWsLive(true);
      return;
    }
    setRun(null);
    setStepResults([]);
    setSelShot(null);
    setLiveXml(null);
    setLiveXmlName(null);
    setWsLive(true);
    loadRun(activeRunId);
    wsRef.current?.close();
    if (wsReconnectTimerRef.current) {
      clearTimeout(wsReconnectTimerRef.current);
      wsReconnectTimerRef.current = null;
    }
    let cancelled = false;
    const wsProto = location.protocol === "https:" ? "wss:" : "ws:";
    const connect = () => {
      if (cancelled) return;
      wsRef.current?.close();
      (async () => {
        const token = await api.bootstrapAuth();
        if (cancelled) return;
        const url = `${wsProto}//${location.host}/ws/runs/${activeRunId}${token ? `?token=${encodeURIComponent(token)}` : ""}`;
        const ws = new WebSocket(url);
        wsRef.current = ws;
        ws.onopen = () => {
          if (!cancelled) {
            setWsLive(true);
            wsReconnectAttemptRef.current = 0;
          }
        };
        ws.onmessage = msg => {
          const ev = JSON.parse(msg.data);
          if (ev.type === "step") {
            const p = ev.payload;
            setStepResults(prev => { const n = [...prev]; n[p.idx] = { idx: p.idx, status: p.status, details: p.details, screenshot: p.screenshot, pageSource: p.pageSource }; return n; });
            if (p.screenshot) setSelShot(p.screenshot);
            if (p.pageSource) {
              setLiveXmlName(p.pageSource);
              fetch(`/api/artifacts/${project.id}/${activeRunId}/${p.pageSource}`).then(r => r.ok ? r.text() : "").then(setLiveXml).catch(() => {});
            }
          }
          if (ev.type === "finished") { loadRun(activeRunId); onRefresh(); }
        };
        ws.onerror = () => { try { ws.close(); } catch { /* ignore */ } };
        ws.onclose = () => {
          if (cancelled) return;
          setWsLive(false);
          const attempt = wsReconnectAttemptRef.current++;
          const delay = Math.min(1000 * Math.pow(2, attempt), 15000);
          wsReconnectTimerRef.current = setTimeout(connect, delay);
        };
      })();
    };
    connect();
    pollRef.current = setInterval(async () => {
      try {
        const r = await api.getRun(activeRunId);
        setRun(r);
        const saved = (r.summary as any)?.stepResults;
        if (Array.isArray(saved) && saved.length) {
          setStepResults(prev => {
            if (["passed", "failed", "error", "cancelled"].includes(r.status)) return saved;
            if (saved.length > (prev?.length ?? 0)) return saved;
            return prev;
          });
        }
        if (["passed", "failed", "error", "cancelled"].includes(r.status)) {
          const shots = (r.artifacts as any)?.screenshots || [];
          if (shots.length) setSelShot(shots[shots.length - 1]);
          if (pollRef.current) clearInterval(pollRef.current);
        }
      } catch { /* ignore */ }
    }, 5000);
    return () => {
      cancelled = true;
      if (wsReconnectTimerRef.current) {
        clearTimeout(wsReconnectTimerRef.current);
        wsReconnectTimerRef.current = null;
      }
      wsReconnectAttemptRef.current = 0;
      wsRef.current?.close();
      wsRef.current = null;
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [activeRunId, loadRun, onRefresh, project.id]);

  const waitForRunComplete = async (runId: number): Promise<Run> => {
    for (let i = 0; i < 300; i++) {
      if (agentPausedRef.current) throw new Error("Agent paused");
      const r = await api.getRun(runId);
      if (["passed", "failed", "error", "cancelled"].includes(r.status)) return r;
      await new Promise(resolve => setTimeout(resolve, 2000));
    }
    throw new Error("Run timed out");
  };

  const addAgentLog = (msg: string) => {
    setAgentProgressLog(prev => [...prev.slice(-49), `[${new Date().toLocaleTimeString()}] ${msg}`]);
  };

  const runAgentLoop = async (test: TestDef) => {
    let bestPassed = 0;
    let currentTest = test;
    let lastFailedFix: any[] = [];
    for (let iter = 0; iter < agentMaxIterations; iter++) {
      if (agentPausedRef.current) {
        addAgentLog("Paused by user");
        return;
      }
      const freshTests = await api.listTests(project.id);
      currentTest = freshTests.find(t => t.id === test.id) || currentTest;
      setAgentStatus(`Running ${currentTest.name} — attempt ${iter + 1}/${agentMaxIterations}`);
      addAgentLog(`Starting run for "${currentTest.name}" (attempt ${iter + 1})`);
      const r = await api.createRun({ project_id: project.id, build_id: buildId!, test_id: currentTest.id, platform, device_target: deviceTarget });
      onRunCreated(r.id);
      addAgentLog(`Run #${r.id} started — waiting for completion...`);
      let completed: Run;
      try {
        completed = await waitForRunComplete(r.id);
      } catch (e: any) {
        if (e.message === "Agent paused") {
          addAgentLog("Stopped after run completed");
          return;
        }
        throw e;
      }
      onRefresh();
      const stepResultsArr = (completed.summary as any)?.stepResults || [];
      const totalSteps = ((completed.summary as any)?.stepDefinitions || []).length || stepResultsArr.length;
      const passedSteps = stepResultsArr.filter((s: any) => s?.status === "passed").length;
      addAgentLog(`Run #${completed.id} finished: ${passedSteps}/${totalSteps} steps passed (${completed.status})`);
      if (passedSteps === totalSteps && totalSteps > 0) {
        addAgentLog(`✓ ${currentTest.name} passed after ${iter + 1} attempt(s)`);
        toast(`Agent: ${currentTest.name} passed after ${iter + 1} attempt(s)`, "success");
        return;
      }
      let testsForFix = freshTests;
      if (passedSteps < bestPassed && iter > 0) {
        const failedIdxRevert = stepResultsArr.findIndex((s: any) => s?.status === "failed");
        const prereqForRevert = currentTest.prerequisite_test_id ? freshTests.find(t => t.id === currentTest.prerequisite_test_id) : null;
        const targetForRevert = prereqForRevert && failedIdxRevert < stepsForPlatform(prereqForRevert, platform).length ? prereqForRevert : currentTest;
        lastFailedFix = (targetForRevert.fix_history as any[])?.slice(-1) ?? [];
        addAgentLog(`Fix regressed (${passedSteps} < ${bestPassed}). Reverting and requesting different solution...`);
        toast(`Agent: fix regressed — reverting and asking AI for a different approach`, "info");
        try {
          await api.undoLastFix(targetForRevert.id);
          onRefresh();
          testsForFix = await api.listTests(project.id);
          currentTest = testsForFix.find(t => t.id === test.id) || currentTest;
        } catch (e: any) {
          addAgentLog(`Revert failed: ${e.message}`);
          return;
        }
      } else {
        bestPassed = Math.max(bestPassed, passedSteps);
      }
      const failedIdx = stepResultsArr.findIndex((s: any) => s?.status === "failed");
      if (failedIdx < 0) return;
      setAgentStatus(`Applying AI fix for step ${failedIdx + 1}...`);
      addAgentLog(`Failed at step ${failedIdx + 1}. Requesting AI fix (tap diagnosis runs on server)...`);
      const prereq = currentTest.prerequisite_test_id ? testsForFix.find(t => t.id === currentTest.prerequisite_test_id) : null;
      const mergedSteps = prereq ? [...stepsForPlatform(prereq, platform), ...stepsForPlatform(currentTest, platform)] : stepsForPlatform(currentTest, platform);
      const targetTest = prereq && failedIdx < stepsForPlatform(prereq, platform).length ? prereq : currentTest;
      const prereqLen = prereq ? stepsForPlatform(prereq, platform).length : 0;
      let failXml = "";
      let failXmlRaw = "";
      const PAGE_SRC_RAW_CAP = 400_000;
      const artifacts = (completed.artifacts as any) || {};
      const pageSources = artifacts.pageSources || [];
      const screenshots = artifacts.screenshots || [];
      const failPs = stepResultsArr[failedIdx]?.pageSource || pageSources[failedIdx];
      if (failPs) {
        try {
          const resp = await fetch(`/api/artifacts/${completed.project_id}/${completed.id}/${failPs}`);
          if (resp.ok) {
            const raw = await resp.text();
            failXmlRaw = raw.slice(0, PAGE_SRC_RAW_CAP);
            failXml = simplifyXmlForAI(raw);
          }
        } catch { /* ignore */ }
      }
      let screenshotB64 = "";
      const failShot = stepResultsArr[failedIdx]?.screenshot || screenshots[failedIdx];
      if (failShot) {
        try {
          const resp = await fetch(`/api/artifacts/${completed.project_id}/${completed.id}/${failShot}`);
          if (resp.ok) {
            const blob = await resp.blob();
            screenshotB64 = await new Promise<string>(res => { const rd = new FileReader(); rd.onloadend = () => res(rd.result as string); rd.readAsDataURL(blob); });
          }
        } catch {}
      }
      const errMsg = completed.error_message || (typeof stepResultsArr[failedIdx]?.details === "string" ? stepResultsArr[failedIdx].details : stepResultsArr[failedIdx]?.details?.error || "");
      const buildInfoAgent = completed.build_id ? builds.find(b => b.id === completed.build_id) : null;
      const metaAgent = (buildInfoAgent?.metadata || {}) as any;
      const appContextAgent = [metaAgent.display_name, metaAgent.package].filter(Boolean).join(" · ") || undefined;
      try {
        const fixRes = await api.fixSteps({
          platform,
          target_platform: completed.platform,
          original_steps: mergedSteps,
          step_results: stepResultsArr.map((s: any) => s ? { status: s.status, details: s.details } : { status: "pending" }),
          failed_step_index: failedIdx,
          error_message: errMsg,
          page_source_xml: failXml,
          page_source_xml_raw: failXmlRaw,
          test_name: currentTest.name,
          screenshot_base64: screenshotB64,
          already_tried_fixes: [...(targetTest.fix_history || []), ...lastFailedFix],
          acceptance_criteria: targetTest.acceptance_criteria || "",
          app_context: appContextAgent,
        });
        if (fixRes.tap_diagnosis) {
          const d = fixRes.tap_diagnosis;
          const causeLabels: Record<string, string> = {
            wrong_selector: "wrong selector strategy",
            timing_race: "timing — element not ready yet",
            scrolled_off: "element scrolled off screen",
            overlay_blocking: "overlay blocking element",
            element_disabled: "element disabled",
            wrong_screen: "app on wrong screen",
            element_missing: "element not in page source",
            xml_parse_failed: "XML parse — use raw hierarchy for diagnosis",
          };
          addAgentLog(
            `Tap diagnosis: ${causeLabels[d.root_cause] || d.root_cause}` +
              (d.suggestions?.[0] ? ` → try ${d.suggestions[0].strategy}=${d.suggestions[0].value}` : "")
          );
          if (d.recommended_wait_ms) addAgentLog(`Diagnosis: add ~${d.recommended_wait_ms}ms wait before tap if needed`);
        }
        addAgentLog(`AI fix applied: ${fixRes.changes.length} change(s). Updating test...`);
        setRun(completed);
        setStepResults(stepResultsArr);
        setFixResult(fixRes);
        setTapDiagnosis(fixRes.tap_diagnosis ?? null);
        setShowFixPanel(true);
        const fixedForTarget = prereq ? (failedIdx < prereqLen ? fixRes.fixed_steps.slice(0, prereqLen) : fixRes.fixed_steps.slice(prereqLen)) : fixRes.fixed_steps;
        await api.updateTest(targetTest.id, { steps: fixedForTarget, platform: completed.platform });
        await api.appendFixHistory(targetTest.id, { analysis: fixRes.analysis, fixed_steps: fixedForTarget, changes: fixRes.changes, run_id: completed.id, steps_before_fix: stepsForPlatform(targetTest, completed.platform), target_platform: completed.platform });
        onRefresh();
        lastFailedFix = [];
        addAgentLog(`Fix saved. Rerunning...`);
      } catch (e: any) {
        addAgentLog(`Fix failed: ${e.message}`);
        toast(`Agent: fix failed — ${e.message}`, "error");
        return;
      }
    }
    addAgentLog(`Max iterations (${agentMaxIterations}) reached`);
    toast(`Agent: ${currentTest.name} — max iterations reached`, "error");
  };

  const startRun = async () => {
    const toRun: TestDef[] = runMode === "single" ? (testId ? tests.filter(t => t.id === testId) : []) : batchTests;
    if (toRun.length === 0) { toast(runMode === "single" ? "Select a test" : runMode === "suite" ? "Select a suite" : "Select a collection", "error"); return; }
    if (!buildId) { toast("Select a build", "error"); return; }
    setBusy(true);
    try {
      if (agentMode) {
        agentPausedRef.current = false;
        setAgentRunning(true);
        setAgentStatus("Starting...");
        setAgentProgressLog([]);
        setShowFixPanel(false);
        setFixResult(null);
        setTapDiagnosis(null);
        for (let i = 0; i < toRun.length; i++) {
          if (agentPausedRef.current) break;
          setAgentStatus(`Test ${i + 1}/${toRun.length}: ${toRun[i].name}`);
          addAgentLog(`--- Test ${i + 1}/${toRun.length}: ${toRun[i].name} ---`);
          await runAgentLoop(toRun[i]);
        }
        setAgentStatus(agentPausedRef.current ? "Paused" : "Done");
        setAgentRunning(false);
        onRefresh();
      } else if (runMode !== "single" && (selectedSuiteId || selectedCollectionId)) {
        const sourceId = runMode === "suite" ? selectedSuiteId! : selectedCollectionId!;
        const batch = await api.createBatchRun({
          project_id: project.id,
          build_id: buildId,
          mode: runMode as "suite" | "collection",
          source_id: sourceId,
          platform,
          device_target: deviceTarget,
        });
        setStepResults([]); setSelShot(null);
        onBatchCreated(batch.id);
        toast(`${batch.source_name}: ${batch.total} tests queued`, "success");
      } else {
        const created: Run[] = [];
        for (const t of toRun) {
          const r = await api.createRun({ project_id: project.id, build_id: buildId, test_id: t.id, platform, device_target: deviceTarget });
          created.push(r);
        }
        setStepResults([]); setSelShot(null);
        if (created.length > 0) { onRunCreated(created[0].id); toast(created.length > 1 ? `${created.length} runs queued — #${created[0].id} started` : `Run #${created[0].id} started`, "success"); }
      }
    } catch (e: any) { toast(e.message, "error"); setAgentRunning(false); }
    finally { setBusy(false); }
  };

  const pauseAgent = () => {
    agentPausedRef.current = true;
    setAgentStatus("Pausing after current run...");
    addAgentLog("Pause requested — will stop after current run completes");
    toast("Agent will pause after current run completes", "info");
  };

  // AI Fix state
  const [fixBusy, setFixBusy] = useState(false);
  const [fixResult, setFixResult] = useState<{ analysis: string; fixed_steps: any[]; changes: any[] } | null>(null);
  const [tapDiagnosis, setTapDiagnosis] = useState<TapDiagnosisOut | null>(null);
  const [showFixPanel, setShowFixPanel] = useState(false);
  const [fixSuggestion, setFixSuggestion] = useState("");
  const [relatedTests, setRelatedTests] = useState<{ dependents: TestDef[]; similar: { test: TestDef; shared_prefix_length: number }[] } | null>(null);

  /** Shown run: full loaded row, or list cache while getRun() loads — avoids flashing Run setup when opening a recent run. */
  const displayRun = useMemo(() => {
    if (!activeRunId) return null;
    if (run && run.id === activeRunId) return run;
    return runs.find(r => r.id === activeRunId) ?? null;
  }, [activeRunId, run, runs]);

  const artBase = displayRun ? `/api/artifacts/${displayRun.project_id}/${displayRun.id}/` : "";
  const screenshots = ((displayRun?.artifacts as any)?.screenshots || []) as string[];
  const pageSources = ((displayRun?.artifacts as any)?.pageSources || []) as string[];
  const testForRun = displayRun?.test_id ? tests.find(t => t.id === displayRun.test_id) : null;
  const prereq = testForRun?.prerequisite_test_id ? tests.find(t => t.id === testForRun.prerequisite_test_id) : null;
  const runPf = (displayRun?.platform ?? platform) as Run["platform"];
  const mergedSteps = prereq ? [...stepsForPlatform(prereq, runPf), ...stepsForPlatform(testForRun, runPf)] : stepsForPlatform(testForRun, runPf);
  const prereqStepsLen = prereq ? stepsForPlatform(prereq, runPf).length : 0;
  const latestPrereqRun = useMemo(() => {
    if (!prereq) return null;
    const matches = runs.filter(r => r.test_id === prereq.id);
    if (matches.length === 0) return null;
    return matches.reduce((a, b) => (a.id > b.id ? a : b));
  }, [runs, prereq]);
  const stepDefs = ((displayRun?.summary as any)?.stepDefinitions as any[]) || mergedSteps;
  const completedSteps = stepResults.filter(s => s?.status).length;
  const passedSteps = stepResults.filter(s => s?.status === "passed").length;
  const totalSteps = stepDefs.length;
  const pct = totalSteps > 0 ? Math.round((passedSteps / totalSteps) * 100) : 0;

  const failedIdx = stepResults.findIndex(s => s?.status === "failed");
  const isFailed = displayRun && ["failed", "error"].includes(displayRun.status) && failedIdx >= 0;
  const failureInPrereqSegment = Boolean(prereq && failedIdx >= 0 && failedIdx < prereqStepsLen);
  const showPrereqHelpActions = Boolean(displayRun && prereq && failureInPrereqSegment && ["failed", "error"].includes(displayRun.status));

  const rerun = async () => {
    if (!displayRun) return;
    setBusy(true);
    try {
      const r = await api.createRun({
        project_id: displayRun.project_id,
        build_id: displayRun.build_id ?? undefined,
        test_id: displayRun.test_id!,
        platform: displayRun.platform,
        device_target: displayRun.device_target,
      });
      setStepResults([]);
      setSelShot(null);
      onRunCreated(r.id);
      toast(`Rerun → #${r.id}`, "success");
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  };

  const loadXmlForStep = useCallback(async (idx: number) => {
    const ps = pageSources[idx] || stepResults[idx]?.pageSource;
    if (ps) {
      try {
        const url = `${artBase}${ps}?v=${idx}`;
        const r = await fetch(url, { cache: "no-store" });
        setLiveXml(r.ok ? await r.text() : null);
      } catch {
        setLiveXml(null);
      }
    } else {
      setLiveXml(null);
    }
  }, [artBase, pageSources, stepResults]);

  const selShotIdx = useMemo(() => {
    if (!selShot || screenshots.length === 0) return -1;
    return screenshots.indexOf(selShot);
  }, [selShot, screenshots]);

  /** Changes when the artifact path for the *selected* step updates (avoids refetching XML on every other step's WS event). */
  const selXmlArtifactKey = useMemo(() => {
    if (!displayRun || selShotIdx < 0) return "";
    return String(pageSources[selShotIdx] || (stepResults[selShotIdx] as any)?.pageSource || "");
  }, [displayRun?.id, selShotIdx, pageSources, stepResults]);

  // Keep XML in sync when selection, run, or this step's page source path changes (incl. after run completes)
  useEffect(() => {
    if (!displayRun || selShotIdx < 0) return;
    loadXmlForStep(selShotIdx);
  }, [selShotIdx, displayRun?.id, displayRun?.status, selXmlArtifactKey, loadXmlForStep]);

  const aiFixRun = async () => {
    if (!displayRun || !testForRun || failedIdx < 0) return;
    setFixBusy(true); setFixResult(null); setTapDiagnosis(null); setShowFixPanel(true);
    toast("AI is analyzing screenshot, XML, and error logs...", "info");

    // Fetch page source: simplified for LLM + raw for tap diagnosis (strict XML parser on server)
    const PAGE_SRC_RAW_CAP = 400_000;
    let failXml = "";
    let failXmlRaw = "";
    const failPs = pageSources[failedIdx] || stepResults[failedIdx]?.pageSource;
    if (failPs) {
      try {
        const r = await fetch(artBase + failPs);
        if (r.ok) {
          const raw = await r.text();
          failXmlRaw = raw.slice(0, PAGE_SRC_RAW_CAP);
          failXml = simplifyXmlForAI(raw);
        }
      } catch { /* ignore */ }
    }
    if (!failXml && liveXml) {
      failXml = simplifyXmlForAI(liveXml);
      if (!failXmlRaw) failXmlRaw = liveXml.slice(0, PAGE_SRC_RAW_CAP);
    }

    // Fetch screenshot from the failed step as base64
    let screenshotB64 = "";
    const failShot = stepResults[failedIdx]?.screenshot || screenshots[failedIdx];
    if (failShot) {
      try {
        const r = await fetch(artBase + failShot);
        if (r.ok) {
          const blob = await r.blob();
          screenshotB64 = await new Promise<string>((resolve) => {
            const reader = new FileReader();
            reader.onloadend = () => resolve(reader.result as string);
            reader.readAsDataURL(blob);
          });
        }
      } catch {}
    }

    const failDetails = stepResults[failedIdx]?.details;
    const errMsg = displayRun.error_message || (typeof failDetails === "string" ? failDetails : failDetails?.error || JSON.stringify(failDetails || {}));

    const prereqStepsLen = prereq ? stepsForPlatform(prereq, displayRun.platform).length : 0;
    const failureInPrereq = prereq && failedIdx < prereqStepsLen;
    const targetTest = failureInPrereq ? prereq : testForRun;
    const testNameWithContext = prereq
      ? `${testForRun.name} (main) · Prerequisite: ${prereq.name} · Failure at step ${failedIdx + 1}${failureInPrereq ? " (in prerequisite)" : ""}`
      : testForRun.name;
    const buildInfo = displayRun.build_id ? builds.find(b => b.id === displayRun.build_id) : null;
    const meta = (buildInfo?.metadata || {}) as any;
    const appContext = [meta.display_name, meta.package].filter(Boolean).join(" · ") || undefined;

    try {
      const res = await api.fixSteps({
        platform: displayRun.platform,
        target_platform: displayRun.platform,
        original_steps: stepDefs,
        step_results: stepResults.map(s => s ? { status: s.status, details: s.details } : { status: "pending" }),
        failed_step_index: failedIdx,
        error_message: errMsg,
        page_source_xml: failXml,
        page_source_xml_raw: failXmlRaw,
        test_name: testNameWithContext,
        screenshot_base64: screenshotB64,
        already_tried_fixes: targetTest.fix_history || [],
        acceptance_criteria: targetTest.acceptance_criteria || "",
        app_context: appContext,
      });
      setFixResult(res);
      setTapDiagnosis(res.tap_diagnosis ?? null);
      setRelatedTests(null);
      setFixBusy(false);
      if (targetTest) {
        void api.getRelatedTests(targetTest.id).then(rel => setRelatedTests(rel)).catch(() => {});
      }
      toast(`AI found ${res.changes.length} fix${res.changes.length !== 1 ? "es" : ""}`, "success");
    } catch (e: any) {
      toast(e.message, "error");
      setShowFixPanel(false);
      setFixBusy(false);
    }
  };

  const refineFixRun = async () => {
    if (!displayRun || !testForRun || !fixResult || !fixSuggestion.trim()) return;
    setFixBusy(true);
    setTapDiagnosis(null);
    toast("AI is refining the fix with your suggestion...", "info");
    const PAGE_SRC_RAW_CAP = 400_000;
    let failXml = "";
    let failXmlRaw = "";
    const failPs = pageSources[failedIdx] || stepResults[failedIdx]?.pageSource;
    if (failPs) {
      try {
        const r = await fetch(artBase + failPs);
        if (r.ok) {
          const raw = await r.text();
          failXmlRaw = raw.slice(0, PAGE_SRC_RAW_CAP);
          failXml = simplifyXmlForAI(raw);
        }
      } catch { /* ignore */ }
    }
    if (!failXml && liveXml) {
      failXml = simplifyXmlForAI(liveXml);
      if (!failXmlRaw) failXmlRaw = liveXml.slice(0, PAGE_SRC_RAW_CAP);
    }
    let screenshotB64 = "";
    const failShot = stepResults[failedIdx]?.screenshot || screenshots[failedIdx];
    if (failShot) {
      try {
        const r = await fetch(artBase + failShot);
        if (r.ok) {
          const blob = await r.blob();
          screenshotB64 = await new Promise<string>((resolve) => {
            const reader = new FileReader();
            reader.onloadend = () => resolve(reader.result as string);
            reader.readAsDataURL(blob);
          });
        }
      } catch {}
    }
    const failDetails = stepResults[failedIdx]?.details;
    const errMsg = displayRun.error_message || (typeof failDetails === "string" ? failDetails : failDetails?.error || JSON.stringify(failDetails || {}));
    const prereqStepsLenR = prereq ? stepsForPlatform(prereq, displayRun.platform).length : 0;
    const failureInPrereqR = prereq && failedIdx < prereqStepsLenR;
    const targetTestR = failureInPrereqR ? prereq : testForRun;
    const testNameWithContextR = prereq
      ? `${testForRun.name} (main) · Prerequisite: ${prereq.name} · Failure at step ${failedIdx + 1}${failureInPrereqR ? " (in prerequisite)" : ""}`
      : testForRun.name;
    const buildInfoR = displayRun.build_id ? builds.find(b => b.id === displayRun.build_id) : null;
    const metaR = (buildInfoR?.metadata || {}) as any;
    const appContextR = [metaR.display_name, metaR.package].filter(Boolean).join(" · ") || undefined;
    try {
      const res = await api.refineFix({
        platform: displayRun.platform,
        target_platform: displayRun.platform,
        original_steps: stepDefs,
        step_results: stepResults.map(s => s ? { status: s.status, details: s.details } : { status: "pending" }),
        failed_step_index: failedIdx,
        error_message: errMsg,
        page_source_xml: failXml,
        page_source_xml_raw: failXmlRaw,
        test_name: testNameWithContextR,
        screenshot_base64: screenshotB64,
        acceptance_criteria: targetTestR.acceptance_criteria || "",
        app_context: appContextR,
        fix_history: targetTestR.fix_history || [],
        previous_analysis: fixResult.analysis,
        previous_fixed_steps: fixResult.fixed_steps,
        previous_changes: fixResult.changes,
        user_suggestion: fixSuggestion.trim(),
      });
      setFixResult(res);
      setTapDiagnosis(res.tap_diagnosis ?? null);
      setFixSuggestion("");
      toast("Fix refined", "success");
    } catch (e: any) {
      toast(e.message, "error");
    } finally { setFixBusy(false); }
  };

  const prereqStepsLenApply = prereq ? stepsForPlatform(prereq, displayRun?.platform ?? platform).length : 0;
  const failureInPrereqApply = prereq && failedIdx >= 0 && failedIdx < prereqStepsLenApply;
  const targetTestApply = failureInPrereqApply && prereq ? prereq : testForRun;
  const fixedStepsForTarget = prereq && fixResult
    ? (failureInPrereqApply ? fixResult.fixed_steps.slice(0, prereqStepsLenApply) : fixResult.fixed_steps.slice(prereqStepsLenApply))
    : (fixResult?.fixed_steps ?? []);

  const applyFix = async (mode: "update" | "new") => {
    if (!fixResult || !testForRun || !displayRun || !targetTestApply) return;
    setBusy(true);
    try {
      if (mode === "update") {
        await api.updateTest(targetTestApply.id, { steps: fixedStepsForTarget, platform: displayRun.platform });
        await api.appendFixHistory(targetTestApply.id, { analysis: fixResult.analysis, fixed_steps: fixedStepsForTarget, changes: fixResult.changes, run_id: displayRun.id, steps_before_fix: stepsForPlatform(targetTestApply, displayRun.platform), target_platform: displayRun.platform });
        toast(failureInPrereqApply ? `Prerequisite "${targetTestApply.name}" updated with fixed steps` : "Test updated with fixed steps", "success");
      } else {
        const psNew = displayRun.platform === "ios_sim" ? { android: [] as any[], ios_sim: fixedStepsForTarget } : { android: fixedStepsForTarget, ios_sim: [] as any[] };
        await api.createTest(project.id, { name: `${targetTestApply.name} (fixed)`, steps: displayRun.platform === "android" ? fixedStepsForTarget : [], platform_steps: psNew, acceptance_criteria: targetTestApply.acceptance_criteria || null });
        toast("New test created with fixed steps", "success");
      }
      setShowFixPanel(false);
      setFixResult(null);
      setTapDiagnosis(null);
      setRelatedTests(null);
      onRefresh();
    } catch (e: any) { toast(e.message, "error"); }
    finally { setBusy(false); }
  };

  const applyFixAllRelated = async () => {
    if (!fixResult || !testForRun || !displayRun) return;
    if (failureInPrereqApply) {
      await applyFix("update");
      return;
    }
    if (!relatedTests?.similar?.length) return;
    const mainFailedIdx = prereq ? failedIdx - prereqStepsLenApply : failedIdx;
    const prefixLen = Math.min(mainFailedIdx + 1, stepsForPlatform(testForRun, displayRun.platform).length, (prereq ? fixResult.fixed_steps.slice(prereqStepsLenApply) : fixResult.fixed_steps).length);
    if (prefixLen < 2) return;
    setBusy(true);
    try {
      const stepsToApply = prereq ? fixResult.fixed_steps.slice(prereqStepsLenApply) : fixResult.fixed_steps;
      await api.updateTest(testForRun.id, { steps: stepsToApply, platform: displayRun.platform });
      await api.appendFixHistory(testForRun.id, { analysis: fixResult.analysis, fixed_steps: stepsToApply, changes: fixResult.changes, run_id: displayRun.id, steps_before_fix: stepsForPlatform(testForRun, displayRun.platform), target_platform: displayRun.platform });
      const res = await api.applyFixToRelated(testForRun.id, {
        fixed_steps: stepsToApply,
        prefix_length: prefixLen,
        original_steps: stepsForPlatform(testForRun, displayRun.platform),
        target_platform: displayRun.platform,
      });
      setShowFixPanel(false);
      setFixResult(null);
      setTapDiagnosis(null);
      setRelatedTests(null);
      onRefresh();
      toast(`Fixed this test + ${res.updated_test_ids.length} related test${res.updated_test_ids.length !== 1 ? "s" : ""}`, "success");
    } catch (e: any) { toast(e.message, "error"); }
    finally { setBusy(false); }
  };

  const rerunWithFix = async () => {
    if (!fixResult || !displayRun || !testForRun) return;
    await applyFix("update");
    setTimeout(async () => {
      try {
        const r = await api.createRun({ project_id: displayRun.project_id, build_id: displayRun.build_id ?? undefined, test_id: testForRun.id, platform: displayRun.platform, device_target: displayRun.device_target });
        setStepResults([]); setSelShot(null); setFixResult(null); setTapDiagnosis(null); setShowFixPanel(false);
        onRunCreated(r.id);
        toast(`Rerun with fix → #${r.id}`, "success");
      } catch (e: any) { toast(e.message, "error"); }
    }, 500);
  };

  const downloadFixReport = async () => {
    if (!fixResult || !displayRun || !testForRun) return;
    const buildInfo = displayRun.build_id ? builds.find(b => b.id === displayRun.build_id) : null;
    const meta = (buildInfo?.metadata || {}) as any;

    // Collect all screenshot URLs as base64 for embedding
    const shotEntries: { idx: number; status: string; b64: string }[] = [];
    for (let i = 0; i < stepResults.length; i++) {
      const sr = stepResults[i];
      const shotName = sr?.screenshot || screenshots[i];
      if (shotName) {
        try {
          const r = await fetch(artBase + shotName);
          if (r.ok) {
            const blob = await r.blob();
            const b64: string = await new Promise(resolve => { const rd = new FileReader(); rd.onloadend = () => resolve(rd.result as string); rd.readAsDataURL(blob); });
            shotEntries.push({ idx: i, status: sr?.status || "pending", b64 });
          }
        } catch {}
      }
    }

    const stepsHtml = stepDefs.map((s: any, i: number) => {
      const sr = stepResults[i];
      const st = sr?.status || "pending";
      const details = sr?.details ? (typeof sr.details === "string" ? sr.details : sr.details?.error || JSON.stringify(sr.details)) : "";
      const bg = st === "passed" ? "#0a2a1a" : st === "failed" ? "#2a0a0f" : "#151a20";
      const color = st === "passed" ? "#00e5a0" : st === "failed" ? "#ff3b5c" : "#8a8f98";
      return `<tr style="background:${bg}"><td style="color:${color};font-weight:700">${i + 1}</td><td>${s.type}</td><td style="font-family:monospace;font-size:11px">${s.selector ? `${s.selector.using}=${s.selector.value}` : "—"}</td><td>${s.text || s.expect || ""}</td><td style="color:${color};font-weight:600">${st.toUpperCase()}</td><td style="font-size:11px;color:#8a8f98">${details}</td></tr>`;
    }).join("");

    const changesHtml = fixResult.changes.map((c: any, ci: number) => {
      return `<div style="margin-bottom:10px;padding:12px;border:1px solid #2a2f38;border-radius:6px"><div style="margin-bottom:6px"><span style="background:rgba(255,59,92,.15);color:#ff3b5c;padding:2px 8px;border-radius:3px;font-family:monospace;font-size:11px">Step ${(c.step_index ?? ci) + 1}</span> <span style="color:#8a8f98;font-size:12px">${c.reason || ""}</span></div><div style="display:grid;grid-template-columns:1fr 1fr;gap:10px"><div><div style="font-size:10px;text-transform:uppercase;color:#ff3b5c;margin-bottom:4px;font-weight:700">Before</div><div style="font-family:monospace;font-size:11px;color:#8a8f98;background:rgba(255,59,92,.06);padding:8px;border-radius:4px;word-break:break-all">${typeof c.was === "string" ? c.was : JSON.stringify(c.was)}</div></div><div><div style="font-size:10px;text-transform:uppercase;color:#00e5a0;margin-bottom:4px;font-weight:700">After</div><div style="font-family:monospace;font-size:11px;color:#00e5a0;background:rgba(0,229,160,.06);padding:8px;border-radius:4px;word-break:break-all">${typeof c.now === "string" ? c.now : JSON.stringify(c.now)}</div></div></div></div>`;
    }).join("");

    const screenshotsHtml = shotEntries.map(s => {
      const border = s.status === "failed" ? "3px solid #ff3b5c" : s.status === "passed" ? "3px solid #00e5a0" : "2px solid #2a2f38";
      const label = s.status === "failed" ? "FAILED" : s.status === "passed" ? "PASSED" : "PENDING";
      const labelColor = s.status === "failed" ? "#ff3b5c" : s.status === "passed" ? "#00e5a0" : "#8a8f98";
      return `<div style="text-align:center"><img src="${s.b64}" style="width:160px;border-radius:8px;border:${border};display:block;margin:0 auto 6px" /><div style="font-size:10px;font-weight:700;color:${labelColor}">Step ${s.idx + 1} — ${label}</div></div>`;
    }).join("");

    const html = `<!DOCTYPE html><html><head><meta charset="UTF-8"><title>QA·OS Bug Report — Run #${displayRun.id}</title>
<style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:'Segoe UI',system-ui,-apple-system,sans-serif;background:#080b0f;color:#e8eaed;padding:32px;line-height:1.6}h1{font-size:22px;color:#00e5a0;margin-bottom:4px}h2{font-size:16px;color:#a78bfa;margin:28px 0 12px;border-bottom:1px solid #1e2430;padding-bottom:8px}h3{font-size:13px;color:#e8eaed;margin-bottom:8px}.meta{font-size:12px;color:#8a8f98;margin-bottom:24px}.card{background:#0d1117;border:1px solid #1e2430;border-radius:8px;padding:16px;margin-bottom:16px}.label{font-size:11px;text-transform:uppercase;color:#8a8f98;margin-bottom:4px;font-weight:600;letter-spacing:.5px}.val{font-size:14px;color:#e8eaed;margin-bottom:10px}table{width:100%;border-collapse:collapse;font-size:12px}th{background:#131920;color:#8a8f98;text-transform:uppercase;font-size:10px;letter-spacing:.5px;text-align:left;padding:8px 10px;border-bottom:1px solid #1e2430}td{padding:8px 10px;border-bottom:1px solid #151a20}.analysis{background:rgba(99,102,241,.08);border:1px solid rgba(99,102,241,.3);border-radius:8px;padding:16px;font-size:13px;line-height:1.8;margin-bottom:16px}.shots-grid{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}.footer{margin-top:32px;border-top:1px solid #1e2430;padding-top:16px;font-size:11px;color:#555}</style></head><body>
<h1>🤖 QA·OS Bug Report</h1>
<div class="meta">Run #${displayRun.id} · ${testForRun.name} · Generated ${new Date().toLocaleString()}</div>

<h2>Build & Device Info</h2>
<div class="card" style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
<div><div class="label">Project</div><div class="val">${project.name}</div>
<div class="label">Platform</div><div class="val">${displayRun.platform === "android" ? "Android" : "iOS Simulator"}</div>
<div class="label">Device</div><div class="val">${displayRun.device_target || "Default"}</div></div>
<div>${buildInfo ? `<div class="label">Build File</div><div class="val">${buildInfo.file_name}</div>
${meta.display_name ? `<div class="label">App Name</div><div class="val">${meta.display_name}${meta.version_name ? ` v${meta.version_name}` : ""}</div>` : ""}
${meta.package ? `<div class="label">Package</div><div class="val" style="font-family:monospace;font-size:12px;color:#7dd3fc">${meta.package}</div>` : ""}
${meta.main_activity ? `<div class="label">Activity</div><div class="val" style="font-family:monospace;font-size:11px;color:#7dd3fc">${meta.main_activity}</div>` : ""}` : `<div class="label">Build</div><div class="val" style="color:#8a8f98">No build attached</div>`}</div>
</div>

<h2>Run Summary</h2>
<div class="card" style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;text-align:center">
<div><div style="font-size:24px;font-weight:700;color:#ff3b5c">${displayRun.status.toUpperCase()}</div><div class="label">Status</div></div>
<div><div style="font-size:24px;font-weight:700">${completedSteps}/${totalSteps}</div><div class="label">Steps Done</div></div>
<div><div style="font-size:24px;font-weight:700;color:#00e5a0">${passedSteps}</div><div class="label">Passed</div></div>
<div><div style="font-size:24px;font-weight:700;color:#ff3b5c">${failedIdx >= 0 ? 1 : 0}</div><div class="label">Failed</div></div>
</div>

${displayRun.error_message ? `<h2>Error Message</h2><div class="card" style="border-color:rgba(255,59,92,.3);color:#ff3b5c;font-family:monospace;font-size:12px;white-space:pre-wrap">${displayRun.error_message}</div>` : ""}

${(targetTestApply?.fix_history?.length ?? 0) > 0 ? `<h2>Previously Tried Fixes (${targetTestApply!.fix_history!.length})</h2><div class="card">${(targetTestApply!.fix_history as any[]).map((h: any, i: number) => "<div style=\"margin-bottom:12px;padding:12px;border:1px solid #2a2f38;border-radius:6px;background:rgba(255,59,92,.04)\"><div style=\"font-size:10px;color:#8a8f98;margin-bottom:4px\">Attempt " + (i + 1) + (h.run_id ? " · Run #" + h.run_id : "") + (h.created_at ? " · " + h.created_at : "") + "</div><div style=\"font-size:12px;color:#e8eaed;margin-bottom:6px\">" + (h.analysis || "") + "</div>" + ((h.changes || []).length > 0 ? "<div style=\"font-size:10px;color:#8a8f98\">" + h.changes.length + " change(s)</div>" : "") + "</div>").join("")}</div>` : ""}

<h2>AI Root Cause Analysis (Current Fix)</h2>
<div class="analysis">${fixResult.analysis}</div>

${fixResult.changes.length > 0 ? `<h2>Changes Applied (${fixResult.changes.length})</h2><div class="card">${changesHtml}</div>` : ""}

<h2>Test Steps</h2>
<div class="card" style="padding:0;overflow:hidden">
<table><thead><tr><th>#</th><th>Action</th><th>Selector</th><th>Value</th><th>Status</th><th>Details</th></tr></thead><tbody>${stepsHtml}</tbody></table>
</div>

${shotEntries.length > 0 ? `<h2>Screenshots</h2><div class="shots-grid">${screenshotsHtml}</div>` : ""}

<div class="footer">QA·OS Automated Bug Report · ${project.name} · Run #${displayRun.id} · ${new Date().toISOString()}</div>
</body></html>`;

    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([html], { type: "text/html" }));
    a.download = `bug_report_run_${displayRun.id}_${(testForRun?.name ?? "test").replace(/\s+/g, "_")}.html`;
    a.click();
    toast("Bug report downloaded", "success");
  };

  const downloadVideo = async () => {
    if (!displayRun) { toast("No run", "error"); return; }
    const arts = displayRun.artifacts as Record<string, unknown> | undefined;
    const vid = arts?.video;
    /* Prefer stitched replay from step screenshots; fall back to device screen recording when no shots */
    if (screenshots.length > 0) {
      toast("Generating video from screenshots...", "info");
      const canvas = document.createElement("canvas");
      canvas.width = 540; canvas.height = 960;
      const ctx = canvas.getContext("2d")!;
      const stream = canvas.captureStream(30);
      const picked = pickScreenRecorderMime();
      const recorder = picked.mime
        ? new MediaRecorder(stream, { mimeType: picked.mime })
        : new MediaRecorder(stream);
      const chunks: Blob[] = [];
      recorder.ondataavailable = e => { if (e.data.size) chunks.push(e.data); };
      recorder.start();
      for (const shot of screenshots) {
        const img = new Image(); img.crossOrigin = "anonymous";
        await new Promise<void>(resolve => {
          img.onload = () => {
            ctx.fillStyle = "#000"; ctx.fillRect(0, 0, canvas.width, canvas.height);
            const scale = Math.min(canvas.width / img.width, canvas.height / img.height);
            const x = (canvas.width - img.width * scale) / 2, y = (canvas.height - img.height * scale) / 2;
            ctx.drawImage(img, x, y, img.width * scale, img.height * scale);
            // Step label overlay
            const idx = screenshots.indexOf(shot);
            const st = stepResults[idx]?.status;
            ctx.fillStyle = st === "failed" ? "rgba(255,59,92,.85)" : "rgba(0,229,160,.85)";
            ctx.fillRect(0, canvas.height - 40, canvas.width, 40);
            ctx.fillStyle = "#fff"; ctx.font = "bold 16px system-ui";
            ctx.fillText(`Step ${idx + 1} — ${stepDefs[idx]?.type || ""}  [${(st || "pending").toUpperCase()}]`, 12, canvas.height - 14);
            resolve();
          };
          img.onerror = () => resolve();
          img.src = artBase + shot;
        });
        await new Promise(r => setTimeout(r, 1500));
      }
      recorder.stop();
      await new Promise<void>(resolve => { recorder.onstop = () => resolve(); });
      const outMime = recorder.mimeType || (picked.mime ?? "video/webm");
      const ext = replayExtFromRecorderMime(outMime);
      const blob = new Blob(chunks, { type: outMime });
      const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
      a.download = `run_${displayRun.id}_replay.${ext}`; a.click();
      toast("Video downloaded", "success");
      return;
    }
    if (typeof vid === "string" && vid.length > 0) {
      toast("Downloading device recording…", "info");
      try {
        const res = await fetch(`/api/artifacts/${project.id}/${displayRun.id}/${encodeURIComponent(vid)}`);
        if (!res.ok) { toast("Video file not found on server", "error"); return; }
        const raw = await res.blob();
        const ct = res.headers.get("content-type")?.split(";")[0]?.trim() ?? "";
        const blob = ct && ct.startsWith("video/") ? new Blob([raw], { type: ct }) : raw;
        const blobType = (blob as Blob).type?.toLowerCase() ?? "";
        const vl = vid.toLowerCase();
        /* Filename must match real bytes — .mp4 with WebM/VP8 data breaks QuickTime */
        const ext =
          blobType.includes("webm") || ct.includes("webm") ? "webm"
          : blobType.includes("quicktime") || ct.includes("quicktime") || vl.endsWith(".mov") ? "mov"
          : vl.endsWith(".webm") ? "webm"
          : vl.endsWith(".mp4") ? "mp4"
          : "mp4";
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = `run_${displayRun.id}_recording.${ext}`;
        a.click();
        toast("Video downloaded", "success");
      } catch (e: any) {
        toast(e?.message || "Download failed", "error");
      }
      return;
    }
    toast("No screenshots or device recording for this run", "error");
  };

  return (
    <>
      <div className="section-head" style={{ marginBottom: 14 }}>
        <div>
          <div className="section-title">{displayRun ? `Live Execution — ${testForRun?.name || `Run #${displayRun.id}`}` : "Execution"}</div>
          <div className="section-sub">{displayRun ? `${displayRun.device_target || "default"} · ${displayRun.platform}${builds.find(b => b.id === displayRun.build_id)?.file_name ? ` · ${builds.find(b => b.id === displayRun.build_id)?.file_name}` : ""}` : "Select test and device to begin"}</div>
        </div>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
          {activeRunId && (
            <button type="button" className="btn-ghost" style={{ fontSize: 11, padding: "7px 14px" }} onClick={onClearActiveRun}>
              ← Run setup
            </button>
          )}
          {agentRunning && <button className="btn-ghost" style={{ fontSize: 11, padding: "7px 14px", color: "#a78bfa", borderColor: "rgba(167,139,250,.4)" }} onClick={pauseAgent}>⏸ Pause Agent</button>}
          {displayRun && (displayRun.status === "running" || displayRun.status === "queued") && !agentRunning && <button className="btn-ghost" style={{ fontSize: 11, padding: "7px 14px", color: "var(--danger)", borderColor: "rgba(255,59,92,.3)" }} onClick={async () => { try { await api.cancelRun(displayRun.id); toast("Stop requested", "info"); } catch (e: any) { toast(e.message, "error"); } }}>⏹ Stop</button>}
          {displayRun && <button className="btn-ghost" style={{ fontSize: 11, padding: "7px 14px" }} onClick={() => api.exportKatalon(displayRun.id)}>⬇ Katalon</button>}
          {displayRun && (screenshots.length > 0 || !!(displayRun.artifacts as Record<string, unknown> | undefined)?.video) && <button className="btn-ghost" style={{ fontSize: 11, padding: "7px 14px" }} onClick={downloadVideo}>🎬 Video</button>}
          {isFailed && <button className="btn-primary" style={{ fontSize: 11, padding: "7px 14px", background: "linear-gradient(135deg, #6366f1, #8b5cf6)" }} onClick={aiFixRun} disabled={fixBusy}>{fixBusy ? "⏳ AI Analyzing..." : "🤖 AI Fix"}</button>}
          {displayRun && ["passed", "failed", "error", "cancelled"].includes(displayRun.status) && <button className="run-now-btn" onClick={rerun} disabled={busy}>▶ Rerun</button>}
        </div>
      </div>

      {activeRunId && !wsLive && (
        <div style={{ padding: "10px 14px", marginBottom: 14, background: "rgba(255,107,53,.1)", border: "1px solid rgba(255,107,53,.35)", borderRadius: 8, fontSize: 12, color: "#ffb020" }}>
          Live feed offline — reconnecting… Step-level events may be delayed; run status still updates via polling.
        </div>
      )}

      {/* Agent progress only inside exec-layout when a run exists — avoids duplicate panels */}

      {/* Controls */}
      {!activeRunId && (
        <div className="panel" style={{ padding: 14, marginBottom: 16 }}>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center", marginBottom: 10 }}>
            <span style={{ fontSize: 11, color: "var(--muted)", fontWeight: 600 }}>Run</span>
            <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 11, cursor: "pointer" }}>
              <input type="radio" name="runMode" checked={runMode === "single"} onChange={() => { setRunMode("single"); setSelectedSuiteId(null); setSelectedCollectionId(null); }} />
              Single test
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 11, cursor: "pointer" }}>
              <input type="radio" name="runMode" checked={runMode === "suite"} onChange={() => { setRunMode("suite"); setTestId(null); setSelectedCollectionId(null); }} />
              Suite
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 11, cursor: "pointer" }}>
              <input type="radio" name="runMode" checked={runMode === "collection"} onChange={() => { setRunMode("collection"); setTestId(null); setSelectedSuiteId(null); }} />
              Collection
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, cursor: "pointer", marginLeft: 12, paddingLeft: 12, borderLeft: "1px solid var(--border)" }}>
              <input type="checkbox" checked={agentMode} onChange={e => setAgentMode(e.target.checked)} />
              <span style={{ color: agentMode ? "#a78bfa" : "var(--muted)" }}>Agent mode</span>
              <span style={{ fontSize: 10, color: "var(--muted)" }} title="Auto-fix on failure until pass or max 5 attempts">(auto-fix)</span>
            </label>
          </div>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
            <select value={platform} onChange={e => setPlatform(e.target.value as any)}><option value="android">Android</option><option value="ios_sim">iOS Simulator</option></select>
            <select value={buildId ?? ""} onChange={e => setBuildId(e.target.value ? Number(e.target.value) : null)}><option value="">{bfp.length > 0 ? "Select build" : "(no build)"}</option>{bfp.map(b => <option key={b.id} value={b.id}>#{b.id} {b.file_name}</option>)}</select>
            {runMode === "single" && (
              <select value={testId ?? ""} onChange={e => setTestId(e.target.value ? Number(e.target.value) : null)}><option value="">Select test</option>{tests.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}</select>
            )}
            {runMode === "suite" && (
              <select value={selectedSuiteId ?? ""} onChange={e => setSelectedSuiteId(e.target.value ? Number(e.target.value) : null)} style={{ minWidth: 200 }}>
                <option value="">Select suite</option>
                {modules.map(m => suites.filter(s => s.module_id === m.id).map(s => <option key={s.id} value={s.id}>{m.name} / {s.name}</option>))}
              </select>
            )}
            {runMode === "collection" && (
              <select value={selectedCollectionId ?? ""} onChange={e => setSelectedCollectionId(e.target.value ? Number(e.target.value) : null)} style={{ minWidth: 180 }}>
                <option value="">Select collection</option>
                {modules.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
              </select>
            )}
            <select value={deviceTarget} onChange={e => setDeviceTarget(e.target.value)}><option value="">Default device</option>{devList.map((d: any) => <option key={d.udid || d.serial} value={d.udid || d.serial}>{d.name || d.serial}</option>)}</select>
            <button className="run-now-btn" onClick={startRun} disabled={busy || (bfp.length > 0 && !buildId) || (runMode === "single" ? !testId : runMode === "suite" ? !selectedSuiteId : !selectedCollectionId) || (runMode !== "single" && batchTests.length === 0)}>
              ▶ {runMode === "single" ? "Start Run" : runMode === "suite" ? `Run Suite (${batchTests.length})` : `Run Collection (${batchTests.length})`}
            </button>
          </div>
          {bfp.length === 0 && builds.length > 0 && (
            <div style={{ fontSize: 11, color: "var(--warn)", marginTop: 8, padding: "0 4px" }}>No builds for {platform === "android" ? "Android" : "iOS"} — upload one in Build Management.</div>
          )}
        </div>
      )}

      {activeRunId && !displayRun && (
        <div className="panel" style={{ padding: 28, marginBottom: 16, textAlign: "center" }}>
          <div className="spinner" style={{ margin: "0 auto 12px" }} />
          <div style={{ fontSize: 13, color: "var(--muted)" }}>Loading run #{activeRunId}…</div>
        </div>
      )}

      {displayRun && (
        <div className="exec-layout">
          <div className="exec-left">
            {/* Agent Progress — inside exec so it stays visible with device/steps */}
            {(agentRunning || agentProgressLog.length > 0) && (
              <div className="panel" style={{ flexShrink: 0, marginBottom: 0, border: "1px solid rgba(167,139,250,.4)", background: "rgba(167,139,250,.04)" }}>
                <div className="panel-header" style={{ background: "linear-gradient(135deg, rgba(167,139,250,.15), rgba(139,92,246,.1))", padding: "10px 14px" }}>
                  <div>
                    <div className="panel-title" style={{ color: "#a78bfa", fontSize: 13 }}>🤖 Agent {agentRunning ? "Running" : "Progress"}</div>
                    <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }}>{agentStatus || (agentProgressLog.length ? "Completed" : "")}</div>
                  </div>
                  <div style={{ display: "flex", gap: 8 }}>
                    {agentRunning && <button className="btn-ghost btn-sm" style={{ color: "#a78bfa", borderColor: "rgba(167,139,250,.5)" }} onClick={pauseAgent}>⏸ Pause</button>}
                    {!agentRunning && agentProgressLog.length > 0 && <button className="btn-ghost btn-sm" style={{ fontSize: 10 }} onClick={() => setAgentProgressLog([])}>Clear log</button>}
                  </div>
                </div>
                <div style={{ padding: 10, maxHeight: 140, overflow: "auto", fontFamily: "var(--mono)", fontSize: 11, lineHeight: 1.5 }}>
                  {agentProgressLog.length === 0 ? <div style={{ color: "var(--muted)" }}>Waiting...</div> : agentProgressLog.map((line, i) => <div key={i} style={{ color: "var(--text)" }}>{line}</div>)}
                </div>
              </div>
            )}
            {/* Device Frame */}
            <div className="device-frame">
              <div className="device-toolbar">
                <div className="device-toolbar-label">Device Screen · {displayRun.device_target || "default"}</div>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  <div className="di-pill"><div className={`dot ${displayRun.status === "running" ? "dot-green" : "dot-gray"}`} />{displayRun.status === "running" ? "Connected" : displayRun.status}</div>
                  <div className="di-pill">{displayRun.platform === "android" ? "Android" : "iOS"}</div>
                </div>
                {displayRun.status === "running" && <div className="rec-badge"><div className="live-dot" />REC</div>}
              </div>
              <div className="device-screen">
                {selShot ? <img key={selShot} src={`${artBase}${selShot}?v=${selShot}`} alt="Device" className="device-screenshot" /> : <div className="device-placeholder">{displayRun.status === "running" ? "Waiting for screenshot..." : "No screenshot"}</div>}
              </div>
            </div>

            {/* Progress Bar */}
            <div className="exec-progress">
              <div className="progress-header"><div className="progress-label">{passedSteps} of {totalSteps} passed{completedSteps > passedSteps ? ` · ${completedSteps - passedSteps} failed` : ""}{displayRun.status === "cancelled" ? ` · stopped at step ${completedSteps}` : ""}</div><div className="progress-pct">{pct}%</div></div>
              <div className="progress-bar"><div className="progress-fill" style={{ width: `${pct}%`, background: displayRun.status === "cancelled" ? "var(--muted)" : completedSteps > passedSteps ? "var(--danger)" : undefined }} /></div>
            </div>

            {/* Live XML Panel */}
            <div className="panel">
              <div className="panel-header">
                <div className="panel-title">Page Source XML — Step {selShotIdx >= 0 ? `S${selShotIdx + 1}` : "—"}</div>
                <div style={{ fontSize: 11, color: "var(--muted)" }}>Captured after each step · click a step to view</div>
              </div>
              <div style={{ padding: 12 }}>
                {liveXml ? (
                  <XmlElementTree
                    key={`${displayRun.id}-${selShotIdx}-${selXmlArtifactKey}`}
                    xml={liveXml}
                    onCopy={(msg) => toast(msg, "success")}
                  />
                ) : (
                  <div className="xml-panel" style={{ color: "var(--muted)" }}>
                    {displayRun.status === "running" ? "Waiting for page source..." : pageSources.length > 0 ? "Click a step to view its XML" : "No page source captured"}
                  </div>
                )}
              </div>
            </div>

            {displayRun.error_message && <div className="error-box" style={{ marginTop: 12 }}><strong>Error:</strong> {displayRun.error_message}</div>}

            {/* AI Fix Panel */}
            {showFixPanel && (
              <div className="panel" style={{ marginTop: 12, border: "1px solid rgba(99,102,241,.4)", maxHeight: 520, overflow: "auto" }}>
                <div className="panel-header" style={{ background: "linear-gradient(135deg, rgba(99,102,241,.1), rgba(139,92,246,.1))", position: "sticky", top: 0, zIndex: 2 }}>
                  <div>
                    <div className="panel-title" style={{ color: "#a78bfa" }}>🤖 AI Fix Analysis{agentRunning ? " (Agent auto-applied)" : ""}</div>
                    {agentRunning && <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 2 }}>Agent applied this fix and is rerunning the test</div>}
                  </div>
                  <button className="btn-ghost btn-sm" onClick={() => { setShowFixPanel(false); setFixResult(null); setTapDiagnosis(null); setFixSuggestion(""); }} style={{ fontSize: 10 }} disabled={agentRunning}>✕ Close</button>
                </div>
                {tapDiagnosis && (
                  <div
                    style={{
                      margin: "10px 14px 0",
                      padding: "10px 12px",
                      borderRadius: 8,
                      border: "1px solid var(--border)",
                      background: "var(--bg3)",
                      fontSize: 11,
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8, flexWrap: "wrap" }}>
                      <span style={{ fontSize: 11, fontWeight: 600, fontFamily: "var(--sans)" }}>Tap diagnosis</span>
                      <span
                        style={{
                          fontSize: 10,
                          padding: "1px 7px",
                          borderRadius: 20,
                          fontWeight: 600,
                          background:
                            {
                              wrong_selector: "rgba(255,176,32,.15)",
                              timing_race: "rgba(99,102,241,.18)",
                              scrolled_off: "rgba(255,176,32,.15)",
                              overlay_blocking: "rgba(255,59,92,.12)",
                              element_disabled: "rgba(255,59,92,.12)",
                              wrong_screen: "rgba(255,176,32,.15)",
                              element_missing: "rgba(255,59,92,.15)",
                              xml_parse_failed: "rgba(255,176,32,.2)",
                            }[tapDiagnosis.root_cause] || "var(--bg2)",
                          color:
                            tapDiagnosis.root_cause === "element_missing" || tapDiagnosis.root_cause === "overlay_blocking" || tapDiagnosis.root_cause === "element_disabled"
                              ? "var(--danger)"
                              : "var(--warn)",
                        }}
                      >
                        {{
                          wrong_selector: "wrong selector",
                          timing_race: "timing issue",
                          scrolled_off: "scrolled off screen",
                          overlay_blocking: "overlay blocking",
                          element_disabled: "element disabled",
                          wrong_screen: "wrong screen",
                          element_missing: "element missing",
                          xml_parse_failed: "XML not parseable",
                        }[tapDiagnosis.root_cause as string] || tapDiagnosis.root_cause}
                      </span>
                      {tapDiagnosis.found && (
                        <span
                          style={{
                            fontSize: 10,
                            padding: "1px 7px",
                            borderRadius: 20,
                            background: "rgba(0,229,160,.12)",
                            color: "var(--accent)",
                            fontWeight: 600,
                          }}
                        >
                          element in hierarchy
                        </span>
                      )}
                    </div>
                    <div style={{ color: "var(--muted)", lineHeight: 1.6, marginBottom: 8 }}>{tapDiagnosis.root_cause_detail}</div>
                    {tapDiagnosis.suggestions?.length > 0 && (
                      <div style={{ border: "1px solid var(--border)", borderRadius: 6, overflow: "hidden", marginBottom: 8 }}>
                        <div
                          style={{
                            fontSize: 9,
                            color: "var(--muted)",
                            padding: "4px 8px",
                            textTransform: "uppercase",
                            letterSpacing: ".6px",
                            borderBottom: "1px solid var(--border)",
                            background: "var(--bg2)",
                          }}
                        >
                          Working selectors — ranked
                        </div>
                        {tapDiagnosis.suggestions.slice(0, 3).map((s, i) => (
                          <div
                            key={i}
                            style={{
                              display: "flex",
                              alignItems: "center",
                              gap: 8,
                              padding: "5px 8px",
                              borderBottom: i < 2 ? "1px solid var(--border)" : "none",
                              background: i === 0 ? "rgba(0,229,160,.04)" : "transparent",
                            }}
                          >
                            <span style={{ fontSize: 10, minWidth: 80, fontFamily: "var(--mono)", color: i === 0 ? "var(--accent)" : "var(--muted)" }}>{s.strategy}</span>
                            <span
                              style={{
                                fontSize: 10,
                                flex: 1,
                                fontFamily: "var(--mono)",
                                color: i === 0 ? "var(--accent)" : "var(--text)",
                                overflow: "hidden",
                                textOverflow: "ellipsis",
                                whiteSpace: "nowrap",
                              }}
                              title={s.value}
                            >
                              {s.value}
                            </span>
                            <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 2 }}>
                              <span
                                style={{
                                  fontSize: 9,
                                  fontWeight: 600,
                                  color: s.score >= 80 ? "var(--accent)" : s.score >= 50 ? "var(--warn)" : "var(--muted)",
                                }}
                              >
                                {s.score}%
                              </span>
                              <div style={{ width: 48, height: 3, background: "var(--bg2)", borderRadius: 2, overflow: "hidden" }}>
                                <div
                                  style={{
                                    height: "100%",
                                    borderRadius: 2,
                                    width: `${Math.min(100, s.score)}%`,
                                    background: s.score >= 80 ? "var(--accent)" : s.score >= 50 ? "var(--warn)" : "var(--muted)",
                                  }}
                                />
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                    {tapDiagnosis.recommended_wait_ms > 0 && (
                      <div style={{ fontSize: 11, color: "var(--accent2)", fontStyle: "italic" }}>
                        Suggested wait: ~{tapDiagnosis.recommended_wait_ms}ms before the tap — timing may be the issue; the AI prompt includes this.
                      </div>
                    )}
                  </div>
                )}
                {fixBusy && !fixResult && (
                  <div style={{ padding: 24, textAlign: "center" }}>
                    <div className="spinner" style={{ margin: "0 auto 12px" }} />
                    <div style={{ fontSize: 12, color: "var(--muted)" }}>AI is analyzing screenshot, page source XML, and error logs...</div>
                    <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 6 }}>This may take 10-20 seconds</div>
                  </div>
                )}
                {fixResult && (
                  <div style={{ padding: 14 }}>
                    {/* Context: screenshot + analysis side by side */}
                    <div style={{ display: "flex", gap: 12, marginBottom: 14 }}>
                      {(() => {
                        const failShot2 = stepResults[failedIdx]?.screenshot || screenshots[failedIdx];
                        return failShot2 ? (
                          <div style={{ flexShrink: 0 }}>
                            <div style={{ fontSize: 9, textTransform: "uppercase", color: "var(--danger)", marginBottom: 4, fontWeight: 700 }}>Failed Screen</div>
                            <img src={artBase + failShot2} alt="Failure" style={{ width: 100, borderRadius: 6, border: "2px solid var(--danger)", display: "block" }} />
                          </div>
                        ) : null;
                      })()}
                      <div style={{ flex: 1, minWidth: 0, padding: 12, background: "rgba(99,102,241,.08)", borderRadius: 6, fontSize: 12, lineHeight: 1.7, color: "var(--text)", whiteSpace: "pre-wrap", wordBreak: "break-word", overflowWrap: "anywhere" }}>
                        <div style={{ fontWeight: 600, marginBottom: 4, color: "#a78bfa", fontFamily: "var(--sans)" }}>Root Cause</div>
                        {fixResult.analysis}
                      </div>
                    </div>

                    {/* Changes diff */}
                    {fixResult.changes.length > 0 && (
                      <div style={{ marginBottom: 14 }}>
                        <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 8, color: "var(--text)", fontFamily: "var(--sans)" }}>Changes ({fixResult.changes.length})</div>
                        {fixResult.changes.map((c: any, ci: number) => (
                          <div key={ci} style={{ marginBottom: 8, padding: 10, border: "1px solid var(--border)", borderRadius: 6, fontSize: 11 }}>
                            <div style={{ display: "flex", gap: 6, marginBottom: 6 }}>
                              <span style={{ background: "rgba(255,59,92,.15)", color: "var(--danger)", padding: "1px 6px", borderRadius: 3, fontFamily: "var(--mono)" }}>Step {(c.step_index ?? ci) + 1}</span>
                              <span style={{ color: "var(--muted)" }}>{c.reason}</span>
                            </div>
                            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                              <div>
                                <div style={{ fontSize: 9, textTransform: "uppercase", color: "var(--danger)", marginBottom: 3, fontWeight: 700 }}>Before</div>
                                <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--muted)", background: "rgba(255,59,92,.06)", padding: 6, borderRadius: 4, wordBreak: "break-all" }}>{typeof c.was === "string" ? c.was : JSON.stringify(c.was)}</div>
                              </div>
                              <div>
                                <div style={{ fontSize: 9, textTransform: "uppercase", color: "var(--accent)", marginBottom: 3, fontWeight: 700 }}>After</div>
                                <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--accent)", background: "rgba(0,229,160,.06)", padding: 6, borderRadius: 4, wordBreak: "break-all" }}>{typeof c.now === "string" ? c.now : JSON.stringify(c.now)}</div>
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}

                    {/* Fixed steps preview */}
                    <div style={{ marginBottom: 14 }}>
                      <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 8, color: "var(--text)", fontFamily: "var(--sans)" }}>Fixed Steps ({fixResult.fixed_steps.length})</div>
                      <div style={{ maxHeight: 200, overflow: "auto", border: "1px solid var(--border)", borderRadius: 6 }}>
                        {fixResult.fixed_steps.map((s: any, i: number) => {
                          const isChanged = fixResult.changes.some((c: any) => (c.step_index ?? -1) === i);
                          return (
                            <div key={i} style={{ padding: "6px 10px", borderBottom: "1px solid var(--border)", fontSize: 11, display: "flex", gap: 8, alignItems: "center", background: isChanged ? "rgba(99,102,241,.06)" : "transparent" }}>
                              <span style={{ color: "var(--muted)", minWidth: 20, fontFamily: "var(--mono)" }}>{i + 1}</span>
                              <span style={{ color: isChanged ? "#a78bfa" : "var(--text)" }}>{s.type}</span>
                              {s.selector && <span style={{ color: "var(--accent2)", fontFamily: "var(--mono)", fontSize: 10 }}>{s.selector.using}={s.selector.value}</span>}
                              {s.text && <span style={{ color: "var(--muted)" }}>"{s.text}"</span>}
                              {isChanged && <span style={{ background: "rgba(99,102,241,.2)", color: "#a78bfa", padding: "0 4px", borderRadius: 3, fontSize: 9, fontWeight: 700 }}>FIXED</span>}
                            </div>
                          );
                        })}
                      </div>
                    </div>

                    {/* Suggest change / Refine */}
                    <div style={{ marginBottom: 14, padding: 10, border: "1px solid var(--border)", borderRadius: 6, background: "var(--card)" }}>
                      <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 6, color: "#a78bfa" }}>Suggest a change</div>
                      <div style={{ fontSize: 10, color: "var(--muted)", marginBottom: 6 }}>Refine the fix with your instruction. Context: original steps, step results, error, page source, screenshot, and the fix above.</div>
                      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                        <input
                          value={fixSuggestion}
                          onChange={e => setFixSuggestion(e.target.value)}
                          placeholder="e.g. use accessibilityId instead of xpath, add a 2s wait before tapping..."
                          className="form-input"
                          style={{ flex: 1, minWidth: 200, fontSize: 11 }}
                        />
                        <button className="btn-primary btn-sm" style={{ background: "linear-gradient(135deg, #6366f1, #8b5cf6)", fontSize: 11 }} onClick={refineFixRun} disabled={fixBusy || !fixSuggestion.trim()}>
                          {fixBusy ? "Refining..." : "Refine fix"}
                        </button>
                      </div>
                    </div>

                    {/* Related tests suggestion */}
                    {relatedTests && (relatedTests.similar.length > 0 || relatedTests.dependents.length > 0) && (
                      <div style={{ marginBottom: 14, padding: 10, background: "rgba(99,102,241,.08)", borderRadius: 6, border: "1px solid rgba(99,102,241,.3)" }}>
                        <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 6, color: "#a78bfa" }}>Related tests</div>
                        {relatedTests.dependents.length > 0 && (
                          <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 4 }}>
                            {relatedTests.dependents.length} test{relatedTests.dependents.length !== 1 ? "s" : ""} use this as prerequisite — they will get the fix automatically.
                          </div>
                        )}
                        {relatedTests.similar.length > 0 && (
                          <>
                            <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 6 }}>
                              {relatedTests.similar.length} test{relatedTests.similar.length !== 1 ? "s" : ""} share similar steps: {relatedTests.similar.map(s => s.test.name).join(", ")}
                            </div>
                            <button className="btn-primary btn-sm" style={{ background: "linear-gradient(135deg, #6366f1, #8b5cf6)", fontSize: 11 }} onClick={applyFixAllRelated} disabled={busy}>
                              🔧 Fix all related tests
                            </button>
                          </>
                        )}
                      </div>
                    )}

                    {/* Prerequisite fix notice */}
                    {failureInPrereqApply && prereq && (
                      <div style={{ marginBottom: 14, padding: 10, background: "rgba(251,191,36,.12)", border: "1px solid rgba(251,191,36,.4)", borderRadius: 6, fontSize: 11, color: "var(--text)" }}>
                        <strong>Failure in prerequisite</strong> — Step {failedIdx + 1} is in &quot;{prereq.name}&quot;. Apply will update the prerequisite test. Dependents (e.g. &quot;{testForRun?.name ?? "main test"}&quot;) will get this fix automatically on next run.
                      </div>
                    )}

                    {/* Action buttons */}
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                      <button className="btn-primary" style={{ fontSize: 11, padding: "8px 16px", background: "linear-gradient(135deg, #6366f1, #8b5cf6)" }} onClick={rerunWithFix} disabled={busy}>
                        🔄 Apply Fix & Rerun
                      </button>
                      <button className="btn-ghost btn-sm" onClick={() => applyFix("update")} disabled={busy}>
                        {failureInPrereqApply ? `Update prerequisite "${prereq?.name}"` : "Update existing test"}
                      </button>
                      <button className="btn-ghost btn-sm" onClick={() => applyFix("new")} disabled={busy}>
                        Save as new test
                      </button>
                      <button className="btn-ghost btn-sm" style={{ marginLeft: "auto" }} onClick={downloadFixReport}>
                        📋 Download Report
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>

          <div className="exec-right">
            {/* Test Steps */}
            <div className="steps-panel">
              <div className="panel-header"><div className="panel-title">Test Steps</div><div style={{ fontSize: 11, color: "var(--muted)" }}>{testForRun?.name}</div></div>
              {showPrereqHelpActions && prereq && displayRun && (
                <div style={{ padding: "10px 12px", borderBottom: "1px solid var(--border)", display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", fontSize: 11, background: "rgba(251,191,36,.08)" }}>
                  <span style={{ color: "var(--warn)", fontWeight: 600 }}>Failed in prerequisite</span>
                  <span style={{ color: "var(--muted)" }}>{prereq.name}</span>
                  {latestPrereqRun ? (
                    <button type="button" className="btn-ghost btn-sm" onClick={() => onOpenPrereqRun(latestPrereqRun.id)}>
                      Open prerequisite run #{latestPrereqRun.id}
                    </button>
                  ) : (
                    <span style={{ color: "var(--muted)", fontSize: 10 }}>No prior run for prerequisite</span>
                  )}
                  <button type="button" className="btn-primary btn-sm" style={{ background: "linear-gradient(135deg, #6366f1, #8b5cf6)" }} onClick={() => onPreparePrerequisiteRun(prereq.id, displayRun)}>
                    Run prerequisite (same build &amp; device)
                  </button>
                  <button type="button" className="btn-ghost btn-sm" onClick={() => onOpenTestInLibrary(prereq.id)}>
                    Edit prerequisite in library
                  </button>
                </div>
              )}
              <div className="steps-scroll">
                {stepDefs.map((s: any, i: number) => {
                  const res = stepResults[i];
                  const st = res?.status || "pending";
                  const isActive = i === completedSteps && displayRun.status === "running";
                  return (
                    <React.Fragment key={i}>
                      {prereq && prereqStepsLen > 0 && i === prereqStepsLen && (
                        <div
                          className="step-item"
                          style={{ cursor: "default", background: "rgba(99,102,241,.06)", justifyContent: "center", fontSize: 10, color: "var(--muted)", fontWeight: 600 }}
                        >
                          — {testForRun?.name ?? "Main test"} (steps below) —
                        </div>
                      )}
                      <div className={`step-item${isActive ? " running" : st === "failed" ? " fail" : ""}`} onClick={() => { const shot = screenshots[i] || res?.screenshot; setSelShot(shot || null); loadXmlForStep(i); }}>
                        <div className="step-num">{String(i + 1).padStart(2, "0")}</div>
                        <div>
                          <div className="step-desc">{s.type}{s.selector ? ` → ${s.selector.value}` : ""}{s.text ? ` "${s.text}"` : ""}</div>
                          <div className="step-reason">{st === "passed" ? "Completed" : st === "failed" ? (typeof res?.details === "string" ? res.details : res?.details?.error || "Failed") : isActive ? "Executing..." : displayRun.status === "cancelled" && !res ? "Skipped" : "Pending"}</div>
                        </div>
                        <div className="step-icon">{st === "passed" ? "✅" : st === "failed" ? "❌" : isActive ? "⏳" : displayRun.status === "cancelled" && !res ? <span style={{ color: "var(--muted)" }}>⊘</span> : <span style={{ color: "var(--muted)" }}>○</span>}</div>
                      </div>
                    </React.Fragment>
                  );
                })}
                {stepDefs.length === 0 && <div style={{ padding: 16, color: "var(--muted)", fontSize: 12 }}>No steps</div>}
              </div>
            </div>

            {/* Screenshots */}
            <div className="screenshot-panel">
              <div className="panel-header" style={{ padding: "0 0 10px" }}><div className="panel-title">Screenshots</div></div>
              <div className="screenshots">
                {screenshots.length > 0 ? screenshots.map((s, i) => {
                  const stepSt = stepResults[i]?.status;
                  return (
                    <div key={i} className={`shot${selShot === s ? " active" : ""}${stepSt === "failed" ? " fail" : ""}`} onClick={() => { setSelShot(s); loadXmlForStep(i); }}>
                      <img src={`${artBase}${s}?v=${i}`} alt={`S${i + 1}`} />
                      {stepSt === "passed" && <div className="shot-pass">✓</div>}
                      {stepSt === "failed" && <div className="shot-fail">✕</div>}
                    </div>
                  );
                }) : <div style={{ color: "var(--muted)", fontSize: 11 }}>No screenshots yet</div>}
              </div>
            </div>

          </div>
        </div>
      )}
    </>
  );
}

/* ── Library ───────────────────────────────────────── */
const STEP_TYPES = [
  "tap", "doubleTap", "longPress", "tapByCoordinates",
  "type", "clear", "clearAndType",
  "wait", "waitForVisible", "waitForNotVisible", "waitForEnabled", "waitForDisabled",
  "swipe", "scroll",
  "assertText", "assertTextContains", "assertVisible", "assertNotVisible",
  "assertEnabled", "assertChecked", "assertAttribute",
  "pressKey", "keyboardAction", "hideKeyboard",
  "launchApp", "closeApp", "resetApp",
  "takeScreenshot", "getPageSource",
];
const NO_SELECTOR_TYPES = new Set([
  "wait", "hideKeyboard", "takeScreenshot", "tapByCoordinates",
  "pressKey", "launchApp", "closeApp", "resetApp", "getPageSource",
]);

const SELECTOR_STRATEGIES: Record<"android" | "ios_sim", string[]> = {
  android: ["accessibilityId", "id", "xpath", "className", "-android uiautomator"],
  ios_sim: ["accessibilityId", "id", "xpath", "className", "-ios predicate string", "-ios class chain"],
};

function SortableStepRow({ s, i, steps, setSteps, stepStatuses, selectorPickStepIndex, onPickStep, figmaNames, platform }: { s: any; i: number; steps: any[]; setSteps: (s: any[]) => void; stepStatuses?: string[]; selectorPickStepIndex?: number | null; onPickStep?: (idx: number) => void; figmaNames?: string[]; platform?: "android" | "ios_sim" }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: `step-${i}` });
  const st = stepStatuses?.[i];
  const pf = platform ?? "android";
  const selectorOptions = SELECTOR_STRATEGIES[pf];
  const style = { transform: CSS.Transform.toString(transform), transition, opacity: isDragging ? 0.5 : 1 };
  const update = (fn: (n: any[]) => void) => { const n = [...steps]; fn(n); setSteps(n); };
  const hasSelector = !NO_SELECTOR_TYPES.has(s.type) && s.type !== "keyboardAction";
  const isPicking = selectorPickStepIndex === i;
  return (
    <div ref={setNodeRef} style={style} className="step-builder-row">
      <span {...attributes} {...listeners} style={{ fontSize: 12, color: "var(--muted)", cursor: "grab", minWidth: 20, userSelect: "none" }} title="Drag to reorder">⋮⋮</span>
      <span style={{ fontSize: 10, color: "var(--muted)", minWidth: 20 }}>{i + 1}</span>
      {st && <span style={{ fontSize: 9, fontWeight: 700, minWidth: 42, color: st === "passed" ? "#00e5a0" : st === "failed" ? "#ff3b5c" : "#8a8f98" }}>{st.toUpperCase()}</span>}
      <select value={s.type} onChange={e => update(n => { n[i] = { ...n[i], type: e.target.value }; })}>
        {STEP_TYPES.map(t => <option key={t}>{t}</option>)}
      </select>
      {hasSelector && (
        <>
          <select value={s.selector?.using || "accessibilityId"} onChange={e => update(n => { n[i] = { ...n[i], selector: { ...n[i].selector, using: e.target.value } }; })} style={{ width: pf === "ios_sim" ? 150 : 110 }}>
            {selectorOptions.map(u => <option key={u}>{u}</option>)}
          </select>
          <input
            value={s.selector?.value || ""}
            onChange={e => update(n => { n[i] = { ...n[i], selector: { ...n[i].selector, value: e.target.value } }; })}
            placeholder="selector value"
            style={{ flex: 1 }}
            list={figmaNames && figmaNames.length > 0 ? `figma-datalist-${i}` : undefined}
          />
          {figmaNames && figmaNames.length > 0 && (
            <datalist id={`figma-datalist-${i}`}>
              {figmaNames.map(n => <option key={n} value={n} />)}
            </datalist>
          )}
          {onPickStep && (
            <button className="btn-ghost btn-sm" style={{ fontSize: 9, padding: "2px 6px", borderColor: isPicking ? "var(--accent)" : undefined }} onClick={() => onPickStep(i)} title="Pick selector from XML tree">
              {isPicking ? "⏳ Pick…" : "📋 Pick"}
            </button>
          )}
        </>
      )}
      {(s.type === "keyboardAction" || s.type === "pressKey") && (
        <select value={s.text || "return"} onChange={e => update(n => { n[i] = { ...n[i], text: e.target.value }; })} style={{ width: 100 }}>
          {["return", "done", "go", "next", "search", "send", "back", "home", "enter", "delete", "tab"].map(k => <option key={k}>{k}</option>)}
        </select>
      )}
      {(s.type === "type" || s.type === "clearAndType") && <input value={s.text || ""} onChange={e => update(n => { n[i] = { ...n[i], text: e.target.value }; })} placeholder="text to type" style={{ flex: 1 }} />}
      {(s.type === "assertText" || s.type === "assertTextContains") && <input value={s.expect || ""} onChange={e => update(n => { n[i] = { ...n[i], expect: e.target.value }; })} placeholder="expected text" style={{ flex: 1 }} />}
      {s.type === "assertAttribute" && (
        <>
          <input value={s.meta?.attribute || ""} onChange={e => update(n => { n[i] = { ...n[i], meta: { ...n[i].meta, attribute: e.target.value } }; })} placeholder="attribute name" style={{ width: 100 }} />
          <input value={s.expect || ""} onChange={e => update(n => { n[i] = { ...n[i], expect: e.target.value }; })} placeholder="expected value" style={{ flex: 1 }} />
        </>
      )}
      {(s.type === "swipe" || s.type === "scroll") && (
        <select value={s.text || "up"} onChange={e => update(n => { n[i] = { ...n[i], text: e.target.value }; })}>
          {["up", "down", "left", "right"].map(d => <option key={d}>{d}</option>)}
        </select>
      )}
      {s.type === "tapByCoordinates" && (
        <>
          <input type="number" value={s.meta?.x || 0} onChange={e => update(n => { n[i] = { ...n[i], meta: { ...n[i].meta, x: Number(e.target.value) } }; })} placeholder="x" style={{ width: 60 }} />
          <input type="number" value={s.meta?.y || 0} onChange={e => update(n => { n[i] = { ...n[i], meta: { ...n[i].meta, y: Number(e.target.value) } }; })} placeholder="y" style={{ width: 60 }} />
        </>
      )}
      {(s.type === "launchApp" || s.type === "closeApp" || s.type === "resetApp") && <input value={s.text || ""} onChange={e => update(n => { n[i] = { ...n[i], text: e.target.value }; })} placeholder="bundle/package ID (optional)" style={{ flex: 1 }} />}
      {s.type === "longPress" && <input type="number" value={s.ms || 2000} onChange={e => update(n => { n[i] = { ...n[i], ms: Number(e.target.value) }; })} placeholder="duration ms" style={{ width: 80 }} />}
      {["wait", "waitForVisible", "waitForNotVisible", "waitForEnabled", "waitForDisabled"].includes(s.type) && <input type="number" value={s.ms || 1000} onChange={e => update(n => { n[i] = { ...n[i], ms: Number(e.target.value) }; })} placeholder="ms" style={{ width: 70 }} />}
      <button className="btn-ghost btn-sm" onClick={() => setSteps(steps.filter((_, j) => j !== i))} title="Remove step">✕</button>
    </div>
  );
}

function StepBuilder({ steps, setSteps, stepStatuses, selectorPickStepIndex, onPickStep, figmaNames, platform }: { steps: any[]; setSteps: (s: any[]) => void; stepStatuses?: string[]; selectorPickStepIndex?: number | null; onPickStep?: (idx: number) => void; figmaNames?: string[]; platform?: "android" | "ios_sim" }) {
  const ids = useMemo(() => steps.map((_, i) => `step-${i}`), [steps.length]);
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 8 } }), useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }));
  const handleDragEnd = useCallback((event: DragEndEvent) => {
    if (!event.over || event.active.id === event.over.id) return;
    const oldIdx = ids.indexOf(String(event.active.id));
    const newIdx = ids.indexOf(String(event.over.id));
    if (oldIdx >= 0 && newIdx >= 0) setSteps(arrayMove(steps, oldIdx, newIdx));
  }, [steps, ids, setSteps]);
  return (
    <div className="step-builder">
      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
        <SortableContext items={ids} strategy={verticalListSortingStrategy}>
          {steps.map((s, i) => <SortableStepRow key={ids[i]} s={s} i={i} steps={steps} setSteps={setSteps} stepStatuses={stepStatuses} selectorPickStepIndex={selectorPickStepIndex} onPickStep={onPickStep} figmaNames={figmaNames} platform={platform} />)}
        </SortableContext>
      </DndContext>
      <button className="btn-ghost btn-sm" style={{ marginTop: 6 }} onClick={() => setSteps([...steps, { type: "tap", selector: { using: "accessibilityId", value: "" } }])}>+ Add Step</button>
    </div>
  );
}

function LibraryView({
  project,
  tests,
  runs,
  modules,
  suites,
  devices,
  onRefresh,
  openTestId,
  onOpenTestConsumed,
}: {
  project: Project;
  tests: TestDef[];
  runs: Run[];
  modules: ModuleDef[];
  suites: SuiteDef[];
  devices: DeviceList;
  onRefresh: () => void;
  openTestId: number | null;
  onOpenTestConsumed: () => void;
}) {
  const [busy, setBusy] = useState(false);
  /** Upload % when set, or null = indeterminate (AI / server processing). */
  const [taskProgress, setTaskProgress] = useState<{ label: string; pct: number | null } | null>(null);
  const [platform, setPlatform] = useState<"android" | "ios_sim">("android");
  const [libTab, setLibTab] = useState<"tests" | "screens">("tests");

  // Screen Library state
  const [screens, setScreens] = useState<ScreenEntry[]>([]);
  const [screenDetail, setScreenDetail] = useState<ScreenEntryFull | null>(null);
  const [showCapture, setShowCapture] = useState(false);
  const [captureName, setCaptureName] = useState("");
  const [captureNotes, setCaptureNotes] = useState("");
  const [captureStatus, setCaptureStatus] = useState("");
  const [screenBuildFilter, setScreenBuildFilter] = useState<number | null>(null);
  const [screenPlatformFilter, setScreenPlatformFilter] = useState<string>("");
  const [editingScreenId, setEditingScreenId] = useState<number | null>(null);
  const [editScreenName, setEditScreenName] = useState("");
  const [editScreenNotes, setEditScreenNotes] = useState("");
  const [builds, setBuilds] = useState<Build[]>([]);
  const [screenFolders, setScreenFolders] = useState<ScreenFolder[]>([]);
  const [activeFolderId, setActiveFolderId] = useState<number | null>(null);
  const [newFolderName, setNewFolderName] = useState("");
  const [showNewFolder, setShowNewFolder] = useState(false);
  const [captureDeviceId, setCaptureDeviceId] = useState("");
  const [screenSessionActive, setScreenSessionActive] = useState(false);
  const lastScreenSessionRef = useRef<{ build_id: number; device_target: string; platform: "android" | "ios_sim" } | null>(null);

  const stopScreenSessionIfAny = useCallback(async () => {
    const prev = lastScreenSessionRef.current;
    if (!prev) return;
    try {
      await api.stopScreenSession({
        project_id: project.id,
        build_id: prev.build_id,
        platform: prev.platform,
        ...(prev.device_target.trim() ? { device_target: prev.device_target.trim() } : {}),
      });
    } catch {
      /* ignore */
    }
    lastScreenSessionRef.current = null;
    setScreenSessionActive(false);
  }, [project.id]);

  const loadScreenFolders = useCallback(async () => {
    try { setScreenFolders(await api.listScreenFolders(project.id)); } catch {}
  }, [project.id]);

  const loadScreens = useCallback(async () => {
    try {
      const s = await api.listScreens(project.id, { buildId: screenBuildFilter, folderId: activeFolderId, platform: screenPlatformFilter });
      setScreens(s);
    } catch {}
  }, [project.id, screenBuildFilter, activeFolderId, screenPlatformFilter]);

  const loadBuilds = useCallback(async () => {
    try { setBuilds(await api.listBuilds(project.id)); } catch {}
  }, [project.id]);

  const devicePickerPlatform = useMemo(
    () => (builds.find(b => b.id === screenBuildFilter) || builds[0])?.platform || platform,
    [builds, screenBuildFilter, platform],
  );

  useEffect(() => {
    if (devicePickerPlatform === "ios_sim") {
      const ids = devices.ios_simulators.map(d => d.udid);
      setCaptureDeviceId(prev => (prev && ids.includes(prev) ? prev : ids[0] || ""));
    } else {
      const ids = devices.android.map(d => String((d as { serial?: string }).serial || ""));
      setCaptureDeviceId(prev => (prev && ids.includes(prev) ? prev : ids[0] || ""));
    }
  }, [devices, devicePickerPlatform]);

  useEffect(() => {
    if (libTab !== "screens" || !showCapture || screenBuildFilter == null) {
      return;
    }
    const selectedBuild = builds.find(b => b.id === screenBuildFilter);
    if (!selectedBuild) return;
    let cancelled = false;
    const tick = () => {
      api
        .screenSessionStatus({
          project_id: project.id,
          build_id: screenBuildFilter,
          platform: selectedBuild.platform,
          ...(captureDeviceId.trim() ? { device_target: captureDeviceId.trim() } : {}),
        })
        .then((r) => {
          if (!cancelled) setScreenSessionActive(r.active);
        })
        .catch(() => {
          if (!cancelled) setScreenSessionActive(false);
        });
    };
    tick();
    const id = setInterval(tick, 3000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [libTab, showCapture, screenBuildFilter, captureDeviceId, project.id, builds]);

  useEffect(() => { loadScreenFolders(); }, [loadScreenFolders]);
  useEffect(() => {
    loadBuilds();
  }, [loadBuilds]);
  useEffect(() => {
    if (libTab === "screens") loadScreens();
  }, [libTab, loadScreens]);

  // Create test state
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [newSteps, setNewSteps] = useState<any[]>([]);
  const [newSuiteId, setNewSuiteId] = useState<number | null>(null);
  const [newPrerequisiteId, setNewPrerequisiteId] = useState<number | null>(null);
  const [newAcceptanceCriteria, setNewAcceptanceCriteria] = useState("");
  const [aiPrompt, setAiPrompt] = useState("");
  const [aiStatus, setAiStatus] = useState("");

  // Edit test state
  const [editId, setEditId] = useState<number | null>(null);
  const [editName, setEditName] = useState("");
  const [editPfTab, setEditPfTab] = useState<"android" | "ios_sim">("android");
  const [editStepsAndroid, setEditStepsAndroid] = useState<any[]>([]);
  const [editStepsIos, setEditStepsIos] = useState<any[]>([]);
  const editSteps = editPfTab === "android" ? editStepsAndroid : editStepsIos;
  const setEditSteps = useCallback(
    (u: any[] | ((prev: any[]) => any[])) => {
      const apply = (prev: any[]) => (typeof u === "function" ? (u as (p: any[]) => any[])(prev) : u);
      if (editPfTab === "android") setEditStepsAndroid(apply);
      else setEditStepsIos(apply);
    },
    [editPfTab],
  );
  const [editSuiteId, setEditSuiteId] = useState<number | null>(null);
  const [editPrerequisiteId, setEditPrerequisiteId] = useState<number | null>(null);
  const [editAcceptanceCriteria, setEditAcceptanceCriteria] = useState("");
  const [aiEditPrompt, setAiEditPrompt] = useState("");
  const [aiEditStatus, setAiEditStatus] = useState("");

  // Test Suite Collection / Suite creation
  const [newModName, setNewModName] = useState("");
  const [newSuiteName, setNewSuiteName] = useState("");
  const [newSuiteModId, setNewSuiteModId] = useState<number | null>(null);

  // Bulk Generate Test Suite
  const [showGenerateSuite, setShowGenerateSuite] = useState(false);
  const [genSuitePrompt, setGenSuitePrompt] = useState("");
  const [genSuiteTargetId, setGenSuiteTargetId] = useState<number | null>(null);
  const [genSuiteStatus, setGenSuiteStatus] = useState("");
  const [genSuiteFolderId, setGenSuiteFolderId] = useState<number | null>(null);
  const [genAiFolderScreens, setGenAiFolderScreens] = useState<ScreenEntry[]>([]);
  const [genAiBuildIds, setGenAiBuildIds] = useState<number[]>([]);
  const [genSuiteFolderScreens, setGenSuiteFolderScreens] = useState<ScreenEntry[]>([]);
  const [genSuiteBuildIds, setGenSuiteBuildIds] = useState<number[]>([]);

  // Import script / sheet (preview → confirm)
  const [showImport, setShowImport] = useState(false);
  const [importSuiteId, setImportSuiteId] = useState<number | null>(null);
  const [importPreview, setImportPreview] = useState<{
    test_cases: any[];
    warnings: string[];
    filename: string;
    row_count?: number;
    scripts?: { name: string; content: string }[];
  } | null>(null);
  const importFileRef = useRef<HTMLInputElement>(null);
  const [importTab, setImportTab] = useState<"single" | "bulk">("single");
  const [importGroovyIdx, setImportGroovyIdx] = useState(0);
  const [bulkImportResult, setBulkImportResult] = useState<{
    groups: Record<string, any[]>;
    total_cases: number;
    total_files: number;
    warnings: string[];
    collections?: Record<string, string[]>;
    katalon_detected?: boolean;
    files: { path: string; cases_count: number; status: string }[];
  } | null>(null);
  const [bulkModuleId, setBulkModuleId] = useState<number | null>(null);
  const [bulkImportFolderId, setBulkImportFolderId] = useState<number | null>(null);
  const [bulkImportFolderScreens, setBulkImportFolderScreens] = useState<ScreenEntry[]>([]);
  const [bulkImportBuildIds, setBulkImportBuildIds] = useState<number[]>([]);
  const [bulkFlatCases, setBulkFlatCases] = useState<any[]>([]);
  const zipImportRef = useRef<HTMLInputElement>(null);
  const folderImportRef = useRef<HTMLInputElement>(null);
  // Filters for test table: null = all suites, [] = none, [id,...] = these suites
  const [filterCollectionId, setFilterCollectionId] = useState<number | null>(null);
  const [filterSuiteIds, setFilterSuiteIds] = useState<number[] | null>(null);
  const [librarySearch, setLibrarySearch] = useState("");
  const [debouncedLibrarySearch, setDebouncedLibrarySearch] = useState("");
  const [figmaNames, setFigmaNames] = useState<string[]>([]);
  useEffect(() => {
    const t = setTimeout(() => setDebouncedLibrarySearch(librarySearch), 200);
    return () => clearTimeout(t);
  }, [librarySearch]);
  useEffect(() => {
    if (libTab !== "tests") return;
    api.listFigmaComponents().then(r => setFigmaNames(r.names || [])).catch(() => setFigmaNames([]));
  }, [libTab]);

  // Related tests when editing (for suggestion banner)
  const [editRelated, setEditRelated] = useState<{ dependents: TestDef[]; similar: { test: TestDef; shared_prefix_length: number }[] } | null>(null);

  // XML click-to-fill: create-test flow only (edit uses manual / Figma datalist)
  const [newXml, setNewXml] = useState<string | null>(null);
  const [newSelectorPickStepIndex, setNewSelectorPickStepIndex] = useState<number | null>(null);

  // Screen context for generation
  const [genFolderId, setGenFolderId] = useState<number | null>(null);

  useEffect(() => {
    if (!genFolderId) {
      setGenAiFolderScreens([]);
      setGenAiBuildIds([]);
      return;
    }
    const pf = platform === "ios_sim" ? "ios_sim" : "android";
    api
      .listScreens(project.id, { folderId: genFolderId, platform: pf })
      .then((rows) => {
        setGenAiFolderScreens(rows);
        const distinct = [
          ...new Set(rows.map((s) => s.build_id).filter((id): id is number => id != null)),
        ].sort((a, b) => a - b);
        setGenAiBuildIds(distinct);
      })
      .catch(() => {
        setGenAiFolderScreens([]);
        setGenAiBuildIds([]);
      });
  }, [genFolderId, platform, project.id]);

  useEffect(() => {
    if (!genSuiteFolderId) {
      setGenSuiteFolderScreens([]);
      setGenSuiteBuildIds([]);
      return;
    }
    const pf = platform === "ios_sim" ? "ios_sim" : "android";
    api
      .listScreens(project.id, { folderId: genSuiteFolderId, platform: pf })
      .then((rows) => {
        setGenSuiteFolderScreens(rows);
        const distinct = [
          ...new Set(rows.map((s) => s.build_id).filter((id): id is number => id != null)),
        ].sort((a, b) => a - b);
        setGenSuiteBuildIds(distinct);
      })
      .catch(() => {
        setGenSuiteFolderScreens([]);
        setGenSuiteBuildIds([]);
      });
  }, [genSuiteFolderId, platform, project.id]);

  const toggleGenAiBuild = (bid: number) => {
    setGenAiBuildIds((prev) =>
      prev.includes(bid) ? prev.filter((x) => x !== bid) : [...prev, bid].sort((a, b) => a - b),
    );
  };
  const toggleGenSuiteBuild = (bid: number) => {
    setGenSuiteBuildIds((prev) =>
      prev.includes(bid) ? prev.filter((x) => x !== bid) : [...prev, bid].sort((a, b) => a - b),
    );
  };

  useEffect(() => {
    if (!bulkImportFolderId) {
      setBulkImportFolderScreens([]);
      setBulkImportBuildIds([]);
      return;
    }
    const pf = platform === "ios_sim" ? "ios_sim" : "android";
    api
      .listScreens(project.id, { folderId: bulkImportFolderId, platform: pf })
      .then((rows) => {
        setBulkImportFolderScreens(rows);
        const distinct = [...new Set(rows.map((s) => s.build_id).filter((id): id is number => id != null))].sort((a, b) => a - b);
        setBulkImportBuildIds(distinct);
      })
      .catch(() => {
        setBulkImportFolderScreens([]);
        setBulkImportBuildIds([]);
      });
  }, [bulkImportFolderId, platform, project.id]);

  const toggleBulkImportBuild = (bid: number) => {
    setBulkImportBuildIds((prev) =>
      prev.includes(bid) ? prev.filter((x) => x !== bid) : [...prev, bid].sort((a, b) => a - b),
    );
  };

  const genAiContextScreenCount = useMemo(() => {
    const hasTagged = genAiFolderScreens.some((s) => s.build_id != null);
    if (!hasTagged) return genAiFolderScreens.length;
    if (genAiBuildIds.length === 0) return 0;
    return genAiFolderScreens.filter((s) => s.build_id != null && genAiBuildIds.includes(s.build_id)).length;
  }, [genAiFolderScreens, genAiBuildIds]);

  const genSuiteContextScreenCount = useMemo(() => {
    const hasTagged = genSuiteFolderScreens.some((s) => s.build_id != null);
    if (!hasTagged) return genSuiteFolderScreens.length;
    if (genSuiteBuildIds.length === 0) return 0;
    return genSuiteFolderScreens.filter((s) => s.build_id != null && genSuiteBuildIds.includes(s.build_id)).length;
  }, [genSuiteFolderScreens, genSuiteBuildIds]);

  const bulkImportContextScreenCount = useMemo(() => {
    const hasTagged = bulkImportFolderScreens.some((s) => s.build_id != null);
    if (!hasTagged) return bulkImportFolderScreens.length;
    if (bulkImportBuildIds.length === 0) return 0;
    return bulkImportFolderScreens.filter((s) => s.build_id != null && bulkImportBuildIds.includes(s.build_id)).length;
  }, [bulkImportFolderScreens, bulkImportBuildIds]);

  const aiGenerate = async () => {
    if (!aiPrompt.trim()) { toast("Describe the test", "error"); return; }
    if (genFolderId) {
      const hasTagged = genAiFolderScreens.some((s) => s.build_id != null);
      if (hasTagged && genAiBuildIds.length === 0) {
        toast("Select at least one build for screen context", "error");
        return;
      }
    }
    setBusy(true);
    setTaskProgress({ label: genFolderId ? "Generating with Screen Library context (XML + screenshots)…" : "Capturing page source (Appium)…", pct: null });
    setAiStatus("Generating...");
    let xml = "";
    try {
      if (!genFolderId) {
        try { const ps = await api.capturePageSource(); if (ps.ok) xml = ps.xml; } catch {}
      }
      setTaskProgress({ label: "AI is generating steps — usually 15–45s…", pct: null });
      const opts = genFolderId
        ? {
            folder_id: genFolderId,
            project_id: project.id,
            ...(genAiBuildIds.length > 0 ? { build_ids: genAiBuildIds } : {}),
          }
        : undefined;
      const res = await api.generateSteps(platform === "ios_sim" ? "ios_sim" : "android", aiPrompt, xml, opts);
      setNewSteps(res.steps);
      setNewAcceptanceCriteria(prev => prev || aiPrompt);
      const grounded = res.grounded && (res.screens_used || 0) > 0;
      setAiStatus(`Generated ${res.steps.length} steps${grounded ? ` (grounded on ${res.screens_used} screen${(res.screens_used || 0) > 1 ? "s" : ""})` : ""}`);
      toast(`AI generated ${res.steps.length} steps${grounded ? " with real selectors + screenshots" : ""}`, "success");
    } catch (e: any) {
      setAiStatus("");
      toast(e.message, "error");
    } finally {
      setBusy(false);
      setTaskProgress(null);
    }
  };

  const saveNew = async () => {
    if (!newName.trim()) { toast("Enter test name", "error"); return; }
    if (!newSteps.length) { toast("Add steps", "error"); return; }
    setBusy(true);
    const ps =
      platform === "ios_sim"
        ? { android: [] as any[], ios_sim: [...newSteps] }
        : { android: [...newSteps], ios_sim: [] as any[] };
    try {
      await api.createTest(project.id, {
        name: newName.trim(),
        steps: platform === "android" ? newSteps : [],
        platform_steps: ps,
        suite_id: newSuiteId,
        prerequisite_test_id: newPrerequisiteId,
        acceptance_criteria: newAcceptanceCriteria.trim() || null,
      });
      toast("Test saved", "success");
      setNewName("");
      setNewSteps([]);
      setNewPrerequisiteId(null);
      setNewAcceptanceCriteria("");
      setNewXml(null);
      setNewSelectorPickStepIndex(null);
      setShowCreate(false);
      onRefresh();
    }
    catch (e: any) { toast(e.message, "error"); }
    finally { setBusy(false); }
  };

  const openEdit = useCallback((t: TestDef) => {
    setEditId(t.id);
    setEditName(t.name);
    setEditStepsAndroid([...stepsForPlatform(t, "android")]);
    setEditStepsIos([...stepsForPlatform(t, "ios_sim")]);
    setEditPfTab("android");
    setEditSuiteId(t.suite_id);
    setEditPrerequisiteId(t.prerequisite_test_id ?? null);
    setEditAcceptanceCriteria(t.acceptance_criteria ?? "");
    setAiEditPrompt("");
    setAiEditStatus("");
    setEditRelated(null);
    api.getRelatedTests(t.id).then(setEditRelated).catch(() => {});
  }, []);

  useEffect(() => {
    if (openTestId == null) return;
    const t = tests.find(x => x.id === openTestId);
    if (t) {
      setLibTab("tests");
      openEdit(t);
      onOpenTestConsumed();
    } else if (tests.length > 0) {
      onOpenTestConsumed();
    }
  }, [openTestId, tests, openEdit, onOpenTestConsumed]);

  const cancelEdit = () => {
    setEditId(null);
    setEditRelated(null);
    setEditStepsAndroid([]);
    setEditStepsIos([]);
  };

  const saveEdit = async () => {
    if (!editId || !editName.trim()) return;
    setBusy(true);
    try {
      const androidOut = editPfTab === "android" ? editSteps : editStepsAndroid;
      const iosOut = editPfTab === "ios_sim" ? editSteps : editStepsIos;
      await api.updateTest(editId, {
        name: editName,
        platform_steps: { android: androidOut, ios_sim: iosOut },
        suite_id: editSuiteId,
        prerequisite_test_id: editPrerequisiteId,
        acceptance_criteria: editAcceptanceCriteria.trim() || null,
      });
      toast("Test updated", "success");
      setEditId(null);
      setEditStepsAndroid([]);
      setEditStepsIos([]);
      onRefresh();
    }
    catch (e: any) { toast(e.message, "error"); }
    finally { setBusy(false); }
  };

  const aiEditRun = async () => {
    if (!aiEditPrompt.trim()) { toast("Describe the change", "error"); return; }
    setBusy(true);
    setTaskProgress({ label: "AI is editing steps…", pct: null });
    setAiEditStatus("AI editing...");
    try {
      const res = await api.editSteps(editPfTab, editSteps, aiEditPrompt);
      setEditSteps(res.steps);
      setAiEditStatus(res.summary || `Applied — ${res.steps.length} steps`);
      toast("AI applied edits", "success");
    } catch (e: any) {
      setAiEditStatus("");
      toast(e.message, "error");
    } finally {
      setBusy(false);
      setTaskProgress(null);
    }
  };

  const createMod = async () => {
    if (!newModName.trim()) return;
    setBusy(true);
    try { await api.createModule(project.id, newModName.trim()); toast("Collection created", "success"); setNewModName(""); onRefresh(); }
    catch (e: any) { toast(e.message, "error"); }
    finally { setBusy(false); }
  };

  const createSuite = async () => {
    if (!newSuiteName.trim() || !newSuiteModId) return;
    setBusy(true);
    try { await api.createSuite(newSuiteModId, newSuiteName.trim()); toast("Suite created", "success"); setNewSuiteName(""); onRefresh(); }
    catch (e: any) { toast(e.message, "error"); }
    finally { setBusy(false); }
  };

  const aiGenerateSuite = async () => {
    if (!genSuitePrompt.trim()) { toast("Describe the feature to test", "error"); return; }
    if (!genSuiteTargetId) { toast("Select a Test Suite", "error"); return; }
    if (genSuiteFolderId) {
      const hasTagged = genSuiteFolderScreens.some((s) => s.build_id != null);
      if (hasTagged && genSuiteBuildIds.length === 0) {
        toast("Select at least one build for screen context", "error");
        return;
      }
    }
    setBusy(true);
    setTaskProgress({ label: genSuiteFolderId ? "Generating with Screen Library context (XML + screenshots)…" : "Capturing page source (Appium)…", pct: null });
    setGenSuiteStatus(genSuiteFolderId ? "Generating with screen context..." : "Capturing page source...");
    let xml = "";
    try {
      if (!genSuiteFolderId) {
        try { const ps = await api.capturePageSource(); if (ps.ok) xml = ps.xml; } catch {}
      }
      setTaskProgress({ label: "AI is generating multiple test cases — may take a minute…", pct: null });
      setGenSuiteStatus("AI generating test cases...");
      const res = await api.generateSuite(
        platform,
        genSuitePrompt,
        project.id,
        genSuiteTargetId,
        xml,
        genSuiteFolderId,
        genSuiteBuildIds.length > 0 ? genSuiteBuildIds : undefined,
      );
      setGenSuiteStatus(`Created ${res.created} test cases`);
      toast(`Generated ${res.created} test cases`, "success");
      setGenSuitePrompt("");
      onRefresh();
    } catch (e: any) {
      setGenSuiteStatus("");
      toast(e.message, "error");
    } finally {
      setBusy(false);
      setTaskProgress(null);
    }
  };

  const lastRunStatus = (testId: number) => { const r = runs.find(r => r.test_id === testId); return r ? r.status : null; };
  const suiteName = (sid: number | null) => { if (!sid) return "—"; const s = suites.find(x => x.id === sid); return s ? s.name : "—"; };
  const moduleName = (sid: number | null) => { if (!sid) return ""; const s = suites.find(x => x.id === sid); if (!s) return ""; const m = modules.find(x => x.id === s.module_id); return m ? m.name : ""; };

  const editTest = editId ? tests.find(t => t.id === editId) : null;
  const editStepStatuses = (() => {
    if (!editId || !editTest) return undefined;
    const lastRunForTest = runs.find(r => r.test_id === editId);
    const lastRunAsPrereq = runs.find(r => { const t = tests.find(x => x.id === r.test_id); return t?.prerequisite_test_id === editId; });
    const lastRun = lastRunForTest || lastRunAsPrereq;
    if (!lastRun?.summary) return undefined;
    const stepResults = (lastRun.summary as any)?.stepResults as any[] | undefined;
    if (!Array.isArray(stepResults)) return undefined;
    const rp = (lastRun.platform || "android") as Run["platform"];
    if (lastRunForTest) {
      const prereq = editTest.prerequisite_test_id ? tests.find(t => t.id === editTest!.prerequisite_test_id) : null;
      const prereqLen = prereq ? stepsForPlatform(prereq, rp).length : 0;
      return stepResults.slice(prereqLen).map((s: any) => s?.status).filter(Boolean);
    }
    return stepResults.slice(0, stepsForPlatform(editTest, rp).length).map((s: any) => s?.status).filter(Boolean);
  })();

  const undoLastFix = async () => {
    if (!editId) return;
    setBusy(true);
    try {
      const res = await api.undoLastFix(editId);
      const tp = (res.target_platform === "ios_sim" ? "ios_sim" : "android") as "android" | "ios_sim";
      if (tp === "ios_sim") setEditStepsIos(res.steps);
      else setEditStepsAndroid(res.steps);
      setEditPfTab(tp);
      toast("Reverted to steps before last AI fix", "success");
      onRefresh();
    } catch (e: any) { toast(e.message, "error"); }
    finally { setBusy(false); }
  };

  const suitesInCollection = filterCollectionId ? suites.filter(s => s.module_id === filterCollectionId) : [];
  const filteredTests = tests.filter(t => {
    if (!filterCollectionId) return true;
    const tSuite = suites.find(s => s.id === t.suite_id);
    if (!tSuite || tSuite.module_id !== filterCollectionId) return false;
    if (filterSuiteIds === null) return true;
    if (filterSuiteIds.length === 0) return false;
    return (t.suite_id != null) && filterSuiteIds.includes(t.suite_id);
  }).filter(t => {
    const q = debouncedLibrarySearch.trim().toLowerCase();
    if (!q) return true;
    if (t.name.toLowerCase().includes(q)) return true;
    if ((t.acceptance_criteria || "").toLowerCase().includes(q)) return true;
    const stepBlob = JSON.stringify([...stepsForPlatform(t, "android"), ...stepsForPlatform(t, "ios_sim")]).toLowerCase();
    return stepBlob.includes(q);
  });

  return (
    <>
      <div className="section-head">
        <div><div className="section-title">Test Library</div><div className="section-sub">{tests.length} test cases · {modules.length} collections · {suites.length} suites · {screens.length} screens</div></div>
        <div style={{ display: "flex", gap: 8 }}>
          {libTab === "tests" && <>
            <button className="btn-ghost btn-sm" onClick={() => {
              const opening = !showImport;
              setShowImport(opening);
              if (opening) setImportTab("single");
              setImportPreview(null);
              setBulkImportResult(null);
              setBulkFlatCases([]);
              setImportGroovyIdx(0);
            }}>{showImport ? "Close" : "⬆ Import"}</button>
            <button className="btn-ghost btn-sm" onClick={() => setShowGenerateSuite(!showGenerateSuite)}>{showGenerateSuite ? "Close" : "✨ Generate Suite"}</button>
            <button className="btn-ghost btn-sm" onClick={() => setShowCreate(!showCreate)}>{showCreate ? "Close" : "+ New Test"}</button>
          </>}
          {libTab === "screens" && <button className="btn-ghost btn-sm" onClick={() => setShowCapture(!showCapture)}>{showCapture ? "Close" : "📸 Capture Screen"}</button>}
        </div>
      </div>

      {/* Tab switcher */}
      <div className="report-tabs" style={{ marginBottom: 12 }}>
        <button className={`report-tab ${libTab === "tests" ? "active" : ""}`} onClick={() => setLibTab("tests")}>Tests</button>
        <button className={`report-tab ${libTab === "screens" ? "active" : ""}`} onClick={() => setLibTab("screens")}>Screens</button>
      </div>

      {taskProgress && (
        <div className="library-task-progress library-task-progress--sticky" aria-live="polite">
          <div className="library-task-progress-label">{taskProgress.label}</div>
          <div className="progress-bar-track">
            {taskProgress.pct == null ? (
              <div className="progress-bar-indeterminate-wrap">
                <div className="indeterminate-fill" />
              </div>
            ) : (
              <div className="progress-fill" style={{ width: `${taskProgress.pct}%` }} />
            )}
          </div>
        </div>
      )}

      {libTab === "tests" && <>
      {/* Import Katalon / Gherkin / Python / sheet / ZIP / folder */}
      {showImport && (
        <div className="panel" style={{ padding: 18, marginBottom: 16, border: "1px solid rgba(0,229,160,.25)" }}>
          <div style={{ fontFamily: "var(--sans)", fontWeight: 600, marginBottom: 8 }}>⬆ Import tests</div>
          <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 12 }}>
            Single file (including .csv / .xlsx): rows become steps from the Steps + Expected columns, each case gets a Katalon Groovy preview. Folder/ZIP for bulk scripts.
          </div>

          <div style={{ display: "flex", gap: 6, marginBottom: 14, flexWrap: "wrap" }}>
            <button type="button" className={importTab === "single" ? "btn-primary btn-sm" : "btn-ghost btn-sm"} onClick={() => setImportTab("single")}>Single file</button>
            <button type="button" className={importTab === "bulk" ? "btn-primary btn-sm" : "btn-ghost btn-sm"} onClick={() => setImportTab("bulk")}>Folder / ZIP</button>
          </div>

          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 12, alignItems: "center" }}>
            <select value={platform} onChange={e => setPlatform(e.target.value as any)}><option value="android">Android</option><option value="ios_sim">iOS</option></select>
            {importTab === "single" && (
              <select value={importSuiteId ?? ""} onChange={e => setImportSuiteId(e.target.value ? Number(e.target.value) : null)} style={{ minWidth: 220 }}>
                <option value="">Select target suite (required)</option>
                {modules.map(m => suites.filter(s => s.module_id === m.id).map(s => <option key={s.id} value={s.id}>{m.name} / {s.name}</option>))}
              </select>
            )}
            {importTab === "bulk" && (
              <>
                <select value={bulkModuleId ?? ""} onChange={e => setBulkModuleId(e.target.value ? Number(e.target.value) : null)} style={{ minWidth: 220 }}>
                  <option value="">Collection for new suites (optional)</option>
                  {modules.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
                </select>
                <select value={bulkImportFolderId ?? ""} onChange={e => setBulkImportFolderId(e.target.value ? Number(e.target.value) : null)} style={{ minWidth: 200 }}>
                  <option value="">Screen Library folder (optional)</option>
                  {screenFolders.map(f => <option key={f.id} value={f.id}>{f.name} ({f.screen_count})</option>)}
                </select>
              </>
            )}
          </div>
          {importTab === "bulk" && bulkImportFolderId && bulkImportFolderScreens.some(s => s.build_id != null) && (
            <div style={{ marginBottom: 10, marginTop: -4 }}>
              <div style={{ fontSize: 10, color: "var(--muted)", marginBottom: 4 }}>Builds in folder (context)</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                {[...new Set(bulkImportFolderScreens.map(s => s.build_id).filter((id): id is number => id != null))].sort((a, b) => a - b).map((bid) => {
                  const b = builds.find(x => x.id === bid);
                  return (
                    <label key={bid} style={{ fontSize: 11, display: "flex", alignItems: "center", gap: 4, cursor: "pointer" }}>
                      <input type="checkbox" checked={bulkImportBuildIds.includes(bid)} onChange={() => toggleBulkImportBuild(bid)} />
                      {b?.file_name ?? `Build #${bid}`}
                    </label>
                  );
                })}
              </div>
              <div style={{ fontSize: 10, color: "var(--accent2)", marginTop: 4 }}>{bulkImportContextScreenCount} screen(s) will ground AI selectors</div>
            </div>
          )}
          {importTab === "bulk" && bulkImportFolderId && !bulkImportFolderScreens.some(s => s.build_id != null) && bulkImportFolderScreens.length > 0 && (
            <div style={{ fontSize: 10, color: "var(--muted)", marginBottom: 8, marginTop: -4 }}>
              {bulkImportFolderScreens.length} screen(s) in folder (no build tag) — all will be sent to AI.
            </div>
          )}
          {importTab === "bulk" && bulkImportFolderId && (
            <div style={{ fontSize: 10, color: "var(--accent)", marginBottom: 8, marginTop: bulkImportFolderScreens.length ? 0 : -4 }}>
              Screen Library XML will ground AI selectors when no Object Repository (.rs) files are found in the upload.
            </div>
          )}

          {importTab === "single" && (
            <>
              <input ref={importFileRef} type="file" accept=".groovy,.java,.feature,.py,.csv,.xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,text/csv" style={{ display: "none" }} onChange={async (e) => {
                const f = e.target.files?.[0];
                e.target.value = "";
                if (!f) return;
                if (!importSuiteId) { toast("Select a target suite first", "error"); return; }
                const n = f.name.toLowerCase();
                setBusy(true);
                setImportPreview(null);
                setTaskProgress({ label: `Uploading ${f.name}…`, pct: 0 });
                const onUp = (p: number | null) => {
                  setTaskProgress({
                    label: p == null ? `Uploading ${f.name}…` : p >= 100 ? `Processing ${f.name} on server…` : `Uploading ${f.name} — ${p}%`,
                    pct: p != null && p < 100 ? p : null,
                  });
                };
                try {
                  if (n.endsWith(".csv") || n.endsWith(".xlsx")) {
                    const r = await api.importSheet(project.id, importSuiteId, platform, f, { onUploadProgress: onUp });
                    setImportGroovyIdx(0);
                    setImportPreview({
                      test_cases: (r.test_cases || []).map((tc: any) => ({ ...tc, import: tc.import !== false })),
                      warnings: r.warnings || [],
                      filename: r.filename,
                      row_count: r.row_count,
                      scripts: r.scripts,
                    });
                    toast(`Parsed ${r.row_count} row(s) · ${(r.scripts?.length ?? 0)} Groovy script(s)`, "success");
                  } else {
                    const r = await api.importScript(project.id, importSuiteId, platform, f, { onUploadProgress: onUp });
                    setImportPreview({ test_cases: (r.test_cases || []).map((tc: any) => ({ ...tc, import: tc.import !== false })), warnings: r.warnings || [], filename: r.filename });
                    const km = r.katalon_import_mode;
                    toast(
                      km === "ai"
                        ? `Preview: ${r.test_cases?.length ?? 0} case(s) — Katalon script parsed with AI (verify selectors on device).`
                        : km === "heuristic"
                          ? `Preview: ${r.test_cases?.length ?? 0} case(s) — heuristic Katalon parse (set AI API key in Settings for Gemini parsing).`
                          : `Preview: ${r.test_cases?.length ?? 0} test case(s)`,
                      "success",
                    );
                  }
                } catch (err: any) { toast(err.message || String(err), "error"); }
                finally { setBusy(false); setTaskProgress(null); }
              }} />
              <button className="btn-primary btn-sm" disabled={busy || !importSuiteId} onClick={() => importFileRef.current?.click()}>{busy ? "…" : "Choose file"}</button>
              {importPreview && (
                <div style={{ borderTop: "1px solid var(--border)", paddingTop: 14 }}>
                  <div style={{ fontSize: 12, marginBottom: 8, color: "var(--text)" }}><strong>{importPreview.filename}</strong>{importPreview.row_count != null ? ` · ${importPreview.row_count} rows` : ""}</div>
                  {importPreview.warnings.length > 0 && (
                    <div style={{ fontSize: 11, color: "var(--warn)", marginBottom: 10 }}>
                      {importPreview.warnings.map((w, i) => <div key={i}>{w}</div>)}
                    </div>
                  )}
                  <div style={{ maxHeight: 280, overflowY: "auto", marginBottom: 12, border: "1px solid var(--border)", borderRadius: 8 }}>
                    {importPreview.test_cases.map((tc, idx) => (
                      <div key={idx} style={{ padding: 10, borderBottom: "1px solid var(--border)", display: "grid", gridTemplateColumns: "28px 1fr auto", gap: 8, alignItems: "start" }}>
                        <input type="checkbox" checked={!!tc.import} onChange={() => setImportPreview(p => !p ? null : { ...p, test_cases: p.test_cases.map((t, j) => j === idx ? { ...t, import: !t.import } : t) })} />
                        <div>
                          <div style={{ fontWeight: 600, fontSize: 12 }}>{tc.name || `Case ${idx + 1}`}</div>
                          <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 4 }}>{Array.isArray(tc.steps) ? `${tc.steps.length} steps` : "—"} · {tc.acceptance_criteria ? String(tc.acceptance_criteria).slice(0, 120) : ""}</div>
                        </div>
                      </div>
                    ))}
                  </div>
                  {(importPreview.scripts?.length ?? 0) > 0 && (
                    <div style={{ marginBottom: 12 }}>
                      <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 6, color: "var(--muted)" }}>Katalon Groovy (from sheet steps)</div>
                      <select value={Math.min(importGroovyIdx, (importPreview.scripts?.length ?? 1) - 1)} onChange={e => setImportGroovyIdx(Number(e.target.value))} style={{ marginBottom: 8, fontSize: 11, minWidth: 240 }}>
                        {importPreview.scripts!.map((s, i) => <option key={i} value={i}>{s.name}</option>)}
                      </select>
                      <pre style={{ maxHeight: 220, overflow: "auto", fontSize: 10, padding: 10, borderRadius: 8, border: "1px solid var(--border)", margin: 0, whiteSpace: "pre-wrap" }}>{importPreview.scripts![Math.min(importGroovyIdx, importPreview.scripts!.length - 1)]?.content || ""}</pre>
                      <button type="button" className="btn-ghost btn-sm" style={{ marginTop: 8 }} disabled={busy} onClick={() => {
                        const i = Math.min(importGroovyIdx, (importPreview.scripts?.length ?? 1) - 1);
                        const one = importPreview.scripts![i];
                        if (!one) return;
                        const blob = new Blob([one.content], { type: "text/plain" });
                        const a = document.createElement("a");
                        a.href = URL.createObjectURL(blob);
                        a.download = one.name;
                        a.click();
                      }}>⬇ Selected .groovy</button>
                    </div>
                  )}
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                    <button className="run-now-btn" style={{ padding: "8px 16px", fontSize: 11 }} disabled={busy || !importSuiteId || !importPreview.test_cases.some((t: any) => t.import)} onClick={async () => {
                      setBusy(true);
                      setTaskProgress({ label: "Saving imported tests to the library…", pct: null });
                      try {
                        const res = await api.confirmScriptImport(project.id, { suite_id: importSuiteId, test_cases: importPreview.test_cases });
                        toast(`Imported ${res.created} test(s)`, "success");
                        setImportPreview(null);
                        setImportGroovyIdx(0);
                        setShowImport(false);
                        onRefresh();
                      } catch (e: any) { toast(e.message, "error"); }
                      finally { setBusy(false); setTaskProgress(null); }
                    }}>✓ Import selected</button>
                    <button type="button" className="btn-ghost btn-sm" disabled={busy || !importPreview.test_cases.some((t: any) => t.import)} onClick={async () => {
                      setBusy(true);
                      setTaskProgress({ label: "Building Katalon ZIP…", pct: null });
                      try {
                        await api.downloadKatalonZip(project.id, {
                          test_cases: importPreview.test_cases.filter((t: any) => t.import),
                          project_name: project.name,
                        });
                        toast("Katalon ZIP downloaded", "success");
                      } catch (e: any) { toast(e.message || String(e), "error"); }
                      finally { setBusy(false); setTaskProgress(null); }
                    }}>⬇ Katalon ZIP</button>
                    <button className="btn-ghost btn-sm" onClick={() => { setImportPreview(null); setImportGroovyIdx(0); }}>Clear preview</button>
                  </div>
                </div>
              )}
            </>
          )}

          {importTab === "bulk" && (
            <>
              <input ref={zipImportRef} type="file" accept=".zip,application/zip" style={{ display: "none" }} onChange={async (e) => {
                const f = e.target.files?.[0];
                e.target.value = "";
                if (!f) return;
                setBusy(true);
                setTaskProgress({ label: `Uploading ${f.name}…`, pct: 0 });
                const onUp = (p: number | null) => {
                  setTaskProgress({
                    label: p == null ? `Uploading ${f.name}…` : p >= 100 ? `Importing ${f.name} on server…` : `Uploading ZIP — ${p}%`,
                    pct: p != null && p < 100 ? p : null,
                  });
                };
                try {
                  const res = await api.importZip(project.id, platform, f, { onUploadProgress: onUp, folderId: bulkImportFolderId, buildIds: bulkImportBuildIds.length ? bulkImportBuildIds : undefined });
                  setBulkImportResult(res);
                  setBulkFlatCases(Object.values(res.groups).flat().map((tc: any) => ({ ...tc, import: tc.import !== false })));
                  toast(`${res.total_files} files · ${res.total_cases} cases parsed`, "success");
                } catch (err: any) { toast(err.message || String(err), "error"); }
                finally { setBusy(false); setTaskProgress(null); }
              }} />
              <input
                ref={folderImportRef}
                type="file"
                style={{ display: "none" }}
                multiple
                {...({ webkitdirectory: "", directory: "" } as React.InputHTMLAttributes<HTMLInputElement>)}
                onChange={async (e) => {
                  const picked = Array.from(e.target.files || []);
                  e.target.value = "";
                  if (!picked.length) return;
                  setBusy(true);
                  const nFiles = picked.length;
                  setTaskProgress({ label: `Uploading folder (${nFiles} files)…`, pct: 0 });
                  const onUp = (p: number | null) => {
                    setTaskProgress({
                      label: p == null ? `Uploading folder (${nFiles} files)…` : p >= 100 ? `Processing folder on server…` : `Uploading folder — ${p}%`,
                      pct: p != null && p < 100 ? p : null,
                    });
                  };
                  try {
                    const res = await api.importFolder(project.id, platform, picked, { onUploadProgress: onUp, folderId: bulkImportFolderId, buildIds: bulkImportBuildIds.length ? bulkImportBuildIds : undefined });
                    setBulkImportResult(res);
                    setBulkFlatCases(Object.values(res.groups).flat().map((tc: any) => ({ ...tc, import: tc.import !== false })));
                    toast(`${res.total_files} files · ${res.total_cases} cases parsed`, "success");
                  } catch (err: any) { toast(err.message || String(err), "error"); }
                  finally { setBusy(false); setTaskProgress(null); }
                }} />
              {!bulkImportResult ? (
                <div
                  style={{ border: "1.5px dashed var(--border2)", borderRadius: 10, padding: 24, textAlign: "center", cursor: "pointer" }}
                  onDragOver={e => { e.preventDefault(); }}
                  onDrop={(e) => {
                    e.preventDefault();
                    const file = e.dataTransfer.files[0];
                    if (!file?.name.toLowerCase().endsWith(".zip")) {
                      toast("Drop a .zip here, or use Browse folder", "error");
                      return;
                    }
                    setBusy(true);
                    setTaskProgress({ label: `Uploading ${file.name}…`, pct: 0 });
                    (async () => {
                      const onUp = (p: number | null) => {
                        setTaskProgress({
                          label: p == null ? `Uploading ${file.name}…` : p >= 100 ? `Importing on server…` : `Uploading ZIP — ${p}%`,
                          pct: p != null && p < 100 ? p : null,
                        });
                      };
                      try {
                        const res = await api.importZip(project.id, platform, file, { onUploadProgress: onUp, folderId: bulkImportFolderId, buildIds: bulkImportBuildIds.length ? bulkImportBuildIds : undefined });
                        setBulkImportResult(res);
                        setBulkFlatCases(Object.values(res.groups).flat().map((tc: any) => ({ ...tc, import: tc.import !== false })));
                        toast(`${res.total_files} files · ${res.total_cases} cases parsed`, "success");
                      } catch (err: any) { toast(err.message || String(err), "error"); }
                      finally { setBusy(false); setTaskProgress(null); }
                    })();
                  }}
                >
                  <div style={{ fontSize: 12, marginBottom: 12, color: "var(--muted)" }}>
                    Bulk import — drop a <strong>.zip</strong>, or select an entire folder at once (browser folder picker).
                  </div>
                  <div style={{ display: "flex", gap: 8, justifyContent: "center", flexWrap: "wrap" }}>
                    <button type="button" className="btn-ghost btn-sm" disabled={busy} onClick={() => folderImportRef.current?.click()}>Browse folder</button>
                    <button type="button" className="btn-ghost btn-sm" disabled={busy} onClick={() => zipImportRef.current?.click()}>Upload .zip</button>
                  </div>
                </div>
              ) : (
                <div style={{ borderTop: "1px solid var(--border)", paddingTop: 14 }}>
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
                    {bulkImportResult.katalon_detected && (
                      <div style={{ fontSize: 10, color: "#8b5cf6", padding: "4px 8px", borderRadius: 6, background: "rgba(139,92,246,.1)", display: "inline-block" }}>
                        Katalon project detected — suites &amp; collections mapped
                      </div>
                    )}
                    {(bulkImportResult as any).grounding && (bulkImportResult as any).grounding !== "none" && (
                      <div style={{ fontSize: 10, color: "var(--accent)", padding: "4px 8px", borderRadius: 6, background: "rgba(0,229,160,.08)", display: "inline-block" }}>
                        {(bulkImportResult as any).grounding === "object_repo"
                          ? `Grounded with Object Repository (${(bulkImportResult as any).object_repo_count} locators)`
                          : "Grounded with Screen Library XML"}
                      </div>
                    )}
                  </div>
                  {bulkImportResult.warnings.length > 0 && (
                    <div style={{ fontSize: 11, color: "var(--warn)", marginBottom: 10, maxHeight: 120, overflowY: "auto" }}>
                      {bulkImportResult.warnings.slice(0, 80).map((w, i) => <div key={i}>{w}</div>)}
                      {bulkImportResult.warnings.length > 80 && <div>… and {bulkImportResult.warnings.length - 80} more</div>}
                    </div>
                  )}
                  {(bulkImportResult.files?.length ?? 0) > 0 && (
                    <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 10, maxHeight: 120, overflowY: "auto", border: "1px solid var(--border)", borderRadius: 8, padding: 8 }}>
                      {bulkImportResult.files!.map((ff, i) => (
                        <div key={i} style={{ display: "flex", justifyContent: "space-between", gap: 8, fontSize: 10 }}>
                          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={ff.path}>{ff.path}</span>
                          <span style={{ flexShrink: 0 }}>{ff.cases_count} · {ff.status}</span>
                        </div>
                      ))}
                    </div>
                  )}
                  <div style={{ maxHeight: 280, overflowY: "auto", marginBottom: 12, border: "1px solid var(--border)", borderRadius: 8 }}>
                    {(() => {
                      const bySuite = bulkFlatCases.reduce<Record<string, { tc: any; idx: number }[]>>((acc, tc, idx) => {
                        const k = String(tc.suggested_suite || "Imported");
                        if (!acc[k]) acc[k] = [];
                        acc[k].push({ tc, idx });
                        return acc;
                      }, {});
                      const collections = bulkImportResult?.collections || {};
                      const hasCollections = Object.keys(collections).length > 0;
                      const claimedSuites = new Set(Object.values(collections).flat());

                      const renderSuite = (suiteName: string, rows: { tc: any; idx: number }[]) => (
                        <div key={suiteName}>
                          <div style={{ padding: "6px 10px", background: "rgba(99,102,241,.12)", fontWeight: 600, fontSize: 11 }}>{suiteName}</div>
                          {rows.map(({ tc, idx }) => (
                            <div key={idx} style={{ padding: 10, borderBottom: "1px solid var(--border)", display: "grid", gridTemplateColumns: "28px 1fr", gap: 8, alignItems: "start", opacity: tc.import === false ? 0.5 : 1 }}>
                              <input type="checkbox" checked={!!tc.import} onChange={() => setBulkFlatCases(p => p.map((t, j) => j === idx ? { ...t, import: !t.import } : t))} />
                              <div>
                                <div style={{ fontWeight: 600, fontSize: 12 }}>{tc.name || `Case ${idx + 1}`}</div>
                                <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 4 }}>
                                  {Array.isArray(tc.steps) ? `${tc.steps.length} steps` : "—"}{tc.acceptance_criteria ? ` · ${String(tc.acceptance_criteria).slice(0, 60)}` : ""}{tc.source_file ? ` · ${String(tc.source_file)}` : ""}
                                </div>
                              </div>
                            </div>
                          ))}
                        </div>
                      );

                      if (!hasCollections) {
                        return Object.entries(bySuite).map(([sn, rows]) => renderSuite(sn, rows));
                      }

                      return (
                        <>
                          {Object.entries(collections).map(([collName, collSuites]) => (
                            <div key={collName}>
                              <div style={{ padding: "8px 10px", background: "rgba(139,92,246,.18)", fontWeight: 700, fontSize: 11, borderBottom: "1px solid var(--border)" }}>
                                📁 {collName}
                              </div>
                              {collSuites.map(sn => bySuite[sn] ? renderSuite(sn, bySuite[sn]) : null)}
                            </div>
                          ))}
                          {Object.entries(bySuite).filter(([sn]) => !claimedSuites.has(sn)).map(([sn, rows]) => renderSuite(sn, rows))}
                        </>
                      );
                    })()}
                  </div>
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                    <button type="button" className="run-now-btn" style={{ padding: "8px 16px", fontSize: 11 }} disabled={busy || !bulkFlatCases.some(t => t.import)} onClick={async () => {
                      setBusy(true);
                      setTaskProgress({ label: "Saving bulk import to the library…", pct: null });
                      try {
                        const r = await api.confirmZipImport(project.id, {
                          module_id: bulkModuleId ?? undefined,
                          test_cases: bulkFlatCases.filter(t => t.import),
                          platform,
                          collections: bulkImportResult?.collections,
                        });
                        const modulesMsg = (r.created_modules?.length ?? 0) > 0 ? ` · ${r.created_modules!.length} collection(s)` : "";
                        toast(`Imported ${r.created} test(s) · ${(r.created_suites ?? []).length} suite(s)${modulesMsg}`, "success");
                        setBulkImportResult(null);
                        setBulkFlatCases([]);
                        setShowImport(false);
                        onRefresh();
                      } catch (e: any) { toast(e.message || String(e), "error"); }
                      finally { setBusy(false); setTaskProgress(null); }
                    }}>✓ Import selected</button>
                    <button type="button" className="btn-ghost btn-sm" disabled={busy || !bulkFlatCases.some(t => t.import)} onClick={async () => {
                      setBusy(true);
                      setTaskProgress({ label: "Building Katalon ZIP…", pct: null });
                      try {
                        await api.downloadKatalonZip(project.id, {
                          test_cases: bulkFlatCases.filter(t => t.import),
                          project_name: project.name,
                        });
                        toast("Katalon ZIP downloaded", "success");
                      } catch (e: any) { toast(e.message || String(e), "error"); }
                      finally { setBusy(false); setTaskProgress(null); }
                    }}>⬇ Katalon ZIP</button>
                    <button type="button" className="btn-ghost btn-sm" onClick={() => { setBulkImportResult(null); setBulkFlatCases([]); }}>Clear</button>
                  </div>
                </div>
              )}
            </>
          )}

        </div>
      )}

      {/* Generate Test Suite (bulk) */}
      {showGenerateSuite && (
        <div className="panel" style={{ padding: 18, marginBottom: 16, border: "1px solid rgba(99,102,241,.3)" }}>
          <div style={{ fontFamily: "var(--sans)", fontWeight: 600, marginBottom: 14 }}>✨ Generate Test Suite</div>
          <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 12 }}>Same inputs as single test: platform, prompt, optional page source. AI generates multiple test cases for the selected suite. A progress bar appears under the Test Library header while this runs.</div>
          <div style={{ display: "flex", gap: 10, marginBottom: 12, flexWrap: "wrap" }}>
            <select value={platform} onChange={e => setPlatform(e.target.value as any)}><option value="android">Android</option><option value="ios_sim">iOS</option></select>
            <select value={genSuiteTargetId ?? ""} onChange={e => setGenSuiteTargetId(e.target.value ? Number(e.target.value) : null)} style={{ fontSize: 11, minWidth: 200 }}>
              <option value="">Select Test Suite</option>
              {modules.map(m => suites.filter(s => s.module_id === m.id).map(s => <option key={s.id} value={s.id}>{m.name} / {s.name}</option>))}
            </select>
            <select value={genSuiteFolderId ?? ""} onChange={e => setGenSuiteFolderId(e.target.value ? Number(e.target.value) : null)} style={{ fontSize: 11, minWidth: 180 }}>
              <option value="">No screen folder</option>
              {screenFolders.map(f => <option key={f.id} value={f.id}>{f.name} ({f.screen_count} screens)</option>)}
            </select>
          </div>
          {genSuiteFolderId && genSuiteFolderScreens.some(s => s.build_id != null) && (
            <div style={{ marginBottom: 10 }}>
              <div style={{ fontSize: 10, color: "var(--muted)", marginBottom: 4 }}>Builds in folder (context)</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                {[...new Set(genSuiteFolderScreens.map(s => s.build_id).filter((id): id is number => id != null))].sort((a, b) => a - b).map((bid) => {
                  const b = builds.find(x => x.id === bid);
                  return (
                    <label key={bid} style={{ fontSize: 11, display: "flex", alignItems: "center", gap: 4, cursor: "pointer" }}>
                      <input type="checkbox" checked={genSuiteBuildIds.includes(bid)} onChange={() => toggleGenSuiteBuild(bid)} />
                      {b?.file_name ?? `Build #${bid}`}
                    </label>
                  );
                })}
              </div>
              <div style={{ fontSize: 10, color: "var(--accent2)", marginTop: 4 }}>{genSuiteContextScreenCount} screen(s) will be sent to AI</div>
            </div>
          )}
          {genSuiteFolderId && !genSuiteFolderScreens.some(s => s.build_id != null) && genSuiteFolderScreens.length > 0 && (
            <div style={{ fontSize: 10, color: "var(--muted)", marginBottom: 8 }}>
              {genSuiteFolderScreens.length} screen(s) in folder (no build id) — all will be sent to AI.
            </div>
          )}
          {genSuiteFolderId && <div style={{ fontSize: 10, color: "var(--accent)", marginBottom: 8 }}>AI will use real selectors + screenshots from the selected folder and builds above.</div>}
          <textarea value={genSuitePrompt} onChange={e => setGenSuitePrompt(e.target.value)} placeholder="Describe the feature: e.g. Login flow — happy path, invalid email, wrong password, empty fields..." rows={3} className="form-input" style={{ width: "100%", marginBottom: 10 }} />
          {genSuiteStatus && <div style={{ fontSize: 11, color: "var(--accent2)", marginBottom: 8 }}>{genSuiteStatus}</div>}
          <button className="btn-primary btn-sm" style={{ background: "linear-gradient(135deg, #6366f1, #8b5cf6)" }} onClick={aiGenerateSuite} disabled={busy || !genSuitePrompt.trim() || !genSuiteTargetId}>{busy ? "Generating..." : "✨ Generate Test Suite"}</button>
        </div>
      )}

      {/* Test Suite Collection / Suite management */}
      <div className="panel" style={{ padding: 12, marginBottom: 12, display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
        <input className="form-input" value={newModName} onChange={e => setNewModName(e.target.value)} placeholder="New collection name" style={{ width: 160 }} />
        <button className="btn-ghost btn-sm" onClick={createMod} disabled={busy || !newModName.trim()}>+ Collection</button>
        <div style={{ borderLeft: "1px solid var(--border)", height: 24 }} />
        <select value={newSuiteModId ?? ""} onChange={e => setNewSuiteModId(e.target.value ? Number(e.target.value) : null)} style={{ fontSize: 11 }}>
          <option value="">Select collection</option>
          {modules.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
        </select>
        <input className="form-input" value={newSuiteName} onChange={e => setNewSuiteName(e.target.value)} placeholder="New suite name" style={{ width: 160 }} />
        <button className="btn-ghost btn-sm" onClick={createSuite} disabled={busy || !newSuiteName.trim() || !newSuiteModId}>+ Suite</button>
      </div>

      {/* Create test */}
      {showCreate && (
        <div className="panel" style={{ padding: 18, marginBottom: 16 }}>
          <div style={{ fontFamily: "var(--sans)", fontWeight: 600, marginBottom: 14 }}>Create Test</div>
          <div style={{ display: "flex", gap: 10, marginBottom: 12, flexWrap: "wrap" }}>
            <select value={platform} onChange={e => setPlatform(e.target.value as any)}><option value="android">Android</option><option value="ios_sim">iOS</option></select>
            <select value={newSuiteId ?? ""} onChange={e => setNewSuiteId(e.target.value ? Number(e.target.value) : null)} style={{ fontSize: 11 }}>
              <option value="">No suite (unassigned)</option>
              {modules.map(m => suites.filter(s => s.module_id === m.id).map(s => <option key={s.id} value={s.id}>{m.name} / {s.name}</option>))}
            </select>
            <select value={newPrerequisiteId ?? ""} onChange={e => setNewPrerequisiteId(e.target.value ? Number(e.target.value) : null)} style={{ fontSize: 11 }} title="Prepend steps from another test (e.g. Login)">
              <option value="">No prerequisite</option>
              {tests.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
            <textarea value={aiPrompt} onChange={e => setAiPrompt(e.target.value)} placeholder="Describe test for AI generation..." rows={2} style={{ flex: 1, minWidth: 200 }} />
            <button className="btn-primary btn-sm" onClick={aiGenerate} disabled={busy}>AI Generate</button>
            <button className="btn-ghost btn-sm" onClick={async () => { setBusy(true); try { const ps = await api.capturePageSource(); if (ps.ok) { setNewXml(ps.xml); toast("Page source captured", "success"); } else toast(ps.message || "No active session", "error"); } catch (e: any) { toast(e.message, "error"); } finally { setBusy(false); } }} disabled={busy} title="Capture current screen XML from Appium">📄 Capture XML</button>
          </div>
            <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 10 }}>
              <select value={genFolderId ?? ""} onChange={e => setGenFolderId(e.target.value ? Number(e.target.value) : null)} style={{ fontSize: 11, minWidth: 180 }}>
                <option value="">No screen folder</option>
                {screenFolders.map(f => <option key={f.id} value={f.id}>{f.name} ({f.screen_count} screens)</option>)}
              </select>
              {genFolderId
                ? <span style={{ fontSize: 10, color: "var(--accent)" }}>Pick folder + builds below for grounded AI.</span>
                : <span style={{ fontSize: 10, color: "var(--muted)" }}>Select a screen folder for better accuracy.</span>}
            </div>
            {genFolderId && genAiFolderScreens.some(s => s.build_id != null) && (
              <div style={{ marginBottom: 10 }}>
                <div style={{ fontSize: 10, color: "var(--muted)", marginBottom: 4 }}>Builds in folder (context)</div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                  {[...new Set(genAiFolderScreens.map(s => s.build_id).filter((id): id is number => id != null))].sort((a, b) => a - b).map((bid) => {
                    const b = builds.find(x => x.id === bid);
                    return (
                      <label key={bid} style={{ fontSize: 11, display: "flex", alignItems: "center", gap: 4, cursor: "pointer" }}>
                        <input type="checkbox" checked={genAiBuildIds.includes(bid)} onChange={() => toggleGenAiBuild(bid)} />
                        {b?.file_name ?? `Build #${bid}`}
                      </label>
                    );
                  })}
                </div>
                <div style={{ fontSize: 10, color: "var(--accent2)", marginTop: 4 }}>{genAiContextScreenCount} screen(s) will be sent to AI</div>
              </div>
            )}
            {genFolderId && !genAiFolderScreens.some(s => s.build_id != null) && genAiFolderScreens.length > 0 && (
              <div style={{ fontSize: 10, color: "var(--muted)", marginBottom: 8 }}>
                {genAiFolderScreens.length} screen(s) in folder (no build id) — all will be sent to AI.
              </div>
            )}
            {aiStatus && <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 8 }}>{aiStatus}</div>}
            <div style={{ fontSize: 10, color: "var(--muted)", marginBottom: 8 }}>While AI Generate runs, a progress bar appears under the Test Library title above.</div>
          <input value={newName} onChange={e => setNewName(e.target.value)} placeholder="Test name" className="form-input" style={{ width: "100%", marginBottom: 10 }} />
          <div style={{ marginBottom: 10 }}>
            <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 4, color: "var(--muted)" }}>Acceptance criteria (source of truth for AI Fix)</div>
            <textarea value={newAcceptanceCriteria} onChange={e => setNewAcceptanceCriteria(e.target.value)} placeholder="What this test must validate. e.g. Login: Email+Password must appear; fail if password field absent" rows={2} className="form-input" style={{ width: "100%", fontSize: 11 }} />
          </div>
          {newSelectorPickStepIndex !== null && <div style={{ fontSize: 11, color: "var(--accent)", marginBottom: 8 }}>Click an element in the XML tree below to fill step {newSelectorPickStepIndex + 1} selector</div>}
          <StepBuilder steps={newSteps} setSteps={setNewSteps} selectorPickStepIndex={newSelectorPickStepIndex} onPickStep={(i) => setNewSelectorPickStepIndex(prev => prev === i ? null : i)} figmaNames={figmaNames} platform={platform} />
          {newXml && (
            <div className="panel" style={{ marginTop: 12, padding: 12, border: "1px solid var(--border)" }}>
              <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 8, color: "var(--muted)" }}>Page Source — click an element to fill selector</div>
              <XmlElementTree xml={newXml} onCopy={(msg) => toast(msg, "success")} onNodeClick={(sel) => {
                if (newSelectorPickStepIndex !== null && newSelectorPickStepIndex < newSteps.length) {
                  setNewSteps(prev => { const n = [...prev]; n[newSelectorPickStepIndex] = { ...n[newSelectorPickStepIndex], selector: { using: sel.using, value: sel.value } }; return n; });
                  setNewSelectorPickStepIndex(null);
                  toast(`Filled step ${newSelectorPickStepIndex + 1} selector`, "success");
                }
              }} />
            </div>
          )}
          <div style={{ marginTop: 10 }}><button className="btn-primary btn-sm" onClick={saveNew} disabled={busy || !newSteps.length}>Save Test</button></div>
        </div>
      )}

      {/* Edit test */}
      {editId && (
        <div className="panel" style={{ padding: 18, marginBottom: 16, border: "1px solid rgba(99,102,241,.4)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 14 }}>
            <div style={{ fontFamily: "var(--sans)", fontWeight: 600 }}>Edit Test — TC-{editId}</div>
            <button className="btn-ghost btn-sm" onClick={cancelEdit}>✕ Cancel</button>
          </div>
          <div style={{ display: "flex", gap: 10, marginBottom: 12, flexWrap: "wrap" }}>
            <input value={editName} onChange={e => setEditName(e.target.value)} className="form-input" style={{ flex: 1, minWidth: 200 }} />
            <select value={editSuiteId ?? ""} onChange={e => setEditSuiteId(e.target.value ? Number(e.target.value) : null)} style={{ fontSize: 11 }}>
              <option value="">No suite</option>
              {modules.map(m => suites.filter(s => s.module_id === m.id).map(s => <option key={s.id} value={s.id}>{m.name} / {s.name}</option>))}
            </select>
            <select value={editPrerequisiteId ?? ""} onChange={e => setEditPrerequisiteId(e.target.value ? Number(e.target.value) : null)} style={{ fontSize: 11 }} title="Prepend steps from another test (e.g. Login)">
              <option value="">No prerequisite</option>
              {tests.filter(t => t.id !== editId).map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </div>
          <div style={{ marginBottom: 12 }}>
            <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 4, color: "var(--muted)" }}>Acceptance criteria (source of truth for AI Fix)</div>
            <textarea value={editAcceptanceCriteria} onChange={e => setEditAcceptanceCriteria(e.target.value)} placeholder="What this test must validate. Used by AI Fix to avoid changing intended behavior." rows={2} className="form-input" style={{ width: "100%", fontSize: 11 }} />
          </div>
          {editRelated && (editRelated.dependents.length > 0 || editRelated.similar.length > 0) && (
            <div style={{ marginBottom: 12, padding: 10, background: "rgba(99,102,241,.08)", borderRadius: 6, border: "1px solid rgba(99,102,241,.3)", fontSize: 11 }}>
              <div style={{ fontWeight: 600, marginBottom: 4, color: "#a78bfa" }}>Related tests</div>
              {editRelated.dependents.length > 0 && <div style={{ color: "var(--muted)" }}>{editRelated.dependents.length} test{editRelated.dependents.length !== 1 ? "s" : ""} use this as prerequisite — they get these steps automatically at runtime.</div>}
              {editRelated.similar.length > 0 && <div style={{ color: "var(--muted)", marginTop: 4 }}>{editRelated.similar.length} test{editRelated.similar.length !== 1 ? "s" : ""} share similar steps: {editRelated.similar.map(s => s.test.name).join(", ")}. When fixing via AI in Execution, use &quot;Fix all related&quot; to update them.</div>}
            </div>
          )}
          {editPrerequisiteId != null && (() => {
            const prereqT = tests.find(t => t.id === editPrerequisiteId);
            if (!prereqT) return null;
            return (
              <div style={{ marginBottom: 12, padding: 10, background: "rgba(251,191,36,.1)", borderRadius: 6, border: "1px solid rgba(251,191,36,.35)", fontSize: 11 }}>
                <div style={{ fontWeight: 600, marginBottom: 4, color: "var(--warn)" }}>Prerequisite</div>
                <div style={{ color: "var(--muted)", marginBottom: 8 }}>
                  &quot;{prereqT.name}&quot; runs first at execution time. The step list below is <strong>only</strong> for this test (TC-{editId}). To change prerequisite steps, open that test.
                </div>
                <button
                  type="button"
                  className="btn-ghost btn-sm"
                  onClick={() => {
                    if (confirm("Switch to prerequisite test? Unsaved changes on this test will be lost.")) openEdit(prereqT);
                  }}
                >
                  Edit prerequisite (TC-{prereqT.id})
                </button>
              </div>
            );
          })()}
          <div style={{ display: "flex", gap: 0, marginBottom: 12, borderRadius: 8, overflow: "hidden", border: "1px solid var(--border)" }}>
            {(["android", "ios_sim"] as const).map(p => (
              <button key={p} type="button" onClick={() => setEditPfTab(p)} style={{ flex: 1, padding: "8px 6px", fontSize: 11, border: "none", cursor: "pointer", background: editPfTab === p ? "var(--accent)" : "rgba(99,102,241,.12)", color: editPfTab === p ? "#042" : "var(--muted)", fontFamily: "var(--mono)" }}>
                {p === "android" ? "Android" : "iOS Simulator"} <span style={{ fontSize: 10, opacity: 0.85 }}>{(p === "android" ? editStepsAndroid : editStepsIos).length} steps</span>
              </button>
            ))}
          </div>
          <div style={{ display: "flex", gap: 8, marginBottom: 12, flexWrap: "wrap", alignItems: "center" }}>
            <input value={aiEditPrompt} onChange={e => setAiEditPrompt(e.target.value)} className="form-input" placeholder={`AI edit · ${editPfTab === "android" ? "Android" : "iOS"} steps`} style={{ flex: 1 }} />
            <button className="btn-primary btn-sm" style={{ background: "linear-gradient(135deg, #6366f1, #8b5cf6)" }} onClick={aiEditRun} disabled={busy}>🤖 AI Edit</button>
          </div>
          {aiEditStatus && <div style={{ fontSize: 11, color: "var(--accent)", marginBottom: 8 }}>{aiEditStatus}</div>}
          <StepBuilder steps={editSteps} setSteps={setEditSteps} stepStatuses={editStepStatuses} figmaNames={figmaNames} platform={editPfTab} />
          <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
            <button className="btn-primary btn-sm" onClick={saveEdit} disabled={busy}>Save Changes</button>
            <button className="btn-ghost btn-sm" onClick={cancelEdit}>Cancel</button>
            {editTest?.fix_history && (editTest.fix_history as any[]).length > 0 && (editTest.fix_history as any[]).slice(-1)[0]?.steps_before_fix != null ? (
              <button className="btn-ghost btn-sm" style={{ color: "#a78bfa" }} onClick={undoLastFix} disabled={busy} title="Revert to steps before last AI fix">↩ Undo last AI fix</button>
            ) : null}
          </div>
        </div>
      )}

      {/* Filter bar — above test cases */}
      <div className="panel" style={{ padding: 12, marginBottom: 12, display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
        <div style={{ fontSize: 11, color: "var(--muted)", fontWeight: 600 }}>Filter</div>
        <input className="form-input" style={{ fontSize: 11, minWidth: 160, maxWidth: 260, padding: "6px 10px" }} placeholder="Search tests…" value={librarySearch} onChange={e => setLibrarySearch(e.target.value)} />
        <select value={filterCollectionId ?? ""} onChange={e => { const v = e.target.value ? Number(e.target.value) : null; setFilterCollectionId(v); setFilterSuiteIds(null); }} style={{ fontSize: 11, minWidth: 180 }}>
          <option value="">All collections</option>
          {modules.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
        </select>
        {filterCollectionId && suitesInCollection.length > 0 && (
          <>
            <div style={{ borderLeft: "1px solid var(--border)", height: 20 }} />
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
              <span style={{ fontSize: 11, color: "var(--muted)" }}>Suites:</span>
              {suitesInCollection.map(s => {
                const checked = filterSuiteIds === null || filterSuiteIds.includes(s.id);
                return (
                  <label key={s.id} style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 11, cursor: "pointer" }}>
                    <input type="checkbox" checked={checked} onChange={() => {
                      if (filterSuiteIds === null) setFilterSuiteIds(suitesInCollection.map(x => x.id).filter(id => id !== s.id));
                      else if (filterSuiteIds.includes(s.id)) { const next = filterSuiteIds.filter(id => id !== s.id); setFilterSuiteIds(next.length === 0 ? [] : next); }
                      else { const next = [...filterSuiteIds, s.id]; setFilterSuiteIds(next.length === suitesInCollection.length ? null : next); }
                    }} />
                    {s.name}
                  </label>
                );
              })}
              <button className="btn-ghost btn-sm" style={{ fontSize: 10 }} onClick={() => setFilterSuiteIds(null)}>All</button>
            </div>
          </>
        )}
        {(filterCollectionId || filterSuiteIds !== null || librarySearch.trim()) && (
          <button className="btn-ghost btn-sm" style={{ fontSize: 10, marginLeft: "auto" }} onClick={() => { setFilterCollectionId(null); setFilterSuiteIds(null); setLibrarySearch(""); }}>Clear</button>
        )}
        <div style={{ fontSize: 11, color: "var(--accent2)", marginLeft: "auto" }}>{filteredTests.length} of {tests.length} test cases</div>
      </div>

      {/* Test table */}
      <div className="panel">
        <table className="tc-table">
          <thead><tr><th>ID</th><th>Title</th><th>Collection</th><th>Suite</th><th>Steps</th><th>Status</th><th></th></tr></thead>
          <tbody>
            {filteredTests.map(t => {
              const st = lastRunStatus(t.id);
              return (
                <tr key={t.id} style={{ cursor: "pointer" }} onClick={() => { if (editId !== t.id) openEdit(t); }}>
                  <td><div className="tc-id">TC-{t.id}</div></td>
                  <td>{t.name}</td>
                  <td style={{ fontSize: 11, color: "var(--accent2)" }}>{moduleName(t.suite_id) || guessModule(t.name)}</td>
                  <td style={{ fontSize: 11, color: "var(--muted)" }}>{suiteName(t.suite_id)}</td>
                  <td style={{ fontSize: 11, color: "var(--muted)" }}>{Math.max(stepsForPlatform(t, "android").length, stepsForPlatform(t, "ios_sim").length)}</td>
                  <td>{st ? <div className="tc-status"><div className={`dot ${statusDot(st)}`} />{st}</div> : <span style={{ color: "var(--muted)", fontSize: 11 }}>—</span>}</td>
                  <td onClick={e => e.stopPropagation()}>
                    <div style={{ display: "flex", gap: 4 }}>
                      <button className="btn-ghost btn-sm" onClick={() => openEdit(t)}>Edit</button>
                      <button className="btn-ghost btn-sm" style={{ color: "var(--danger)" }} onClick={async () => { if (confirm(`Delete "${t.name}"?`)) { await api.deleteTest(t.id); toast("Deleted", "info"); onRefresh(); } }}>Del</button>
                    </div>
                  </td>
                </tr>
              );
            })}
            {filteredTests.length === 0 && <tr><td colSpan={7} style={{ color: "var(--muted)", padding: 18 }}>{tests.length === 0 ? "No tests yet. Click \"+ New Test\" to create one." : "No test cases match the filter."}</td></tr>}
          </tbody>
        </table>
      </div>
      </>}

      {/* ── Screen Library tab ── */}
      {libTab === "screens" && <>
        <div style={{ display: "grid", gridTemplateColumns: "200px 1fr", gap: 16 }}>
          {/* Folder sidebar */}
          <div className="panel" style={{ padding: 12, alignSelf: "start" }}>
            <div style={{ fontSize: 10, fontWeight: 600, color: "var(--muted)", textTransform: "uppercase", letterSpacing: ".5px", marginBottom: 8 }}>Folders</div>
            <button className={`btn-ghost btn-sm ${activeFolderId === null ? "active" : ""}`} style={{ width: "100%", textAlign: "left", fontSize: 11, marginBottom: 2 }} onClick={() => setActiveFolderId(null)}>
              All screens
            </button>
            {screenFolders.map(f => (
              <div key={f.id} style={{ display: "flex", alignItems: "center", gap: 4, marginBottom: 2 }}>
                <button className={`btn-ghost btn-sm ${activeFolderId === f.id ? "active" : ""}`} style={{ flex: 1, textAlign: "left", fontSize: 11, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} onClick={() => setActiveFolderId(f.id)}>
                  {f.name} <span style={{ color: "var(--muted)", fontSize: 10 }}>({f.screen_count})</span>
                </button>
                <button className="btn-ghost btn-sm" style={{ fontSize: 9, color: "var(--danger)", padding: "2px 4px", flexShrink: 0 }} onClick={async () => { if (confirm(`Delete folder "${f.name}" and unlink its screens?`)) { await api.deleteScreenFolder(f.id); loadScreenFolders(); if (activeFolderId === f.id) setActiveFolderId(null); loadScreens(); } }}>×</button>
              </div>
            ))}
            {showNewFolder ? (
              <div style={{ marginTop: 8, display: "flex", gap: 4 }}>
                <input type="text" value={newFolderName} onChange={e => setNewFolderName(e.target.value)} placeholder="Folder name" autoFocus style={{ flex: 1, fontSize: 11, padding: "4px 6px" }} onKeyDown={async e => {
                  if (e.key === "Enter" && newFolderName.trim()) {
                    await api.createScreenFolder({ project_id: project.id, name: newFolderName.trim() });
                    setNewFolderName(""); setShowNewFolder(false); loadScreenFolders();
                  }
                  if (e.key === "Escape") { setShowNewFolder(false); setNewFolderName(""); }
                }} />
                <button className="btn-primary btn-sm" style={{ fontSize: 10 }} disabled={!newFolderName.trim()} onClick={async () => {
                  await api.createScreenFolder({ project_id: project.id, name: newFolderName.trim() });
                  setNewFolderName(""); setShowNewFolder(false); loadScreenFolders();
                }}>Add</button>
              </div>
            ) : (
              <button className="btn-ghost btn-sm" style={{ width: "100%", textAlign: "left", fontSize: 10, color: "var(--accent)", marginTop: 6 }} onClick={() => setShowNewFolder(true)}>+ New folder</button>
            )}
          </div>

          {/* Main content */}
          <div>
            {/* Capture panel */}
            {showCapture && (() => {
              const selectedBuild =
                screenBuildFilter != null ? builds.find(b => b.id === screenBuildFilter) ?? null : null;
              const capturePlatform = selectedBuild?.platform || platform;
              return (
              <div className="panel" style={{ padding: 16, marginBottom: 12, border: "1px solid rgba(139,92,246,.25)" }}>
                <div style={{ fontFamily: "var(--sans)", fontWeight: 600, marginBottom: 4, fontSize: 13 }}>Capture Screen</div>
                <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 10 }}>
                  <strong>Start build</strong> runs install / first-folder / build-switch logic <strong>once</strong> and keeps one Appium session open. <strong>Capture</strong> only reads the UI tree and screenshot (no reinstall, no quit). Pick a <strong>specific build</strong> (not Latest) and the <strong>Device</strong> that matches your emulator. Changing build or device stops the previous session.
                </div>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", marginBottom: 10 }}>
                  <span style={{ fontSize: 11, color: screenSessionActive ? "var(--accent)" : "var(--muted)" }}>
                    {screenSessionActive ? "● Session active" : "○ No session — Start build first"}
                  </span>
                  <button
                    type="button"
                    className="btn-primary btn-sm"
                    disabled={busy || !activeFolderId || screenBuildFilter == null}
                    onClick={async () => {
                      if (screenBuildFilter == null || !selectedBuild) return;
                      setBusy(true);
                      setCaptureStatus("Starting Appium session…");
                      try {
                        const res = await api.startScreenSession({
                          project_id: project.id,
                          folder_id: activeFolderId!,
                          build_id: screenBuildFilter,
                          platform: selectedBuild.platform,
                          ...(captureDeviceId.trim() ? { device_target: captureDeviceId.trim() } : {}),
                        });
                        lastScreenSessionRef.current = {
                          build_id: screenBuildFilter,
                          device_target: captureDeviceId,
                          platform: selectedBuild.platform as "android" | "ios_sim",
                        };
                        setScreenSessionActive(true);
                        const bits: string[] = [];
                        if (res.reused) bits.push("reused existing session");
                        if (res.flags?.fresh_install) bits.push("fresh reinstall (first screen in folder)");
                        if (res.flags?.build_changed) bits.push("build switch — old app(s) removed");
                        setCaptureStatus(
                          `Session ready${bits.length ? ` — ${bits.join("; ")}` : ""}. Use Capture while you navigate; session stays open.`,
                        );
                      } catch (e: any) {
                        setCaptureStatus(`Error: ${e?.message || e}`);
                        setScreenSessionActive(false);
                        lastScreenSessionRef.current = null;
                      } finally {
                        setBusy(false);
                      }
                    }}
                  >
                    Start build
                  </button>
                  <button
                    type="button"
                    className="btn-ghost btn-sm"
                    disabled={busy || screenBuildFilter == null || !selectedBuild}
                    onClick={async () => {
                      if (screenBuildFilter == null || !selectedBuild) return;
                      setBusy(true);
                      try {
                        await api.stopScreenSession({
                          project_id: project.id,
                          build_id: screenBuildFilter,
                          platform: selectedBuild.platform,
                          ...(captureDeviceId.trim() ? { device_target: captureDeviceId.trim() } : {}),
                        });
                        lastScreenSessionRef.current = null;
                        setScreenSessionActive(false);
                        setCaptureStatus("Session stopped.");
                      } catch (e: any) {
                        setCaptureStatus(`Error: ${e?.message || e}`);
                      } finally {
                        setBusy(false);
                      }
                    }}
                  >
                    Stop session
                  </button>
                </div>
                {screenBuildFilter == null && (
                  <div style={{ fontSize: 10, color: "var(--warn)", marginBottom: 8 }}>
                    Select a specific build (not &quot;Latest&quot;) before Start build or Capture.
                  </div>
                )}
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "end" }}>
                  <div style={{ minWidth: 130 }}>
                    <label style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", display: "block", marginBottom: 3 }}>Folder</label>
                    <select value={activeFolderId ?? ""} onChange={e => setActiveFolderId(e.target.value ? Number(e.target.value) : null)} style={{ fontSize: 11, width: "100%" }}>
                      <option value="" disabled>Select folder</option>
                      {screenFolders.map(f => <option key={f.id} value={f.id}>{f.name}</option>)}
                    </select>
                  </div>
                  <div style={{ flex: 1, minWidth: 140 }}>
                    <label style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", display: "block", marginBottom: 3 }}>Screen name</label>
                    <input type="text" value={captureName} onChange={e => setCaptureName(e.target.value)} placeholder='e.g. "Login screen"' style={{ width: "100%", fontSize: 11, padding: "6px 8px" }} />
                  </div>
                  <div>
                    <label style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", display: "block", marginBottom: 3 }}>Build</label>
                    <select value={screenBuildFilter ?? ""} onChange={e => {
                      void stopScreenSessionIfAny();
                      const val = e.target.value ? Number(e.target.value) : null;
                      setScreenBuildFilter(val);
                      const b = builds.find(x => x.id === val);
                      if (b) setPlatform(b.platform as any);
                    }} style={{ fontSize: 11 }}>
                      <option value="">Latest</option>
                      {builds.map(b => <option key={b.id} value={b.id}>{b.file_name} ({b.platform})</option>)}
                    </select>
                  </div>
                  <div>
                    <label style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", display: "block", marginBottom: 3 }}>Platform</label>
                    <span className={`screen-badge screen-badge--${capturePlatform === "ios_sim" ? "ios" : "android"}`} style={{ padding: "5px 10px", fontSize: 10 }}>
                      {capturePlatform === "ios_sim" ? "iOS" : "Android"}
                    </span>
                  </div>
                  <div style={{ minWidth: 160 }}>
                    <label style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", display: "block", marginBottom: 3 }}>Device</label>
                    <select value={captureDeviceId} onChange={e => { void stopScreenSessionIfAny(); setCaptureDeviceId(e.target.value); }} style={{ fontSize: 11, width: "100%", maxWidth: 220 }}>
                      {capturePlatform === "ios_sim"
                        ? (devices.ios_simulators.length === 0
                          ? <option value="">No simulator — boot one in Xcode</option>
                          : devices.ios_simulators.map(d => <option key={d.udid} value={d.udid}>{d.name} ({d.state})</option>))
                        : (devices.android.length === 0
                          ? <option value="">No device — start emulator / USB</option>
                          : devices.android.map(d => {
                              const serial = String((d as { serial?: string }).serial || "");
                              return <option key={serial} value={serial}>{serial}</option>;
                            }))}
                    </select>
                  </div>
                  <div style={{ flex: 1, minWidth: 120 }}>
                    <label style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", display: "block", marginBottom: 3 }}>Notes</label>
                    <input type="text" value={captureNotes} onChange={e => setCaptureNotes(e.target.value)} placeholder="Optional" style={{ width: "100%", fontSize: 11, padding: "6px 8px" }} />
                  </div>
                  <button
                    className="btn-primary btn-sm"
                    disabled={
                      busy ||
                      !captureName.trim() ||
                      !activeFolderId ||
                      screenBuildFilter == null ||
                      !screenSessionActive
                    }
                    onClick={async () => {
                      if (screenBuildFilter == null) return;
                      setBusy(true);
                      setCaptureStatus("Capturing page source and screenshot…");
                      try {
                        const entry = await api.captureScreen({
                          project_id: project.id,
                          build_id: screenBuildFilter,
                          folder_id: activeFolderId!,
                          name: captureName.trim(),
                          platform: capturePlatform,
                          notes: captureNotes,
                          ...(captureDeviceId.trim() ? { device_target: captureDeviceId.trim() } : {}),
                        });
                        setCaptureStatus(`Captured "${entry.name}" — ${entry.xml_length.toLocaleString()} chars of XML`);
                        setCaptureName("");
                        setCaptureNotes("");
                        loadScreens(); loadScreenFolders();
                      } catch (e: any) {
                        setCaptureStatus(`Error: ${e?.message || e}`);
                      } finally {
                        setBusy(false);
                      }
                    }}
                  >
                    Capture
                  </button>
                </div>
                {!activeFolderId && <div style={{ marginTop: 8, fontSize: 10, color: "var(--warn)" }}>Select or create a folder first.</div>}
                {captureStatus && (
                  <div style={{ marginTop: 8, fontSize: 11, padding: "6px 10px", background: captureStatus.startsWith("Error") ? "rgba(255,59,92,.06)" : "rgba(0,229,160,.06)", borderRadius: 6, color: captureStatus.startsWith("Error") ? "var(--danger)" : captureStatus.startsWith("Capturing") ? "var(--warn)" : "var(--accent)" }}>
                    {captureStatus}
                  </div>
                )}
              </div>
              );
            })()}

            {/* Toolbar */}
            <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12, flexWrap: "wrap" }}>
              <button className={`btn-sm ${showCapture ? "btn-ghost" : "btn-primary"}`} style={{ fontSize: 11 }} onClick={() => setShowCapture(!showCapture)}>{showCapture ? "Hide Capture" : "Capture Screen"}</button>
              <div className="seg-btn" style={{ marginLeft: 8 }}>
                {(["", "android", "ios_sim"] as const).map(p => (
                  <button key={p} className={screenPlatformFilter === p ? "active" : ""} onClick={() => setScreenPlatformFilter(p)}>{p === "" ? "All" : p === "android" ? "Android" : "iOS"}</button>
                ))}
              </div>
              <div style={{ marginLeft: "auto", fontSize: 11, color: "var(--muted)" }}>
                {activeFolderId ? screenFolders.find(f => f.id === activeFolderId)?.name ?? "" : "All"} — {screens.length} screen{screens.length !== 1 ? "s" : ""}
              </div>
            </div>

            {screens.length === 0 && (
              <div className="panel" style={{ padding: 32, textAlign: "center" }}>
                <div style={{ fontSize: 14, fontWeight: 600, fontFamily: "var(--sans)", marginBottom: 8 }}>{activeFolderId ? "No screens in this folder" : "No screens yet"}</div>
                <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 16 }}>{activeFolderId ? "Click Capture Screen above to add screens to this folder." : "Create a folder in the sidebar, select it, then capture screens."}</div>
                {!showCapture && <button className="btn-primary btn-sm" onClick={() => setShowCapture(true)}>Capture Screen</button>}
              </div>
            )}

            {/* Screen grid */}
            {screens.length > 0 && (
              <div className="screen-grid">
                {screens.map(s => (
                  <div key={s.id} className={`screen-card ${s.stale ? "screen-card--stale" : ""}`} onClick={() => {
                    if (editingScreenId === s.id) return;
                    api.getScreen(s.id).then(setScreenDetail).catch(() => {});
                  }}>
                    <div className="screen-card-img">
                      {s.screenshot_path ? <img src={api.screenScreenshotUrl(s.id, s.captured_at)} alt={s.name} /> : <div className="screen-card-placeholder">No screenshot</div>}
                    </div>
                    <div className="screen-card-body">
                      {editingScreenId === s.id ? (
                        <div onClick={e => e.stopPropagation()}>
                          <input type="text" value={editScreenName} onChange={e => setEditScreenName(e.target.value)} style={{ width: "100%", fontSize: 11, marginBottom: 4, padding: "4px 6px" }} />
                          <input type="text" value={editScreenNotes} onChange={e => setEditScreenNotes(e.target.value)} placeholder="Notes..." style={{ width: "100%", fontSize: 10, padding: "4px 6px" }} />
                          <div style={{ display: "flex", gap: 4, marginTop: 6 }}>
                            <button className="btn-primary btn-sm" style={{ fontSize: 10 }} onClick={async () => {
                              await api.updateScreen(s.id, { name: editScreenName, notes: editScreenNotes });
                              setEditingScreenId(null); loadScreens();
                            }}>Save</button>
                            <button className="btn-ghost btn-sm" style={{ fontSize: 10 }} onClick={() => setEditingScreenId(null)}>Cancel</button>
                          </div>
                        </div>
                      ) : (
                        <>
                          <div className="screen-card-name">{s.name}</div>
                          <div className="screen-card-meta">
                            <span className={`screen-badge screen-badge--${s.platform === "ios_sim" ? "ios" : "android"}`}>{s.platform === "ios_sim" ? "iOS" : "Android"}</span>
                            {s.stale && <span className="screen-badge screen-badge--stale">Stale</span>}
                            <span>{s.xml_length.toLocaleString()} chars</span>
                          </div>
                          {s.notes && <div className="screen-card-notes">{s.notes}</div>}
                          <div className="screen-card-actions" onClick={e => e.stopPropagation()}>
                            <button className="btn-ghost btn-sm" style={{ fontSize: 10 }} onClick={() => { setEditingScreenId(s.id); setEditScreenName(s.name); setEditScreenNotes(s.notes || ""); }}>Edit</button>
                            <button className="btn-ghost btn-sm" style={{ fontSize: 10, color: "var(--danger)" }} onClick={async () => { if (confirm(`Delete screen "${s.name}"?`)) { await api.deleteScreen(s.id); loadScreens(); loadScreenFolders(); if (screenDetail?.id === s.id) setScreenDetail(null); } }}>Delete</button>
                          </div>
                        </>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Screen detail modal */}
        {screenDetail && (
          <div className="modal-backdrop" onClick={() => setScreenDetail(null)}>
            <div className="modal" style={{ maxWidth: 960, width: "95vw", maxHeight: "90vh", display: "flex", flexDirection: "column" }} onClick={e => e.stopPropagation()}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "16px 20px", borderBottom: "1px solid var(--border)", flexShrink: 0 }}>
                <div style={{ fontWeight: 700, fontSize: 15 }}>{screenDetail.name}</div>
                <div style={{ display: "flex", gap: 10, alignItems: "center", fontSize: 11, color: "var(--muted)" }}>
                  <span className="screen-badge">{screenDetail.platform}</span>
                  <span>Build {screenDetail.build_id ?? "—"}</span>
                  <span>{screenDetail.captured_at ? new Date(screenDetail.captured_at).toLocaleString() : ""}</span>
                  <span>{screenDetail.xml_length.toLocaleString()} chars</span>
                  <button className="btn-ghost btn-sm" onClick={() => setScreenDetail(null)}>Close</button>
                </div>
              </div>
              {screenDetail.notes && <div style={{ padding: "8px 20px", fontSize: 11, color: "var(--muted)", borderBottom: "1px solid var(--border)", flexShrink: 0 }}>{screenDetail.notes}</div>}
              <div style={{ display: "grid", gridTemplateColumns: screenDetail.screenshot_path ? "220px 1fr" : "1fr", flex: 1, minHeight: 0, overflow: "hidden" }}>
                {screenDetail.screenshot_path && (
                  <div style={{ padding: 16, overflow: "auto", borderRight: "1px solid var(--border)" }}>
                    <img src={api.screenScreenshotUrl(screenDetail.id, screenDetail.captured_at)} alt={screenDetail.name} style={{ width: "100%", borderRadius: 8, border: "1px solid var(--border)" }} />
                  </div>
                )}
                <div style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
                  <div style={{ padding: "10px 16px 6px", fontSize: 10, fontWeight: 600, color: "var(--muted)", textTransform: "uppercase", letterSpacing: ".5px", flexShrink: 0 }}>XML Source</div>
                  <pre className="code-panel" style={{ flex: 1, margin: "0 16px 16px", overflowY: "auto", overflowX: "hidden", fontSize: 10.5, lineHeight: 1.6, whiteSpace: "pre-wrap", wordBreak: "break-all", tabSize: 2 }}>
                    {screenDetail.xml_snapshot || "(empty)"}
                  </pre>
                </div>
              </div>
            </div>
          </div>
        )}
      </>}
    </>
  );
}

/* ── Reports ───────────────────────────────────────── */
function ReportsView({ project, runs, tests, modules, suites, onRefresh }: { project: Project; runs: Run[]; tests: TestDef[]; modules: ModuleDef[]; suites: SuiteDef[]; onRefresh?: () => void }) {
  type Scope = "suite" | "collection";
  const [scope, setScope] = useState<Scope>("suite");
  const [scopeId, setScopeId] = useState<number | null>(null);
  const [platform, setPlatform] = useState<"" | "android" | "ios_sim">("");
  const [days, setDays] = useState<number>(14);
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [search, setSearch] = useState("");
  const [tab, setTab] = useState<string>("health");
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [confluenceBusy, setConfluenceBusy] = useState(false);

  const [suiteData, setSuiteData] = useState<SuiteHealthResponse | null>(null);
  const [trendData, setTrendData] = useState<SuiteTrendItem[] | null>(null);
  const [stepCov, setStepCov] = useState<StepCoverageItem[] | null>(null);
  const [triageData, setTriageData] = useState<TriageResponse | null>(null);
  const [collData, setCollData] = useState<CollectionHealthResponse | null>(null);
  const [blockers, setBlockers] = useState<BlockerItem[] | null>(null);

  // auto-select first suite/collection
  useEffect(() => {
    if (scopeId) return;
    if (scope === "suite" && suites.length) setScopeId(suites[0].id);
    if (scope === "collection" && modules.length) setScopeId(modules[0].id);
  }, [scope, suites, modules, scopeId]);

  // data loading
  useEffect(() => {
    if (!scopeId) return;
    setLoading(true);
    if (scope === "suite") {
      Promise.all([
        api.getSuiteHealth(scopeId, days, platform),
        api.getSuiteTrend(scopeId, days, platform),
        api.getSuiteStepCoverage(scopeId, days, platform),
        api.getSuiteTriage(scopeId, days, platform),
      ]).then(([h, t, sc, tr]) => { setSuiteData(h); setTrendData(t); setStepCov(sc); setTriageData(tr); setCollData(null); setBlockers(null); })
        .catch(() => {})
        .finally(() => setLoading(false));
    } else {
      Promise.all([
        api.getCollectionHealth(scopeId, days, platform),
        api.getCollectionBlockers(scopeId, days, platform),
      ]).then(([ch, bl]) => { setCollData(ch); setBlockers(bl); setSuiteData(null); setTrendData(null); setStepCov(null); setTriageData(null); })
        .catch(() => {})
        .finally(() => setLoading(false));
    }
  }, [scope, scopeId, days, platform]);

  const statusColor = (s: string) => s === "passing" ? "var(--accent)" : s === "failing" ? "var(--danger)" : s === "flaky" ? "var(--warn)" : "var(--muted)";
  const rateColor = (pct: number) => pct >= 80 ? "var(--accent)" : pct >= 50 ? "var(--warn)" : "var(--danger)";
  const catLabel: Record<string, string> = { selector_not_found: "Selector Not Found", element_timeout: "Element Timeout", assertion_failure: "Assertion Failure", network_error: "Network/API Error", app_crash: "App Crash/ANR", other: "Other" };
  const catColor: Record<string, string> = { selector_not_found: "#ff3b5c", element_timeout: "#ffb020", assertion_failure: "#a78bfa", network_error: "#3b82f6", app_crash: "#ff6b35", other: "#6b7280" };

  const filteredTests = useMemo(() => {
    if (!suiteData) return [];
    let t = suiteData.tests;
    if (statusFilter !== "all") t = t.filter(r => r.status === statusFilter);
    if (search.trim()) {
      const q = search.toLowerCase();
      t = t.filter(r => r.name.toLowerCase().includes(q) || (r.acceptance_criteria || "").toLowerCase().includes(q));
    }
    return t;
  }, [suiteData, statusFilter, search]);

  // ── Suite view tabs ──
  const renderHealthList = () => (
    <div className="panel" style={{ padding: 0, overflow: "hidden" }}>
      <div style={{ display: "grid", gridTemplateColumns: "20px 1fr 130px 80px 130px 56px", gap: 8, padding: "8px 14px", borderBottom: "1px solid var(--border)", fontSize: 10, color: "var(--muted)", textTransform: "uppercase" as const, letterSpacing: ".5px" }}>
        <div></div><div>Test</div><div>Steps</div><div>Platform</div><div>History</div><div style={{ textAlign: "right" }}>Pass%</div>
      </div>
      {filteredTests.length === 0 && <div style={{ padding: 20, color: "var(--muted)", fontSize: 12 }}>No test cases match the current filters.</div>}
      {filteredTests.map(t => (
        <React.Fragment key={t.id}>
          <div className="health-row" onClick={() => setExpandedId(expandedId === t.id ? null : t.id)}>
            <div style={{ width: 12, height: 12, borderRadius: "50%", background: statusColor(t.status) }} />
            <div className="h-name">{t.name}</div>
            <div className="h-steps" style={{ color: t.steps_total > 0 && t.steps_ran / t.steps_total < 0.5 ? "var(--danger)" : "var(--text)" }}>{t.steps_ran} of {t.steps_total} steps</div>
            <div className="h-platform" style={{ color: t.platform === "both" ? "var(--text)" : "var(--warn)" }}>{t.platform === "both" ? "Both" : t.platform === "ios_sim" ? "iOS" : "Android"}</div>
            <div className="run-strip">
              {t.run_history.map(rh => <div key={rh.id} className={`rc ${rh.status === "passed" ? "rc-pass" : rh.status === "failed" || rh.status === "error" ? "rc-fail" : "rc-other"}`} title={`#${rh.id} ${rh.status}`} />)}
            </div>
            <div className="h-rate" style={{ color: rateColor(t.pass_rate_pct) }}>{t.pass_rate_pct}%</div>
          </div>
          {expandedId === t.id && (
            <div className="health-expanded">
              {t.acceptance_criteria && <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 10 }}>{t.acceptance_criteria}</div>}
              <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginBottom: 12, fontSize: 11, color: "var(--muted)" }}>
                <span>Fail streak: <strong style={{ color: t.fail_streak > 0 ? "var(--danger)" : "var(--text)" }}>{t.fail_streak}</strong></span>
                <span>Last passed: {t.last_passed_at ? new Date(t.last_passed_at).toLocaleDateString() : "Never"}</span>
                <span>AI fixes used: {t.ai_fixes_count}</span>
              </div>
              {t.last_failed_run && (
                <>
                  <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 6, color: "var(--muted)" }}>
                    {t.steps_ran} of {t.steps_total} steps executed — {t.steps_ran < t.steps_total
                      ? `test stopped at step ${t.steps_ran + 1}`
                      : t.last_failed_run.step_results.some(sr => sr.status === "failed")
                        ? `failed at step ${(t.last_failed_run.step_results.findIndex(sr => sr.status === "failed") + 1)}`
                        : "all steps passed"}
                  </div>
                  {t.last_failed_run.step_results.map(sr => {
                    const isFailed = sr.status === "failed";
                    const isSkipped = sr.status !== "passed" && sr.status !== "failed";
                    return (
                      <div key={sr.index} className={`step-row ${isFailed ? "step-row--failed" : ""} ${isSkipped ? "step-row--skipped" : ""}`}>
                        <div className="step-num">{sr.index + 1}</div>
                        <div className="step-dot" style={{ background: sr.status === "passed" ? "var(--accent)" : sr.status === "failed" ? "var(--danger)" : "#555" }} />
                        <div>
                          <span style={{ fontWeight: 500 }}>{sr.type}</span>
                          {sr.selector && <span className="step-selector"> · {typeof sr.selector === "object" ? `${sr.selector.using}="${sr.selector.value}"` : String(sr.selector)}</span>}
                          {isSkipped && <span style={{ color: "var(--muted)", marginLeft: 8 }}>Not reached</span>}
                        </div>
                        <div className="step-dur">{sr.duration_ms != null ? `${(sr.duration_ms / 1000).toFixed(1)}s` : ""}</div>
                        <div>
                          {sr.screenshot && <img src={`/api/artifacts/${project.id}/${t.last_failed_run!.id}/${encodeURIComponent(sr.screenshot)}`} alt="" style={{ width: 32, height: 56, objectFit: "cover", borderRadius: 4, border: `1px solid ${isFailed ? "var(--danger)" : "var(--border)"}` }} />}
                        </div>
                      </div>
                    );
                  })}
                  {t.last_failed_run.error_message && <div className="error-detail">{t.last_failed_run.error_message}</div>}
                  {t.last_failed_run.ai_fix && (
                    <div className="ai-fix-box">
                      <div className="ai-label">AI Fix Suggestion</div>
                      <div className="ai-text">{t.last_failed_run.ai_fix.analysis}</div>
                    </div>
                  )}
                </>
              )}
              <div style={{ display: "flex", gap: 8, marginTop: 12, paddingTop: 10, borderTop: "1px solid var(--border)" }}>
                <button className="btn-ghost btn-sm" style={{ fontSize: 10 }} onClick={() => {
                  const a = document.createElement("a");
                  a.href = `/api/tests/${t.id}/export/html`;
                  a.download = `${t.name.replace(/\s+/g, "_")}_report.html`;
                  a.click();
                }}>Download Report</button>
              </div>
            </div>
          )}
        </React.Fragment>
      ))}
    </div>
  );

  const renderCharts = () => (
    <div>
      {/* Pass rate trend */}
      <div className="chart-card">
        <div className="chart-title">Pass Rate by Test Case ({days}d)</div>
        {trendData && trendData.length > 0 ? (
          <ResponsiveContainer width="100%" height={Math.max(200, trendData.length * 32)}>
            <BarChart data={trendData} layout="vertical" margin={{ left: 120, right: 20 }}>
              <XAxis type="number" domain={[0, 100]} tickFormatter={v => `${v}%`} stroke="#555" fontSize={10} />
              <YAxis type="category" dataKey="test_name" width={110} tick={{ fontSize: 10, fill: "#8a8f98" }} tickFormatter={v => v.length > 18 ? v.slice(0, 16) + "…" : v} />
              <Tooltip formatter={(v: any) => `${v}%`} contentStyle={{ background: "#131920", border: "1px solid #1e2430", fontSize: 11 }} />
              <Bar dataKey="pass_rate_pct" radius={[0, 4, 4, 0]}>
                {trendData.map((d, i) => <Cell key={i} fill={d.pass_rate_pct >= 80 ? "#00e5a0" : d.pass_rate_pct >= 50 ? "#ffb020" : "#ff3b5c"} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        ) : <div style={{ color: "var(--muted)", fontSize: 12, padding: 20 }}>No trend data</div>}
      </div>

      {/* Steps coverage */}
      <div className="chart-card">
        <div className="chart-title">Steps Executed vs Skipped</div>
        {stepCov && stepCov.length > 0 ? (
          <ResponsiveContainer width="100%" height={Math.max(200, stepCov.length * 32)}>
            <BarChart data={stepCov.map(s => ({ ...s, skipped: Math.max(0, s.avg_steps_total - s.avg_steps_ran) }))} layout="vertical" margin={{ left: 120, right: 20 }}>
              <XAxis type="number" stroke="#555" fontSize={10} />
              <YAxis type="category" dataKey="test_name" width={110} tick={{ fontSize: 10, fill: "#8a8f98" }} tickFormatter={v => v.length > 18 ? v.slice(0, 16) + "…" : v} />
              <Tooltip contentStyle={{ background: "#131920", border: "1px solid #1e2430", fontSize: 11 }} />
              <Bar dataKey="avg_steps_ran" stackId="a" fill="#00e5a0" name="Ran" radius={[0, 0, 0, 0]} />
              <Bar dataKey="skipped" stackId="a" fill="#555" name="Skipped" radius={[0, 4, 4, 0]} />
              <Legend wrapperStyle={{ fontSize: 10 }} />
            </BarChart>
          </ResponsiveContainer>
        ) : <div style={{ color: "var(--muted)", fontSize: 12, padding: 20 }}>No step data</div>}
      </div>

      {/* Failure type donut */}
      {triageData && triageData.categories.length > 0 && (
        <div className="chart-card">
          <div className="chart-title">Failure Type Breakdown ({triageData.total_failures} failures)</div>
          <div style={{ display: "flex", alignItems: "center", gap: 24, flexWrap: "wrap" }}>
            <ResponsiveContainer width={200} height={200}>
              <PieChart>
                <Pie data={triageData.categories} dataKey="count" nameKey="category" cx="50%" cy="50%" innerRadius={50} outerRadius={80} paddingAngle={2}>
                  {triageData.categories.map((c, i) => <Cell key={i} fill={catColor[c.category] || "#6b7280"} />)}
                </Pie>
                <Tooltip contentStyle={{ background: "#131920", border: "1px solid #1e2430", fontSize: 11 }} formatter={(v: any, name: any) => [v, catLabel[name] || name]} />
              </PieChart>
            </ResponsiveContainer>
            <div className="donut-legend">
              {triageData.categories.map(c => (
                <div key={c.category} className="donut-legend-item">
                  <div className="donut-legend-dot" style={{ background: catColor[c.category] || "#6b7280" }} />
                  <span>{catLabel[c.category] || c.category}: {c.count} ({c.pct}%)</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );

  const renderMatrix = () => {
    if (!suiteData) return null;
    const allRunIds = new Set<number>();
    for (const t of suiteData.tests) for (const rh of t.run_history) allRunIds.add(rh.id);
    const sortedIds = Array.from(allRunIds).sort((a, b) => a - b);
    return (
      <div className="panel" style={{ padding: 16 }}>
        <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 12 }}>Run Matrix — {sortedIds.length} runs × {suiteData.tests.length} tests</div>
        <div className="matrix-grid">
          {suiteData.tests.map(t => {
            const rhMap = new Map(t.run_history.map(r => [r.id, r.status]));
            return (
              <div key={t.id} className="matrix-row">
                <div className="matrix-label" title={t.name}>{t.name}</div>
                {sortedIds.map(rid => {
                  const st = rhMap.get(rid);
                  const bg = st === "passed" ? "#00e5a0" : st === "failed" || st === "error" ? "#ff3b5c" : st ? "#555" : "#1a1f26";
                  return <div key={rid} className="matrix-cell" style={{ background: bg }} title={st ? `#${rid}: ${st}` : `#${rid}: not run`} onClick={() => { if (st === "failed" || st === "error") setExpandedId(expandedId === t.id ? null : t.id); }} />;
                })}
              </div>
            );
          })}
        </div>
      </div>
    );
  };

  const renderTriage = () => {
    if (!triageData) return null;
    return (
      <div className="panel" style={{ padding: 16 }}>
        <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 12 }}>Failure Triage — {triageData.total_failures} total failures</div>
        {triageData.categories.map(c => (
          <details key={c.category} className="triage-cat" style={{ borderLeftColor: catColor[c.category] || "#555", borderLeftWidth: 3, borderLeftStyle: "solid" }}>
            <summary style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ fontWeight: 600, fontSize: 12 }}>{catLabel[c.category] || c.category}</span>
              <span style={{ fontSize: 11, color: "var(--muted)" }}>{c.count} ({c.pct}%)</span>
            </summary>
            <div style={{ marginTop: 10 }}>
              {c.affected_tests.map((at, i) => (
                <div key={i} style={{ padding: "6px 0", borderBottom: "1px solid var(--border)", fontSize: 11 }}>
                  <div style={{ fontWeight: 500 }}>{at.name}</div>
                  {at.error_message && <div style={{ color: "var(--danger)", fontFamily: "var(--mono)", fontSize: 10, marginTop: 4 }}>{at.error_message.slice(0, 200)}</div>}
                </div>
              ))}
            </div>
          </details>
        ))}
      </div>
    );
  };

  const renderExport = () => {
    if (!scopeId) return null;
    return (
      <div className="panel" style={{ padding: 20 }}>
        <div style={{ fontSize: 13, fontWeight: 600, fontFamily: "var(--sans)", marginBottom: 16 }}>Export & Download</div>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          {scope === "suite" && (
            <>
              <button className="btn-primary btn-sm" onClick={() => api.downloadSuiteHtml(scopeId!, days, platform)}>HTML Report</button>
              <button className="btn-ghost btn-sm" onClick={() => api.downloadSuiteCsv(scopeId!, days, platform)}>CSV</button>
              <button className="btn-ghost btn-sm" onClick={() => api.downloadSuiteScreenshots(scopeId!, days, platform)}>Screenshots ZIP</button>
            </>
          )}
          {scope === "collection" && (
            <button className="btn-primary btn-sm" onClick={() => api.downloadCollectionHtml(scopeId!, days, platform)}>Collection HTML Report</button>
          )}
        </div>
      </div>
    );
  };

  // ── Collection view ──
  const renderCollectionView = () => {
    if (!collData) return <div style={{ padding: 20, color: "var(--muted)" }}>Loading collection data...</div>;
    const c = collData.collection;
    const m = collData.metrics;
    const verdictCls = c.verdict === "READY" ? "verdict-ready" : c.verdict === "BLOCKED" ? "verdict-blocked" : "verdict-not-ready";
    return (
      <>
        <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 20 }}>
          <div style={{ fontFamily: "var(--sans)", fontSize: 18, fontWeight: 700 }}>{c.name}</div>
          <span className={`verdict-badge ${verdictCls}`}>{c.verdict}</span>
        </div>

        <div className="report-metrics">
          <div className="metric-card"><div className="metric-val">{m.total}</div><div className="metric-lbl">Total Tests</div></div>
          <div className="metric-card"><div className="metric-val" style={{ color: "var(--accent)" }}>{m.passing}</div><div className="metric-lbl">Passing</div></div>
          <div className="metric-card"><div className="metric-val" style={{ color: "var(--danger)" }}>{m.failing}</div><div className="metric-lbl">Failing</div></div>
          <div className="metric-card"><div className="metric-val" style={{ color: "#ff6b35" }}>{m.blockers}</div><div className="metric-lbl">Blockers</div></div>
          <div className="metric-card"><div className="metric-val" style={{ color: "var(--warn)" }}>{m.flaky}</div><div className="metric-lbl">Flaky</div></div>
          <div className="metric-card"><div className="metric-val">{m.never_run}</div><div className="metric-lbl">Never Ran</div></div>
        </div>

        {/* Suite breakdown */}
        <div className="panel" style={{ padding: 0, marginBottom: 16, overflow: "hidden" }}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 120px 56px 56px 56px 56px auto", gap: 8, padding: "8px 14px", fontSize: 10, color: "var(--muted)", textTransform: "uppercase" as const, borderBottom: "1px solid var(--border)" }}>
            <div>Suite</div><div>Pass Rate</div><div>%</div><div>Pass</div><div>Fail</div><div>Block</div><div>Last Run</div>
          </div>
          {collData.suites.map(s => (
            <div key={s.id} style={{ display: "grid", gridTemplateColumns: "1fr 120px 56px 56px 56px 56px auto", gap: 8, padding: "10px 14px", borderBottom: "1px solid var(--border)", fontSize: 12, alignItems: "center", cursor: "pointer" }} onClick={() => { setScope("suite"); setScopeId(s.id); setTab("health"); }}>
              <div style={{ fontWeight: 500 }}>{s.name}</div>
              <div style={{ height: 8, background: "rgba(255,255,255,.06)", borderRadius: 4, overflow: "hidden" }}>
                <div style={{ height: "100%", width: `${s.pass_rate_pct}%`, background: rateColor(s.pass_rate_pct), borderRadius: 4 }} />
              </div>
              <div style={{ color: rateColor(s.pass_rate_pct), fontWeight: 700 }}>{s.pass_rate_pct}%</div>
              <div style={{ color: "var(--accent)" }}>{s.pass_count}</div>
              <div style={{ color: "var(--danger)" }}>{s.fail_count}</div>
              <div style={{ color: "#ff6b35" }}>{s.blocker_count}</div>
              <div style={{ fontSize: 10, color: "var(--muted)" }}>{s.last_run_at ? ago(s.last_run_at) : "—"}</div>
            </div>
          ))}
        </div>

        {/* Blockers */}
        {blockers && blockers.length > 0 && (
          <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: 13, fontWeight: 600, fontFamily: "var(--sans)", marginBottom: 10, color: "var(--danger)" }}>Blockers ({blockers.length})</div>
            {blockers.map(b => (
              <div key={b.test_id} className="blocker-card">
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                  <div style={{ fontWeight: 600, fontSize: 12 }}>{b.test_name}</div>
                  <div style={{ fontSize: 10, color: "var(--muted)" }}>{b.suite_name} · streak {b.fail_streak}</div>
                </div>
                {b.error_message && <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "#ff9999", marginBottom: 6 }}>{b.error_message.slice(0, 200)}</div>}
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  {b.screenshot_path && b.run_id > 0 && <img src={`/api/artifacts/${project.id}/${b.run_id}/${encodeURIComponent(b.screenshot_path)}`} alt="" style={{ width: 40, height: 72, objectFit: "cover", borderRadius: 4 }} />}
                  {b.ai_fix_available && <span style={{ fontSize: 10, color: "#a78bfa" }}>AI fix available</span>}
                </div>
              </div>
            ))}
          </div>
        )}

        {/* 30-day trend */}
        {collData.trend_30d.length > 1 && (
          <div className="chart-card">
            <div className="chart-title">30-Day Pass Rate Trend</div>
            <ResponsiveContainer width="100%" height={180}>
              <LineChart data={collData.trend_30d} margin={{ left: 10, right: 20 }}>
                <XAxis dataKey="date" tick={{ fontSize: 9, fill: "#8a8f98" }} tickFormatter={d => d.slice(5)} />
                <YAxis domain={[0, 100]} tick={{ fontSize: 10, fill: "#8a8f98" }} tickFormatter={v => `${v}%`} />
                <Tooltip contentStyle={{ background: "#131920", border: "1px solid #1e2430", fontSize: 11 }} formatter={(v: any) => `${v}%`} />
                <Line type="monotone" dataKey="pass_rate_pct" stroke="#00e5a0" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}

        {renderExport()}
      </>
    );
  };

  // ── Main render ──
  return (
    <>
      <div className="section-head">
        <div>
          <div className="section-title">Reports</div>
          <div className="section-sub">{project.name} · test-case-first · {days}-day window</div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn-ghost btn-sm" disabled={confluenceBusy} onClick={async () => {
            setConfluenceBusy(true);
            try {
              const r = await api.syncConfluenceProject(project.id);
              if (r.page_url) window.open(r.page_url, "_blank");
              toast(r.title ? `Confluence: ${r.title}` : "Published to Confluence", "success");
            } catch (e: any) {
              toast(e?.message || "Confluence sync failed", "error");
            } finally {
              setConfluenceBusy(false);
            }
          }}>{confluenceBusy ? "Publishing…" : "Push to Confluence"}</button>
          <button className="btn-ghost btn-sm" onClick={() => onRefresh?.()}>Refresh</button>
        </div>
      </div>

      {/* Filter bar */}
      <div className="report-filter-bar">
        <select value={`${scope}:${scopeId || ""}`} onChange={e => {
          const [s, id] = e.target.value.split(":");
          setScope(s as Scope);
          setScopeId(id ? Number(id) : null);
          setExpandedId(null);
          setTab("health");
        }}>
          <optgroup label="Collections">
            {modules.map(m => <option key={`c-${m.id}`} value={`collection:${m.id}`}>{m.name}</option>)}
          </optgroup>
          <optgroup label="Suites">
            {modules.map(m => suites.filter(s => s.module_id === m.id).map(s => <option key={`s-${s.id}`} value={`suite:${s.id}`}>{m.name} / {s.name}</option>))}
          </optgroup>
        </select>

        <div className="seg-btn">
          {(["", "android", "ios_sim"] as const).map(p => (
            <button key={p} className={platform === p ? "active" : ""} onClick={() => setPlatform(p)}>{p === "" ? "All" : p === "android" ? "Android" : "iOS"}</button>
          ))}
        </div>

        <select value={days} onChange={e => setDays(Number(e.target.value))}>
          <option value={7}>7 days</option>
          <option value={14}>14 days</option>
          <option value={30}>30 days</option>
          <option value={90}>90 days</option>
        </select>

        {scope === "suite" && (
          <>
            <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)}>
              <option value="all">All statuses</option>
              <option value="failing">Failing</option>
              <option value="passing">Passing</option>
              <option value="flaky">Flaky</option>
            </select>
            <input type="text" placeholder="Search tests..." value={search} onChange={e => setSearch(e.target.value)} />
          </>
        )}
      </div>

      {loading && <div style={{ padding: 20, textAlign: "center", color: "var(--muted)" }}>Loading report data...</div>}

      {!loading && scope === "collection" && renderCollectionView()}

      {!loading && scope === "suite" && suiteData && (
        <>
          {/* Suite header */}
          <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 16, flexWrap: "wrap" }}>
            <div style={{ fontFamily: "var(--sans)", fontSize: 16, fontWeight: 700 }}>{suiteData.suite.name}</div>
            <span style={{ fontSize: 11, color: "var(--muted)" }}>{suiteData.suite.module_name}</span>
            <span style={{ fontSize: 13, fontWeight: 700, color: rateColor(suiteData.suite.pass_rate) }}>{suiteData.suite.pass_rate}% pass</span>
            {suiteData.suite.last_run_at && <span style={{ fontSize: 10, color: "var(--muted)" }}>Last run: {ago(suiteData.suite.last_run_at)}</span>}
          </div>

          {/* Metrics */}
          <div className="report-metrics">
            <div className="metric-card"><div className="metric-val">{suiteData.metrics.total}</div><div className="metric-lbl">Total</div></div>
            <div className="metric-card"><div className="metric-val" style={{ color: "var(--accent)" }}>{suiteData.metrics.passing}</div><div className="metric-lbl">Passing</div></div>
            <div className="metric-card"><div className="metric-val" style={{ color: "var(--danger)" }}>{suiteData.metrics.failing}</div><div className="metric-lbl">Failing</div></div>
            <div className="metric-card"><div className="metric-val" style={{ color: "var(--warn)" }}>{suiteData.metrics.flaky}</div><div className="metric-lbl">Flaky</div></div>
            <div className="metric-card"><div className="metric-val">{suiteData.metrics.never_run}</div><div className="metric-lbl">Never Ran</div></div>
            <div className="metric-card"><div className="metric-val">{suiteData.metrics.avg_steps_pct}%</div><div className="metric-lbl">Avg Steps</div></div>
          </div>

          {/* Tabs */}
          <div className="report-tabs">
            {["health", "charts", "matrix", "triage", "export"].map(t => (
              <button key={t} className={`report-tab ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>{t.charAt(0).toUpperCase() + t.slice(1)}</button>
            ))}
          </div>

          {tab === "health" && renderHealthList()}
          {tab === "charts" && renderCharts()}
          {tab === "matrix" && renderMatrix()}
          {tab === "triage" && renderTriage()}
          {tab === "export" && renderExport()}
        </>
      )}
    </>
  );
}

/* ── Builds ────────────────────────────────────────── */
function BuildsView({ project, builds, runs, onRefresh, onRunTest }: { project: Project; builds: Build[]; runs: Run[]; onRefresh: () => void; onRunTest: (b: Build) => void }) {
  const [platform, setPlatform] = useState<Build["platform"]>("android");
  const fileRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);

  const upload = async (file: File) => {
    setBusy(true);
    try { await api.uploadBuild(project.id, platform, file); toast("Build uploaded", "success"); onRefresh(); }
    catch (e: any) { toast(e.message, "error"); }
    finally { setBusy(false); }
  };

  return (
    <>
      <div className="section-head">
        <div><div className="section-title">Build Management</div><div className="section-sub">Upload APK or IPA · manage versions</div></div>
        <div style={{ display: "flex", gap: 10 }}>
          <select value={platform} onChange={e => setPlatform(e.target.value as any)}><option value="android">Android</option><option value="ios_sim">iOS Simulator</option></select>
          <button className="btn-primary" onClick={() => fileRef.current?.click()} disabled={busy}>+ Upload Build</button>
          <input ref={fileRef} type="file" hidden onChange={e => { if (e.target.files?.[0]) upload(e.target.files[0]); }} />
        </div>
      </div>

      <div className="upload-zone" style={{ marginBottom: 20 }} onClick={() => fileRef.current?.click()}>
        <div className="upload-icon">⬆</div>
        <div className="upload-text">Drop your APK or IPA here</div>
        <div className="upload-sub">Android .apk · iOS .ipa · auto-reads manifest</div>
      </div>

      <div className="builds-grid">
        {builds.map((b, i) => {
          const meta = b.metadata as any || {};
          const bRuns = runs.filter(r => r.build_id === b.id);
          const bPassed = bRuns.filter(r => r.status === "passed").length;
          const bRate = bRuns.length > 0 ? Math.round((bPassed / bRuns.length) * 100) : 0;
          return (
            <div key={b.id} className={`build-card-main${i === 0 ? " latest" : ""}`}>
              <div className="bcm-header">
                <div className="bcm-name">{b.file_name}</div>
                <div className={`bcm-tag${i > 0 ? " gray" : ""}`}>{i === 0 ? "latest" : "previous"}</div>
              </div>
              <div className="bcm-meta">{b.platform === "android" ? "Android" : "iOS"} · uploaded {ago(b.created_at)}</div>
              {meta.package && <div className="bcm-meta" style={{ color: "var(--accent2)" }}>{meta.package}{meta.main_activity ? ` · ${meta.main_activity}` : ""}</div>}
              <div className="bcm-stats">
                <div className="bcm-stat"><div className="bcm-stat-val" style={{ color: bRate >= 90 ? "var(--accent)" : "var(--warn)" }}>{bRate}%</div><div className="bcm-stat-lbl">Pass rate</div></div>
                <div className="bcm-stat"><div className="bcm-stat-val">{bRuns.length}</div><div className="bcm-stat-lbl">Runs</div></div>
              </div>
              <div style={{ display: "flex", gap: 8, marginTop: 14, paddingTop: 14, borderTop: "1px solid var(--border)" }}>
                <button className="btn-primary" style={{ flex: 1, fontSize: 11, padding: "7px 0" }} onClick={(e) => { e.stopPropagation(); onRunTest(b); }}>▶ Run Test</button>
                <button className="btn-ghost btn-sm" style={{ fontSize: 11, padding: "7px 12px", color: "var(--danger)", borderColor: "rgba(255,59,92,.3)" }} onClick={async (e) => {
                  e.stopPropagation();
                  if (!confirm(`Delete build "${b.file_name}"? Runs using this build will not be deleted.`)) return;
                  try { await api.deleteBuild(b.id); toast("Build deleted", "success"); onRefresh(); }
                  catch (err: any) { toast(err.message, "error"); }
                }}>🗑 Delete</button>
              </div>
            </div>
          );
        })}
        {builds.length === 0 && <div style={{ color: "var(--muted)", padding: 20, fontSize: 12 }}>No builds uploaded yet. Click Upload or drop a file above.</div>}
      </div>
    </>
  );
}

/* ── Settings ──────────────────────────────────────── */
function SettingsView() {
  const [s, setS] = useState<Record<string, any>>({});
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [appiumSt, setAppiumSt] = useState<string | null>(null);
  const [confSt, setConfSt] = useState<string | null>(null);
  const [settingsTab, setSettingsTab] = useState("apikeys");

  useEffect(() => { api.getSettings().then(d => { setS(d); setLoaded(true); }); }, []);

  const save = async () => {
    setBusy(true);
    try { const d = await api.saveSettings(s); setS(d); toast("Settings saved", "success"); }
    catch (e: any) { toast(e.message, "error"); }
    finally { setBusy(false); }
  };
  const upd = (k: string, v: string) => setS(p => ({ ...p, [k]: v }));

  if (!loaded) return <div className="center-screen"><div className="spinner" /></div>;

  return (
    <>
      <div className="section-head">
        <div><div className="section-title">Settings</div><div className="section-sub">API keys · integrations · device configuration</div></div>
        <button className="btn-primary" onClick={save} disabled={busy}>Save Changes</button>
      </div>

      <div className="settings-layout">
        <div className="settings-nav">
          {[["apikeys", "API Keys"], ["devices", "Devices"], ["integrations", "Integrations"]].map(([k, l]) => (
            <button key={k} className={`settings-nav-item${settingsTab === k ? " active" : ""}`} onClick={() => setSettingsTab(k)}>{l}</button>
          ))}
        </div>
        <div>
          {settingsTab === "apikeys" && (
            <>
              <div className="form-section">
                <div className="form-section-title">AI (Gemini)</div>
                <div className="form-row">
                  <div className="form-group"><div className="form-label">API Key</div><input className={`form-input${(s.ai_api_key || s.ai_key) ? " connected" : ""}`} type="password" value={s.ai_api_key || s.ai_key || ""} onChange={e => upd("ai_api_key", e.target.value)} />{(s.ai_api_key || s.ai_key) && <div className="conn-status">✓ Key configured</div>}</div>
                  <div className="form-group"><div className="form-label">Model</div><input className="form-input" value={s.ai_model || "gemini-2.5-flash"} onChange={e => upd("ai_model", e.target.value)} /></div>
                </div>
              </div>
              <div className="form-section">
                <div className="form-section-title">Confluence</div>
                <div className="form-row">
                  <div className="form-group"><div className="form-label">Base URL</div><input className={`form-input${s.confluence_url ? " connected" : ""}`} value={s.confluence_url || ""} onChange={e => upd("confluence_url", e.target.value)} placeholder="https://your-domain.atlassian.net/wiki" /></div>
                  <div className="form-group"><div className="form-label">API Token</div><input className={`form-input${s.confluence_token ? " connected" : ""}`} type="password" value={s.confluence_token || ""} onChange={e => upd("confluence_token", e.target.value)} />{confSt && <div className={`conn-status${confSt.startsWith("✓") ? "" : " err"}`}>{confSt}</div>}</div>
                  <div className="form-group"><div className="form-label">Space key (optional)</div><input className="form-input" value={s.confluence_space_key || ""} onChange={e => upd("confluence_space_key", e.target.value)} placeholder="e.g. DEV — leave blank to use first space" /></div>
                </div>
                <button className="btn-ghost btn-sm" onClick={async () => { setConfSt("Testing..."); try { const r = await api.testConfluence(); setConfSt(r.ok ? `✓ ${r.message}` : r.message); } catch (e: any) { setConfSt(e.message); } }}>Test Connection</button>
              </div>
            </>
          )}

          {settingsTab === "devices" && (
            <div className="form-section">
              <div className="form-section-title">Appium</div>
              <div className="form-row">
                <div className="form-group"><div className="form-label">Server Host</div><input className={`form-input${appiumSt?.startsWith("✓") ? " connected" : ""}`} value={s.appium_host || "127.0.0.1"} onChange={e => upd("appium_host", e.target.value)} />{appiumSt && <div className={`conn-status${appiumSt.startsWith("✓") ? "" : " err"}`}>{appiumSt}</div>}</div>
                <div className="form-group"><div className="form-label">Port</div><input className="form-input" value={s.appium_port || "4723"} onChange={e => upd("appium_port", e.target.value)} /></div>
              </div>
              <button className="btn-ghost btn-sm" onClick={async () => { setAppiumSt("Testing..."); try { const r = await api.testAppium(); setAppiumSt(r.ok ? `✓ ${r.message}` : r.message); } catch (e: any) { setAppiumSt(e.message); } }}>Test Connection</button>
            </div>
          )}

          {settingsTab === "integrations" && (
            <div className="form-section">
              <div className="form-section-title">Figma (Optional)</div>
              <div className="form-row">
                <div className="form-group"><div className="form-label">Access Token</div><input className="form-input" type="password" value={s.figma_token || ""} onChange={e => upd("figma_token", e.target.value)} placeholder="figd_..." /></div>
                <div className="form-group"><div className="form-label">File Key</div><input className="form-input" value={s.figma_file_key || ""} onChange={e => upd("figma_file_key", e.target.value)} /></div>
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
