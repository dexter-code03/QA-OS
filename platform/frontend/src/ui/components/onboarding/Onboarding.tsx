import React, { useRef, useState } from "react";
import { api, type Build, type DeviceList, type Project } from "../../api";
import { toast } from "../../helpers";

/* ── Onboarding ────────────────────────────────────── */
export function Onboarding({ onDone }: { onDone: (p: Project) => void }) {
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
