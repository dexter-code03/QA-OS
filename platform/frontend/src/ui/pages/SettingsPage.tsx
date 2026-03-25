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
  const [mitmdumpAvailable, setMitmdumpAvailable] = useState<boolean | null>(null);

  useEffect(() => { api.getSettings().then(d => { setS(d); setLoaded(true); }); }, []);
  useEffect(() => { api.health().then(h => { setMitmdumpAvailable(h.mitmdump_available ?? null); }).catch(() => {}); }, []);

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
            <>
              <div className="form-section">
                <div className="form-section-title">Appium</div>
                <div className="form-row">
                  <div className="form-group"><div className="form-label">Server Host</div><input className={`form-input${appiumSt?.startsWith("✓") ? " connected" : ""}`} value={s.appium_host || "127.0.0.1"} onChange={e => upd("appium_host", e.target.value)} />{appiumSt && <div className={`conn-status${appiumSt.startsWith("✓") ? "" : " err"}`}>{appiumSt}</div>}</div>
                  <div className="form-group"><div className="form-label">Port</div><input className="form-input" value={s.appium_port || "4723"} onChange={e => upd("appium_port", e.target.value)} /></div>
                </div>
                <button className="btn-ghost btn-sm" onClick={async () => { setAppiumSt("Testing..."); try { const r = await api.testAppium(); setAppiumSt(r.ok ? `✓ ${r.message}` : r.message); } catch (e: any) { setAppiumSt(e.message); } }}>Test Connection</button>
              </div>

              <div className="form-section">
                <div className="form-section-title">API Traffic Logging</div>
                <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 10, lineHeight: 1.5 }}>
                  Captures HTTP/HTTPS traffic from the device during test runs.
                </div>

                <div className="form-group" style={{ marginBottom: 12 }}>
                  <div className="form-label">Capture Mode</div>
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                    {([["auto", "Auto"], ["chucker", "Chucker"], ["pulse", "Pulse"], ["logcat", "Logcat"], ["mitmproxy", "mitmproxy"]] as const).map(([val, label]) => (
                      <button key={val} className={`btn-ghost btn-sm${(s.api_capture_mode || "auto") === val ? " active" : ""}`}
                        style={{ padding: "4px 12px", fontSize: 11, borderRadius: 6, background: (s.api_capture_mode || "auto") === val ? "var(--accent)" : undefined, color: (s.api_capture_mode || "auto") === val ? "#fff" : undefined }}
                        onClick={() => upd("api_capture_mode", val)}>{label}</button>
                    ))}
                  </div>
                  <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4 }}>
                    {(s.api_capture_mode || "auto") === "auto" && "Auto-detects the best method: Chucker/Pulse → Logcat → mitmproxy."}
                    {s.api_capture_mode === "chucker" && "Reads Chucker's SQLite DB on Android. No proxy, no certs — requires Chucker in the app build."}
                    {s.api_capture_mode === "pulse" && "Reads Pulse's data store on iOS simulators. No proxy, no certs — requires Pulse in the app build."}
                    {s.api_capture_mode === "logcat" && "Reads OkHttp/Retrofit logs from adb logcat. Requires the app's logging interceptor to be enabled."}
                    {s.api_capture_mode === "mitmproxy" && "Intercepts all HTTP/HTTPS via proxy. Requires mitmdump + trusted CA cert on the device."}
                  </div>
                </div>

                <div style={{ display: "flex", flexDirection: "column", gap: 5, marginBottom: 8 }}>
                  <div style={{ fontSize: 10, fontWeight: 600, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.5px" }}>Android</div>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, paddingLeft: 4 }}>
                    <span style={{ width: 14, textAlign: "center", color: "#34d399" }}>✓</span>
                    <span>Chucker</span>
                    <span style={{ fontSize: 10, color: "var(--muted)" }}>— reads in-app HTTP inspector DB (recommended)</span>
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, paddingLeft: 4 }}>
                    <span style={{ width: 14, textAlign: "center", color: "#34d399" }}>✓</span>
                    <span>Logcat</span>
                    <span style={{ fontSize: 10, color: "var(--muted)" }}>— reads OkHttp logs (needs app logging interceptor)</span>
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, paddingLeft: 4 }}>
                    <span style={{ width: 14, textAlign: "center", color: mitmdumpAvailable ? "#34d399" : "#f87171" }}>{mitmdumpAvailable ? "✓" : "✗"}</span>
                    <span style={{ color: mitmdumpAvailable ? "var(--text)" : "var(--muted)" }}>mitmproxy</span>
                    <span style={{ fontSize: 10, color: "var(--muted)" }}>— full HTTPS interception (needs mitmdump + CA cert)</span>
                  </div>
                  <div style={{ fontSize: 10, fontWeight: 600, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.5px", marginTop: 6 }}>iOS Simulator</div>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, paddingLeft: 4 }}>
                    <span style={{ width: 14, textAlign: "center", color: "#34d399" }}>✓</span>
                    <span>Pulse</span>
                    <span style={{ fontSize: 10, color: "var(--muted)" }}>— reads in-app network logger store (recommended)</span>
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, paddingLeft: 4 }}>
                    <span style={{ width: 14, textAlign: "center", color: mitmdumpAvailable ? "#34d399" : "#f87171" }}>{mitmdumpAvailable ? "✓" : "✗"}</span>
                    <span style={{ color: mitmdumpAvailable ? "var(--text)" : "var(--muted)" }}>mitmproxy</span>
                    <span style={{ fontSize: 10, color: "var(--muted)" }}>— full HTTPS interception (needs mitmdump)</span>
                  </div>
                </div>

                <details style={{ fontSize: 11, color: "var(--muted)" }}>
                  <summary style={{ cursor: "pointer", color: "var(--text)" }}>Setup details</summary>
                  <div style={{ padding: "8px 0", lineHeight: 1.7 }}>
                    <strong>Chucker (Android, recommended)</strong> — works if the app includes <code style={{ color: "var(--text)", fontSize: 10 }}>com.github.chuckerteam.chucker</code>. Reads its SQLite DB via adb. No proxy, no certs needed.<br /><br />
                    <strong>Pulse (iOS, recommended)</strong> — works if the app includes <code style={{ color: "var(--text)", fontSize: 10 }}>kean/Pulse</code>. Reads its data store from the simulator filesystem.<br /><br />
                    <strong>Logcat</strong> — works if the app has OkHttp logging enabled. Add to OkHttp client:<br />
                    <code style={{ color: "var(--text)", fontSize: 10 }}>.addInterceptor(HttpLoggingInterceptor().setLevel(BODY))</code><br /><br />
                    <strong>mitmproxy</strong> — captures all traffic but needs HTTPS cert setup. Best on rootable emulators (Google APIs images).
                  </div>
                </details>
              </div>
            </>
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
