import React, { useEffect, useMemo, useState } from "react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, PieChart, Pie, Cell, LineChart, Line, Legend } from "recharts";
import {
  api,
  BlockerItem,
  CollectionHealthResponse,
  ModuleDef,
  Project,
  Run,
  StepCoverageItem,
  SuiteDef,
  SuiteHealthResponse,
  SuiteTrendItem,
  TestDef,
  TriageResponse,
} from "../api";
import { ago, toast } from "../helpers";

export function ReportsPage({ project, runs, tests, modules, suites, onRefresh }: { project: Project; runs: Run[]; tests: TestDef[]; modules: ModuleDef[]; suites: SuiteDef[]; onRefresh?: () => void }) {
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
        <div></div><div>Test</div><div>Steps</div><div>Platform</div><div>History</div><div style={{ textAlign: "right" }}>Pass Rate</div>
      </div>
      {filteredTests.length === 0 && <div style={{ padding: 20, color: "var(--muted)", fontSize: 12 }}>No test cases match the current filters.</div>}
      {filteredTests.map(t => (
        <React.Fragment key={t.id}>
          <div className="health-row" onClick={() => setExpandedId(expandedId === t.id ? null : t.id)}>
            <div style={{ width: 12, height: 12, borderRadius: "50%", background: statusColor(t.status) }} />
            <div className="h-name" title={t.name}>{t.name}</div>
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
                {t.latest_run && (
                  <span>Latest run: <strong style={{ color: t.latest_run.status === "passed" ? "var(--accent)" : t.latest_run.status === "failed" ? "var(--danger)" : "var(--text)" }}>#{t.latest_run.id} {t.latest_run.status.toUpperCase()}</strong></span>
                )}
                <span>Fail streak: <strong style={{ color: t.fail_streak > 0 ? "var(--danger)" : "var(--text)" }}>{t.fail_streak}</strong></span>
                <span>Last passed: {t.last_passed_at ? new Date(t.last_passed_at).toLocaleDateString() : "Never"}</span>
                <span>AI fixes used: {t.ai_fixes_count}</span>
              </div>
              {t.last_failed_run && (
                <>
                  <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 6, color: "var(--muted)" }}>
                    {t.latest_run && t.latest_run.status === "passed" && t.latest_run.id !== t.last_failed_run.id
                      ? <span style={{ color: "var(--accent)" }}>Latest run passed. Last failure details below (Run #{t.last_failed_run.id}):</span>
                      : <>{t.steps_ran} of {t.steps_total} steps executed — {t.steps_ran < t.steps_total
                        ? `test stopped at step ${t.steps_ran + 1}`
                        : t.last_failed_run.step_results.some(sr => sr.status === "failed")
                          ? `failed at step ${(t.last_failed_run.step_results.findIndex(sr => sr.status === "failed") + 1)}`
                          : "all steps passed"}</>
                    }
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
            <BarChart data={trendData} layout="vertical" margin={{ left: 160, right: 20 }}>
              <XAxis type="number" domain={[0, 100]} tickFormatter={v => `${v}%`} stroke="#555" fontSize={10} />
              <YAxis type="category" dataKey="test_name" width={150} tick={{ fontSize: 10, fill: "#8a8f98" }} tickFormatter={v => v.length > 32 ? v.slice(0, 30) + "…" : v} />
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
            <BarChart data={stepCov.map(s => ({ ...s, skipped: Math.max(0, s.avg_steps_total - s.avg_steps_ran) }))} layout="vertical" margin={{ left: 160, right: 20 }}>
              <XAxis type="number" stroke="#555" fontSize={10} />
              <YAxis type="category" dataKey="test_name" width={150} tick={{ fontSize: 10, fill: "#8a8f98" }} tickFormatter={v => v.length > 32 ? v.slice(0, 30) + "…" : v} />
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
