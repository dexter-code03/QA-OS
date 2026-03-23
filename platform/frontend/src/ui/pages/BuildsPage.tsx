import React, { useRef, useState } from "react";
import { api, Build, Project, Run } from "../api";
import { ago, toast } from "../helpers";

/* ── Builds ────────────────────────────────────────── */
export function BuildsPage({ project, builds, runs, onRefresh, onRunTest }: { project: Project; builds: Build[]; runs: Run[]; onRefresh: () => void; onRunTest: (b: Build) => void }) {
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
