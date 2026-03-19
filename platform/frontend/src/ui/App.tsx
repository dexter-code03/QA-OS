import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, Build, DeviceList, ModuleDef, Project, Run, SuiteDef, TestDef } from "./api";
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
function guessModule(n: string) { const p = n.split(/[_\s-]+/); return p.length >= 2 ? p[0][0].toUpperCase() + p[0].slice(1) : "General"; }
function statusDot(s: string) { return s === "passed" ? "dot-green" : s === "failed" || s === "error" ? "dot-danger" : s === "running" ? "dot-warn" : "dot-gray"; }
function statusIcon(s: string) { return s === "passed" ? "si-pass" : s === "failed" || s === "error" ? "si-fail" : s === "running" ? "si-run" : "si-skip"; }
function ago(d: string | null) { if (!d) return "—"; const m = Math.round((Date.now() - new Date(d).getTime()) / 60000); return m < 60 ? `${m}m ago` : `${Math.round(m / 60)}h ago`; }

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

  const refresh = useCallback(async () => {
    if (!project) return;
    const [b, t, r, m] = await Promise.all([api.listBuilds(project.id), api.listTests(project.id), api.listRuns(project.id), api.listModules(project.id)]);
    setBuilds(b); setTests(t); setRuns(r); setModules(m);
    const allSuites: SuiteDef[] = [];
    for (const mod of m) { try { const s = await api.listSuites(mod.id); allSuites.push(...s); } catch {} }
    setSuites(allSuites);
  }, [project]);

  useEffect(() => {
    api.health().then(() => { setOk(true); return api.getOnboarding(); }).then(o => setOnboarded(o.completed)).catch(() => setOk(false));
  }, []);

  useEffect(() => { if (onboarded) api.listProjects().then(ps => { setProjects(ps); if (ps.length > 0 && !project) setProject(ps[0]); }); }, [onboarded]);
  const loadDevices = useCallback(() => { api.listDevices().then(setDevices).catch(() => {}); }, []);
  useEffect(() => { if (project) { refresh(); loadDevices(); } }, [project, refresh, loadDevices]);

  const liveRun = runs.find(r => r.status === "running");

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
              {k === "execution" && liveRun && <span className="badge warn">1</span>}
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
          {liveRun && <div className="status-pill warn"><div className="live-dot" />Run in progress</div>}
          {!liveRun && runs.length > 0 && <div className="status-pill"><div className="live-dot" />Ready</div>}
        </div>
      </div>

      <div className="layout">
        {/* ── SIDEBAR ── */}
        <div className="sidebar">
          <div className="sidebar-section">
            <div className="sidebar-label">Recent Runs</div>
            {runs.slice(0, 5).map(r => (
              <div key={r.id} style={{ display: "flex", alignItems: "center" }}>
                <button className={`sidebar-item${activeRunId === r.id ? " active" : ""}`} style={{ flex: 1 }} onClick={() => { setActiveRunId(r.id); setPage("execution"); }}>
                  <div className={`dot ${statusDot(r.status)}`} />
                  {tests.find(t => t.id === r.test_id)?.name || `Run #${r.id}`}{r.status === "running" ? " · Live" : ""}
                </button>
                {r.status !== "running" && (
                  <button style={{ background: "none", border: "none", color: "var(--muted)", fontSize: 10, cursor: "pointer", padding: "2px 6px" }} title="Delete run" onClick={async (e) => { e.stopPropagation(); if (confirm(`Delete run #${r.id}?`)) { try { await api.deleteRun(r.id); if (activeRunId === r.id) setActiveRunId(null); refresh(); toast("Run deleted", "info"); } catch (err: any) { toast(err.message, "error"); } } }}>✕</button>
                )}
              </div>
            ))}
            {runs.length === 0 && <div style={{ padding: "4px 16px", fontSize: 11, color: "var(--muted)" }}>No runs yet</div>}
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
                    <div className="sidebar-item" style={{ fontWeight: 600, fontSize: 12, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                      <span>📁 {m.name}</span>
                      <span style={{ display: "flex", gap: 2 }}>
                        <button style={{ background: "none", border: "none", color: "var(--muted)", fontSize: 9, cursor: "pointer" }} title="Rename collection" onClick={async () => { const n = prompt("Rename collection:", m.name); if (n && n.trim()) { try { await api.renameModule(m.id, n.trim()); refresh(); toast("Renamed", "success"); } catch (e: any) { toast(e.message, "error"); } } }}>✏️</button>
                        <button style={{ background: "none", border: "none", color: "var(--muted)", fontSize: 9, cursor: "pointer" }} title="Delete collection" onClick={async () => { if (confirm(`Delete collection "${m.name}" and all its suites?`)) { try { await api.deleteModule(m.id); refresh(); toast("Deleted", "info"); } catch (e: any) { toast(e.message, "error"); } } }}>🗑</button>
                      </span>
                    </div>
                    {mSuites.map(s => {
                      const sTests = tests.filter(t => t.suite_id === s.id);
                      return (
                        <div key={s.id} style={{ paddingLeft: 16, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                          <div className="sidebar-item" style={{ fontSize: 11, color: "var(--accent2)", flex: 1 }}>📋 {s.name} ({sTests.length})</div>
                          <span style={{ display: "flex", gap: 2 }}>
                            <button style={{ background: "none", border: "none", color: "var(--muted)", fontSize: 9, cursor: "pointer" }} title="Rename Test Suite" onClick={async () => { const n = prompt("Rename suite:", s.name); if (n && n.trim()) { try { await api.renameSuite(s.id, n.trim()); refresh(); toast("Renamed", "success"); } catch (e: any) { toast(e.message, "error"); } } }}>✏️</button>
                            <button style={{ background: "none", border: "none", color: "var(--muted)", fontSize: 9, cursor: "pointer" }} title="Delete suite" onClick={async () => { if (confirm(`Delete suite "${s.name}"?`)) { try { await api.deleteSuite(s.id); refresh(); toast("Deleted", "info"); } catch (e: any) { toast(e.message, "error"); } } }}>🗑</button>
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
          {page === "dashboard" && <DashboardView project={project} tests={tests} runs={runs} builds={builds} onNav={setPage} onOpenRun={id => { setActiveRunId(id); setPage("execution"); }} />}
          {page === "execution" && <ExecutionView project={project} tests={tests} builds={builds} runs={runs} devices={devices} modules={modules} suites={suites} activeRunId={activeRunId} onRunCreated={id => { setActiveRunId(id); refresh(); }} onRefresh={refresh} />}
          {page === "library" && <LibraryView project={project} tests={tests} runs={runs} modules={modules} suites={suites} onRefresh={refresh} />}
          {page === "reports" && <ReportsView project={project} runs={runs} tests={tests} />}
          {page === "builds" && <BuildsView project={project} builds={builds} runs={runs} onRefresh={refresh} />}
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
function DashboardView({ project, tests, runs, builds, onNav, onOpenRun }: { project: Project; tests: TestDef[]; runs: Run[]; builds: Build[]; onNav: (p: Page) => void; onOpenRun: (id: number) => void }) {
  const passed = runs.filter(r => r.status === "passed").length;
  const failed = runs.filter(r => r.status === "failed" || r.status === "error").length;
  const total = runs.length;
  const rate = total > 0 ? ((passed / total) * 100).toFixed(1) : "0";
  const modules = [...new Set(tests.map(t => guessModule(t.name)))];

  const moduleStats = modules.map(m => {
    const mTests = tests.filter(t => guessModule(t.name) === m);
    const ids = new Set(mTests.map(t => t.id));
    const mRuns = runs.filter(r => r.test_id && ids.has(r.test_id));
    const mp = mRuns.filter(r => r.status === "passed").length;
    return { module: m, total: mRuns.length, passed: mp, rate: mRuns.length > 0 ? Math.round((mp / mRuns.length) * 100) : 0 };
  });

  return (
    <>
      <div className="section-head">
        <div><div className="section-title">Dashboard</div><div className="section-sub">{project.name} · overview</div></div>
        <button className="run-now-btn" onClick={() => onNav("execution")}>▶ Run Tests</button>
      </div>
      <div className="metrics-row">
        <div className="metric-card"><div className="metric-val">{rate}%</div><div className="metric-label">Pass Rate</div></div>
        <div className="metric-card blue"><div className="metric-val">{total}</div><div className="metric-label">Tests Run</div></div>
        <div className="metric-card danger"><div className="metric-val">{failed}</div><div className="metric-label">Failed</div></div>
        <div className="metric-card warn"><div className="metric-val">{tests.length}</div><div className="metric-label">Test Cases</div></div>
        <div className="metric-card purple"><div className="metric-val">{builds.length}</div><div className="metric-label">Builds</div></div>
      </div>
      <div className="runs-grid">
        <div className="panel">
          <div className="panel-header"><div className="panel-title">Recent Runs</div><div style={{ fontSize: 11, color: "var(--muted)", cursor: "pointer" }} onClick={() => onNav("execution")}>View all →</div></div>
          <div className="panel-body">
            {runs.slice(0, 5).map(r => (
              <div key={r.id} className="run-row" onClick={() => onOpenRun(r.id)}>
                <div className={`status-icon ${statusIcon(r.status)}`} />
                <div><div className="run-name">{tests.find(t => t.id === r.test_id)?.name || `Run #${r.id}`}</div><div className="run-meta">{r.device_target || "default"} · {r.platform}</div></div>
                <div className={`run-pct ${r.status === "passed" ? "pct-pass" : "pct-fail"}`}>{r.status}</div>
                <div className="run-tag">{r.platform}</div>
                <div className="run-dur">{ago(r.finished_at || r.started_at)}</div>
              </div>
            ))}
            {runs.length === 0 && <div style={{ padding: 18, color: "var(--muted)", fontSize: 12 }}>No runs yet. Create a test and start a run.</div>}
          </div>
        </div>
        <div className="panel">
          <div className="panel-header"><div className="panel-title">Pass rate by collection</div></div>
          <div className="chart-bar-group">
            {moduleStats.map(ms => (
              <div key={ms.module} className="bar-row">
                <div className="bar-label">{ms.module}</div>
                <div className="bar-track"><div className="bar-fill" style={{ width: `${ms.rate}%`, background: ms.rate >= 90 ? "var(--accent)" : ms.rate >= 70 ? "var(--warn)" : "var(--danger)" }} /></div>
                <div className="bar-val">{ms.rate}%</div>
              </div>
            ))}
            {moduleStats.length === 0 && <div style={{ color: "var(--muted)", fontSize: 12 }}>Create tests to see collection breakdown</div>}
          </div>
        </div>
      </div>
    </>
  );
}

/* ── Execution ─────────────────────────────────────── */
function ExecutionView({ project, tests, builds, runs, devices, modules, suites, activeRunId, onRunCreated, onRefresh }: { project: Project; tests: TestDef[]; builds: Build[]; runs: Run[]; devices: DeviceList; modules: ModuleDef[]; suites: SuiteDef[]; activeRunId: number | null; onRunCreated: (id: number) => void; onRefresh: () => void }) {
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

  const bfp = useMemo(() => builds.filter(b => b.platform === platform), [builds, platform]);
  const devList = platform === "android" ? devices.android : devices.ios_simulators;

  const testsInSuite = selectedSuiteId ? tests.filter(t => t.suite_id === selectedSuiteId) : [];
  const testsInCollection = selectedCollectionId ? tests.filter(t => { const s = suites.find(x => x.id === t.suite_id); return s && s.module_id === selectedCollectionId; }) : [];
  const batchTests = runMode === "suite" ? testsInSuite : runMode === "collection" ? testsInCollection : [];

  const loadRun = useCallback(async (id: number) => {
    const r = await api.getRun(id);
    setRun(r);
    const saved = (r.summary as any)?.stepResults;
    if (Array.isArray(saved) && saved.length) { setStepResults(saved); const shots = (r.artifacts as any)?.screenshots || []; if (shots.length) setSelShot(shots[shots.length - 1]); }
    return r;
  }, []);

  useEffect(() => {
    if (!activeRunId) { setRun(null); setStepResults([]); setSelShot(null); return; }
    loadRun(activeRunId);
    wsRef.current?.close();
    const ws = new WebSocket(`ws://${location.host}/ws/runs/${activeRunId}`);
    wsRef.current = ws;
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
    pollRef.current = setInterval(async () => { try { const r = await api.getRun(activeRunId); setRun(r); if (["passed", "failed", "error"].includes(r.status) && pollRef.current) clearInterval(pollRef.current); } catch {} }, 5000);
    return () => { ws.close(); if (pollRef.current) clearInterval(pollRef.current); };
  }, [activeRunId, loadRun, onRefresh]);

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
        const targetForRevert = prereqForRevert && failedIdxRevert < (prereqForRevert.steps || []).length ? prereqForRevert : currentTest;
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
      addAgentLog(`Failed at step ${failedIdx + 1}. Requesting AI fix...`);
      const prereq = currentTest.prerequisite_test_id ? testsForFix.find(t => t.id === currentTest.prerequisite_test_id) : null;
      const mergedSteps = prereq ? [...(prereq.steps || []), ...(currentTest.steps || [])] : (currentTest.steps || []);
      const targetTest = prereq && failedIdx < (prereq.steps || []).length ? prereq : currentTest;
      const prereqLen = prereq ? (prereq.steps || []).length : 0;
      let failXml = "";
      const artifacts = (completed.artifacts as any) || {};
      const pageSources = artifacts.pageSources || [];
      const screenshots = artifacts.screenshots || [];
      const failPs = stepResultsArr[failedIdx]?.pageSource || pageSources[failedIdx];
      if (failPs) {
        try { const resp = await fetch(`/api/artifacts/${completed.project_id}/${completed.id}/${failPs}`); if (resp.ok) failXml = simplifyXmlForAI(await resp.text()); } catch {}
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
          original_steps: mergedSteps,
          step_results: stepResultsArr.map((s: any) => s ? { status: s.status, details: s.details } : { status: "pending" }),
          failed_step_index: failedIdx,
          error_message: errMsg,
          page_source_xml: failXml,
          test_name: currentTest.name,
          screenshot_base64: screenshotB64,
          already_tried_fixes: [...(targetTest.fix_history || []), ...lastFailedFix],
          acceptance_criteria: targetTest.acceptance_criteria || "",
          app_context: appContextAgent,
        });
        addAgentLog(`AI fix applied: ${fixRes.changes.length} change(s). Updating test...`);
        setRun(completed);
        setStepResults(stepResultsArr);
        setFixResult(fixRes);
        setShowFixPanel(true);
        const fixedForTarget = prereq ? (failedIdx < prereqLen ? fixRes.fixed_steps.slice(0, prereqLen) : fixRes.fixed_steps.slice(prereqLen)) : fixRes.fixed_steps;
        await api.updateTest(targetTest.id, { steps: fixedForTarget });
        await api.appendFixHistory(targetTest.id, { analysis: fixRes.analysis, fixed_steps: fixedForTarget, changes: fixRes.changes, run_id: completed.id, steps_before_fix: targetTest.steps });
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
        for (let i = 0; i < toRun.length; i++) {
          if (agentPausedRef.current) break;
          setAgentStatus(`Test ${i + 1}/${toRun.length}: ${toRun[i].name}`);
          addAgentLog(`--- Test ${i + 1}/${toRun.length}: ${toRun[i].name} ---`);
          await runAgentLoop(toRun[i]);
        }
        setAgentStatus(agentPausedRef.current ? "Paused" : "Done");
        setAgentRunning(false);
        onRefresh();
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

  const rerun = async () => {
    if (!run) return; setBusy(true);
    try { const r = await api.createRun({ project_id: run.project_id, build_id: run.build_id ?? undefined, test_id: run.test_id!, platform: run.platform, device_target: run.device_target }); setStepResults([]); setSelShot(null); onRunCreated(r.id); toast(`Rerun → #${r.id}`, "success"); }
    catch (e: any) { toast(e.message, "error"); }
    finally { setBusy(false); }
  };

  // AI Fix state
  const [fixBusy, setFixBusy] = useState(false);
  const [fixResult, setFixResult] = useState<{ analysis: string; fixed_steps: any[]; changes: any[] } | null>(null);
  const [showFixPanel, setShowFixPanel] = useState(false);
  const [fixSuggestion, setFixSuggestion] = useState("");
  const [relatedTests, setRelatedTests] = useState<{ dependents: TestDef[]; similar: { test: TestDef; shared_prefix_length: number }[] } | null>(null);

  const artBase = run ? `/api/artifacts/${run.project_id}/${run.id}/` : "";
  const screenshots = ((run?.artifacts as any)?.screenshots || []) as string[];
  const pageSources = ((run?.artifacts as any)?.pageSources || []) as string[];
  const testForRun = run?.test_id ? tests.find(t => t.id === run.test_id) : null;
  const prereq = testForRun?.prerequisite_test_id ? tests.find(t => t.id === testForRun.prerequisite_test_id) : null;
  const mergedSteps = prereq ? [...(prereq.steps || []), ...(testForRun?.steps || [])] : (testForRun?.steps || []);
  const stepDefs = ((run?.summary as any)?.stepDefinitions as any[]) || mergedSteps;
  const completedSteps = stepResults.filter(s => s?.status).length;
  const passedSteps = stepResults.filter(s => s?.status === "passed").length;
  const totalSteps = stepDefs.length;
  const pct = totalSteps > 0 ? Math.round((completedSteps / totalSteps) * 100) : 0;

  const failedIdx = stepResults.findIndex(s => s?.status === "failed");
  const isFailed = run && ["failed", "error"].includes(run.status) && failedIdx >= 0;

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

  // Keep XML in sync with selected screenshot when user taps step/screenshot or run loads
  useEffect(() => {
    if (!run || !selShot || screenshots.length === 0) return;
    const idx = screenshots.indexOf(selShot);
    if (idx >= 0) loadXmlForStep(idx);
  }, [selShot, run?.id, screenshots, loadXmlForStep]);

  const aiFixRun = async () => {
    if (!run || !testForRun || failedIdx < 0) return;
    setFixBusy(true); setFixResult(null); setShowFixPanel(true);
    toast("AI is analyzing screenshot, XML, and error logs...", "info");

    // Fetch page source XML from the failed step
    let failXml = "";
    const failPs = pageSources[failedIdx] || stepResults[failedIdx]?.pageSource;
    if (failPs) {
      try { const r = await fetch(artBase + failPs); if (r.ok) failXml = simplifyXmlForAI(await r.text()); } catch {}
    }
    if (!failXml && liveXml) failXml = simplifyXmlForAI(liveXml);

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
    const errMsg = run.error_message || (typeof failDetails === "string" ? failDetails : failDetails?.error || JSON.stringify(failDetails || {}));

    const prereqStepsLen = prereq ? (prereq.steps || []).length : 0;
    const failureInPrereq = prereq && failedIdx < prereqStepsLen;
    const targetTest = failureInPrereq ? prereq : testForRun;
    const testNameWithContext = prereq
      ? `${testForRun.name} (main) · Prerequisite: ${prereq.name} · Failure at step ${failedIdx + 1}${failureInPrereq ? " (in prerequisite)" : ""}`
      : testForRun.name;
    const buildInfo = run.build_id ? builds.find(b => b.id === run.build_id) : null;
    const meta = (buildInfo?.metadata || {}) as any;
    const appContext = [meta.display_name, meta.package].filter(Boolean).join(" · ") || undefined;

    try {
      const res = await api.fixSteps({
        platform: run.platform,
        original_steps: stepDefs,
        step_results: stepResults.map(s => s ? { status: s.status, details: s.details } : { status: "pending" }),
        failed_step_index: failedIdx,
        error_message: errMsg,
        page_source_xml: failXml,
        test_name: testNameWithContext,
        screenshot_base64: screenshotB64,
        already_tried_fixes: targetTest.fix_history || [],
        acceptance_criteria: targetTest.acceptance_criteria || "",
        app_context: appContext,
      });
      setFixResult(res);
      setRelatedTests(null);
      if (targetTest) {
        try { const rel = await api.getRelatedTests(targetTest.id); setRelatedTests(rel); } catch {}
      }
      toast(`AI found ${res.changes.length} fix${res.changes.length !== 1 ? "es" : ""}`, "success");
    } catch (e: any) {
      toast(e.message, "error");
      setShowFixPanel(false);
    } finally { setFixBusy(false); }
  };

  const refineFixRun = async () => {
    if (!run || !testForRun || !fixResult || !fixSuggestion.trim()) return;
    setFixBusy(true);
    toast("AI is refining the fix with your suggestion...", "info");
    let failXml = "";
    const failPs = pageSources[failedIdx] || stepResults[failedIdx]?.pageSource;
    if (failPs) { try { const r = await fetch(artBase + failPs); if (r.ok) failXml = simplifyXmlForAI(await r.text()); } catch {} }
    if (!failXml && liveXml) failXml = simplifyXmlForAI(liveXml);
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
    const errMsg = run.error_message || (typeof failDetails === "string" ? failDetails : failDetails?.error || JSON.stringify(failDetails || {}));
    const prereqStepsLenR = prereq ? (prereq.steps || []).length : 0;
    const failureInPrereqR = prereq && failedIdx < prereqStepsLenR;
    const targetTestR = failureInPrereqR ? prereq : testForRun;
    const testNameWithContextR = prereq
      ? `${testForRun.name} (main) · Prerequisite: ${prereq.name} · Failure at step ${failedIdx + 1}${failureInPrereqR ? " (in prerequisite)" : ""}`
      : testForRun.name;
    const buildInfoR = run.build_id ? builds.find(b => b.id === run.build_id) : null;
    const metaR = (buildInfoR?.metadata || {}) as any;
    const appContextR = [metaR.display_name, metaR.package].filter(Boolean).join(" · ") || undefined;
    try {
      const res = await api.refineFix({
        platform: run.platform,
        original_steps: stepDefs,
        step_results: stepResults.map(s => s ? { status: s.status, details: s.details } : { status: "pending" }),
        failed_step_index: failedIdx,
        error_message: errMsg,
        page_source_xml: failXml,
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
      setFixSuggestion("");
      toast("Fix refined", "success");
    } catch (e: any) {
      toast(e.message, "error");
    } finally { setFixBusy(false); }
  };

  const prereqStepsLenApply = prereq ? (prereq.steps || []).length : 0;
  const failureInPrereqApply = prereq && failedIdx >= 0 && failedIdx < prereqStepsLenApply;
  const targetTestApply = failureInPrereqApply && prereq ? prereq : testForRun;
  const fixedStepsForTarget = prereq && fixResult
    ? (failureInPrereqApply ? fixResult.fixed_steps.slice(0, prereqStepsLenApply) : fixResult.fixed_steps.slice(prereqStepsLenApply))
    : (fixResult?.fixed_steps ?? []);

  const applyFix = async (mode: "update" | "new") => {
    if (!fixResult || !testForRun || !run || !targetTestApply) return;
    setBusy(true);
    try {
      if (mode === "update") {
        await api.updateTest(targetTestApply.id, { steps: fixedStepsForTarget });
        await api.appendFixHistory(targetTestApply.id, { analysis: fixResult.analysis, fixed_steps: fixedStepsForTarget, changes: fixResult.changes, run_id: run.id, steps_before_fix: targetTestApply.steps });
        toast(failureInPrereqApply ? `Prerequisite "${targetTestApply.name}" updated with fixed steps` : "Test updated with fixed steps", "success");
      } else {
        await api.createTest(project.id, { name: `${targetTestApply.name} (fixed)`, steps: fixedStepsForTarget, acceptance_criteria: targetTestApply.acceptance_criteria || null });
        toast("New test created with fixed steps", "success");
      }
      setShowFixPanel(false);
      setFixResult(null);
      setRelatedTests(null);
      onRefresh();
    } catch (e: any) { toast(e.message, "error"); }
    finally { setBusy(false); }
  };

  const applyFixAllRelated = async () => {
    if (!fixResult || !testForRun || !run) return;
    if (failureInPrereqApply) {
      await applyFix("update");
      return;
    }
    if (!relatedTests?.similar?.length) return;
    const mainFailedIdx = prereq ? failedIdx - prereqStepsLenApply : failedIdx;
    const prefixLen = Math.min(mainFailedIdx + 1, testForRun.steps.length, (prereq ? fixResult.fixed_steps.slice(prereqStepsLenApply) : fixResult.fixed_steps).length);
    if (prefixLen < 2) return;
    setBusy(true);
    try {
      const stepsToApply = prereq ? fixResult.fixed_steps.slice(prereqStepsLenApply) : fixResult.fixed_steps;
      await api.updateTest(testForRun.id, { steps: stepsToApply });
      await api.appendFixHistory(testForRun.id, { analysis: fixResult.analysis, fixed_steps: stepsToApply, changes: fixResult.changes, run_id: run.id, steps_before_fix: testForRun.steps });
      const res = await api.applyFixToRelated(testForRun.id, {
        fixed_steps: stepsToApply,
        prefix_length: prefixLen,
        original_steps: testForRun.steps,
      });
      setShowFixPanel(false);
      setFixResult(null);
      setRelatedTests(null);
      onRefresh();
      toast(`Fixed this test + ${res.updated_test_ids.length} related test${res.updated_test_ids.length !== 1 ? "s" : ""}`, "success");
    } catch (e: any) { toast(e.message, "error"); }
    finally { setBusy(false); }
  };

  const rerunWithFix = async () => {
    if (!fixResult || !run || !testForRun) return;
    await applyFix("update");
    setTimeout(async () => {
      try {
        const r = await api.createRun({ project_id: run.project_id, build_id: run.build_id ?? undefined, test_id: testForRun.id, platform: run.platform, device_target: run.device_target });
        setStepResults([]); setSelShot(null); setFixResult(null); setShowFixPanel(false);
        onRunCreated(r.id);
        toast(`Rerun with fix → #${r.id}`, "success");
      } catch (e: any) { toast(e.message, "error"); }
    }, 500);
  };

  const downloadFixReport = async () => {
    if (!fixResult || !run || !testForRun) return;
    const buildInfo = run.build_id ? builds.find(b => b.id === run.build_id) : null;
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

    const html = `<!DOCTYPE html><html><head><meta charset="UTF-8"><title>QA·OS Bug Report — Run #${run.id}</title>
<style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:'Segoe UI',system-ui,-apple-system,sans-serif;background:#080b0f;color:#e8eaed;padding:32px;line-height:1.6}h1{font-size:22px;color:#00e5a0;margin-bottom:4px}h2{font-size:16px;color:#a78bfa;margin:28px 0 12px;border-bottom:1px solid #1e2430;padding-bottom:8px}h3{font-size:13px;color:#e8eaed;margin-bottom:8px}.meta{font-size:12px;color:#8a8f98;margin-bottom:24px}.card{background:#0d1117;border:1px solid #1e2430;border-radius:8px;padding:16px;margin-bottom:16px}.label{font-size:11px;text-transform:uppercase;color:#8a8f98;margin-bottom:4px;font-weight:600;letter-spacing:.5px}.val{font-size:14px;color:#e8eaed;margin-bottom:10px}table{width:100%;border-collapse:collapse;font-size:12px}th{background:#131920;color:#8a8f98;text-transform:uppercase;font-size:10px;letter-spacing:.5px;text-align:left;padding:8px 10px;border-bottom:1px solid #1e2430}td{padding:8px 10px;border-bottom:1px solid #151a20}.analysis{background:rgba(99,102,241,.08);border:1px solid rgba(99,102,241,.3);border-radius:8px;padding:16px;font-size:13px;line-height:1.8;margin-bottom:16px}.shots-grid{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}.footer{margin-top:32px;border-top:1px solid #1e2430;padding-top:16px;font-size:11px;color:#555}</style></head><body>
<h1>🤖 QA·OS Bug Report</h1>
<div class="meta">Run #${run.id} · ${testForRun.name} · Generated ${new Date().toLocaleString()}</div>

<h2>Build & Device Info</h2>
<div class="card" style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
<div><div class="label">Project</div><div class="val">${project.name}</div>
<div class="label">Platform</div><div class="val">${run.platform === "android" ? "Android" : "iOS Simulator"}</div>
<div class="label">Device</div><div class="val">${run.device_target || "Default"}</div></div>
<div>${buildInfo ? `<div class="label">Build File</div><div class="val">${buildInfo.file_name}</div>
${meta.display_name ? `<div class="label">App Name</div><div class="val">${meta.display_name}${meta.version_name ? ` v${meta.version_name}` : ""}</div>` : ""}
${meta.package ? `<div class="label">Package</div><div class="val" style="font-family:monospace;font-size:12px;color:#7dd3fc">${meta.package}</div>` : ""}
${meta.main_activity ? `<div class="label">Activity</div><div class="val" style="font-family:monospace;font-size:11px;color:#7dd3fc">${meta.main_activity}</div>` : ""}` : `<div class="label">Build</div><div class="val" style="color:#8a8f98">No build attached</div>`}</div>
</div>

<h2>Run Summary</h2>
<div class="card" style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;text-align:center">
<div><div style="font-size:24px;font-weight:700;color:#ff3b5c">${run.status.toUpperCase()}</div><div class="label">Status</div></div>
<div><div style="font-size:24px;font-weight:700">${completedSteps}/${totalSteps}</div><div class="label">Steps Done</div></div>
<div><div style="font-size:24px;font-weight:700;color:#00e5a0">${passedSteps}</div><div class="label">Passed</div></div>
<div><div style="font-size:24px;font-weight:700;color:#ff3b5c">${failedIdx >= 0 ? 1 : 0}</div><div class="label">Failed</div></div>
</div>

${run.error_message ? `<h2>Error Message</h2><div class="card" style="border-color:rgba(255,59,92,.3);color:#ff3b5c;font-family:monospace;font-size:12px;white-space:pre-wrap">${run.error_message}</div>` : ""}

${(targetTestApply?.fix_history?.length ?? 0) > 0 ? `<h2>Previously Tried Fixes (${targetTestApply!.fix_history!.length})</h2><div class="card">${(targetTestApply!.fix_history as any[]).map((h: any, i: number) => "<div style=\"margin-bottom:12px;padding:12px;border:1px solid #2a2f38;border-radius:6px;background:rgba(255,59,92,.04)\"><div style=\"font-size:10px;color:#8a8f98;margin-bottom:4px\">Attempt " + (i + 1) + (h.run_id ? " · Run #" + h.run_id : "") + (h.created_at ? " · " + h.created_at : "") + "</div><div style=\"font-size:12px;color:#e8eaed;margin-bottom:6px\">" + (h.analysis || "").slice(0, 300) + ((h.analysis || "").length > 300 ? "…" : "") + "</div>" + ((h.changes || []).length > 0 ? "<div style=\"font-size:10px;color:#8a8f98\">" + h.changes.length + " change(s)</div>" : "") + "</div>").join("")}</div>` : ""}

<h2>AI Root Cause Analysis (Current Fix)</h2>
<div class="analysis">${fixResult.analysis}</div>

${fixResult.changes.length > 0 ? `<h2>Changes Applied (${fixResult.changes.length})</h2><div class="card">${changesHtml}</div>` : ""}

<h2>Test Steps</h2>
<div class="card" style="padding:0;overflow:hidden">
<table><thead><tr><th>#</th><th>Action</th><th>Selector</th><th>Value</th><th>Status</th><th>Details</th></tr></thead><tbody>${stepsHtml}</tbody></table>
</div>

${shotEntries.length > 0 ? `<h2>Screenshots</h2><div class="shots-grid">${screenshotsHtml}</div>` : ""}

<div class="footer">QA·OS Automated Bug Report · ${project.name} · Run #${run.id} · ${new Date().toISOString()}</div>
</body></html>`;

    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([html], { type: "text/html" }));
    a.download = `bug_report_run_${run.id}_${(testForRun?.name ?? "test").replace(/\s+/g, "_")}.html`;
    a.click();
    toast("Bug report downloaded", "success");
  };

  const downloadVideo = async () => {
    if (!run || screenshots.length === 0) { toast("No screenshots to generate video", "error"); return; }
    toast("Generating video from screenshots...", "info");
    const canvas = document.createElement("canvas");
    canvas.width = 540; canvas.height = 960;
    const ctx = canvas.getContext("2d")!;
    const stream = canvas.captureStream(30);
    const recorder = new MediaRecorder(stream, { mimeType: "video/webm" });
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
    const blob = new Blob(chunks, { type: "video/webm" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
    a.download = `run_${run.id}_replay.webm`; a.click();
    toast("Video downloaded", "success");
  };

  return (
    <>
      <div className="section-head" style={{ marginBottom: 14 }}>
        <div>
          <div className="section-title">{run ? `Live Execution — ${testForRun?.name || `Run #${run.id}`}` : "Execution"}</div>
          <div className="section-sub">{run ? `${run.device_target || "default"} · ${run.platform}${builds.find(b => b.id === run.build_id)?.file_name ? ` · ${builds.find(b => b.id === run.build_id)?.file_name}` : ""}` : "Select test and device to begin"}</div>
        </div>
        <div style={{ display: "flex", gap: 10 }}>
          {agentRunning && <button className="btn-ghost" style={{ fontSize: 11, padding: "7px 14px", color: "#a78bfa", borderColor: "rgba(167,139,250,.4)" }} onClick={pauseAgent}>⏸ Pause Agent</button>}
          {run && run.status === "running" && !agentRunning && <button className="btn-ghost" style={{ fontSize: 11, padding: "7px 14px" }}>⏸ Pause</button>}
          {run && run.status === "running" && !agentRunning && <button className="btn-ghost" style={{ fontSize: 11, padding: "7px 14px", color: "var(--danger)", borderColor: "rgba(255,59,92,.3)" }}>⏹ Stop</button>}
          {run && <button className="btn-ghost" style={{ fontSize: 11, padding: "7px 14px" }} onClick={() => api.exportKatalon(run.id)}>⬇ Katalon</button>}
          {run && screenshots.length > 0 && <button className="btn-ghost" style={{ fontSize: 11, padding: "7px 14px" }} onClick={downloadVideo}>🎬 Video</button>}
          {isFailed && <button className="btn-primary" style={{ fontSize: 11, padding: "7px 14px", background: "linear-gradient(135deg, #6366f1, #8b5cf6)" }} onClick={aiFixRun} disabled={fixBusy}>{fixBusy ? "⏳ AI Analyzing..." : "🤖 AI Fix"}</button>}
          {run && ["passed", "failed", "error"].includes(run.status) && <button className="run-now-btn" onClick={rerun} disabled={busy}>▶ Rerun</button>}
        </div>
      </div>

      {/* Agent Progress Panel — when no run yet (before execution opens) */}
      {(agentRunning || agentProgressLog.length > 0) && !run && (
        <div className="panel" style={{ marginBottom: 16, border: "1px solid rgba(167,139,250,.4)", background: "rgba(167,139,250,.04)" }}>
          <div className="panel-header" style={{ background: "linear-gradient(135deg, rgba(167,139,250,.15), rgba(139,92,246,.1))" }}>
            <div>
              <div className="panel-title" style={{ color: "#a78bfa" }}>🤖 Agent {agentRunning ? "Running" : "Progress"}</div>
              <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }}>{agentStatus || (agentProgressLog.length ? "Completed" : "")}</div>
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              {agentRunning && <button className="btn-ghost btn-sm" style={{ color: "#a78bfa", borderColor: "rgba(167,139,250,.5)" }} onClick={pauseAgent}>⏸ Pause</button>}
              {!agentRunning && agentProgressLog.length > 0 && <button className="btn-ghost btn-sm" style={{ fontSize: 10 }} onClick={() => setAgentProgressLog([])}>Clear log</button>}
            </div>
          </div>
          <div style={{ padding: 12, maxHeight: 180, overflow: "auto", fontFamily: "var(--mono)", fontSize: 11, lineHeight: 1.6 }}>
            {agentProgressLog.length === 0 ? <div style={{ color: "var(--muted)" }}>Waiting...</div> : agentProgressLog.map((line, i) => <div key={i} style={{ color: "var(--text)" }}>{line}</div>)}
          </div>
        </div>
      )}

      {/* Controls */}
      {!run && (
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
            <select value={buildId ?? ""} onChange={e => setBuildId(e.target.value ? Number(e.target.value) : null)}><option value="">(no build)</option>{bfp.map(b => <option key={b.id} value={b.id}>#{b.id} {b.file_name}</option>)}</select>
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
            <button className="run-now-btn" onClick={startRun} disabled={busy || (runMode === "single" ? !testId : runMode === "suite" ? !selectedSuiteId : !selectedCollectionId) || (runMode !== "single" && batchTests.length === 0)}>
              ▶ {runMode === "single" ? "Start Run" : runMode === "suite" ? `Run Suite (${batchTests.length})` : `Run Collection (${batchTests.length})`}
            </button>
          </div>
        </div>
      )}

      {run && (
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
                <div className="device-toolbar-label">Device Screen · {run.device_target || "default"}</div>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  <div className="di-pill"><div className={`dot ${run.status === "running" ? "dot-green" : "dot-gray"}`} />{run.status === "running" ? "Connected" : run.status}</div>
                  <div className="di-pill">{run.platform === "android" ? "Android" : "iOS"}</div>
                </div>
                {run.status === "running" && <div className="rec-badge"><div className="live-dot" />REC</div>}
              </div>
              <div className="device-screen">
                {selShot ? <img key={selShot} src={`${artBase}${selShot}?v=${selShot}`} alt="Device" className="device-screenshot" /> : <div className="device-placeholder">{run.status === "running" ? "Waiting for screenshot..." : "No screenshot"}</div>}
              </div>
            </div>

            {/* Progress Bar */}
            <div className="exec-progress">
              <div className="progress-header"><div className="progress-label">Step {completedSteps} of {totalSteps}</div><div className="progress-pct">{pct}% complete</div></div>
              <div className="progress-bar"><div className="progress-fill" style={{ width: `${pct}%` }} /></div>
            </div>

            {/* Live XML Panel */}
            <div className="panel">
              <div className="panel-header">
                <div className="panel-title">Page Source XML — Step {selShot && screenshots.length ? (() => { const i = screenshots.indexOf(selShot); return i >= 0 ? `S${i + 1}` : "—"; })() : "—"}</div>
                <div style={{ fontSize: 11, color: "var(--muted)" }}>Captured after each step · click a step to view</div>
              </div>
              <div style={{ padding: 12 }}>
                {liveXml ? (
                  <XmlElementTree xml={liveXml} onCopy={(msg) => toast(msg, "success")} />
                ) : (
                  <div className="xml-panel" style={{ color: "var(--muted)" }}>
                    {run.status === "running" ? "Waiting for page source..." : pageSources.length > 0 ? "Click a step to view its XML" : "No page source captured"}
                  </div>
                )}
              </div>
            </div>

            {run.error_message && <div className="error-box" style={{ marginTop: 12 }}><strong>Error:</strong> {run.error_message}</div>}

            {/* AI Fix Panel */}
            {showFixPanel && (
              <div className="panel" style={{ marginTop: 12, border: "1px solid rgba(99,102,241,.4)", maxHeight: 520, overflow: "auto" }}>
                <div className="panel-header" style={{ background: "linear-gradient(135deg, rgba(99,102,241,.1), rgba(139,92,246,.1))", position: "sticky", top: 0, zIndex: 2 }}>
                  <div>
                    <div className="panel-title" style={{ color: "#a78bfa" }}>🤖 AI Fix Analysis{agentRunning ? " (Agent auto-applied)" : ""}</div>
                    {agentRunning && <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 2 }}>Agent applied this fix and is rerunning the test</div>}
                  </div>
                  <button className="btn-ghost btn-sm" onClick={() => { setShowFixPanel(false); setFixResult(null); setFixSuggestion(""); }} style={{ fontSize: 10 }} disabled={agentRunning}>✕ Close</button>
                </div>
                {fixBusy && (
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
                      <div style={{ flex: 1, padding: 12, background: "rgba(99,102,241,.08)", borderRadius: 6, fontSize: 12, lineHeight: 1.7, color: "var(--text)" }}>
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
              <div className="steps-scroll">
                {stepDefs.map((s: any, i: number) => {
                  const res = stepResults[i];
                  const st = res?.status || "pending";
                  const isActive = i === completedSteps && run.status === "running";
                  return (
                    <div key={i} className={`step-item${isActive ? " running" : st === "failed" ? " fail" : ""}`} onClick={() => { const shot = screenshots[i] || res?.screenshot; setSelShot(shot || null); loadXmlForStep(i); }}>
                      <div className="step-num">{String(i + 1).padStart(2, "0")}</div>
                      <div>
                        <div className="step-desc">{s.type}{s.selector ? ` → ${s.selector.value}` : ""}{s.text ? ` "${s.text}"` : ""}</div>
                        <div className="step-reason">{st === "passed" ? "Completed" : st === "failed" ? (typeof res?.details === "string" ? res.details : res?.details?.error || "Failed") : isActive ? "Executing..." : "Pending"}</div>
                      </div>
                      <div className="step-icon">{st === "passed" ? "✅" : st === "failed" ? "❌" : isActive ? "⏳" : <span style={{ color: "var(--muted)" }}>○</span>}</div>
                    </div>
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

            {/* Analytics Events */}
            <div className="panel">
              <div className="panel-header">
                <div className="panel-title">Analytics Events</div>
                <div style={{ fontSize: 11, color: "var(--muted)" }}>assertion tracking</div>
              </div>
              <div>
                {(() => {
                  const events = (run?.summary as any)?.events || [];
                  const assertSteps = stepDefs.map((s: any, i: number) => ({ s, i })).filter(({ s }) => s.type === "assertText" || s.type === "assertVisible" || s.type?.includes("analytics") || s.type?.includes("assert"));
                  if (events.length > 0) {
                    return events.map((ev: any, i: number) => (
                      <div key={i} className="event-row" style={{ padding: "8px 14px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", gap: 10, fontSize: 11 }}>
                        <div style={{ fontSize: 10, color: "var(--muted)", minWidth: 28 }}>S{ev.step + 1}</div>
                        <div style={{ background: "rgba(99,102,241,.15)", color: "#a78bfa", fontSize: 9, padding: "1px 5px", borderRadius: 3 }}>{ev.type}</div>
                        <div style={{ color: "var(--accent2)", flex: 1, fontFamily: "var(--mono)" }}>{ev.name}</div>
                        {ev.expected && <div style={{ color: "var(--muted)", fontSize: 10 }}>expect: {ev.expected}</div>}
                        {ev.actual && <div style={{ color: "var(--muted)", fontSize: 10 }}>got: {ev.actual}</div>}
                        {ev.status === "passed" && <div style={{ background: "rgba(0,229,160,.15)", color: "var(--accent)", fontSize: 10, padding: "1px 6px", borderRadius: 3 }}>✓ fired</div>}
                        {ev.status === "failed" && <div style={{ background: "rgba(255,59,92,.15)", color: "var(--danger)", fontSize: 10, padding: "1px 6px", borderRadius: 3 }}>✕ {ev.error || "failed"}</div>}
                      </div>
                    ));
                  }
                  if (assertSteps.length > 0) {
                    return assertSteps.map(({ s, i }: any) => {
                      const res = stepResults[i];
                      const st = res?.status;
                      const evName = s.selector?.value || s.expect || s.text || `step_${i}`;
                      return (
                        <div key={i} className="event-row" style={{ padding: "8px 14px", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", gap: 10, fontSize: 11 }}>
                          <div style={{ fontSize: 10, color: "var(--muted)", minWidth: 28 }}>S{i + 1}</div>
                          <div style={{ background: "rgba(99,102,241,.15)", color: "#a78bfa", fontSize: 9, padding: "1px 5px", borderRadius: 3 }}>{s.type}</div>
                          <div style={{ color: "var(--accent2)", flex: 1, fontFamily: "var(--mono)" }}>{evName}</div>
                          {st === "passed" && <div style={{ background: "rgba(0,229,160,.15)", color: "var(--accent)", fontSize: 10, padding: "1px 6px", borderRadius: 3 }}>✓ fired</div>}
                          {st === "failed" && <div style={{ background: "rgba(255,59,92,.15)", color: "var(--danger)", fontSize: 10, padding: "1px 6px", borderRadius: 3 }}>✕ failed</div>}
                          {!st && <div style={{ background: "rgba(255,176,32,.15)", color: "var(--warn)", fontSize: 10, padding: "1px 6px", borderRadius: 3 }}>pending</div>}
                        </div>
                      );
                    });
                  }
                  return <div style={{ padding: 14, color: "var(--muted)", fontSize: 11 }}>No analytics assertions in this test. Add assertText or assertVisible steps to track events.</div>;
                })()}
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

/* ── Library ───────────────────────────────────────── */
const STEP_TYPES = ["tap", "type", "wait", "waitForVisible", "assertText", "assertVisible", "takeScreenshot", "swipe", "keyboardAction", "hideKeyboard"];
const NO_SELECTOR_TYPES = new Set(["wait", "hideKeyboard", "takeScreenshot"]);

function StepBuilder({ steps, setSteps, stepStatuses }: { steps: any[]; setSteps: (s: any[]) => void; stepStatuses?: string[] }) {
  return (
    <div className="step-builder">
      {steps.map((s, i) => {
        const st = stepStatuses?.[i];
        return (
        <div key={i} className="step-builder-row">
          <span style={{ fontSize: 10, color: "var(--muted)", minWidth: 20 }}>{i + 1}</span>
          {st && <span style={{ fontSize: 9, fontWeight: 700, minWidth: 42, color: st === "passed" ? "#00e5a0" : st === "failed" ? "#ff3b5c" : "#8a8f98" }}>{st.toUpperCase()}</span>}
          <select value={s.type} onChange={e => { const n = [...steps]; n[i] = { ...n[i], type: e.target.value }; setSteps(n); }}>
            {STEP_TYPES.map(t => <option key={t}>{t}</option>)}
          </select>
          {!NO_SELECTOR_TYPES.has(s.type) && s.type !== "keyboardAction" && (
            <>
              <select value={s.selector?.using || "accessibilityId"} onChange={e => { const n = [...steps]; n[i] = { ...n[i], selector: { ...n[i].selector, using: e.target.value } }; setSteps(n); }} style={{ width: 110 }}>
                {["accessibilityId", "id", "xpath", "className"].map(u => <option key={u}>{u}</option>)}
              </select>
              <input value={s.selector?.value || ""} onChange={e => { const n = [...steps]; n[i] = { ...n[i], selector: { ...n[i].selector, value: e.target.value } }; setSteps(n); }} placeholder="selector value" style={{ flex: 1 }} />
            </>
          )}
          {s.type === "keyboardAction" && (
            <select value={s.text || "return"} onChange={e => { const n = [...steps]; n[i] = { ...n[i], text: e.target.value }; setSteps(n); }} style={{ width: 100 }}>
              {["return", "done", "go", "next", "search", "send"].map(k => <option key={k}>{k}</option>)}
            </select>
          )}
          {s.type === "type" && <input value={s.text || ""} onChange={e => { const n = [...steps]; n[i] = { ...n[i], text: e.target.value }; setSteps(n); }} placeholder="text to type" style={{ flex: 1 }} />}
          {s.type === "assertText" && <input value={s.expect || ""} onChange={e => { const n = [...steps]; n[i] = { ...n[i], expect: e.target.value }; setSteps(n); }} placeholder="expected text" style={{ flex: 1 }} />}
          {s.type === "swipe" && (
            <select value={s.text || "up"} onChange={e => { const n = [...steps]; n[i] = { ...n[i], text: e.target.value }; setSteps(n); }}>
              {["up", "down", "left", "right"].map(d => <option key={d}>{d}</option>)}
            </select>
          )}
          {(s.type === "wait" || s.type === "waitForVisible") && <input type="number" value={s.ms || 1000} onChange={e => { const n = [...steps]; n[i] = { ...n[i], ms: Number(e.target.value) }; setSteps(n); }} placeholder="ms" style={{ width: 70 }} />}
          <button className="btn-ghost btn-sm" onClick={() => setSteps(steps.filter((_, j) => j !== i))} title="Remove step">✕</button>
        </div>
      );})}
      <button className="btn-ghost btn-sm" style={{ marginTop: 6 }} onClick={() => setSteps([...steps, { type: "tap", selector: { using: "accessibilityId", value: "" } }])}>+ Add Step</button>
    </div>
  );
}

function LibraryView({ project, tests, runs, modules, suites, onRefresh }: { project: Project; tests: TestDef[]; runs: Run[]; modules: ModuleDef[]; suites: SuiteDef[]; onRefresh: () => void }) {
  const [busy, setBusy] = useState(false);
  const [platform, setPlatform] = useState<"android" | "ios_sim">("android");

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
  const [editSteps, setEditSteps] = useState<any[]>([]);
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

  // Filters for test table: null = all suites, [] = none, [id,...] = these suites
  const [filterCollectionId, setFilterCollectionId] = useState<number | null>(null);
  const [filterSuiteIds, setFilterSuiteIds] = useState<number[] | null>(null);

  // Related tests when editing (for suggestion banner)
  const [editRelated, setEditRelated] = useState<{ dependents: TestDef[]; similar: { test: TestDef; shared_prefix_length: number }[] } | null>(null);

  const aiGenerate = async () => {
    if (!aiPrompt.trim()) { toast("Describe the test", "error"); return; }
    setBusy(true); setAiStatus("Generating...");
    let xml = "";
    try { const ps = await api.capturePageSource(); if (ps.ok) xml = ps.xml; } catch {}
    try { const res = await api.generateSteps(platform, aiPrompt, xml); setNewSteps(res.steps); setNewAcceptanceCriteria(prev => prev || aiPrompt); setAiStatus(`Generated ${res.steps.length} steps`); toast(`AI generated ${res.steps.length} steps`, "success"); }
    catch (e: any) { setAiStatus(""); toast(e.message, "error"); }
    finally { setBusy(false); }
  };

  const saveNew = async () => {
    if (!newName.trim()) { toast("Enter test name", "error"); return; }
    if (!newSteps.length) { toast("Add steps", "error"); return; }
    setBusy(true);
    try { await api.createTest(project.id, { name: newName.trim(), steps: newSteps, suite_id: newSuiteId, prerequisite_test_id: newPrerequisiteId, acceptance_criteria: newAcceptanceCriteria.trim() || null }); toast("Test saved", "success"); setNewName(""); setNewSteps([]); setNewPrerequisiteId(null); setNewAcceptanceCriteria(""); setShowCreate(false); onRefresh(); }
    catch (e: any) { toast(e.message, "error"); }
    finally { setBusy(false); }
  };

  const openEdit = (t: TestDef) => {
    setEditId(t.id); setEditName(t.name); setEditSteps([...t.steps]); setEditSuiteId(t.suite_id); setEditPrerequisiteId(t.prerequisite_test_id ?? null); setEditAcceptanceCriteria(t.acceptance_criteria ?? ""); setAiEditPrompt(""); setAiEditStatus("");
    setEditRelated(null);
    api.getRelatedTests(t.id).then(setEditRelated).catch(() => {});
  };
  const cancelEdit = () => { setEditId(null); setEditRelated(null); };

  const saveEdit = async () => {
    if (!editId || !editName.trim()) return;
    setBusy(true);
    try { await api.updateTest(editId, { name: editName, steps: editSteps, suite_id: editSuiteId, prerequisite_test_id: editPrerequisiteId, acceptance_criteria: editAcceptanceCriteria.trim() || null }); toast("Test updated", "success"); setEditId(null); onRefresh(); }
    catch (e: any) { toast(e.message, "error"); }
    finally { setBusy(false); }
  };

  const aiEditRun = async () => {
    if (!aiEditPrompt.trim()) { toast("Describe the change", "error"); return; }
    setBusy(true); setAiEditStatus("AI editing...");
    try { const res = await api.editSteps(platform, editSteps, aiEditPrompt); setEditSteps(res.steps); setAiEditStatus(res.summary || `Applied — ${res.steps.length} steps`); toast("AI applied edits", "success"); }
    catch (e: any) { setAiEditStatus(""); toast(e.message, "error"); }
    finally { setBusy(false); }
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
    setBusy(true); setGenSuiteStatus("Capturing page source...");
    let xml = "";
    try { const ps = await api.capturePageSource(); if (ps.ok) xml = ps.xml; } catch {}
    setGenSuiteStatus("AI generating test cases...");
    try {
      const res = await api.generateSuite(platform, genSuitePrompt, project.id, genSuiteTargetId, xml);
      setGenSuiteStatus(`Created ${res.created} test cases`);
      toast(`Generated ${res.created} test cases`, "success");
      setGenSuitePrompt("");
      onRefresh();
    } catch (e: any) { setGenSuiteStatus(""); toast(e.message, "error"); }
    finally { setBusy(false); }
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
    if (lastRunForTest) {
      const prereq = editTest.prerequisite_test_id ? tests.find(t => t.id === editTest!.prerequisite_test_id) : null;
      const prereqLen = prereq ? (prereq.steps || []).length : 0;
      return stepResults.slice(prereqLen).map((s: any) => s?.status).filter(Boolean);
    }
    return stepResults.slice(0, editTest.steps?.length ?? 0).map((s: any) => s?.status).filter(Boolean);
  })();

  const undoLastFix = async () => {
    if (!editId) return;
    setBusy(true);
    try {
      const res = await api.undoLastFix(editId);
      setEditSteps(res.steps);
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
  });

  return (
    <>
      <div className="section-head">
        <div><div className="section-title">Test Library</div><div className="section-sub">{tests.length} test cases · {modules.length} collections · {suites.length} suites</div></div>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn-ghost btn-sm" onClick={() => setShowGenerateSuite(!showGenerateSuite)}>{showGenerateSuite ? "Close" : "✨ Generate Suite"}</button>
          <button className="btn-ghost btn-sm" onClick={() => setShowCreate(!showCreate)}>{showCreate ? "Close" : "+ New Test"}</button>
        </div>
      </div>

      {/* Generate Test Suite (bulk) */}
      {showGenerateSuite && (
        <div className="panel" style={{ padding: 18, marginBottom: 16, border: "1px solid rgba(99,102,241,.3)" }}>
          <div style={{ fontFamily: "var(--sans)", fontWeight: 600, marginBottom: 14 }}>✨ Generate Test Suite</div>
          <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 12 }}>Same inputs as single test: platform, prompt, optional page source. AI generates multiple test cases for the selected suite.</div>
          <div style={{ display: "flex", gap: 10, marginBottom: 12, flexWrap: "wrap" }}>
            <select value={platform} onChange={e => setPlatform(e.target.value as any)}><option value="android">Android</option><option value="ios_sim">iOS</option></select>
            <select value={genSuiteTargetId ?? ""} onChange={e => setGenSuiteTargetId(e.target.value ? Number(e.target.value) : null)} style={{ fontSize: 11, minWidth: 200 }}>
              <option value="">Select Test Suite</option>
              {modules.map(m => suites.filter(s => s.module_id === m.id).map(s => <option key={s.id} value={s.id}>{m.name} / {s.name}</option>))}
            </select>
          </div>
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
          </div>
          {aiStatus && <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 8 }}>{aiStatus}</div>}
          <input value={newName} onChange={e => setNewName(e.target.value)} placeholder="Test name" className="form-input" style={{ width: "100%", marginBottom: 10 }} />
          <div style={{ marginBottom: 10 }}>
            <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 4, color: "var(--muted)" }}>Acceptance criteria (source of truth for AI Fix)</div>
            <textarea value={newAcceptanceCriteria} onChange={e => setNewAcceptanceCriteria(e.target.value)} placeholder="What this test must validate. e.g. Login: Email+Password must appear; fail if password field absent" rows={2} className="form-input" style={{ width: "100%", fontSize: 11 }} />
          </div>
          <StepBuilder steps={newSteps} setSteps={setNewSteps} />
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
          <div style={{ display: "flex", gap: 8, marginBottom: 12, flexWrap: "wrap", alignItems: "center" }}>
            <select value={platform} onChange={e => setPlatform(e.target.value as any)} style={{ fontSize: 11 }}><option value="android">Android</option><option value="ios_sim">iOS</option></select>
            <input value={aiEditPrompt} onChange={e => setAiEditPrompt(e.target.value)} className="form-input" placeholder="AI instruction: e.g. 'add login step before step 3'" style={{ flex: 1 }} />
            <button className="btn-primary btn-sm" style={{ background: "linear-gradient(135deg, #6366f1, #8b5cf6)" }} onClick={aiEditRun} disabled={busy}>🤖 AI Edit</button>
          </div>
          {aiEditStatus && <div style={{ fontSize: 11, color: "var(--accent)", marginBottom: 8 }}>{aiEditStatus}</div>}
          <StepBuilder steps={editSteps} setSteps={setEditSteps} stepStatuses={editStepStatuses} />
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
        {(filterCollectionId || filterSuiteIds !== null) && (
          <button className="btn-ghost btn-sm" style={{ fontSize: 10, marginLeft: "auto" }} onClick={() => { setFilterCollectionId(null); setFilterSuiteIds(null); }}>Clear</button>
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
                  <td style={{ fontSize: 11, color: "var(--muted)" }}>{t.steps.length}</td>
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
    </>
  );
}

/* ── Reports ───────────────────────────────────────── */
function ReportsView({ project, runs, tests }: { project: Project; runs: Run[]; tests: TestDef[] }) {
  const passed = runs.filter(r => r.status === "passed").length;
  const failed = runs.filter(r => r.status === "failed" || r.status === "error").length;
  const total = runs.length;
  const rate = total > 0 ? ((passed / total) * 100).toFixed(1) : "0";
  const mods = [...new Set(tests.map(t => guessModule(t.name)))];
  const moduleStats = mods.map(m => { const mT = tests.filter(t => guessModule(t.name) === m); const ids = new Set(mT.map(t => t.id)); const mR = runs.filter(r => r.test_id && ids.has(r.test_id)); const mp = mR.filter(r => r.status === "passed").length; return { module: m, tests: mT, runs: mR, total: mR.length, passed: mp, failed: mR.filter(r => r.status === "failed" || r.status === "error").length, rate: mR.length > 0 ? Math.round((mp / mR.length) * 100) : 0 }; });

  const exportModuleReport = async (mod: typeof moduleStats[0]) => {
    const shotEntries: { runId: number; testName: string; shots: { idx: number; status: string; b64: string }[] }[] = [];
    for (const r of mod.runs.slice(0, 10)) {
      const arts = (r.artifacts as any)?.screenshots || [];
      const summary = (r.summary as any)?.stepResults || [];
      const runShots: typeof shotEntries[0]["shots"] = [];
      for (let i = 0; i < Math.min(arts.length, 4); i++) {
        try {
          const resp = await fetch(`/api/artifacts/${r.project_id}/${r.id}/${arts[i]}`);
          if (resp.ok) { const blob = await resp.blob(); const b64: string = await new Promise(res => { const rd = new FileReader(); rd.onloadend = () => res(rd.result as string); rd.readAsDataURL(blob); }); runShots.push({ idx: i, status: summary[i]?.status || "unknown", b64 }); }
        } catch {}
      }
      shotEntries.push({ runId: r.id, testName: tests.find(t => t.id === r.test_id)?.name || `Run #${r.id}`, shots: runShots });
    }

    const runsHtml = mod.runs.map(r => {
      const t = tests.find(t => t.id === r.test_id);
      const bg = r.status === "passed" ? "#0a2a1a" : r.status === "failed" || r.status === "error" ? "#2a0a0f" : "#151a20";
      const color = r.status === "passed" ? "#00e5a0" : r.status === "failed" ? "#ff3b5c" : "#8a8f98";
      return `<tr style="background:${bg}"><td>#${r.id}</td><td>${t?.name || "—"}</td><td style="color:${color};font-weight:700">${r.status.toUpperCase()}</td><td>${r.platform}</td><td>${r.device_target || "default"}</td><td style="font-size:11px;color:#8a8f98">${r.error_message || ""}</td></tr>`;
    }).join("");

    const failedRunsDetail = mod.runs.filter(r => r.status === "failed" || r.status === "error").map(r => {
      const t = tests.find(t => t.id === r.test_id);
      const steps = (r.summary as any)?.stepResults || [];
      const failIdx = steps.findIndex((s: any) => s?.status === "failed");
      const stepsHtml = (t?.steps || []).map((s: any, i: number) => {
        const sr = steps[i]; const st = sr?.status || "pending";
        const c = st === "passed" ? "#00e5a0" : st === "failed" ? "#ff3b5c" : "#8a8f98";
        return `<div style="padding:4px 8px;border-bottom:1px solid #1e2430;font-size:11px;display:flex;gap:8px"><span style="color:${c};min-width:24px;font-weight:700">${i + 1}</span><span>${s.type}</span><span style="color:#7dd3fc;font-family:monospace;font-size:10px">${s.selector ? `${s.selector.using}=${s.selector.value}` : ""}</span>${st === "failed" ? `<span style="color:#ff3b5c;margin-left:auto">${typeof sr?.details === "string" ? sr.details : sr?.details?.error || "Failed"}</span>` : ""}</div>`;
      }).join("");

      const entry = shotEntries.find(e => e.runId === r.id);
      const shotsHtml = entry?.shots.map(s => `<img src="${s.b64}" style="width:120px;border-radius:6px;border:2px solid ${s.status === "failed" ? "#ff3b5c" : "#00e5a0"}" />`).join("") || "";

      return `<div style="margin-bottom:20px;border:1px solid rgba(255,59,92,.3);border-radius:8px;overflow:hidden"><div style="background:rgba(255,59,92,.08);padding:12px;font-weight:600">#${r.id} — ${t?.name || "Unknown"} — FAILED at step ${failIdx >= 0 ? failIdx + 1 : "?"}</div><div style="padding:12px">${r.error_message ? `<div style="background:rgba(255,59,92,.1);padding:10px;border-radius:6px;font-family:monospace;font-size:11px;color:#ff3b5c;margin-bottom:12px">${r.error_message}</div>` : ""}<div style="border:1px solid #1e2430;border-radius:6px;margin-bottom:12px">${stepsHtml}</div>${shotsHtml ? `<div style="display:flex;gap:8px;flex-wrap:wrap">${shotsHtml}</div>` : ""}</div></div>`;
    }).join("");

    const html = `<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Collection Report — ${mod.module}</title>
<style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:'Segoe UI',system-ui,sans-serif;background:#080b0f;color:#e8eaed;padding:32px;line-height:1.6}h1{font-size:22px;color:#00e5a0;margin-bottom:4px}h2{font-size:16px;color:#a78bfa;margin:28px 0 12px;border-bottom:1px solid #1e2430;padding-bottom:8px}.meta{font-size:12px;color:#8a8f98;margin-bottom:24px}.card{background:#0d1117;border:1px solid #1e2430;border-radius:8px;padding:16px;margin-bottom:16px}table{width:100%;border-collapse:collapse;font-size:12px}th{background:#131920;color:#8a8f98;text-transform:uppercase;font-size:10px;text-align:left;padding:8px 10px;border-bottom:1px solid #1e2430}td{padding:8px 10px;border-bottom:1px solid #151a20}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;text-align:center;margin-bottom:20px}.stat-val{font-size:28px;font-weight:700}.stat-lbl{font-size:10px;text-transform:uppercase;color:#8a8f98;margin-top:4px}</style></head><body>
<h1>📁 Collection Report — ${mod.module}</h1>
<div class="meta">${project.name} · Generated ${new Date().toLocaleString()}</div>
<div class="stats"><div><div class="stat-val" style="color:${mod.rate >= 90 ? "#00e5a0" : mod.rate >= 70 ? "#ffb020" : "#ff3b5c"}">${mod.rate}%</div><div class="stat-lbl">Pass Rate</div></div><div><div class="stat-val">${mod.total}</div><div class="stat-lbl">Total Runs</div></div><div><div class="stat-val" style="color:#00e5a0">${mod.passed}</div><div class="stat-lbl">Passed</div></div><div><div class="stat-val" style="color:#ff3b5c">${mod.failed}</div><div class="stat-lbl">Failed</div></div></div>
<h2>All Runs (${mod.total})</h2>
<div class="card" style="padding:0;overflow:hidden"><table><thead><tr><th>ID</th><th>Test</th><th>Status</th><th>Platform</th><th>Device</th><th>Error</th></tr></thead><tbody>${runsHtml}</tbody></table></div>
${failedRunsDetail ? `<h2>Failed Runs — Detail</h2>${failedRunsDetail}` : ""}
<div style="margin-top:32px;border-top:1px solid #1e2430;padding-top:16px;font-size:11px;color:#555">QA·OS Collection Report · ${project.name} · ${mod.module} · ${new Date().toISOString()}</div>
</body></html>`;

    const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([html], { type: "text/html" })); a.download = `collection_report_${mod.module}.html`; a.click();
  };

  const exportAll = () => {
    const rows = runs.map(r => `<tr><td>#${r.id}</td><td>${tests.find(t => t.id === r.test_id)?.name || "—"}</td><td style="color:${r.status === "passed" ? "#00e5a0" : "#ff3b5c"};font-weight:700">${r.status}</td><td>${r.platform}</td><td>${r.device_target || "default"}</td><td>${r.finished_at || "—"}</td><td style="font-size:11px;color:#8a8f98">${r.error_message || ""}</td></tr>`).join("");
    const html = `<!DOCTYPE html><html><head><title>QA Report - ${project.name}</title><style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:system-ui;padding:32px;background:#080b0f;color:#e8eaed}table{border-collapse:collapse;width:100%;font-size:12px}th,td{border:1px solid #1e2430;padding:8px;text-align:left}th{background:#131920;color:#8a8f98;text-transform:uppercase;font-size:10px}h1{color:#00e5a0;margin-bottom:12px}</style></head><body><h1>${project.name} — Full Test Report</h1><p style="color:#8a8f98;margin-bottom:20px">Generated: ${new Date().toLocaleString()} | Total: ${total} | Passed: ${passed} | Failed: ${failed} | Pass Rate: ${rate}%</p><table><thead><tr><th>ID</th><th>Test</th><th>Status</th><th>Platform</th><th>Device</th><th>Finished</th><th>Error</th></tr></thead><tbody>${rows}</tbody></table></body></html>`;
    const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([html], { type: "text/html" })); a.download = `qa_report_${project.name}.html`; a.click();
  };

  return (
    <>
      <div className="section-head">
        <div><div className="section-title">Reports</div><div className="section-sub">{project.name} · all runs</div></div>
        <div style={{ display: "flex", gap: 10 }}><button className="btn-ghost btn-sm" onClick={exportAll}>⬇ Full Report</button></div>
      </div>

      <div className="report-grid">
        <div className="panel" style={{ gridColumn: "span 2" }}>
          <div className="panel-header"><div className="panel-title">Summary</div></div>
          <div style={{ padding: 20, display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: 16 }}>
            <div><div style={{ fontSize: 28, fontFamily: "var(--sans)", fontWeight: 700 }}>{rate}%</div><div style={{ fontSize: 11, color: "var(--muted)", textTransform: "uppercase" }}>Pass Rate</div></div>
            <div><div style={{ fontSize: 28, fontFamily: "var(--sans)", fontWeight: 700 }}>{total}</div><div style={{ fontSize: 11, color: "var(--muted)", textTransform: "uppercase" }}>Total Runs</div></div>
            <div><div style={{ fontSize: 28, fontFamily: "var(--sans)", fontWeight: 700, color: "var(--accent)" }}>{passed}</div><div style={{ fontSize: 11, color: "var(--muted)", textTransform: "uppercase" }}>Passed</div></div>
            <div><div style={{ fontSize: 28, fontFamily: "var(--sans)", fontWeight: 700, color: "var(--danger)" }}>{failed}</div><div style={{ fontSize: 11, color: "var(--muted)", textTransform: "uppercase" }}>Failed</div></div>
          </div>
        </div>
        <div className="panel">
          <div className="panel-header"><div className="panel-title">By Collection</div></div>
          <div className="chart-bar-group">
            {moduleStats.map(ms => (
              <div key={ms.module} className="bar-row" style={{ cursor: "pointer" }} onClick={() => exportModuleReport(ms)}>
                <div className="bar-label">{ms.module}</div>
                <div className="bar-track"><div className="bar-fill" style={{ width: `${ms.rate}%`, background: ms.rate >= 90 ? "var(--accent)" : ms.rate >= 70 ? "var(--warn)" : "var(--danger)" }} /></div>
                <div className="bar-val">{ms.rate}%</div>
                <div style={{ fontSize: 10, color: "var(--accent2)", cursor: "pointer" }} title="Download collection report">📋</div>
              </div>
            ))}
            {moduleStats.length === 0 && <div style={{ color: "var(--muted)", fontSize: 12 }}>Create tests to see collection breakdown</div>}
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="panel-header"><div className="panel-title">All Runs</div></div>
        <div className="panel-body">
          {runs.map(r => (
            <div key={r.id} className="run-row">
              <div className={`status-icon ${statusIcon(r.status)}`} />
              <div><div className="run-name">{tests.find(t => t.id === r.test_id)?.name || `Run #${r.id}`}</div><div className="run-meta">{r.device_target || "default"} · {r.platform}</div></div>
              <div className={`run-pct ${r.status === "passed" ? "pct-pass" : "pct-fail"}`}>{r.status}</div>
              <div className="run-tag">{r.platform}</div>
              <div className="run-dur">{ago(r.finished_at)}</div>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}

/* ── Builds ────────────────────────────────────────── */
function BuildsView({ project, builds, runs, onRefresh }: { project: Project; builds: Build[]; runs: Run[]; onRefresh: () => void }) {
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
