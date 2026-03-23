import React, { useState } from "react";
import { api, Build, ModuleDef, Project, Run, SuiteDef, TestDef } from "../api";
import { ago, buildDailyDigestText, Page, statusIcon, toast } from "../helpers";

export function DashboardPage({
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
            ▶ Go to Live Run
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
