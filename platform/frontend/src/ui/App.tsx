import React, { useCallback, useEffect, useState } from "react";
import { api, BatchRun, Build, DeviceList, ModuleDef, Project, Run, SuiteDef, TestDef } from "./api";
import { BuildsPage } from "./pages/BuildsPage";
import { DashboardPage } from "./pages/DashboardPage";
import { ReportsPage } from "./pages/ReportsPage";
import { SettingsPage } from "./pages/SettingsPage";
import { ExecutionPage } from "./pages/ExecutionPage";
import { LibraryPage } from "./pages/LibraryPage";
import { BatchRunView } from "./components/execution/BatchRunView";
import { Onboarding } from "./components/onboarding/Onboarding";
import { ErrorBoundary } from "./components/shared/ErrorBoundary";
import { ago, statusDot, toast, useToasts } from "./helpers";
import type { Page, ExecutionSetupPreset } from "./helpers";

/* ── Toast ─────────────────────────────────────────── */
function Toasts() {
  const ts = useToasts();
  return (
    <div className="toast-container">
      {ts.map(t => <div key={t.id} className={`toast toast-${t.type}`}>{t.msg}</div>)}
    </div>
  );
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

  useEffect(() => {
    function handleKeydown(e: KeyboardEvent) {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement) return;
      if (e.key === "/") {
        e.preventDefault();
        const inp = document.querySelector<HTMLInputElement>('input[placeholder*="Search"]');
        if (inp) {
          inp.focus();
          return;
        }
      }
      const pages: Page[] = ["dashboard", "execution", "library", "reports", "builds", "settings"];
      const num = parseInt(e.key);
      if (num >= 1 && num <= 6) { setPage(pages[num - 1]); return; }
    }
    window.addEventListener("keydown", handleKeydown);
    return () => window.removeEventListener("keydown", handleKeydown);
  }, []);

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
      <div className="mobile-lock-msg">
        <h2>Desktop Only</h2>
        <p>QA OS is optimized for desktop browsers. Please use a screen wider than 768px.</p>
      </div>
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
                  <span title={br.source_name} style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", minWidth: 0, fontSize: 11 }}>
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
                  <span title={tests.find(t => t.id === r.test_id)?.name || "Run #" + r.id} style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", minWidth: 0 }}>
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
              <div key={d.serial} className="sidebar-item"><div className="dot dot-green" />{d.serial}<span className="device-badge connected">Connected</span></div>
            ))}
            {devices.ios_simulators.map(d => (
              <div key={d.udid} className="sidebar-item">
                <div className={`dot ${d.state === "Booted" ? "dot-green" : "dot-gray"}`} />{d.name}
                <span className={`device-badge ${d.state === "Booted" ? "booted" : "shutdown"}`}>{d.state === "Booted" ? "Booted" : "Off"}</span>
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
          {page === "dashboard" && (<ErrorBoundary>
            <DashboardPage
              project={project}
              tests={tests}
              runs={runs}
              builds={builds}
              modules={modules}
              suites={suites}
              onNav={setPage}
              onOpenRun={id => { setActiveRunId(id); setPage("execution"); }}
            />
          </ErrorBoundary>)}
          {page === "execution" && activeBatchId && (<ErrorBoundary>
            <BatchRunView
              batchId={activeBatchId}
              onBack={() => setActiveBatchId(null)}
              onDrillIn={(runId) => { setActiveRunId(runId); setActiveBatchId(null); }}
              onRefresh={refresh}
              onBatchCreated={(id) => { setActiveBatchId(id); setActiveRunId(null); }}
            />
          </ErrorBoundary>)}
          {page === "execution" && !activeBatchId && (<ErrorBoundary>
            <ExecutionPage
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
          </ErrorBoundary>)}
          {page === "library" && (<ErrorBoundary>
            <LibraryPage
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
          </ErrorBoundary>)}
          {page === "reports" && <ErrorBoundary><ReportsPage project={project} runs={runs} tests={tests} modules={modules} suites={suites} onRefresh={refresh} /></ErrorBoundary>}
          {page === "builds" && <ErrorBoundary><BuildsPage project={project} builds={builds} runs={runs} onRefresh={refresh} onRunTest={(b) => {
                setExecutionSetupPreset({ testId: 0, buildId: b.id, platform: b.platform as Run["platform"], deviceTarget: "" });
                setActiveRunId(null); setActiveBatchId(null); setPage("execution");
              }} /></ErrorBoundary>}
          {page === "settings" && <ErrorBoundary><SettingsPage /></ErrorBoundary>}
        </div>
      </div>
    </>
  );
}
