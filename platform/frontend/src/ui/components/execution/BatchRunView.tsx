import React, { useCallback, useEffect, useRef, useState } from "react";
import { api, type BatchRun } from "../../api";
import { ago, toast } from "../../helpers";

/* ── Batch Run View ────────────────────────────────── */
const BATCH_TERMINAL = ["passed", "failed", "partial", "cancelled"];

export function BatchRunView({ batchId, onBack, onDrillIn, onRefresh, onBatchCreated }: { batchId: number; onBack: () => void; onDrillIn: (runId: number) => void; onRefresh: () => void; onBatchCreated: (id: number) => void }) {
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
