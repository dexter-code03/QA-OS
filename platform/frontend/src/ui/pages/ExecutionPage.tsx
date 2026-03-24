import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, Build, DataSet, DeviceList, ModuleDef, Project, Run, SuiteDef, TapDiagnosisOut, TestDef } from "../api";
import {
  toast,
  stepsForPlatform,
  pickScreenRecorderMime,
  replayExtFromRecorderMime,
  type ExecutionSetupPreset,
} from "../helpers";
import { XmlElementTree, simplifyXmlForAI } from "../XmlElementTree";

const WS_BASE_DELAY = 1000;
const WS_MAX_DELAY = 30_000;
const WS_MAX_RETRIES = 10;

/* ── Execution ─────────────────────────────────────── */
export function ExecutionPage({
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
  const [dataSetId, setDataSetId] = useState<number | null>(null);
  const [projectDataSets, setProjectDataSets] = useState<DataSet[]>([]);
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
  const wsRetryRef = useRef(0);
  const wsDelayRef = useRef(WS_BASE_DELAY);
  const wsConnectGenRef = useRef(0);
  const lastWsEventSeqRef = useRef(0);
  const runTerminalRef = useRef(false);
  const [wsLive, setWsLive] = useState(true);
  const [wsReconnectFailed, setWsReconnectFailed] = useState(false);

  const bfp = useMemo(() => builds.filter(b => b.platform === platform), [builds, platform]);
  const devList = platform === "android" ? devices.android : devices.ios_simulators;

  const testsInSuite = selectedSuiteId ? tests.filter(t => t.suite_id === selectedSuiteId) : [];
  const testsInCollection = selectedCollectionId ? tests.filter(t => { const s = suites.find(x => x.id === t.suite_id); return s && s.module_id === selectedCollectionId; }) : [];
  const batchTests = runMode === "suite" ? testsInSuite : runMode === "collection" ? testsInCollection : [];

  useEffect(() => {
    api.listDataSets(project.id).then(setProjectDataSets).catch(() => {});
  }, [project.id]);

  const selectedDataSet = useMemo(() => projectDataSets.find(ds => ds.id === dataSetId) ?? null, [projectDataSets, dataSetId]);

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
      setWsReconnectFailed(false);
      return;
    }
    setRun(null);
    setStepResults([]);
    setSelShot(null);
    setLiveXml(null);
    setLiveXmlName(null);
    setWsLive(true);
    setWsReconnectFailed(false);
    wsRetryRef.current = 0;
    wsDelayRef.current = WS_BASE_DELAY;
    lastWsEventSeqRef.current = 0;
    loadRun(activeRunId);
    wsRef.current?.close();
    if (wsReconnectTimerRef.current) {
      clearTimeout(wsReconnectTimerRef.current);
      wsReconnectTimerRef.current = null;
    }
    let cancelled = false;
    const wsProto = location.protocol === "https:" ? "wss:" : "ws:";
    const applyWsEvent = (raw: unknown) => {
      const ev = raw as { type?: string; payload?: any; seq?: number };
      if (typeof ev.seq === "number") lastWsEventSeqRef.current = ev.seq;
      if (ev.type === "step" && ev.payload) {
        const p = ev.payload;
        setStepResults(prev => {
          const n = [...prev];
          n[p.idx] = { idx: p.idx, status: p.status, details: p.details, screenshot: p.screenshot, pageSource: p.pageSource };
          return n;
        });
        if (p.screenshot) setSelShot(p.screenshot);
        if (p.pageSource) {
          setLiveXmlName(p.pageSource);
          fetch(`/api/artifacts/${project.id}/${activeRunId}/${p.pageSource}`)
            .then(r => (r.ok ? r.text() : ""))
            .then(setLiveXml)
            .catch(() => {});
        }
      }
      if (ev.type === "finished") {
        loadRun(activeRunId);
        onRefresh();
      }
    };
    const connect = () => {
      if (cancelled) return;
      wsRef.current?.close();
      (async () => {
        const myGen = ++wsConnectGenRef.current;
        const token = await api.bootstrapAuth();
        if (cancelled || myGen !== wsConnectGenRef.current) return;
        const url = `${wsProto}//${location.host}/ws/runs/${activeRunId}${token ? `?token=${encodeURIComponent(token)}` : ""}`;
        const ws = new WebSocket(url);
        if (cancelled || myGen !== wsConnectGenRef.current) {
          try {
            ws.close();
          } catch {
            /* ignore */
          }
          return;
        }
        wsRef.current = ws;
        ws.onopen = () => {
          if (cancelled || myGen !== wsConnectGenRef.current) return;
          setWsLive(true);
          setWsReconnectFailed(false);
          wsRetryRef.current = 0;
          wsDelayRef.current = WS_BASE_DELAY;
          void (async () => {
            try {
              const { events } = await api.getRunEvents(activeRunId, lastWsEventSeqRef.current);
              if (cancelled || myGen !== wsConnectGenRef.current) return;
              for (const e of events) applyWsEvent(e);
            } catch {
              /* recovery is best-effort */
            }
          })();
        };
        ws.onmessage = msg => {
          try {
            applyWsEvent(JSON.parse(msg.data));
          } catch {
            /* ignore malformed */
          }
        };
        ws.onerror = () => {
          try {
            ws.close();
          } catch {
            /* ignore */
          }
        };
        ws.onclose = e => {
          if (cancelled || myGen !== wsConnectGenRef.current) return;
          setWsLive(false);
          if (e.code === 1000 || e.code === 1008 || runTerminalRef.current) return;
          if (wsRetryRef.current >= WS_MAX_RETRIES) {
            setWsReconnectFailed(true);
            return;
          }
          wsRetryRef.current++;
          const delay = wsDelayRef.current;
          wsDelayRef.current = Math.min(wsDelayRef.current * 2, WS_MAX_DELAY);
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
      wsConnectGenRef.current++;
      if (wsReconnectTimerRef.current) {
        clearTimeout(wsReconnectTimerRef.current);
        wsReconnectTimerRef.current = null;
      }
      wsRetryRef.current = 0;
      wsDelayRef.current = WS_BASE_DELAY;
      wsRef.current?.close();
      wsRef.current = null;
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [activeRunId, loadRun, onRefresh, project.id]);

  useEffect(() => {
    if (!activeRunId) {
      runTerminalRef.current = false;
      return;
    }
    if (!run || run.id !== activeRunId) {
      runTerminalRef.current = false;
      return;
    }
    runTerminalRef.current = ["passed", "failed", "error", "cancelled"].includes(run.status);
  }, [run, activeRunId]);

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
        const agentSummary = completed.summary as any;
        const agentTemplateSteps = agentSummary?.templateSteps || [];
        const agentDataContext = agentSummary?.dataContext || {};

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
          ...(agentTemplateSteps.length > 0 ? { template_steps: agentTemplateSteps } : {}),
          ...(Object.keys(agentDataContext).length > 0 ? { data_context: agentDataContext } : {}),
          ...(completed.data_set_id ? { data_set_id: completed.data_set_id } : {}),
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
    const noStepTests = toRun.filter(t => stepsForPlatform(t, platform).length === 0);
    if (noStepTests.length > 0) {
      const names = noStepTests.slice(0, 3).map(t => t.name).join(", ");
      toast(`No ${platform === "ios_sim" ? "iOS" : "Android"} steps for: ${names}${noStepTests.length > 3 ? ` (+${noStepTests.length - 3} more)` : ""}. Generate steps for this platform first.`, "error");
      return;
    }
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
          ...(dataSetId ? { data_set_id: dataSetId } : {}),
        });
        setStepResults([]); setSelShot(null);
        onBatchCreated(batch.id);
        toast(`${batch.source_name}: ${batch.total} tests queued`, "success");
      } else {
        const created: Run[] = [];
        for (const t of toRun) {
          const r = await api.createRun({ project_id: project.id, build_id: buildId, test_id: t.id, platform, device_target: deviceTarget, ...(dataSetId ? { data_set_id: dataSetId } : {}) });
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
  const [fixResult, setFixResult] = useState<{ analysis: string; fixed_steps: any[]; changes: any[]; fix_type?: string; data_fixes?: Record<string, string>; data_set_updated?: boolean } | null>(null);
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
      const summary = displayRun.summary as any;
      const templateSteps = summary?.templateSteps || [];
      const dataContext = summary?.dataContext || {};

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
        ...(templateSteps.length > 0 ? { template_steps: templateSteps } : {}),
        ...(Object.keys(dataContext).length > 0 ? { data_context: dataContext } : {}),
        ...(displayRun.data_set_id ? { data_set_id: displayRun.data_set_id } : {}),
      });
      setFixResult(res);
      setTapDiagnosis(res.tap_diagnosis ?? null);
      setRelatedTests(null);
      setFixBusy(false);
      if (targetTest) {
        void api.getRelatedTests(targetTest.id).then(rel => setRelatedTests(rel)).catch(() => {});
      }
      const fixLabel = res.fix_type === "data" ? "data fix" : res.fix_type === "both" ? "step + data fix" : "step fix";
      const changeCount = res.changes?.length || 0;
      let fixMsg = `AI found ${changeCount} ${fixLabel}${changeCount !== 1 ? "es" : ""}`;
      if (res.data_set_updated) fixMsg += " · DataSet updated";
      toast(fixMsg, "success");
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

${testForRun.acceptance_criteria ? `<h2>Test Description / Acceptance Criteria</h2><div class="card" style="font-size:13px;line-height:1.8;white-space:pre-wrap">${testForRun.acceptance_criteria.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</div>` : ""}

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
          <div className="section-sub">{displayRun ? `${displayRun.device_target || "default"} · ${displayRun.platform}${builds.find(b => b.id === displayRun.build_id)?.file_name ? ` · ${builds.find(b => b.id === displayRun.build_id)?.file_name}` : ""}${displayRun.data_set_id ? ` · Data Set #${displayRun.data_set_id}${displayRun.data_row_index != null ? ` (row ${displayRun.data_row_index + 1})` : ""}` : ""}` : "Select test and device to begin"}</div>
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
            <select value={dataSetId ?? ""} onChange={e => setDataSetId(e.target.value ? Number(e.target.value) : null)} style={{ maxWidth: 180 }}>
              <option value="">No data set</option>
              {projectDataSets.map(ds => <option key={ds.id} value={ds.id}>{ds.name}{ds.environment ? ` (${ds.environment})` : ""}{ds.rows.length > 0 ? ` · ${ds.rows.length} rows` : ""}</option>)}
            </select>
            {selectedDataSet && selectedDataSet.rows.length > 1 && (
              <span className="badge badge-not-run" style={{ fontSize: 10 }}>Data-driven: {selectedDataSet.rows.length} iterations</span>
            )}
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
                        <div style={{ display: "flex", gap: 8, alignItems: "center", fontWeight: 600, marginBottom: 4, color: "#a78bfa", fontFamily: "var(--sans)" }}>
                          Root Cause
                          {fixResult.fix_type && (
                            <span style={{ fontSize: 10, padding: "1px 6px", borderRadius: 4, background: fixResult.fix_type === "data" ? "rgba(34,197,94,.15)" : fixResult.fix_type === "both" ? "rgba(255,176,32,.15)" : "rgba(99,102,241,.15)", color: fixResult.fix_type === "data" ? "#22c55e" : fixResult.fix_type === "both" ? "#ffb020" : "#818cf8" }}>
                              {fixResult.fix_type === "data" ? "Data Fix" : fixResult.fix_type === "both" ? "Step + Data Fix" : "Step Fix"}
                            </span>
                          )}
                          {fixResult.data_set_updated && (
                            <span style={{ fontSize: 10, padding: "1px 6px", borderRadius: 4, background: "rgba(34,197,94,.15)", color: "#22c55e" }}>DataSet updated</span>
                          )}
                        </div>
                        {fixResult.analysis}
                        {fixResult.data_fixes && Object.keys(fixResult.data_fixes).length > 0 && (
                          <div style={{ marginTop: 8, padding: 8, background: "rgba(34,197,94,.08)", borderRadius: 4, fontSize: 11 }}>
                            <div style={{ fontWeight: 600, marginBottom: 4, color: "#22c55e" }}>Data Fixes Applied</div>
                            {Object.entries(fixResult.data_fixes).map(([k, v]) => (
                              <div key={k}><code>${"{" + k + "}"}</code> → <code>{v}</code></div>
                            ))}
                          </div>
                        )}
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
