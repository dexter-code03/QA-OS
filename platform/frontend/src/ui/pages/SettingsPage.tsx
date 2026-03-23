import React, { useEffect, useState } from "react";
import { api } from "../api";
import { toast } from "../helpers";

/* ── Settings ──────────────────────────────────────── */
export function SettingsPage() {
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
        <button className={`btn-primary${busy ? " btn-loading" : ""}`} onClick={save} disabled={busy}>Save Changes</button>
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
