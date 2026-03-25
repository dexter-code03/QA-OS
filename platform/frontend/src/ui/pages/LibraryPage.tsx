import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { DndContext, closestCenter, DragEndEvent, KeyboardSensor, PointerSensor, useSensor, useSensors } from "@dnd-kit/core";
import { arrayMove, SortableContext, sortableKeyboardCoordinates, useSortable, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import {
  api,
  Build,
  DataFolder,
  DataSet,
  DeviceList,
  ModuleDef,
  Project,
  Run,
  SuiteDef,
  TestDef,
  ScreenEntry,
  ScreenEntryFull,
  ScreenFolder,
} from "../api";
import { XmlElementTree } from "../XmlElementTree";
import { guessModule, statusDot, stepsForPlatform, toast } from "../helpers";
import { parseCSV, detectColumns, isDetectionConfident } from "../utils/csvParser";
import type { ManualTestCase, ColumnMapping } from "../utils/csvParser";

/* ── Library ───────────────────────────────────────── */
const STEP_TYPES = [
  "tap", "doubleTap", "longPress", "tapByCoordinates",
  "type", "clear", "clearAndType",
  "wait", "waitForVisible", "waitForNotVisible", "waitForEnabled", "waitForDisabled",
  "swipe", "scroll",
  "assertText", "assertTextContains", "assertVisible", "assertNotVisible",
  "assertEnabled", "assertChecked", "assertAttribute",
  "pressKey", "keyboardAction", "hideKeyboard",
  "launchApp", "closeApp", "resetApp",
  "takeScreenshot", "getPageSource",
];
const NO_SELECTOR_TYPES = new Set([
  "wait", "hideKeyboard", "takeScreenshot", "tapByCoordinates",
  "pressKey", "launchApp", "closeApp", "resetApp", "getPageSource",
]);

const SELECTOR_STRATEGIES: Record<"android" | "ios_sim", string[]> = {
  android: ["accessibilityId", "id", "xpath", "className", "-android uiautomator"],
  ios_sim: ["accessibilityId", "id", "xpath", "className", "-ios predicate string", "-ios class chain"],
};

function SortableStepRow({ s, i, steps, setSteps, stepStatuses, selectorPickStepIndex, onPickStep, figmaNames, platform }: { s: any; i: number; steps: any[]; setSteps: (s: any[]) => void; stepStatuses?: string[]; selectorPickStepIndex?: number | null; onPickStep?: (idx: number) => void; figmaNames?: string[]; platform?: "android" | "ios_sim" }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: `step-${i}` });
  const st = stepStatuses?.[i];
  const pf = platform ?? "android";
  const selectorOptions = SELECTOR_STRATEGIES[pf];
  const style = { transform: CSS.Transform.toString(transform), transition, opacity: isDragging ? 0.5 : 1 };
  const update = (fn: (n: any[]) => void) => { const n = [...steps]; fn(n); setSteps(n); };
  const hasSelector = !NO_SELECTOR_TYPES.has(s.type) && s.type !== "keyboardAction";
  const isPicking = selectorPickStepIndex === i;
  return (
    <div ref={setNodeRef} style={style} className="step-builder-row">
      <span {...attributes} {...listeners} style={{ fontSize: 12, color: "var(--muted)", cursor: "grab", minWidth: 20, userSelect: "none" }} title="Drag to reorder">⋮⋮</span>
      <span style={{ fontSize: 10, color: "var(--muted)", minWidth: 20 }}>{i + 1}</span>
      {st && <span style={{ fontSize: 9, fontWeight: 700, minWidth: 42, color: st === "passed" ? "#00e5a0" : st === "failed" ? "#ff3b5c" : "#8a8f98" }}>{st.toUpperCase()}</span>}
      <select value={s.type} onChange={e => update(n => { n[i] = { ...n[i], type: e.target.value }; })}>
        {STEP_TYPES.map(t => <option key={t}>{t}</option>)}
      </select>
      {hasSelector && (
        <>
          <select value={s.selector?.using || "accessibilityId"} onChange={e => update(n => { n[i] = { ...n[i], selector: { ...n[i].selector, using: e.target.value } }; })} style={{ width: pf === "ios_sim" ? 150 : 110 }}>
            {selectorOptions.map(u => <option key={u}>{u}</option>)}
          </select>
          <input
            value={s.selector?.value || ""}
            onChange={e => update(n => { n[i] = { ...n[i], selector: { ...n[i].selector, value: e.target.value } }; })}
            placeholder="selector value"
            style={{ flex: 1 }}
            list={figmaNames && figmaNames.length > 0 ? `figma-datalist-${i}` : undefined}
          />
          {figmaNames && figmaNames.length > 0 && (
            <datalist id={`figma-datalist-${i}`}>
              {figmaNames.map(n => <option key={n} value={n} />)}
            </datalist>
          )}
          {onPickStep && (
            <button className="btn-ghost btn-sm" style={{ fontSize: 9, padding: "2px 6px", borderColor: isPicking ? "var(--accent)" : undefined }} onClick={() => onPickStep(i)} title="Pick selector from XML tree">
              {isPicking ? "⏳ Pick…" : "📋 Pick"}
            </button>
          )}
        </>
      )}
      {(s.type === "keyboardAction" || s.type === "pressKey") && (
        <select value={s.text || "return"} onChange={e => update(n => { n[i] = { ...n[i], text: e.target.value }; })} style={{ width: 100 }}>
          {["return", "done", "go", "next", "search", "send", "back", "home", "enter", "delete", "tab"].map(k => <option key={k}>{k}</option>)}
        </select>
      )}
      {(s.type === "type" || s.type === "clearAndType") && <input value={s.text || ""} onChange={e => update(n => { n[i] = { ...n[i], text: e.target.value }; })} placeholder="text to type" style={{ flex: 1 }} />}
      {(s.type === "assertText" || s.type === "assertTextContains") && <input value={s.expect || ""} onChange={e => update(n => { n[i] = { ...n[i], expect: e.target.value }; })} placeholder="expected text" style={{ flex: 1 }} />}
      {s.type === "assertAttribute" && (
        <>
          <input value={s.meta?.attribute || ""} onChange={e => update(n => { n[i] = { ...n[i], meta: { ...n[i].meta, attribute: e.target.value } }; })} placeholder="attribute name" style={{ width: 100 }} />
          <input value={s.expect || ""} onChange={e => update(n => { n[i] = { ...n[i], expect: e.target.value }; })} placeholder="expected value" style={{ flex: 1 }} />
        </>
      )}
      {(s.type === "swipe" || s.type === "scroll") && (
        <select value={s.text || "up"} onChange={e => update(n => { n[i] = { ...n[i], text: e.target.value }; })}>
          {["up", "down", "left", "right"].map(d => <option key={d}>{d}</option>)}
        </select>
      )}
      {s.type === "tapByCoordinates" && (
        <>
          <input type="number" value={s.meta?.x || 0} onChange={e => update(n => { n[i] = { ...n[i], meta: { ...n[i].meta, x: Number(e.target.value) } }; })} placeholder="x" style={{ width: 60 }} />
          <input type="number" value={s.meta?.y || 0} onChange={e => update(n => { n[i] = { ...n[i], meta: { ...n[i].meta, y: Number(e.target.value) } }; })} placeholder="y" style={{ width: 60 }} />
        </>
      )}
      {(s.type === "launchApp" || s.type === "closeApp" || s.type === "resetApp") && <input value={s.text || ""} onChange={e => update(n => { n[i] = { ...n[i], text: e.target.value }; })} placeholder="bundle/package ID (optional)" style={{ flex: 1 }} />}
      {s.type === "longPress" && <input type="number" value={s.ms || 2000} onChange={e => update(n => { n[i] = { ...n[i], ms: Number(e.target.value) }; })} placeholder="duration ms" style={{ width: 80 }} />}
      {["wait", "waitForVisible", "waitForNotVisible", "waitForEnabled", "waitForDisabled"].includes(s.type) && <input type="number" value={s.ms || 1000} onChange={e => update(n => { n[i] = { ...n[i], ms: Number(e.target.value) }; })} placeholder="ms" style={{ width: 70 }} />}
      <button className="btn-ghost btn-sm" onClick={() => setSteps(steps.filter((_, j) => j !== i))} title="Remove step">✕</button>
    </div>
  );
}

function StepBuilder({ steps, setSteps, stepStatuses, selectorPickStepIndex, onPickStep, figmaNames, platform }: { steps: any[]; setSteps: (s: any[]) => void; stepStatuses?: string[]; selectorPickStepIndex?: number | null; onPickStep?: (idx: number) => void; figmaNames?: string[]; platform?: "android" | "ios_sim" }) {
  const ids = useMemo(() => steps.map((_, i) => `step-${i}`), [steps.length]);
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 8 } }), useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }));
  const handleDragEnd = useCallback((event: DragEndEvent) => {
    if (!event.over || event.active.id === event.over.id) return;
    const oldIdx = ids.indexOf(String(event.active.id));
    const newIdx = ids.indexOf(String(event.over.id));
    if (oldIdx >= 0 && newIdx >= 0) setSteps(arrayMove(steps, oldIdx, newIdx));
  }, [steps, ids, setSteps]);
  return (
    <div className="step-builder">
      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
        <SortableContext items={ids} strategy={verticalListSortingStrategy}>
          {steps.map((s, i) => <SortableStepRow key={ids[i]} s={s} i={i} steps={steps} setSteps={setSteps} stepStatuses={stepStatuses} selectorPickStepIndex={selectorPickStepIndex} onPickStep={onPickStep} figmaNames={figmaNames} platform={platform} />)}
        </SortableContext>
      </DndContext>
      <button className="btn-ghost btn-sm" style={{ marginTop: 6 }} onClick={() => setSteps([...steps, { type: "tap", selector: { using: "accessibilityId", value: "" } }])}>+ Add Step</button>
    </div>
  );
}

export function LibraryPage({
  project,
  tests,
  runs,
  modules,
  suites,
  devices,
  onRefresh,
  openTestId,
  onOpenTestConsumed,
}: {
  project: Project;
  tests: TestDef[];
  runs: Run[];
  modules: ModuleDef[];
  suites: SuiteDef[];
  devices: DeviceList;
  onRefresh: () => void;
  openTestId: number | null;
  onOpenTestConsumed: () => void;
}) {
  const [busy, setBusy] = useState(false);
  /** Upload % when set, or null = indeterminate (AI / server processing). */
  const [taskProgress, setTaskProgress] = useState<{ label: string; pct: number | null } | null>(null);
  const [platform, setPlatform] = useState<"android" | "ios_sim">("android");
  const [libTab, setLibTab] = useState<"tests" | "screens" | "data">("tests");

  // Data Layer state
  const [dataFolders, setDataFolders] = useState<DataFolder[]>([]);
  const [activeDataFolderId, setActiveDataFolderId] = useState<number | null>(null);
  const [dataSets, setDataSets] = useState<DataSet[]>([]);
  const [selectedDataSet, setSelectedDataSet] = useState<DataSet | null>(null);
  const [showNewDataFolder, setShowNewDataFolder] = useState(false);
  const [newDataFolderName, setNewDataFolderName] = useState("");
  const [showNewDataSet, setShowNewDataSet] = useState(false);
  const [newDataSetName, setNewDataSetName] = useState("");
  const [editingVarKey, setEditingVarKey] = useState("");
  const [editingVarVal, setEditingVarVal] = useState("");
  const [newRowCol, setNewRowCol] = useState("");
  const [csvImportFile, setCsvImportFile] = useState<File | null>(null);

  // Screen Library state
  const [screens, setScreens] = useState<ScreenEntry[]>([]);
  const [screenDetail, setScreenDetail] = useState<ScreenEntryFull | null>(null);
  const [showCapture, setShowCapture] = useState(false);
  const [captureName, setCaptureName] = useState("");
  const [captureNotes, setCaptureNotes] = useState("");
  const [captureStatus, setCaptureStatus] = useState("");
  const [screenBuildFilter, setScreenBuildFilter] = useState<number | null>(null);
  const [screenPlatformFilter, setScreenPlatformFilter] = useState<string>("");
  const [editingScreenId, setEditingScreenId] = useState<number | null>(null);
  const [editScreenName, setEditScreenName] = useState("");
  const [editScreenNotes, setEditScreenNotes] = useState("");
  const [builds, setBuilds] = useState<Build[]>([]);
  const [screenFolders, setScreenFolders] = useState<ScreenFolder[]>([]);
  const [activeFolderId, setActiveFolderId] = useState<number | null>(null);
  const [newFolderName, setNewFolderName] = useState("");
  const [showNewFolder, setShowNewFolder] = useState(false);
  const [captureDeviceId, setCaptureDeviceId] = useState("");
  const [screenSessionActive, setScreenSessionActive] = useState(false);
  const lastScreenSessionRef = useRef<{ build_id: number; device_target: string; platform: "android" | "ios_sim" } | null>(null);

  const stopScreenSessionIfAny = useCallback(async () => {
    const prev = lastScreenSessionRef.current;
    if (!prev) return;
    try {
      await api.stopScreenSession({
        project_id: project.id,
        build_id: prev.build_id,
        platform: prev.platform,
        ...(prev.device_target.trim() ? { device_target: prev.device_target.trim() } : {}),
      });
    } catch {
      /* ignore */
    }
    lastScreenSessionRef.current = null;
    setScreenSessionActive(false);
  }, [project.id]);

  const loadScreenFolders = useCallback(async () => {
    try { setScreenFolders(await api.listScreenFolders(project.id)); } catch {}
  }, [project.id]);

  const loadScreens = useCallback(async () => {
    try {
      const s = await api.listScreens(project.id, { buildId: screenBuildFilter, folderId: activeFolderId, platform: screenPlatformFilter });
      setScreens(s);
    } catch {}
  }, [project.id, screenBuildFilter, activeFolderId, screenPlatformFilter]);

  const loadBuilds = useCallback(async () => {
    try { setBuilds(await api.listBuilds(project.id)); } catch {}
  }, [project.id]);

  const loadDataFolders = useCallback(async () => {
    try { setDataFolders(await api.listDataFolders(project.id)); } catch {}
  }, [project.id]);

  const loadDataSets = useCallback(async () => {
    try {
      const ds = await api.listDataSets(project.id, { folderId: activeDataFolderId });
      setDataSets(ds);
    } catch {}
  }, [project.id, activeDataFolderId]);

  const devicePickerPlatform = useMemo(
    () => (builds.find(b => b.id === screenBuildFilter) || builds[0])?.platform || platform,
    [builds, screenBuildFilter, platform],
  );

  useEffect(() => {
    if (devicePickerPlatform === "ios_sim") {
      const ids = devices.ios_simulators.map(d => d.udid);
      setCaptureDeviceId(prev => (prev && ids.includes(prev) ? prev : ids[0] || ""));
    } else {
      const ids = devices.android.map(d => String((d as { serial?: string }).serial || ""));
      setCaptureDeviceId(prev => (prev && ids.includes(prev) ? prev : ids[0] || ""));
    }
  }, [devices, devicePickerPlatform]);

  useEffect(() => {
    if (libTab !== "screens" || !showCapture || screenBuildFilter == null) {
      return;
    }
    const selectedBuild = builds.find(b => b.id === screenBuildFilter);
    if (!selectedBuild) return;
    let cancelled = false;
    const tick = () => {
      api
        .screenSessionStatus({
          project_id: project.id,
          build_id: screenBuildFilter,
          platform: selectedBuild.platform,
          ...(captureDeviceId.trim() ? { device_target: captureDeviceId.trim() } : {}),
        })
        .then((r) => {
          if (!cancelled) setScreenSessionActive(r.active);
        })
        .catch(() => {
          if (!cancelled) setScreenSessionActive(false);
        });
    };
    tick();
    const id = setInterval(tick, 3000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [libTab, showCapture, screenBuildFilter, captureDeviceId, project.id, builds]);

  useEffect(() => { loadScreenFolders(); }, [loadScreenFolders]);
  useEffect(() => { loadDataFolders(); }, [loadDataFolders]);
  useEffect(() => { if (libTab === "data") loadDataSets(); }, [libTab, loadDataSets]);
  useEffect(() => {
    loadBuilds();
  }, [loadBuilds]);
  useEffect(() => {
    if (libTab === "screens") loadScreens();
  }, [libTab, loadScreens]);

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
  const [editPfTab, setEditPfTab] = useState<"android" | "ios_sim">("android");
  const [editStepsAndroid, setEditStepsAndroid] = useState<any[]>([]);
  const [editStepsIos, setEditStepsIos] = useState<any[]>([]);
  const editSteps = editPfTab === "android" ? editStepsAndroid : editStepsIos;
  const setEditSteps = useCallback(
    (u: any[] | ((prev: any[]) => any[])) => {
      const apply = (prev: any[]) => (typeof u === "function" ? (u as (p: any[]) => any[])(prev) : u);
      if (editPfTab === "android") setEditStepsAndroid(apply);
      else setEditStepsIos(apply);
    },
    [editPfTab],
  );
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
  const [genSuiteFolderId, setGenSuiteFolderId] = useState<number | null>(null);
  const [genAiFolderScreens, setGenAiFolderScreens] = useState<ScreenEntry[]>([]);
  const [genAiBuildIds, setGenAiBuildIds] = useState<number[]>([]);
  const [genSuiteFolderScreens, setGenSuiteFolderScreens] = useState<ScreenEntry[]>([]);
  const [genSuiteBuildIds, setGenSuiteBuildIds] = useState<number[]>([]);

  // Confluence PRD source
  const [suiteSource, setSuiteSource] = useState<"prompt" | "confluence">("prompt");
  const [confSearchQuery, setConfSearchQuery] = useState("");
  const [confSearchResults, setConfSearchResults] = useState<{ id: string; title: string; space_key: string }[]>([]);
  const [confSelectedPage, setConfSelectedPage] = useState<{ id: string; title: string; text?: string } | null>(null);
  const [confSearching, setConfSearching] = useState(false);
  const [confLoading, setConfLoading] = useState(false);

  // Figma context toggle
  const [useFigma, setUseFigma] = useState(false);
  const [figmaPreview, setFigmaPreview] = useState<{ file_name: string; pages: { name: string; frames: { name: string; type: string }[] }[]; component_names: string[] } | null>(null);

  // CSV Import for Generate Suite
  const csvFileRef = useRef<HTMLInputElement>(null);
  const [csvParsed, setCsvParsed] = useState<import("../utils/csvParser").ManualTestCase[]>([]);
  const [csvMapping, setCsvMapping] = useState<import("../utils/csvParser").ColumnMapping | null>(null);
  const [csvHeaders, setCsvHeaders] = useState<string[]>([]);
  const [csvNeedsMapping, setCsvNeedsMapping] = useState(false);
  const [showCsvPreview, setShowCsvPreview] = useState(false);
  const [csvRawText, setCsvRawText] = useState("");

  // Import script / sheet (preview → confirm)
  const [showImport, setShowImport] = useState(false);
  const [importSuiteId, setImportSuiteId] = useState<number | null>(null);
  const [importPreview, setImportPreview] = useState<{
    test_cases: any[];
    warnings: string[];
    filename: string;
    row_count?: number;
    scripts?: { name: string; content: string }[];
  } | null>(null);
  const importFileRef = useRef<HTMLInputElement>(null);
  const [importTab, setImportTab] = useState<"single" | "bulk">("single");
  const [importGroovyIdx, setImportGroovyIdx] = useState(0);
  const [bulkImportResult, setBulkImportResult] = useState<{
    groups: Record<string, any[]>;
    total_cases: number;
    total_files: number;
    warnings: string[];
    collections?: Record<string, string[]>;
    katalon_detected?: boolean;
    files: { path: string; cases_count: number; status: string }[];
  } | null>(null);
  const [bulkModuleId, setBulkModuleId] = useState<number | null>(null);
  const [bulkImportFolderId, setBulkImportFolderId] = useState<number | null>(null);
  const [bulkImportFolderScreens, setBulkImportFolderScreens] = useState<ScreenEntry[]>([]);
  const [bulkImportBuildIds, setBulkImportBuildIds] = useState<number[]>([]);
  const [bulkFlatCases, setBulkFlatCases] = useState<any[]>([]);
  const zipImportRef = useRef<HTMLInputElement>(null);
  const folderImportRef = useRef<HTMLInputElement>(null);
  // Filters for test table: null = all suites, [] = none, [id,...] = these suites
  const [filterCollectionId, setFilterCollectionId] = useState<number | null>(null);
  const [filterSuiteIds, setFilterSuiteIds] = useState<number[] | null>(null);
  const [librarySearch, setLibrarySearch] = useState("");
  const [debouncedLibrarySearch, setDebouncedLibrarySearch] = useState("");
  const [figmaNames, setFigmaNames] = useState<string[]>([]);
  useEffect(() => {
    const t = setTimeout(() => setDebouncedLibrarySearch(librarySearch), 200);
    return () => clearTimeout(t);
  }, [librarySearch]);
  useEffect(() => {
    if (libTab !== "tests") return;
    api.listFigmaComponents().then(r => setFigmaNames(r.names || [])).catch(() => setFigmaNames([]));
  }, [libTab]);

  // Related tests when editing (for suggestion banner)
  const [editRelated, setEditRelated] = useState<{ dependents: TestDef[]; similar: { test: TestDef; shared_prefix_length: number }[] } | null>(null);

  // XML click-to-fill: create-test flow only (edit uses manual / Figma datalist)
  const [newXml, setNewXml] = useState<string | null>(null);
  const [newSelectorPickStepIndex, setNewSelectorPickStepIndex] = useState<number | null>(null);

  // Screen context for generation
  const [genFolderId, setGenFolderId] = useState<number | null>(null);

  useEffect(() => {
    if (!genFolderId) {
      setGenAiFolderScreens([]);
      setGenAiBuildIds([]);
      return;
    }
    const pf = platform === "ios_sim" ? "ios_sim" : "android";
    api
      .listScreens(project.id, { folderId: genFolderId, platform: pf })
      .then((rows) => {
        setGenAiFolderScreens(rows);
        const distinct = [
          ...new Set(rows.map((s) => s.build_id).filter((id): id is number => id != null)),
        ].sort((a, b) => a - b);
        setGenAiBuildIds(distinct);
      })
      .catch(() => {
        setGenAiFolderScreens([]);
        setGenAiBuildIds([]);
      });
  }, [genFolderId, platform, project.id]);

  useEffect(() => {
    if (!genSuiteFolderId) {
      setGenSuiteFolderScreens([]);
      setGenSuiteBuildIds([]);
      return;
    }
    const pf = platform === "ios_sim" ? "ios_sim" : "android";
    api
      .listScreens(project.id, { folderId: genSuiteFolderId, platform: pf })
      .then((rows) => {
        setGenSuiteFolderScreens(rows);
        const distinct = [
          ...new Set(rows.map((s) => s.build_id).filter((id): id is number => id != null)),
        ].sort((a, b) => a - b);
        setGenSuiteBuildIds(distinct);
      })
      .catch(() => {
        setGenSuiteFolderScreens([]);
        setGenSuiteBuildIds([]);
      });
  }, [genSuiteFolderId, platform, project.id]);

  const toggleGenAiBuild = (bid: number) => {
    setGenAiBuildIds((prev) =>
      prev.includes(bid) ? prev.filter((x) => x !== bid) : [...prev, bid].sort((a, b) => a - b),
    );
  };
  const toggleGenSuiteBuild = (bid: number) => {
    setGenSuiteBuildIds((prev) =>
      prev.includes(bid) ? prev.filter((x) => x !== bid) : [...prev, bid].sort((a, b) => a - b),
    );
  };

  useEffect(() => {
    if (!bulkImportFolderId) {
      setBulkImportFolderScreens([]);
      setBulkImportBuildIds([]);
      return;
    }
    const pf = platform === "ios_sim" ? "ios_sim" : "android";
    api
      .listScreens(project.id, { folderId: bulkImportFolderId, platform: pf })
      .then((rows) => {
        setBulkImportFolderScreens(rows);
        const distinct = [...new Set(rows.map((s) => s.build_id).filter((id): id is number => id != null))].sort((a, b) => a - b);
        setBulkImportBuildIds(distinct);
      })
      .catch(() => {
        setBulkImportFolderScreens([]);
        setBulkImportBuildIds([]);
      });
  }, [bulkImportFolderId, platform, project.id]);

  const toggleBulkImportBuild = (bid: number) => {
    setBulkImportBuildIds((prev) =>
      prev.includes(bid) ? prev.filter((x) => x !== bid) : [...prev, bid].sort((a, b) => a - b),
    );
  };

  const genAiContextScreenCount = useMemo(() => {
    const hasTagged = genAiFolderScreens.some((s) => s.build_id != null);
    if (!hasTagged) return genAiFolderScreens.length;
    if (genAiBuildIds.length === 0) return 0;
    return genAiFolderScreens.filter((s) => s.build_id != null && genAiBuildIds.includes(s.build_id)).length;
  }, [genAiFolderScreens, genAiBuildIds]);

  const genSuiteContextScreenCount = useMemo(() => {
    const hasTagged = genSuiteFolderScreens.some((s) => s.build_id != null);
    if (!hasTagged) return genSuiteFolderScreens.length;
    if (genSuiteBuildIds.length === 0) return 0;
    return genSuiteFolderScreens.filter((s) => s.build_id != null && genSuiteBuildIds.includes(s.build_id)).length;
  }, [genSuiteFolderScreens, genSuiteBuildIds]);

  const bulkImportContextScreenCount = useMemo(() => {
    const hasTagged = bulkImportFolderScreens.some((s) => s.build_id != null);
    if (!hasTagged) return bulkImportFolderScreens.length;
    if (bulkImportBuildIds.length === 0) return 0;
    return bulkImportFolderScreens.filter((s) => s.build_id != null && bulkImportBuildIds.includes(s.build_id)).length;
  }, [bulkImportFolderScreens, bulkImportBuildIds]);

  const aiGenerate = async () => {
    if (!aiPrompt.trim()) { toast("Describe the test", "error"); return; }
    if (genFolderId) {
      const hasTagged = genAiFolderScreens.some((s) => s.build_id != null);
      if (hasTagged && genAiBuildIds.length === 0) {
        toast("Select at least one build for screen context", "error");
        return;
      }
    }
    setBusy(true);
    setTaskProgress({ label: genFolderId ? "Generating with Screen Library context (XML + screenshots)…" : "Capturing page source (Appium)…", pct: null });
    setAiStatus("Generating...");
    let xml = "";
    try {
      if (!genFolderId) {
        try { const ps = await api.capturePageSource(); if (ps.ok) xml = ps.xml; } catch {}
      }
      setTaskProgress({ label: "AI is generating steps — usually 15–45s…", pct: null });
      const opts = genFolderId
        ? {
            folder_id: genFolderId,
            project_id: project.id,
            ...(genAiBuildIds.length > 0 ? { build_ids: genAiBuildIds } : {}),
          }
        : undefined;
      const res = await api.generateSteps(platform === "ios_sim" ? "ios_sim" : "android", aiPrompt, xml, opts);
      setNewSteps(res.steps);
      setNewAcceptanceCriteria(prev => prev || aiPrompt);
      const grounded = res.grounded && (res.screens_used || 0) > 0;
      setAiStatus(`Generated ${res.steps.length} steps${grounded ? ` (grounded on ${res.screens_used} screen${(res.screens_used || 0) > 1 ? "s" : ""})` : ""}`);
      toast(`AI generated ${res.steps.length} steps${grounded ? " with real selectors + screenshots" : ""}`, "success");
    } catch (e: any) {
      setAiStatus("");
      toast(e.message, "error");
    } finally {
      setBusy(false);
      setTaskProgress(null);
    }
  };

  const saveNew = async () => {
    if (!newName.trim()) { toast("Enter test name", "error"); return; }
    if (!newSteps.length) { toast("Add steps", "error"); return; }
    setBusy(true);
    const ps =
      platform === "ios_sim"
        ? { android: [] as any[], ios_sim: [...newSteps] }
        : { android: [...newSteps], ios_sim: [] as any[] };
    try {
      await api.createTest(project.id, {
        name: newName.trim(),
        steps: platform === "android" ? newSteps : [],
        platform_steps: ps,
        suite_id: newSuiteId,
        prerequisite_test_id: newPrerequisiteId,
        acceptance_criteria: newAcceptanceCriteria.trim() || null,
      });
      toast("Test saved", "success");
      setNewName("");
      setNewSteps([]);
      setNewPrerequisiteId(null);
      setNewAcceptanceCriteria("");
      setNewXml(null);
      setNewSelectorPickStepIndex(null);
      setShowCreate(false);
      onRefresh();
    }
    catch (e: any) { toast(e.message, "error"); }
    finally { setBusy(false); }
  };

  const openEdit = useCallback((t: TestDef) => {
    setEditId(t.id);
    setEditName(t.name);
    setEditStepsAndroid([...stepsForPlatform(t, "android")]);
    setEditStepsIos([...stepsForPlatform(t, "ios_sim")]);
    setEditPfTab("android");
    setEditSuiteId(t.suite_id);
    setEditPrerequisiteId(t.prerequisite_test_id ?? null);
    setEditAcceptanceCriteria(t.acceptance_criteria ?? "");
    setAiEditPrompt("");
    setAiEditStatus("");
    setEditRelated(null);
    api.getRelatedTests(t.id).then(setEditRelated).catch(() => {});
  }, []);

  useEffect(() => {
    if (openTestId == null) return;
    const t = tests.find(x => x.id === openTestId);
    if (t) {
      setLibTab("tests");
      openEdit(t);
      onOpenTestConsumed();
    } else if (tests.length > 0) {
      onOpenTestConsumed();
    }
  }, [openTestId, tests, openEdit, onOpenTestConsumed]);

  const cancelEdit = () => {
    setEditId(null);
    setEditRelated(null);
    setEditStepsAndroid([]);
    setEditStepsIos([]);
  };

  const saveEdit = async () => {
    if (!editId || !editName.trim()) return;
    setBusy(true);
    try {
      const androidOut = editPfTab === "android" ? editSteps : editStepsAndroid;
      const iosOut = editPfTab === "ios_sim" ? editSteps : editStepsIos;
      await api.updateTest(editId, {
        name: editName,
        platform_steps: { android: androidOut, ios_sim: iosOut },
        suite_id: editSuiteId,
        prerequisite_test_id: editPrerequisiteId,
        acceptance_criteria: editAcceptanceCriteria.trim() || null,
      });
      toast("Test updated", "success");
      setEditId(null);
      setEditStepsAndroid([]);
      setEditStepsIos([]);
      onRefresh();
    }
    catch (e: any) { toast(e.message, "error"); }
    finally { setBusy(false); }
  };

  const aiEditRun = async () => {
    if (!aiEditPrompt.trim()) { toast("Describe the change", "error"); return; }
    setBusy(true);
    setTaskProgress({ label: "AI is editing steps…", pct: null });
    setAiEditStatus("AI editing...");
    try {
      const res = await api.editSteps(editPfTab, editSteps, aiEditPrompt);
      setEditSteps(res.steps);
      setAiEditStatus(res.summary || `Applied — ${res.steps.length} steps`);
      toast("AI applied edits", "success");
    } catch (e: any) {
      setAiEditStatus("");
      toast(e.message, "error");
    } finally {
      setBusy(false);
      setTaskProgress(null);
    }
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

  const handleCsvUpload = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      const text = ev.target?.result as string;
      setCsvRawText(text);
      const lines = text.trim().split(/\r?\n/);
      if (lines.length < 2) { toast("CSV has no data rows", "error"); return; }
      const headers = lines[0].split(",").map((h) => h.trim().replace(/^"|"$/g, ""));
      setCsvHeaders(headers);
      const mapping = detectColumns(headers);
      setCsvMapping(mapping);
      const confident = isDetectionConfident(headers, mapping);
      setCsvNeedsMapping(!confident);
      const parsed = parseCSV(text, mapping);
      setCsvParsed(parsed);
      setShowCsvPreview(true);
      toast(`Parsed ${parsed.length} test case(s) from CSV`, "success");
    };
    reader.readAsText(file);
    e.target.value = "";
  }, []);

  const clearCsv = useCallback(() => {
    setCsvParsed([]);
    setCsvMapping(null);
    setCsvHeaders([]);
    setCsvNeedsMapping(false);
    setShowCsvPreview(false);
    setCsvRawText("");
  }, []);

  // Confluence search with debounce
  const confSearchTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  const doConfluenceSearch = useCallback((q: string) => {
    setConfSearchQuery(q);
    if (confSearchTimeout.current) clearTimeout(confSearchTimeout.current);
    if (!q.trim()) { setConfSearchResults([]); return; }
    confSearchTimeout.current = setTimeout(async () => {
      setConfSearching(true);
      try {
        const res = await api.confluenceSearch(q);
        setConfSearchResults(res.pages);
      } catch { setConfSearchResults([]); }
      finally { setConfSearching(false); }
    }, 400);
  }, []);

  const selectConfluencePage = useCallback(async (page: { id: string; title: string }) => {
    setConfLoading(true);
    try {
      const full = await api.confluenceFetchPage(page.id);
      setConfSelectedPage({ id: full.id, title: full.title, text: full.text });
      setConfSearchResults([]);
      setConfSearchQuery("");
    } catch (e: any) {
      toast(`Failed to load Confluence page: ${e.message}`, "error");
    } finally { setConfLoading(false); }
  }, []);

  const loadFigmaPreview = useCallback(async () => {
    try {
      const res = await api.figmaOverview();
      setFigmaPreview(res);
    } catch { setFigmaPreview(null); }
  }, []);

  useEffect(() => {
    if (useFigma && !figmaPreview) loadFigmaPreview();
  }, [useFigma]);

  const reapplyMapping = useCallback((m: ColumnMapping) => {
    setCsvMapping(m);
    const parsed = parseCSV(csvRawText, m);
    setCsvParsed(parsed);
  }, [csvRawText]);

  const aiGenerateSuite = async () => {
    const hasConfluence = suiteSource === "confluence" && confSelectedPage?.id;
    if (!genSuitePrompt.trim() && csvParsed.length === 0 && !hasConfluence) { toast("Describe the feature, select a Confluence page, or upload CSV", "error"); return; }
    if (!genSuiteTargetId) { toast("Select a Test Suite", "error"); return; }
    if (genSuiteFolderId) {
      const hasTagged = genSuiteFolderScreens.some((s) => s.build_id != null);
      if (hasTagged && genSuiteBuildIds.length === 0) {
        toast("Select at least one build for screen context", "error");
        return;
      }
    }
    setBusy(true);
    const sources = [genSuiteFolderId ? "Screen Library" : null, hasConfluence ? "Confluence PRD" : null, useFigma ? "Figma" : null].filter(Boolean).join(" + ");
    setTaskProgress({ label: sources ? `Generating with ${sources}…` : "Capturing page source (Appium)…", pct: null });
    setGenSuiteStatus(sources ? `Generating with ${sources}...` : "Capturing page source...");
    let xml = "";
    try {
      if (!genSuiteFolderId) {
        try { const ps = await api.capturePageSource(); if (ps.ok) xml = ps.xml; } catch {}
      }
      setTaskProgress({ label: "AI is generating multiple test cases — may take a minute…", pct: null });
      setGenSuiteStatus("AI generating test cases...");
      const suitePrompt = genSuitePrompt || (csvParsed.length > 0 ? `Convert ${csvParsed.length} manual test cases to Appium automation` : hasConfluence ? "" : "");
      const res = await api.generateSuite(
        platform,
        suitePrompt,
        project.id,
        genSuiteTargetId,
        xml,
        genSuiteFolderId,
        genSuiteBuildIds.length > 0 ? genSuiteBuildIds : undefined,
        csvParsed.length > 0 ? csvParsed : undefined,
        {
          confluencePageId: hasConfluence ? confSelectedPage!.id : undefined,
          useFigma: useFigma || undefined,
        },
      );
      const dsMsg = res.data_set_id ? ` · Data Set #${res.data_set_id} created` : "";
      setGenSuiteStatus(`Created ${res.created} test cases${dsMsg}`);
      toast(`Generated ${res.created} test cases${dsMsg}`, "success");
      setGenSuitePrompt("");
      clearCsv();
      onRefresh();
    } catch (e: any) {
      setGenSuiteStatus("");
      toast(e.message, "error");
    } finally {
      setBusy(false);
      setTaskProgress(null);
    }
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
    const rp = (lastRun.platform || "android") as Run["platform"];
    if (lastRunForTest) {
      const prereq = editTest.prerequisite_test_id ? tests.find(t => t.id === editTest!.prerequisite_test_id) : null;
      const prereqLen = prereq ? stepsForPlatform(prereq, rp).length : 0;
      return stepResults.slice(prereqLen).map((s: any) => s?.status).filter(Boolean);
    }
    return stepResults.slice(0, stepsForPlatform(editTest, rp).length).map((s: any) => s?.status).filter(Boolean);
  })();

  const undoLastFix = async () => {
    if (!editId) return;
    setBusy(true);
    try {
      const res = await api.undoLastFix(editId);
      const tp = (res.target_platform === "ios_sim" ? "ios_sim" : "android") as "android" | "ios_sim";
      if (tp === "ios_sim") setEditStepsIos(res.steps);
      else setEditStepsAndroid(res.steps);
      setEditPfTab(tp);
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
  }).filter(t => {
    const q = debouncedLibrarySearch.trim().toLowerCase();
    if (!q) return true;
    if (t.name.toLowerCase().includes(q)) return true;
    if ((t.acceptance_criteria || "").toLowerCase().includes(q)) return true;
    const stepBlob = JSON.stringify([...stepsForPlatform(t, "android"), ...stepsForPlatform(t, "ios_sim")]).toLowerCase();
    return stepBlob.includes(q);
  });

  return (
    <>
      <div className="section-head">
        <div><div className="section-title">Test Library</div><div className="section-sub">{tests.length} test cases · {modules.length} collections · {suites.length} suites · {screens.length} screens</div></div>
        <div style={{ display: "flex", gap: 8 }}>
          {libTab === "tests" && <>
            <button className="btn-ghost btn-sm" onClick={() => {
              const opening = !showImport;
              setShowImport(opening);
              if (opening) setImportTab("single");
              setImportPreview(null);
              setBulkImportResult(null);
              setBulkFlatCases([]);
              setImportGroovyIdx(0);
            }}>{showImport ? "Close" : "⬆ Import"}</button>
            <button className="btn-ghost btn-sm" onClick={() => setShowGenerateSuite(!showGenerateSuite)}>{showGenerateSuite ? "Close" : "✨ Generate Suite"}</button>
            <button className="btn-ghost btn-sm" onClick={() => setShowCreate(!showCreate)}>{showCreate ? "Close" : "+ New Test"}</button>
          </>}
          {libTab === "screens" && <button className="btn-ghost btn-sm" onClick={() => setShowCapture(!showCapture)}>{showCapture ? "Close" : "📸 Capture Screen"}</button>}
          {libTab === "data" && <>
            <label className="btn-ghost btn-sm" style={{ cursor: "pointer" }}>
              ⬆ Import CSV
              <input type="file" accept=".csv" style={{ display: "none" }} onChange={async e => {
                const f = e.target.files?.[0];
                if (!f) return;
                try {
                  const ds = await api.importDataSetCsv(project.id, f, { folderId: activeDataFolderId ?? undefined });
                  toast(`Imported "${ds.name}" with ${ds.rows.length} rows`, "success");
                  loadDataSets();
                  loadDataFolders();
                  setSelectedDataSet(ds);
                } catch (err: any) {
                  toast(err.message || "CSV import failed", "error");
                }
                e.target.value = "";
              }} />
            </label>
            <button className="btn-ghost btn-sm" onClick={() => setShowNewDataSet(true)}>+ New Data Set</button>
          </>}
        </div>
      </div>

      {/* Tab switcher */}
      <div className="report-tabs" style={{ marginBottom: 12 }}>
        <button className={`report-tab ${libTab === "tests" ? "active" : ""}`} onClick={() => setLibTab("tests")}>Tests</button>
        <button className={`report-tab ${libTab === "screens" ? "active" : ""}`} onClick={() => setLibTab("screens")}>Screens</button>
        <button className={`report-tab ${libTab === "data" ? "active" : ""}`} onClick={() => setLibTab("data")}>Test Data</button>
      </div>

      {taskProgress && (
        <div className="library-task-progress library-task-progress--sticky" aria-live="polite">
          <div className="library-task-progress-label">{taskProgress.label}</div>
          <div className="progress-bar-track">
            {taskProgress.pct == null ? (
              <div className="progress-bar-indeterminate-wrap">
                <div className="indeterminate-fill" />
              </div>
            ) : (
              <div className="progress-fill" style={{ width: `${taskProgress.pct}%` }} />
            )}
          </div>
        </div>
      )}

      {libTab === "tests" && <>
      {/* Import Katalon / Gherkin / Python / sheet / ZIP / folder */}
      {showImport && (
        <div className="panel" style={{ padding: 18, marginBottom: 16, border: "1px solid rgba(0,229,160,.25)" }}>
          <div style={{ fontFamily: "var(--sans)", fontWeight: 600, marginBottom: 8 }}>⬆ Import tests</div>
          <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 12 }}>
            Single file (including .csv / .xlsx): rows become steps from the Steps + Expected columns, each case gets a Katalon Groovy preview. Folder/ZIP for bulk scripts.
          </div>

          <div style={{ display: "flex", gap: 6, marginBottom: 14, flexWrap: "wrap" }}>
            <button type="button" className={importTab === "single" ? "btn-primary btn-sm" : "btn-ghost btn-sm"} onClick={() => setImportTab("single")}>Single file</button>
            <button type="button" className={importTab === "bulk" ? "btn-primary btn-sm" : "btn-ghost btn-sm"} onClick={() => setImportTab("bulk")}>Folder / ZIP</button>
          </div>

          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 12, alignItems: "center" }}>
            <select value={platform} onChange={e => setPlatform(e.target.value as any)}><option value="android">Android</option><option value="ios_sim">iOS</option></select>
            {importTab === "single" && (
              <select value={importSuiteId ?? ""} onChange={e => setImportSuiteId(e.target.value ? Number(e.target.value) : null)} style={{ minWidth: 220 }}>
                <option value="">Select target suite (required)</option>
                {modules.map(m => suites.filter(s => s.module_id === m.id).map(s => <option key={s.id} value={s.id}>{m.name} / {s.name}</option>))}
              </select>
            )}
            {importTab === "bulk" && (
              <>
                <select value={bulkModuleId ?? ""} onChange={e => setBulkModuleId(e.target.value ? Number(e.target.value) : null)} style={{ minWidth: 220 }}>
                  <option value="">Collection for new suites (optional)</option>
                  {modules.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
                </select>
                <select value={bulkImportFolderId ?? ""} onChange={e => setBulkImportFolderId(e.target.value ? Number(e.target.value) : null)} style={{ minWidth: 200 }}>
                  <option value="">Screen Library folder (optional)</option>
                  {screenFolders.map(f => <option key={f.id} value={f.id}>{f.name} ({f.screen_count})</option>)}
                </select>
              </>
            )}
          </div>
          {importTab === "bulk" && bulkImportFolderId && bulkImportFolderScreens.some(s => s.build_id != null) && (
            <div style={{ marginBottom: 10, marginTop: -4 }}>
              <div style={{ fontSize: 10, color: "var(--muted)", marginBottom: 4 }}>Builds in folder (context)</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                {[...new Set(bulkImportFolderScreens.map(s => s.build_id).filter((id): id is number => id != null))].sort((a, b) => a - b).map((bid) => {
                  const b = builds.find(x => x.id === bid);
                  return (
                    <label key={bid} style={{ fontSize: 11, display: "flex", alignItems: "center", gap: 4, cursor: "pointer" }}>
                      <input type="checkbox" checked={bulkImportBuildIds.includes(bid)} onChange={() => toggleBulkImportBuild(bid)} />
                      {b?.file_name ?? `Build #${bid}`}
                    </label>
                  );
                })}
              </div>
              <div style={{ fontSize: 10, color: "var(--accent2)", marginTop: 4 }}>{bulkImportContextScreenCount} screen(s) will ground AI selectors</div>
            </div>
          )}
          {importTab === "bulk" && bulkImportFolderId && !bulkImportFolderScreens.some(s => s.build_id != null) && bulkImportFolderScreens.length > 0 && (
            <div style={{ fontSize: 10, color: "var(--muted)", marginBottom: 8, marginTop: -4 }}>
              {bulkImportFolderScreens.length} screen(s) in folder (no build tag) — all will be sent to AI.
            </div>
          )}
          {importTab === "bulk" && bulkImportFolderId && (
            <div style={{ fontSize: 10, color: "var(--accent)", marginBottom: 8, marginTop: bulkImportFolderScreens.length ? 0 : -4 }}>
              Screen Library XML will ground AI selectors when no Object Repository (.rs) files are found in the upload.
            </div>
          )}

          {importTab === "single" && (
            <>
              <input ref={importFileRef} type="file" accept=".groovy,.java,.feature,.py,.csv,.xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,text/csv" style={{ display: "none" }} onChange={async (e) => {
                const f = e.target.files?.[0];
                e.target.value = "";
                if (!f) return;
                if (!importSuiteId) { toast("Select a target suite first", "error"); return; }
                const n = f.name.toLowerCase();
                setBusy(true);
                setImportPreview(null);
                setTaskProgress({ label: `Uploading ${f.name}…`, pct: 0 });
                const onUp = (p: number | null) => {
                  setTaskProgress({
                    label: p == null ? `Uploading ${f.name}…` : p >= 100 ? `Processing ${f.name} on server…` : `Uploading ${f.name} — ${p}%`,
                    pct: p != null && p < 100 ? p : null,
                  });
                };
                try {
                  if (n.endsWith(".csv") || n.endsWith(".xlsx")) {
                    const r = await api.importSheet(project.id, importSuiteId, platform, f, { onUploadProgress: onUp });
                    setImportGroovyIdx(0);
                    setImportPreview({
                      test_cases: (r.test_cases || []).map((tc: any) => ({ ...tc, import: tc.import !== false })),
                      warnings: r.warnings || [],
                      filename: r.filename,
                      row_count: r.row_count,
                      scripts: r.scripts,
                    });
                    toast(`Parsed ${r.row_count} row(s) · ${(r.scripts?.length ?? 0)} Groovy script(s)`, "success");
                  } else {
                    const r = await api.importScript(project.id, importSuiteId, platform, f, { onUploadProgress: onUp });
                    setImportPreview({ test_cases: (r.test_cases || []).map((tc: any) => ({ ...tc, import: tc.import !== false })), warnings: r.warnings || [], filename: r.filename });
                    const km = r.katalon_import_mode;
                    toast(
                      km === "ai"
                        ? `Preview: ${r.test_cases?.length ?? 0} case(s) — Katalon script parsed with AI (verify selectors on device).`
                        : km === "heuristic"
                          ? `Preview: ${r.test_cases?.length ?? 0} case(s) — heuristic Katalon parse (set AI API key in Settings for Gemini parsing).`
                          : `Preview: ${r.test_cases?.length ?? 0} test case(s)`,
                      "success",
                    );
                  }
                } catch (err: any) { toast(err.message || String(err), "error"); }
                finally { setBusy(false); setTaskProgress(null); }
              }} />
              <button className="btn-primary btn-sm" disabled={busy || !importSuiteId} onClick={() => importFileRef.current?.click()}>{busy ? "…" : "Choose file"}</button>
              {importPreview && (
                <div style={{ borderTop: "1px solid var(--border)", paddingTop: 14 }}>
                  <div style={{ fontSize: 12, marginBottom: 8, color: "var(--text)" }}><strong>{importPreview.filename}</strong>{importPreview.row_count != null ? ` · ${importPreview.row_count} rows` : ""}</div>
                  {importPreview.warnings.length > 0 && (
                    <div style={{ fontSize: 11, color: "var(--warn)", marginBottom: 10 }}>
                      {importPreview.warnings.map((w, i) => <div key={i}>{w}</div>)}
                    </div>
                  )}
                  <div style={{ maxHeight: 280, overflowY: "auto", marginBottom: 12, border: "1px solid var(--border)", borderRadius: 8 }}>
                    {importPreview.test_cases.map((tc, idx) => (
                      <div key={idx} style={{ padding: 10, borderBottom: "1px solid var(--border)", display: "grid", gridTemplateColumns: "28px 1fr auto", gap: 8, alignItems: "start" }}>
                        <input type="checkbox" checked={!!tc.import} onChange={() => setImportPreview(p => !p ? null : { ...p, test_cases: p.test_cases.map((t, j) => j === idx ? { ...t, import: !t.import } : t) })} />
                        <div>
                          <div style={{ fontWeight: 600, fontSize: 12 }}>{tc.name || `Case ${idx + 1}`}</div>
                          <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 4 }}>{Array.isArray(tc.steps) ? `${tc.steps.length} steps` : "—"} · {tc.acceptance_criteria ? String(tc.acceptance_criteria).slice(0, 120) : ""}</div>
                        </div>
                      </div>
                    ))}
                  </div>
                  {(importPreview.scripts?.length ?? 0) > 0 && (
                    <div style={{ marginBottom: 12 }}>
                      <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 6, color: "var(--muted)" }}>Katalon Groovy (from sheet steps)</div>
                      <select value={Math.min(importGroovyIdx, (importPreview.scripts?.length ?? 1) - 1)} onChange={e => setImportGroovyIdx(Number(e.target.value))} style={{ marginBottom: 8, fontSize: 11, minWidth: 240 }}>
                        {importPreview.scripts!.map((s, i) => <option key={i} value={i}>{s.name}</option>)}
                      </select>
                      <pre style={{ maxHeight: 220, overflow: "auto", fontSize: 10, padding: 10, borderRadius: 8, border: "1px solid var(--border)", margin: 0, whiteSpace: "pre-wrap" }}>{importPreview.scripts![Math.min(importGroovyIdx, importPreview.scripts!.length - 1)]?.content || ""}</pre>
                      <button type="button" className="btn-ghost btn-sm" style={{ marginTop: 8 }} disabled={busy} onClick={() => {
                        const i = Math.min(importGroovyIdx, (importPreview.scripts?.length ?? 1) - 1);
                        const one = importPreview.scripts![i];
                        if (!one) return;
                        const blob = new Blob([one.content], { type: "text/plain" });
                        const a = document.createElement("a");
                        a.href = URL.createObjectURL(blob);
                        a.download = one.name;
                        a.click();
                      }}>⬇ Selected .groovy</button>
                    </div>
                  )}
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                    <button className="run-now-btn" style={{ padding: "8px 16px", fontSize: 11 }} disabled={busy || !importSuiteId || !importPreview.test_cases.some((t: any) => t.import)} onClick={async () => {
                      setBusy(true);
                      setTaskProgress({ label: "Saving imported tests to the library…", pct: null });
                      try {
                        const res = await api.confirmScriptImport(project.id, { suite_id: importSuiteId, test_cases: importPreview.test_cases });
                        toast(`Imported ${res.created} test(s)`, "success");
                        setImportPreview(null);
                        setImportGroovyIdx(0);
                        setShowImport(false);
                        onRefresh();
                      } catch (e: any) { toast(e.message, "error"); }
                      finally { setBusy(false); setTaskProgress(null); }
                    }}>✓ Import selected</button>
                    <button type="button" className="btn-ghost btn-sm" disabled={busy || !importPreview.test_cases.some((t: any) => t.import)} onClick={async () => {
                      setBusy(true);
                      setTaskProgress({ label: "Building Katalon ZIP…", pct: null });
                      try {
                        await api.downloadKatalonZip(project.id, {
                          test_cases: importPreview.test_cases.filter((t: any) => t.import),
                          project_name: project.name,
                        });
                        toast("Katalon ZIP downloaded", "success");
                      } catch (e: any) { toast(e.message || String(e), "error"); }
                      finally { setBusy(false); setTaskProgress(null); }
                    }}>⬇ Katalon ZIP</button>
                    <button className="btn-ghost btn-sm" onClick={() => { setImportPreview(null); setImportGroovyIdx(0); }}>Clear preview</button>
                  </div>
                </div>
              )}
            </>
          )}

          {importTab === "bulk" && (
            <>
              <input ref={zipImportRef} type="file" accept=".zip,application/zip" style={{ display: "none" }} onChange={async (e) => {
                const f = e.target.files?.[0];
                e.target.value = "";
                if (!f) return;
                setBusy(true);
                setTaskProgress({ label: `Uploading ${f.name}…`, pct: 0 });
                const onUp = (p: number | null) => {
                  setTaskProgress({
                    label: p == null ? `Uploading ${f.name}…` : p >= 100 ? `Importing ${f.name} on server…` : `Uploading ZIP — ${p}%`,
                    pct: p != null && p < 100 ? p : null,
                  });
                };
                try {
                  const res = await api.importZip(project.id, platform, f, { onUploadProgress: onUp, folderId: bulkImportFolderId, buildIds: bulkImportBuildIds.length ? bulkImportBuildIds : undefined });
                  setBulkImportResult(res);
                  setBulkFlatCases(Object.values(res.groups).flat().map((tc: any) => ({ ...tc, import: tc.import !== false })));
                  toast(`${res.total_files} files · ${res.total_cases} cases parsed`, "success");
                } catch (err: any) { toast(err.message || String(err), "error"); }
                finally { setBusy(false); setTaskProgress(null); }
              }} />
              <input
                ref={folderImportRef}
                type="file"
                style={{ display: "none" }}
                multiple
                {...({ webkitdirectory: "", directory: "" } as React.InputHTMLAttributes<HTMLInputElement>)}
                onChange={async (e) => {
                  const picked = Array.from(e.target.files || []);
                  e.target.value = "";
                  if (!picked.length) return;
                  setBusy(true);
                  const nFiles = picked.length;
                  setTaskProgress({ label: `Uploading folder (${nFiles} files)…`, pct: 0 });
                  const onUp = (p: number | null) => {
                    setTaskProgress({
                      label: p == null ? `Uploading folder (${nFiles} files)…` : p >= 100 ? `Processing folder on server…` : `Uploading folder — ${p}%`,
                      pct: p != null && p < 100 ? p : null,
                    });
                  };
                  try {
                    const res = await api.importFolder(project.id, platform, picked, { onUploadProgress: onUp, folderId: bulkImportFolderId, buildIds: bulkImportBuildIds.length ? bulkImportBuildIds : undefined });
                    setBulkImportResult(res);
                    setBulkFlatCases(Object.values(res.groups).flat().map((tc: any) => ({ ...tc, import: tc.import !== false })));
                    toast(`${res.total_files} files · ${res.total_cases} cases parsed`, "success");
                  } catch (err: any) { toast(err.message || String(err), "error"); }
                  finally { setBusy(false); setTaskProgress(null); }
                }} />
              {!bulkImportResult ? (
                <div
                  style={{ border: "1.5px dashed var(--border2)", borderRadius: 10, padding: 24, textAlign: "center", cursor: "pointer" }}
                  onDragOver={e => { e.preventDefault(); }}
                  onDrop={(e) => {
                    e.preventDefault();
                    const file = e.dataTransfer.files[0];
                    if (!file?.name.toLowerCase().endsWith(".zip")) {
                      toast("Drop a .zip here, or use Browse folder", "error");
                      return;
                    }
                    setBusy(true);
                    setTaskProgress({ label: `Uploading ${file.name}…`, pct: 0 });
                    (async () => {
                      const onUp = (p: number | null) => {
                        setTaskProgress({
                          label: p == null ? `Uploading ${file.name}…` : p >= 100 ? `Importing on server…` : `Uploading ZIP — ${p}%`,
                          pct: p != null && p < 100 ? p : null,
                        });
                      };
                      try {
                        const res = await api.importZip(project.id, platform, file, { onUploadProgress: onUp, folderId: bulkImportFolderId, buildIds: bulkImportBuildIds.length ? bulkImportBuildIds : undefined });
                        setBulkImportResult(res);
                        setBulkFlatCases(Object.values(res.groups).flat().map((tc: any) => ({ ...tc, import: tc.import !== false })));
                        toast(`${res.total_files} files · ${res.total_cases} cases parsed`, "success");
                      } catch (err: any) { toast(err.message || String(err), "error"); }
                      finally { setBusy(false); setTaskProgress(null); }
                    })();
                  }}
                >
                  <div style={{ fontSize: 12, marginBottom: 12, color: "var(--muted)" }}>
                    Bulk import — drop a <strong>.zip</strong>, or select an entire folder at once (browser folder picker).
                  </div>
                  <div style={{ display: "flex", gap: 8, justifyContent: "center", flexWrap: "wrap" }}>
                    <button type="button" className="btn-ghost btn-sm" disabled={busy} onClick={() => folderImportRef.current?.click()}>Browse folder</button>
                    <button type="button" className="btn-ghost btn-sm" disabled={busy} onClick={() => zipImportRef.current?.click()}>Upload .zip</button>
                  </div>
                </div>
              ) : (
                <div style={{ borderTop: "1px solid var(--border)", paddingTop: 14 }}>
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
                    {bulkImportResult.katalon_detected && (
                      <div style={{ fontSize: 10, color: "#8b5cf6", padding: "4px 8px", borderRadius: 6, background: "rgba(139,92,246,.1)", display: "inline-block" }}>
                        Katalon project detected — suites &amp; collections mapped
                      </div>
                    )}
                    {(bulkImportResult as any).grounding && (bulkImportResult as any).grounding !== "none" && (
                      <div style={{ fontSize: 10, color: "var(--accent)", padding: "4px 8px", borderRadius: 6, background: "rgba(0,229,160,.08)", display: "inline-block" }}>
                        {(bulkImportResult as any).grounding === "object_repo"
                          ? `Grounded with Object Repository (${(bulkImportResult as any).object_repo_count} locators)`
                          : "Grounded with Screen Library XML"}
                      </div>
                    )}
                  </div>
                  {bulkImportResult.warnings.length > 0 && (
                    <div style={{ fontSize: 11, color: "var(--warn)", marginBottom: 10, maxHeight: 120, overflowY: "auto" }}>
                      {bulkImportResult.warnings.slice(0, 80).map((w, i) => <div key={i}>{w}</div>)}
                      {bulkImportResult.warnings.length > 80 && <div>… and {bulkImportResult.warnings.length - 80} more</div>}
                    </div>
                  )}
                  {(bulkImportResult.files?.length ?? 0) > 0 && (
                    <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 10, maxHeight: 120, overflowY: "auto", border: "1px solid var(--border)", borderRadius: 8, padding: 8 }}>
                      {bulkImportResult.files!.map((ff, i) => (
                        <div key={i} style={{ display: "flex", justifyContent: "space-between", gap: 8, fontSize: 10 }}>
                          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={ff.path}>{ff.path}</span>
                          <span style={{ flexShrink: 0 }}>{ff.cases_count} · {ff.status}</span>
                        </div>
                      ))}
                    </div>
                  )}
                  <div style={{ maxHeight: 280, overflowY: "auto", marginBottom: 12, border: "1px solid var(--border)", borderRadius: 8 }}>
                    {(() => {
                      const bySuite = bulkFlatCases.reduce<Record<string, { tc: any; idx: number }[]>>((acc, tc, idx) => {
                        const k = String(tc.suggested_suite || "Imported");
                        if (!acc[k]) acc[k] = [];
                        acc[k].push({ tc, idx });
                        return acc;
                      }, {});
                      const collections = bulkImportResult?.collections || {};
                      const hasCollections = Object.keys(collections).length > 0;
                      const claimedSuites = new Set(Object.values(collections).flat());

                      const renderSuite = (suiteName: string, rows: { tc: any; idx: number }[]) => (
                        <div key={suiteName}>
                          <div style={{ padding: "6px 10px", background: "rgba(99,102,241,.12)", fontWeight: 600, fontSize: 11 }}>{suiteName}</div>
                          {rows.map(({ tc, idx }) => (
                            <div key={idx} style={{ padding: 10, borderBottom: "1px solid var(--border)", display: "grid", gridTemplateColumns: "28px 1fr", gap: 8, alignItems: "start", opacity: tc.import === false ? 0.5 : 1 }}>
                              <input type="checkbox" checked={!!tc.import} onChange={() => setBulkFlatCases(p => p.map((t, j) => j === idx ? { ...t, import: !t.import } : t))} />
                              <div>
                                <div style={{ fontWeight: 600, fontSize: 12 }}>{tc.name || `Case ${idx + 1}`}</div>
                                <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 4 }}>
                                  {Array.isArray(tc.steps) ? `${tc.steps.length} steps` : "—"}{tc.acceptance_criteria ? ` · ${String(tc.acceptance_criteria).slice(0, 60)}` : ""}{tc.source_file ? ` · ${String(tc.source_file)}` : ""}
                                </div>
                              </div>
                            </div>
                          ))}
                        </div>
                      );

                      if (!hasCollections) {
                        return Object.entries(bySuite).map(([sn, rows]) => renderSuite(sn, rows));
                      }

                      return (
                        <>
                          {Object.entries(collections).map(([collName, collSuites]) => (
                            <div key={collName}>
                              <div style={{ padding: "8px 10px", background: "rgba(139,92,246,.18)", fontWeight: 700, fontSize: 11, borderBottom: "1px solid var(--border)" }}>
                                📁 {collName}
                              </div>
                              {collSuites.map(sn => bySuite[sn] ? renderSuite(sn, bySuite[sn]) : null)}
                            </div>
                          ))}
                          {Object.entries(bySuite).filter(([sn]) => !claimedSuites.has(sn)).map(([sn, rows]) => renderSuite(sn, rows))}
                        </>
                      );
                    })()}
                  </div>
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                    <button type="button" className="run-now-btn" style={{ padding: "8px 16px", fontSize: 11 }} disabled={busy || !bulkFlatCases.some(t => t.import)} onClick={async () => {
                      setBusy(true);
                      setTaskProgress({ label: "Saving bulk import to the library…", pct: null });
                      try {
                        const r = await api.confirmZipImport(project.id, {
                          module_id: bulkModuleId ?? undefined,
                          test_cases: bulkFlatCases.filter(t => t.import),
                          platform,
                          collections: bulkImportResult?.collections,
                        });
                        const modulesMsg = (r.created_modules?.length ?? 0) > 0 ? ` · ${r.created_modules!.length} collection(s)` : "";
                        toast(`Imported ${r.created} test(s) · ${(r.created_suites ?? []).length} suite(s)${modulesMsg}`, "success");
                        setBulkImportResult(null);
                        setBulkFlatCases([]);
                        setShowImport(false);
                        onRefresh();
                      } catch (e: any) { toast(e.message || String(e), "error"); }
                      finally { setBusy(false); setTaskProgress(null); }
                    }}>✓ Import selected</button>
                    <button type="button" className="btn-ghost btn-sm" disabled={busy || !bulkFlatCases.some(t => t.import)} onClick={async () => {
                      setBusy(true);
                      setTaskProgress({ label: "Building Katalon ZIP…", pct: null });
                      try {
                        await api.downloadKatalonZip(project.id, {
                          test_cases: bulkFlatCases.filter(t => t.import),
                          project_name: project.name,
                        });
                        toast("Katalon ZIP downloaded", "success");
                      } catch (e: any) { toast(e.message || String(e), "error"); }
                      finally { setBusy(false); setTaskProgress(null); }
                    }}>⬇ Katalon ZIP</button>
                    <button type="button" className="btn-ghost btn-sm" onClick={() => { setBulkImportResult(null); setBulkFlatCases([]); }}>Clear</button>
                  </div>
                </div>
              )}
            </>
          )}

        </div>
      )}

      {/* Generate Test Suite (bulk) */}
      {showGenerateSuite && (
        <div className="panel" style={{ padding: 18, marginBottom: 16, border: "1px solid rgba(99,102,241,.3)" }}>
          <div style={{ fontFamily: "var(--sans)", fontWeight: 600, marginBottom: 14 }}>✨ Generate Test Suite</div>
          <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 12 }}>Same inputs as single test: platform, prompt, optional page source. AI generates multiple test cases for the selected suite. A progress bar appears under the Test Library header while this runs.</div>
          <div style={{ display: "flex", gap: 10, marginBottom: 12, flexWrap: "wrap" }}>
            <select value={platform} onChange={e => setPlatform(e.target.value as any)}><option value="android">Android</option><option value="ios_sim">iOS</option></select>
            <select value={genSuiteTargetId ?? ""} onChange={e => setGenSuiteTargetId(e.target.value ? Number(e.target.value) : null)} style={{ fontSize: 11, minWidth: 200 }}>
              <option value="">Select Test Suite</option>
              {modules.map(m => suites.filter(s => s.module_id === m.id).map(s => <option key={s.id} value={s.id}>{m.name} / {s.name}</option>))}
            </select>
            <select value={genSuiteFolderId ?? ""} onChange={e => setGenSuiteFolderId(e.target.value ? Number(e.target.value) : null)} style={{ fontSize: 11, minWidth: 180 }}>
              <option value="">No screen folder</option>
              {screenFolders.map(f => <option key={f.id} value={f.id}>{f.name} ({f.screen_count} screens)</option>)}
            </select>
          </div>
          {genSuiteFolderId && genSuiteFolderScreens.some(s => s.build_id != null) && (
            <div style={{ marginBottom: 10 }}>
              <div style={{ fontSize: 10, color: "var(--muted)", marginBottom: 4 }}>Builds in folder (context)</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                {[...new Set(genSuiteFolderScreens.map(s => s.build_id).filter((id): id is number => id != null))].sort((a, b) => a - b).map((bid) => {
                  const b = builds.find(x => x.id === bid);
                  return (
                    <label key={bid} style={{ fontSize: 11, display: "flex", alignItems: "center", gap: 4, cursor: "pointer" }}>
                      <input type="checkbox" checked={genSuiteBuildIds.includes(bid)} onChange={() => toggleGenSuiteBuild(bid)} />
                      {b?.file_name ?? `Build #${bid}`}
                    </label>
                  );
                })}
              </div>
              <div style={{ fontSize: 10, color: "var(--accent2)", marginTop: 4 }}>{genSuiteContextScreenCount} screen(s) will be sent to AI</div>
            </div>
          )}
          {genSuiteFolderId && !genSuiteFolderScreens.some(s => s.build_id != null) && genSuiteFolderScreens.length > 0 && (
            <div style={{ fontSize: 10, color: "var(--muted)", marginBottom: 8 }}>
              {genSuiteFolderScreens.length} screen(s) in folder (no build id) — all will be sent to AI.
            </div>
          )}
          {genSuiteFolderId && <div style={{ fontSize: 10, color: "var(--accent)", marginBottom: 8 }}>AI will use real selectors + screenshots from the selected folder and builds above.</div>}

          {/* Figma toggle */}
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
            <label style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 11, cursor: "pointer" }}>
              <input type="checkbox" checked={useFigma} onChange={e => setUseFigma(e.target.checked)} />
              Include Figma design context
            </label>
            {useFigma && figmaPreview && (
              <span style={{ fontSize: 10, color: "var(--accent2)" }}>
                {figmaPreview.file_name} — {figmaPreview.component_names.length} components, {figmaPreview.pages.reduce((a, p) => a + p.frames.length, 0)} frames
              </span>
            )}
            {useFigma && !figmaPreview && <span style={{ fontSize: 10, color: "var(--muted)" }}>Loading Figma...</span>}
          </div>

          {/* Figma preview */}
          {useFigma && figmaPreview && figmaPreview.pages.length > 0 && (
            <div style={{ marginBottom: 10, padding: 8, background: "rgba(168,85,247,.06)", border: "1px solid rgba(168,85,247,.2)", borderRadius: 6, maxHeight: 120, overflowY: "auto" }}>
              <div style={{ fontSize: 10, fontWeight: 600, color: "#a855f7", marginBottom: 4 }}>Figma Frames (will be sent as design context)</div>
              {figmaPreview.pages.map((pg, pi) => (
                <div key={pi} style={{ fontSize: 10 }}>
                  <span style={{ fontWeight: 500 }}>{pg.name}:</span>{" "}
                  {pg.frames.map(f => f.name).join(", ") || "no frames"}
                </div>
              ))}
            </div>
          )}

          {/* PRD Source tabs */}
          <div style={{ display: "flex", gap: 0, marginBottom: 10, borderBottom: "1px solid var(--border)" }}>
            <button
              onClick={() => setSuiteSource("prompt")}
              style={{ fontSize: 11, padding: "6px 14px", background: suiteSource === "prompt" ? "rgba(99,102,241,.1)" : "transparent", border: "none", borderBottom: suiteSource === "prompt" ? "2px solid var(--accent)" : "2px solid transparent", color: suiteSource === "prompt" ? "var(--accent)" : "var(--muted)", cursor: "pointer", fontWeight: suiteSource === "prompt" ? 600 : 400 }}
            >
              Manual Prompt
            </button>
            <button
              onClick={() => setSuiteSource("confluence")}
              style={{ fontSize: 11, padding: "6px 14px", background: suiteSource === "confluence" ? "rgba(99,102,241,.1)" : "transparent", border: "none", borderBottom: suiteSource === "confluence" ? "2px solid var(--accent)" : "2px solid transparent", color: suiteSource === "confluence" ? "var(--accent)" : "var(--muted)", cursor: "pointer", fontWeight: suiteSource === "confluence" ? 600 : 400 }}
            >
              Confluence PRD
            </button>
          </div>

          {/* Prompt source */}
          {suiteSource === "prompt" && (
            <textarea value={genSuitePrompt} onChange={e => setGenSuitePrompt(e.target.value)} placeholder={csvParsed.length > 0 ? "Optional context to supplement CSV import..." : "Describe the feature: e.g. Login flow — happy path, invalid email, wrong password, empty fields..."} rows={3} className="form-input" style={{ width: "100%", marginBottom: 10 }} />
          )}

          {/* Confluence source */}
          {suiteSource === "confluence" && (
            <div style={{ marginBottom: 10 }}>
              {!confSelectedPage ? (
                <div>
                  <div style={{ position: "relative" }}>
                    <input
                      className="form-input"
                      style={{ width: "100%", marginBottom: 4 }}
                      placeholder="Search Confluence pages by title..."
                      value={confSearchQuery}
                      onChange={e => doConfluenceSearch(e.target.value)}
                    />
                    {confSearching && <span style={{ position: "absolute", right: 10, top: 8, fontSize: 10, color: "var(--muted)" }}>Searching...</span>}
                  </div>
                  {confSearchResults.length > 0 && (
                    <div style={{ border: "1px solid var(--border)", borderRadius: 6, maxHeight: 180, overflowY: "auto", background: "var(--bg-raised)" }}>
                      {confSearchResults.map(p => (
                        <div
                          key={p.id}
                          onClick={() => selectConfluencePage(p)}
                          style={{ padding: "8px 12px", cursor: "pointer", fontSize: 11, borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between" }}
                          onMouseEnter={e => (e.currentTarget.style.background = "rgba(99,102,241,.08)")}
                          onMouseLeave={e => (e.currentTarget.style.background = "transparent")}
                        >
                          <span style={{ fontWeight: 500 }}>{p.title}</span>
                          <span style={{ color: "var(--muted)", fontSize: 10 }}>{p.space_key}</span>
                        </div>
                      ))}
                    </div>
                  )}
                  {confSearchQuery && !confSearching && confSearchResults.length === 0 && (
                    <div style={{ fontSize: 10, color: "var(--muted)", padding: 4 }}>No pages found. Make sure Confluence is configured in Settings.</div>
                  )}
                </div>
              ) : (
                <div style={{ padding: 10, background: "rgba(34,111,235,.06)", border: "1px solid rgba(34,111,235,.2)", borderRadius: 6 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                    <div>
                      <span style={{ fontSize: 12, fontWeight: 600, color: "var(--text)" }}>{confSelectedPage.title}</span>
                      {confLoading && <span style={{ fontSize: 10, color: "var(--muted)", marginLeft: 8 }}>Loading content...</span>}
                    </div>
                    <button className="btn-ghost btn-sm" onClick={() => { setConfSelectedPage(null); setConfSearchQuery(""); }} style={{ fontSize: 10, color: "var(--danger)" }}>✕ Remove</button>
                  </div>
                  {confSelectedPage.text && (
                    <div style={{ fontSize: 10, color: "var(--muted)", maxHeight: 100, overflowY: "auto", whiteSpace: "pre-wrap", lineHeight: 1.4 }}>
                      {confSelectedPage.text.slice(0, 1000)}{confSelectedPage.text.length > 1000 ? "..." : ""}
                    </div>
                  )}
                  {confSelectedPage.text && (
                    <div style={{ fontSize: 10, color: "var(--accent2)", marginTop: 4 }}>{confSelectedPage.text.length.toLocaleString()} characters of PRD content will be sent to AI</div>
                  )}
                </div>
              )}
              <textarea value={genSuitePrompt} onChange={e => setGenSuitePrompt(e.target.value)} placeholder="Additional instructions (optional) — e.g. focus on error handling, skip admin flows..." rows={2} className="form-input" style={{ width: "100%", marginTop: 8 }} />
            </div>
          )}

          {/* CSV Import Section */}
          <div style={{ borderTop: "1px dashed var(--border)", margin: "8px 0 12px", position: "relative" }}>
            <span style={{ position: "absolute", top: -8, left: "50%", transform: "translateX(-50%)", background: "var(--bg)", padding: "0 12px", fontSize: 10, color: "var(--muted)" }}>OR</span>
          </div>
          <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 10, flexWrap: "wrap" }}>
            <input ref={csvFileRef} type="file" accept=".csv" onChange={handleCsvUpload} style={{ display: "none" }} />
            <button className="btn-ghost btn-sm" onClick={() => csvFileRef.current?.click()} style={{ fontSize: 11 }}>📎 Upload Manual Tests (CSV)</button>
            <a href="/template.csv" download="qa_os_template.csv" style={{ fontSize: 10, color: "var(--accent)", textDecoration: "underline" }}>Download template CSV</a>
            {csvParsed.length > 0 && <button className="btn-ghost btn-sm" onClick={clearCsv} style={{ fontSize: 10, color: "var(--danger)" }}>✕ Clear CSV</button>}
          </div>

          {/* Column Mapping UI (shown when auto-detection uncertain) */}
          {csvNeedsMapping && csvHeaders.length > 0 && csvMapping && (
            <div className="panel" style={{ padding: 10, marginBottom: 10, background: "rgba(255,176,32,.05)", border: "1px solid rgba(255,176,32,.2)" }}>
              <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 6 }}>Column Mapping — auto-detection uncertain</div>
              <div style={{ display: "flex", gap: 12, flexWrap: "wrap", fontSize: 11 }}>
                {(["name", "steps", "expected", "priority"] as const).map((field) => (
                  <label key={field} style={{ display: "flex", gap: 4, alignItems: "center" }}>
                    <span style={{ minWidth: 60, fontWeight: 500 }}>{field === "name" ? "Test Name*" : field === "steps" ? "Steps*" : field === "expected" ? "Expected" : "Priority"}</span>
                    <select
                      value={csvMapping[field] ?? ""}
                      onChange={(e) => {
                        const m = { ...csvMapping, [field]: e.target.value || null } as ColumnMapping;
                        reapplyMapping(m);
                      }}
                      style={{ fontSize: 11, minWidth: 120 }}
                    >
                      <option value="">— none —</option>
                      {csvHeaders.map((h) => <option key={h} value={h}>{h}</option>)}
                    </select>
                  </label>
                ))}
              </div>
            </div>
          )}

          {/* CSV Preview */}
          {csvParsed.length > 0 && (
            <div className="panel" style={{ padding: 10, marginBottom: 10, background: "rgba(99,102,241,.04)", border: "1px solid rgba(99,102,241,.15)" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                <span style={{ fontSize: 11, fontWeight: 600 }}>Parsed Test Cases — {csvParsed.length} detected</span>
                <button className="btn-ghost btn-sm" onClick={() => setShowCsvPreview(!showCsvPreview)} style={{ fontSize: 10 }}>{showCsvPreview ? "▾ Hide" : "▸ Show"}</button>
              </div>
              {showCsvPreview && csvParsed.slice(0, 20).map((tc, i) => (
                <div key={i} style={{ fontSize: 11, padding: "4px 0", borderBottom: "1px solid var(--border)" }}>
                  <span style={{ fontWeight: 500 }}>{tc.name}</span>
                  <span style={{ color: "var(--muted)", marginLeft: 8 }}>Steps: {tc.steps.length}</span>
                  {tc.expected && <span style={{ color: "var(--muted)", marginLeft: 8 }}>Expected: {tc.expected.slice(0, 50)}{tc.expected.length > 50 ? "…" : ""}</span>}
                </div>
              ))}
              {csvParsed.length > 20 && <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 4 }}>... {csvParsed.length - 20} more</div>}
            </div>
          )}

          {genSuiteStatus && <div style={{ fontSize: 11, color: "var(--accent2)", marginBottom: 8 }}>{genSuiteStatus}</div>}
          <button className="btn-primary btn-sm" style={{ background: "linear-gradient(135deg, #6366f1, #8b5cf6)" }} onClick={aiGenerateSuite} disabled={busy || (!genSuitePrompt.trim() && csvParsed.length === 0 && !(suiteSource === "confluence" && confSelectedPage?.id)) || !genSuiteTargetId}>{busy ? "Generating..." : csvParsed.length > 0 ? `✨ Generate ${csvParsed.length} Test Cases` : confSelectedPage ? "✨ Generate from Confluence PRD" : "✨ Generate Test Suite"}</button>
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
            <button className="btn-ghost btn-sm" onClick={async () => { setBusy(true); try { const ps = await api.capturePageSource(); if (ps.ok) { setNewXml(ps.xml); toast("Page source captured", "success"); } else toast(ps.message || "No active session", "error"); } catch (e: any) { toast(e.message, "error"); } finally { setBusy(false); } }} disabled={busy} title="Capture current screen XML from Appium">📄 Capture XML</button>
          </div>
            <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 10 }}>
              <select value={genFolderId ?? ""} onChange={e => setGenFolderId(e.target.value ? Number(e.target.value) : null)} style={{ fontSize: 11, minWidth: 180 }}>
                <option value="">No screen folder</option>
                {screenFolders.map(f => <option key={f.id} value={f.id}>{f.name} ({f.screen_count} screens)</option>)}
              </select>
              {genFolderId
                ? <span style={{ fontSize: 10, color: "var(--accent)" }}>Pick folder + builds below for grounded AI.</span>
                : <span style={{ fontSize: 10, color: "var(--muted)" }}>Select a screen folder for better accuracy.</span>}
            </div>
            {genFolderId && genAiFolderScreens.some(s => s.build_id != null) && (
              <div style={{ marginBottom: 10 }}>
                <div style={{ fontSize: 10, color: "var(--muted)", marginBottom: 4 }}>Builds in folder (context)</div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                  {[...new Set(genAiFolderScreens.map(s => s.build_id).filter((id): id is number => id != null))].sort((a, b) => a - b).map((bid) => {
                    const b = builds.find(x => x.id === bid);
                    return (
                      <label key={bid} style={{ fontSize: 11, display: "flex", alignItems: "center", gap: 4, cursor: "pointer" }}>
                        <input type="checkbox" checked={genAiBuildIds.includes(bid)} onChange={() => toggleGenAiBuild(bid)} />
                        {b?.file_name ?? `Build #${bid}`}
                      </label>
                    );
                  })}
                </div>
                <div style={{ fontSize: 10, color: "var(--accent2)", marginTop: 4 }}>{genAiContextScreenCount} screen(s) will be sent to AI</div>
              </div>
            )}
            {genFolderId && !genAiFolderScreens.some(s => s.build_id != null) && genAiFolderScreens.length > 0 && (
              <div style={{ fontSize: 10, color: "var(--muted)", marginBottom: 8 }}>
                {genAiFolderScreens.length} screen(s) in folder (no build id) — all will be sent to AI.
              </div>
            )}
            {aiStatus && <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 8 }}>{aiStatus}</div>}
            <div style={{ fontSize: 10, color: "var(--muted)", marginBottom: 8 }}>While AI Generate runs, a progress bar appears under the Test Library title above.</div>
          <input value={newName} onChange={e => setNewName(e.target.value)} placeholder="Test name" className="form-input" style={{ width: "100%", marginBottom: 10 }} />
          <div style={{ marginBottom: 10 }}>
            <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 4, color: "var(--muted)" }}>Acceptance criteria (source of truth for AI Fix)</div>
            <textarea value={newAcceptanceCriteria} onChange={e => setNewAcceptanceCriteria(e.target.value)} placeholder="What this test must validate. e.g. Login: Email+Password must appear; fail if password field absent" rows={2} className="form-input" style={{ width: "100%", fontSize: 11 }} />
          </div>
          {newSelectorPickStepIndex !== null && <div style={{ fontSize: 11, color: "var(--accent)", marginBottom: 8 }}>Click an element in the XML tree below to fill step {newSelectorPickStepIndex + 1} selector</div>}
          <StepBuilder steps={newSteps} setSteps={setNewSteps} selectorPickStepIndex={newSelectorPickStepIndex} onPickStep={(i) => setNewSelectorPickStepIndex(prev => prev === i ? null : i)} figmaNames={figmaNames} platform={platform} />
          {newXml && (
            <div className="panel" style={{ marginTop: 12, padding: 12, border: "1px solid var(--border)" }}>
              <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 8, color: "var(--muted)" }}>Page Source — click an element to fill selector</div>
              <XmlElementTree xml={newXml} onCopy={(msg) => toast(msg, "success")} onNodeClick={(sel) => {
                if (newSelectorPickStepIndex !== null && newSelectorPickStepIndex < newSteps.length) {
                  setNewSteps(prev => { const n = [...prev]; n[newSelectorPickStepIndex] = { ...n[newSelectorPickStepIndex], selector: { using: sel.using, value: sel.value } }; return n; });
                  setNewSelectorPickStepIndex(null);
                  toast(`Filled step ${newSelectorPickStepIndex + 1} selector`, "success");
                }
              }} />
            </div>
          )}
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
          {editPrerequisiteId != null && (() => {
            const prereqT = tests.find(t => t.id === editPrerequisiteId);
            if (!prereqT) return null;
            return (
              <div style={{ marginBottom: 12, padding: 10, background: "rgba(251,191,36,.1)", borderRadius: 6, border: "1px solid rgba(251,191,36,.35)", fontSize: 11 }}>
                <div style={{ fontWeight: 600, marginBottom: 4, color: "var(--warn)" }}>Prerequisite</div>
                <div style={{ color: "var(--muted)", marginBottom: 8 }}>
                  &quot;{prereqT.name}&quot; runs first at execution time. The step list below is <strong>only</strong> for this test (TC-{editId}). To change prerequisite steps, open that test.
                </div>
                <button
                  type="button"
                  className="btn-ghost btn-sm"
                  onClick={() => {
                    if (confirm("Switch to prerequisite test? Unsaved changes on this test will be lost.")) openEdit(prereqT);
                  }}
                >
                  Edit prerequisite (TC-{prereqT.id})
                </button>
              </div>
            );
          })()}
          <div style={{ display: "flex", gap: 0, marginBottom: 12, borderRadius: 8, overflow: "hidden", border: "1px solid var(--border)" }}>
            {(["android", "ios_sim"] as const).map(p => (
              <button key={p} type="button" onClick={() => setEditPfTab(p)} style={{ flex: 1, padding: "8px 6px", fontSize: 11, border: "none", cursor: "pointer", background: editPfTab === p ? "var(--accent)" : "rgba(99,102,241,.12)", color: editPfTab === p ? "#042" : "var(--muted)", fontFamily: "var(--mono)" }}>
                {p === "android" ? "Android" : "iOS Simulator"} <span style={{ fontSize: 10, opacity: 0.85 }}>{(p === "android" ? editStepsAndroid : editStepsIos).length} steps</span>
              </button>
            ))}
          </div>
          <div style={{ display: "flex", gap: 8, marginBottom: 12, flexWrap: "wrap", alignItems: "center" }}>
            <input value={aiEditPrompt} onChange={e => setAiEditPrompt(e.target.value)} className="form-input" placeholder={`AI edit · ${editPfTab === "android" ? "Android" : "iOS"} steps`} style={{ flex: 1 }} />
            <button className="btn-primary btn-sm" style={{ background: "linear-gradient(135deg, #6366f1, #8b5cf6)" }} onClick={aiEditRun} disabled={busy}>🤖 AI Edit</button>
          </div>
          {aiEditStatus && <div style={{ fontSize: 11, color: "var(--accent)", marginBottom: 8 }}>{aiEditStatus}</div>}
          {editSteps.length === 0 && (
            <div style={{ padding: "20px 16px", background: "rgba(255,176,32,.06)", border: "1px dashed rgba(255,176,32,.3)", borderRadius: 6, fontSize: 12, color: "var(--warn)", marginBottom: 12 }}>
              No {editPfTab === "ios_sim" ? "iOS" : "Android"} steps generated for this test. Use AI Generate with {editPfTab === "ios_sim" ? "iOS" : "Android"} platform selected, or add steps manually below.
            </div>
          )}
          <StepBuilder steps={editSteps} setSteps={setEditSteps} stepStatuses={editStepStatuses} figmaNames={figmaNames} platform={editPfTab} />
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
        <input className="form-input" style={{ fontSize: 11, minWidth: 160, maxWidth: 260, padding: "6px 10px" }} placeholder="Search tests…" value={librarySearch} onChange={e => setLibrarySearch(e.target.value)} />
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
        {(filterCollectionId || filterSuiteIds !== null || librarySearch.trim()) && (
          <button className="btn-ghost btn-sm" style={{ fontSize: 10, marginLeft: "auto" }} onClick={() => { setFilterCollectionId(null); setFilterSuiteIds(null); setLibrarySearch(""); }}>Clear</button>
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
                  <td style={{ fontSize: 11, color: "var(--muted)" }}>{Math.max(stepsForPlatform(t, "android").length, stepsForPlatform(t, "ios_sim").length)}</td>
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
      </>}

      {/* ── Screen Library tab ── */}
      {libTab === "screens" && <>
        <div style={{ display: "grid", gridTemplateColumns: "200px 1fr", gap: 16 }}>
          {/* Folder sidebar */}
          <div className="panel" style={{ padding: 12, alignSelf: "start" }}>
            <div style={{ fontSize: 10, fontWeight: 600, color: "var(--muted)", textTransform: "uppercase", letterSpacing: ".5px", marginBottom: 8 }}>Folders</div>
            <button className={`btn-ghost btn-sm ${activeFolderId === null ? "active" : ""}`} style={{ width: "100%", textAlign: "left", fontSize: 11, marginBottom: 2 }} onClick={() => setActiveFolderId(null)}>
              All screens
            </button>
            {screenFolders.map(f => (
              <div key={f.id} style={{ display: "flex", alignItems: "center", gap: 4, marginBottom: 2 }}>
                <button className={`btn-ghost btn-sm ${activeFolderId === f.id ? "active" : ""}`} style={{ flex: 1, textAlign: "left", fontSize: 11, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} onClick={() => setActiveFolderId(f.id)}>
                  {f.name} <span style={{ color: "var(--muted)", fontSize: 10 }}>({f.screen_count})</span>
                </button>
                <button className="btn-ghost btn-sm" style={{ fontSize: 9, color: "var(--danger)", padding: "2px 4px", flexShrink: 0 }} onClick={async () => { if (confirm(`Delete folder "${f.name}" and unlink its screens?`)) { await api.deleteScreenFolder(f.id); loadScreenFolders(); if (activeFolderId === f.id) setActiveFolderId(null); loadScreens(); } }}>×</button>
              </div>
            ))}
            {showNewFolder ? (
              <div style={{ marginTop: 8, display: "flex", gap: 4 }}>
                <input type="text" value={newFolderName} onChange={e => setNewFolderName(e.target.value)} placeholder="Folder name" autoFocus style={{ flex: 1, fontSize: 11, padding: "4px 6px" }} onKeyDown={async e => {
                  if (e.key === "Enter" && newFolderName.trim()) {
                    await api.createScreenFolder({ project_id: project.id, name: newFolderName.trim() });
                    setNewFolderName(""); setShowNewFolder(false); loadScreenFolders();
                  }
                  if (e.key === "Escape") { setShowNewFolder(false); setNewFolderName(""); }
                }} />
                <button className="btn-primary btn-sm" style={{ fontSize: 10 }} disabled={!newFolderName.trim()} onClick={async () => {
                  await api.createScreenFolder({ project_id: project.id, name: newFolderName.trim() });
                  setNewFolderName(""); setShowNewFolder(false); loadScreenFolders();
                }}>Add</button>
              </div>
            ) : (
              <button className="btn-ghost btn-sm" style={{ width: "100%", textAlign: "left", fontSize: 10, color: "var(--accent)", marginTop: 6 }} onClick={() => setShowNewFolder(true)}>+ New folder</button>
            )}
          </div>

          {/* Main content */}
          <div>
            {/* Capture panel */}
            {showCapture && (() => {
              const selectedBuild =
                screenBuildFilter != null ? builds.find(b => b.id === screenBuildFilter) ?? null : null;
              const capturePlatform = selectedBuild?.platform || platform;
              return (
              <div className="panel" style={{ padding: 16, marginBottom: 12, border: "1px solid rgba(139,92,246,.25)" }}>
                <div style={{ fontFamily: "var(--sans)", fontWeight: 600, marginBottom: 4, fontSize: 13 }}>Capture Screen</div>
                <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 10 }}>
                  <strong>Start build</strong> runs install / first-folder / build-switch logic <strong>once</strong> and keeps one Appium session open. <strong>Capture</strong> only reads the UI tree and screenshot (no reinstall, no quit). Pick a <strong>specific build</strong> (not Latest) and the <strong>Device</strong> that matches your emulator. Changing build or device stops the previous session.
                </div>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", marginBottom: 10 }}>
                  <span style={{ fontSize: 11, color: screenSessionActive ? "var(--accent)" : "var(--muted)" }}>
                    {screenSessionActive ? "● Session active" : "○ No session — Start build first"}
                  </span>
                  <button
                    type="button"
                    className="btn-primary btn-sm"
                    disabled={busy || !activeFolderId || screenBuildFilter == null}
                    onClick={async () => {
                      if (screenBuildFilter == null || !selectedBuild) return;
                      setBusy(true);
                      setCaptureStatus("Starting Appium session…");
                      try {
                        const res = await api.startScreenSession({
                          project_id: project.id,
                          folder_id: activeFolderId!,
                          build_id: screenBuildFilter,
                          platform: selectedBuild.platform,
                          ...(captureDeviceId.trim() ? { device_target: captureDeviceId.trim() } : {}),
                        });
                        lastScreenSessionRef.current = {
                          build_id: screenBuildFilter,
                          device_target: captureDeviceId,
                          platform: selectedBuild.platform as "android" | "ios_sim",
                        };
                        setScreenSessionActive(true);
                        const bits: string[] = [];
                        if (res.reused) bits.push("reused existing session");
                        if (res.flags?.fresh_install) bits.push("fresh reinstall (first screen in folder)");
                        if (res.flags?.build_changed) bits.push("build switch — old app(s) removed");
                        setCaptureStatus(
                          `Session ready${bits.length ? ` — ${bits.join("; ")}` : ""}. Use Capture while you navigate; session stays open.`,
                        );
                      } catch (e: any) {
                        setCaptureStatus(`Error: ${e?.message || e}`);
                        setScreenSessionActive(false);
                        lastScreenSessionRef.current = null;
                      } finally {
                        setBusy(false);
                      }
                    }}
                  >
                    Start build
                  </button>
                  <button
                    type="button"
                    className="btn-ghost btn-sm"
                    disabled={busy || screenBuildFilter == null || !selectedBuild}
                    onClick={async () => {
                      if (screenBuildFilter == null || !selectedBuild) return;
                      setBusy(true);
                      try {
                        await api.stopScreenSession({
                          project_id: project.id,
                          build_id: screenBuildFilter,
                          platform: selectedBuild.platform,
                          ...(captureDeviceId.trim() ? { device_target: captureDeviceId.trim() } : {}),
                        });
                        lastScreenSessionRef.current = null;
                        setScreenSessionActive(false);
                        setCaptureStatus("Session stopped.");
                      } catch (e: any) {
                        setCaptureStatus(`Error: ${e?.message || e}`);
                      } finally {
                        setBusy(false);
                      }
                    }}
                  >
                    Stop session
                  </button>
                </div>
                {screenBuildFilter == null && (
                  <div style={{ fontSize: 10, color: "var(--warn)", marginBottom: 8 }}>
                    Select a specific build (not &quot;Latest&quot;) before Start build or Capture.
                  </div>
                )}
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "end" }}>
                  <div style={{ minWidth: 130 }}>
                    <label style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", display: "block", marginBottom: 3 }}>Folder</label>
                    <select value={activeFolderId ?? ""} onChange={e => setActiveFolderId(e.target.value ? Number(e.target.value) : null)} style={{ fontSize: 11, width: "100%" }}>
                      <option value="" disabled>Select folder</option>
                      {screenFolders.map(f => <option key={f.id} value={f.id}>{f.name}</option>)}
                    </select>
                  </div>
                  <div style={{ flex: 1, minWidth: 140 }}>
                    <label style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", display: "block", marginBottom: 3 }}>Screen name</label>
                    <input type="text" value={captureName} onChange={e => setCaptureName(e.target.value)} placeholder='e.g. "Login screen"' style={{ width: "100%", fontSize: 11, padding: "6px 8px" }} />
                  </div>
                  <div>
                    <label style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", display: "block", marginBottom: 3 }}>Build</label>
                    <select value={screenBuildFilter ?? ""} onChange={e => {
                      void stopScreenSessionIfAny();
                      const val = e.target.value ? Number(e.target.value) : null;
                      setScreenBuildFilter(val);
                      const b = builds.find(x => x.id === val);
                      if (b) setPlatform(b.platform as any);
                    }} style={{ fontSize: 11 }}>
                      <option value="">Latest</option>
                      {builds.map(b => <option key={b.id} value={b.id}>{b.file_name} ({b.platform})</option>)}
                    </select>
                  </div>
                  <div>
                    <label style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", display: "block", marginBottom: 3 }}>Platform</label>
                    <span className={`screen-badge screen-badge--${capturePlatform === "ios_sim" ? "ios" : "android"}`} style={{ padding: "5px 10px", fontSize: 10 }}>
                      {capturePlatform === "ios_sim" ? "iOS" : "Android"}
                    </span>
                  </div>
                  <div style={{ minWidth: 160 }}>
                    <label style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", display: "block", marginBottom: 3 }}>Device</label>
                    <select value={captureDeviceId} onChange={e => { void stopScreenSessionIfAny(); setCaptureDeviceId(e.target.value); }} style={{ fontSize: 11, width: "100%", maxWidth: 220 }}>
                      {capturePlatform === "ios_sim"
                        ? (devices.ios_simulators.length === 0
                          ? <option value="">No simulator — boot one in Xcode</option>
                          : devices.ios_simulators.map(d => <option key={d.udid} value={d.udid}>{d.name} ({d.state})</option>))
                        : (devices.android.length === 0
                          ? <option value="">No device — start emulator / USB</option>
                          : devices.android.map(d => {
                              const serial = String((d as { serial?: string }).serial || "");
                              return <option key={serial} value={serial}>{serial}</option>;
                            }))}
                    </select>
                  </div>
                  <div style={{ flex: 1, minWidth: 120 }}>
                    <label style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", display: "block", marginBottom: 3 }}>Notes</label>
                    <input type="text" value={captureNotes} onChange={e => setCaptureNotes(e.target.value)} placeholder="Optional" style={{ width: "100%", fontSize: 11, padding: "6px 8px" }} />
                  </div>
                  <button
                    className="btn-primary btn-sm"
                    disabled={
                      busy ||
                      !captureName.trim() ||
                      !activeFolderId ||
                      screenBuildFilter == null ||
                      !screenSessionActive
                    }
                    onClick={async () => {
                      if (screenBuildFilter == null) return;
                      setBusy(true);
                      setCaptureStatus("Capturing page source and screenshot…");
                      try {
                        const entry = await api.captureScreen({
                          project_id: project.id,
                          build_id: screenBuildFilter,
                          folder_id: activeFolderId!,
                          name: captureName.trim(),
                          platform: capturePlatform,
                          notes: captureNotes,
                          ...(captureDeviceId.trim() ? { device_target: captureDeviceId.trim() } : {}),
                        });
                        setCaptureStatus(`Captured "${entry.name}" — ${entry.xml_length.toLocaleString()} chars of XML`);
                        setCaptureName("");
                        setCaptureNotes("");
                        loadScreens(); loadScreenFolders();
                      } catch (e: any) {
                        setCaptureStatus(`Error: ${e?.message || e}`);
                      } finally {
                        setBusy(false);
                      }
                    }}
                  >
                    Capture
                  </button>
                </div>
                {!activeFolderId && <div style={{ marginTop: 8, fontSize: 10, color: "var(--warn)" }}>Select or create a folder first.</div>}
                {captureStatus && (
                  <div style={{ marginTop: 8, fontSize: 11, padding: "6px 10px", background: captureStatus.startsWith("Error") ? "rgba(255,59,92,.06)" : "rgba(0,229,160,.06)", borderRadius: 6, color: captureStatus.startsWith("Error") ? "var(--danger)" : captureStatus.startsWith("Capturing") ? "var(--warn)" : "var(--accent)" }}>
                    {captureStatus}
                  </div>
                )}
              </div>
              );
            })()}

            {/* Toolbar */}
            <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12, flexWrap: "wrap" }}>
              <button className={`btn-sm ${showCapture ? "btn-ghost" : "btn-primary"}`} style={{ fontSize: 11 }} onClick={() => setShowCapture(!showCapture)}>{showCapture ? "Hide Capture" : "Capture Screen"}</button>
              <div className="seg-btn" style={{ marginLeft: 8 }}>
                {(["", "android", "ios_sim"] as const).map(p => (
                  <button key={p} className={screenPlatformFilter === p ? "active" : ""} onClick={() => setScreenPlatformFilter(p)}>{p === "" ? "All" : p === "android" ? "Android" : "iOS"}</button>
                ))}
              </div>
              <div style={{ marginLeft: "auto", fontSize: 11, color: "var(--muted)" }}>
                {activeFolderId ? screenFolders.find(f => f.id === activeFolderId)?.name ?? "" : "All"} — {screens.length} screen{screens.length !== 1 ? "s" : ""}
              </div>
            </div>

            {screens.length === 0 && (
              <div className="panel" style={{ padding: 32, textAlign: "center" }}>
                <div style={{ fontSize: 14, fontWeight: 600, fontFamily: "var(--sans)", marginBottom: 8 }}>{activeFolderId ? "No screens in this folder" : "No screens yet"}</div>
                <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 16 }}>{activeFolderId ? "Click Capture Screen above to add screens to this folder." : "Create a folder in the sidebar, select it, then capture screens."}</div>
                {!showCapture && <button className="btn-primary btn-sm" onClick={() => setShowCapture(true)}>Capture Screen</button>}
              </div>
            )}

            {/* Screen grid */}
            {screens.length > 0 && (
              <div className="screen-grid">
                {screens.map(s => (
                  <div key={s.id} className={`screen-card ${s.stale ? "screen-card--stale" : ""}`} onClick={() => {
                    if (editingScreenId === s.id) return;
                    api.getScreen(s.id).then(setScreenDetail).catch(() => {});
                  }}>
                    <div className="screen-card-img">
                      {s.screenshot_path ? <img src={api.screenScreenshotUrl(s.id, s.captured_at)} alt={s.name} /> : <div className="screen-card-placeholder">No screenshot</div>}
                    </div>
                    <div className="screen-card-body">
                      {editingScreenId === s.id ? (
                        <div onClick={e => e.stopPropagation()}>
                          <input type="text" value={editScreenName} onChange={e => setEditScreenName(e.target.value)} style={{ width: "100%", fontSize: 11, marginBottom: 4, padding: "4px 6px" }} />
                          <input type="text" value={editScreenNotes} onChange={e => setEditScreenNotes(e.target.value)} placeholder="Notes..." style={{ width: "100%", fontSize: 10, padding: "4px 6px" }} />
                          <div style={{ display: "flex", gap: 4, marginTop: 6 }}>
                            <button className="btn-primary btn-sm" style={{ fontSize: 10 }} onClick={async () => {
                              await api.updateScreen(s.id, { name: editScreenName, notes: editScreenNotes });
                              setEditingScreenId(null); loadScreens();
                            }}>Save</button>
                            <button className="btn-ghost btn-sm" style={{ fontSize: 10 }} onClick={() => setEditingScreenId(null)}>Cancel</button>
                          </div>
                        </div>
                      ) : (
                        <>
                          <div className="screen-card-name">{s.name}</div>
                          <div className="screen-card-meta">
                            <span className={`screen-badge screen-badge--${s.platform === "ios_sim" ? "ios" : "android"}`}>{s.platform === "ios_sim" ? "iOS" : "Android"}</span>
                            {s.stale && <span className="screen-badge screen-badge--stale">Stale</span>}
                            <span>{s.xml_length.toLocaleString()} chars</span>
                          </div>
                          {s.notes && <div className="screen-card-notes">{s.notes}</div>}
                          <div className="screen-card-actions" onClick={e => e.stopPropagation()}>
                            <button className="btn-ghost btn-sm" style={{ fontSize: 10 }} onClick={() => { setEditingScreenId(s.id); setEditScreenName(s.name); setEditScreenNotes(s.notes || ""); }}>Edit</button>
                            <button className="btn-ghost btn-sm" style={{ fontSize: 10, color: "var(--danger)" }} onClick={async () => { if (confirm(`Delete screen "${s.name}"?`)) { await api.deleteScreen(s.id); loadScreens(); loadScreenFolders(); if (screenDetail?.id === s.id) setScreenDetail(null); } }}>Delete</button>
                          </div>
                        </>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Screen detail modal */}
        {screenDetail && (
          <div className="modal-backdrop" onClick={() => setScreenDetail(null)}>
            <div className="modal" style={{ maxWidth: 960, width: "95vw", maxHeight: "90vh", display: "flex", flexDirection: "column" }} onClick={e => e.stopPropagation()}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "16px 20px", borderBottom: "1px solid var(--border)", flexShrink: 0 }}>
                <div style={{ fontWeight: 700, fontSize: 15 }}>{screenDetail.name}</div>
                <div style={{ display: "flex", gap: 10, alignItems: "center", fontSize: 11, color: "var(--muted)" }}>
                  <span className="screen-badge">{screenDetail.platform}</span>
                  <span>Build {screenDetail.build_id ?? "—"}</span>
                  <span>{screenDetail.captured_at ? new Date(screenDetail.captured_at).toLocaleString() : ""}</span>
                  <span>{screenDetail.xml_length.toLocaleString()} chars</span>
                  <button className="btn-ghost btn-sm" onClick={() => setScreenDetail(null)}>Close</button>
                </div>
              </div>
              {screenDetail.notes && <div style={{ padding: "8px 20px", fontSize: 11, color: "var(--muted)", borderBottom: "1px solid var(--border)", flexShrink: 0 }}>{screenDetail.notes}</div>}
              <div style={{ display: "grid", gridTemplateColumns: screenDetail.screenshot_path ? "220px 1fr" : "1fr", flex: 1, minHeight: 0, overflow: "hidden" }}>
                {screenDetail.screenshot_path && (
                  <div style={{ padding: 16, overflow: "auto", borderRight: "1px solid var(--border)" }}>
                    <img src={api.screenScreenshotUrl(screenDetail.id, screenDetail.captured_at)} alt={screenDetail.name} style={{ width: "100%", borderRadius: 8, border: "1px solid var(--border)" }} />
                  </div>
                )}
                <div style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
                  <div style={{ padding: "10px 16px 6px", fontSize: 10, fontWeight: 600, color: "var(--muted)", textTransform: "uppercase", letterSpacing: ".5px", flexShrink: 0 }}>XML Source</div>
                  <pre className="code-panel" style={{ flex: 1, margin: "0 16px 16px", overflowY: "auto", overflowX: "hidden", fontSize: 10.5, lineHeight: 1.6, whiteSpace: "pre-wrap", wordBreak: "break-all", tabSize: 2 }}>
                    {screenDetail.xml_snapshot || "(empty)"}
                  </pre>
                </div>
              </div>
            </div>
          </div>
        )}
      </>}

      {/* ── Test Data tab ── */}
      {libTab === "data" && <>
        {/* New Data Set inline form */}
        {showNewDataSet && (
          <div className="panel" style={{ padding: 14, marginBottom: 12, display: "flex", gap: 8, alignItems: "center" }}>
            <input className="input-sm" placeholder="Data set name" value={newDataSetName} onChange={e => setNewDataSetName(e.target.value)} style={{ flex: 1 }} />
            <button className="btn-primary btn-sm" disabled={!newDataSetName.trim()} onClick={async () => {
              try {
                const ds = await api.createDataSet({ project_id: project.id, folder_id: activeDataFolderId, name: newDataSetName.trim() });
                toast(`Created "${ds.name}"`, "success");
                setNewDataSetName("");
                setShowNewDataSet(false);
                loadDataSets();
                loadDataFolders();
                setSelectedDataSet(ds);
              } catch (err: any) { toast(err.message || "Failed", "error"); }
            }}>Create</button>
            <button className="btn-ghost btn-sm" onClick={() => { setShowNewDataSet(false); setNewDataSetName(""); }}>Cancel</button>
          </div>
        )}

        <div style={{ display: "grid", gridTemplateColumns: "200px 1fr", gap: 16 }}>
          {/* Folder sidebar */}
          <div className="panel" style={{ padding: 12, alignSelf: "start" }}>
            <div style={{ fontSize: 10, fontWeight: 600, color: "var(--muted)", textTransform: "uppercase", letterSpacing: ".5px", marginBottom: 8 }}>Folders</div>
            <button className={`btn-ghost btn-sm ${activeDataFolderId === null ? "active" : ""}`} style={{ width: "100%", textAlign: "left", fontSize: 11, marginBottom: 2 }} onClick={() => { setActiveDataFolderId(null); setSelectedDataSet(null); }}>
              All data
            </button>
            {dataFolders.map(f => (
              <div key={f.id} style={{ display: "flex", alignItems: "center", gap: 4, marginBottom: 2 }}>
                <button className={`btn-ghost btn-sm ${activeDataFolderId === f.id ? "active" : ""}`} style={{ flex: 1, textAlign: "left", fontSize: 11, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} onClick={() => { setActiveDataFolderId(f.id); setSelectedDataSet(null); }}>
                  {f.name} <span style={{ color: "var(--muted)", fontSize: 10 }}>({f.data_set_count})</span>
                </button>
                <button className="btn-ghost btn-sm" style={{ fontSize: 9, color: "var(--danger)", padding: "2px 4px", flexShrink: 0 }} onClick={async () => {
                  if (confirm(`Delete folder "${f.name}" and all its data sets?`)) {
                    await api.deleteDataFolder(f.id);
                    loadDataFolders();
                    if (activeDataFolderId === f.id) { setActiveDataFolderId(null); setSelectedDataSet(null); }
                    loadDataSets();
                  }
                }}>×</button>
              </div>
            ))}
            {showNewDataFolder ? (
              <div style={{ display: "flex", gap: 4, marginTop: 6 }}>
                <input className="input-sm" placeholder="Folder name" value={newDataFolderName} onChange={e => setNewDataFolderName(e.target.value)} style={{ flex: 1, fontSize: 11 }} onKeyDown={e => { if (e.key === "Enter" && newDataFolderName.trim()) { api.createDataFolder({ project_id: project.id, name: newDataFolderName.trim() }).then(() => { loadDataFolders(); setNewDataFolderName(""); setShowNewDataFolder(false); }); } }} />
                <button className="btn-ghost btn-sm" style={{ fontSize: 9 }} onClick={() => { setShowNewDataFolder(false); setNewDataFolderName(""); }}>×</button>
              </div>
            ) : (
              <button className="btn-ghost btn-sm" style={{ width: "100%", textAlign: "left", fontSize: 10, marginTop: 6, color: "var(--accent)" }} onClick={() => setShowNewDataFolder(true)}>+ New folder</button>
            )}
          </div>

          {/* Main content */}
          <div>
            {!selectedDataSet ? (
              /* Data set list */
              dataSets.length === 0 ? (
                <div className="panel" style={{ padding: 24, textAlign: "center", color: "var(--muted)", fontSize: 12 }}>
                  No data sets yet. Click "+ New Data Set" or "Import CSV" to get started.
                </div>
              ) : (
                <div style={{ display: "grid", gap: 8 }}>
                  {dataSets.map(ds => (
                    <div key={ds.id} className="panel" style={{ padding: 12, cursor: "pointer", display: "flex", alignItems: "center", gap: 10 }} onClick={() => setSelectedDataSet(ds)}>
                      <div style={{ flex: 1 }}>
                        <div style={{ fontWeight: 600, fontSize: 13 }}>{ds.name} {ds.is_default && <span className="badge badge-not-run" style={{ fontSize: 9 }}>DEFAULT</span>}</div>
                        <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }}>
                          {ds.environment && <span style={{ marginRight: 8 }}>env: {ds.environment}</span>}
                          {Object.keys(ds.variables).length > 0 && <span style={{ marginRight: 8 }}>{Object.keys(ds.variables).length} variables</span>}
                          {ds.rows.length > 0 && <span>{ds.rows.length} rows</span>}
                          {Object.keys(ds.variables).length === 0 && ds.rows.length === 0 && <span>Empty</span>}
                        </div>
                      </div>
                      <div style={{ display: "flex", gap: 4 }}>
                        <button className="btn-ghost btn-sm" style={{ fontSize: 10 }} onClick={e => { e.stopPropagation(); api.duplicateDataSet(ds.id).then(() => { loadDataSets(); loadDataFolders(); toast("Duplicated", "success"); }); }}>Duplicate</button>
                        <button className="btn-ghost btn-sm" style={{ fontSize: 10 }} onClick={e => { e.stopPropagation(); api.exportDataSetCsv(ds.id); }}>CSV</button>
                        <button className="btn-ghost btn-sm" style={{ fontSize: 10 }} onClick={e => { e.stopPropagation(); api.exportDataSetJson(ds.id); }}>JSON</button>
                        <button className="btn-ghost btn-sm" style={{ fontSize: 10, color: "var(--danger)" }} onClick={async e => {
                          e.stopPropagation();
                          if (confirm(`Delete "${ds.name}"?`)) { await api.deleteDataSet(ds.id); loadDataSets(); loadDataFolders(); }
                        }}>Delete</button>
                      </div>
                    </div>
                  ))}
                </div>
              )
            ) : (
              /* Data set editor */
              <div className="panel" style={{ padding: 16 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
                  <div>
                    <button className="btn-ghost btn-sm" style={{ fontSize: 10, marginRight: 8 }} onClick={() => setSelectedDataSet(null)}>← Back</button>
                    <strong style={{ fontSize: 14 }}>{selectedDataSet.name}</strong>
                    {selectedDataSet.is_default && <span className="badge badge-not-run" style={{ fontSize: 9, marginLeft: 6 }}>DEFAULT</span>}
                  </div>
                  <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                    <select value={selectedDataSet.environment} onChange={async e => {
                      const updated = await api.updateDataSet(selectedDataSet.id, { environment: e.target.value });
                      setSelectedDataSet(updated);
                      loadDataSets();
                    }} style={{ fontSize: 11 }}>
                      <option value="">No environment</option>
                      <option value="staging">staging</option>
                      <option value="production">production</option>
                      <option value="edge">edge</option>
                      <option value="dev">dev</option>
                    </select>
                    {!selectedDataSet.is_default && (
                      <button className="btn-ghost btn-sm" style={{ fontSize: 10 }} onClick={async () => {
                        const updated = await api.setDefaultDataSet(selectedDataSet.id);
                        setSelectedDataSet(updated);
                        loadDataSets();
                        toast("Set as default", "success");
                      }}>Set Default</button>
                    )}
                  </div>
                </div>

                {/* Key-Value Variables */}
                <div style={{ marginBottom: 16 }}>
                  <div style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)", textTransform: "uppercase", letterSpacing: ".5px", marginBottom: 6 }}>Variables</div>
                  <table style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
                    <thead>
                      <tr style={{ borderBottom: "1px solid var(--border)" }}>
                        <th style={{ textAlign: "left", padding: "4px 8px", fontSize: 10, color: "var(--muted)" }}>Key</th>
                        <th style={{ textAlign: "left", padding: "4px 8px", fontSize: 10, color: "var(--muted)" }}>Value</th>
                        <th style={{ width: 50 }}></th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(selectedDataSet.variables).map(([k, v]) => (
                        <tr key={k} style={{ borderBottom: "1px solid var(--border)" }}>
                          <td style={{ padding: "4px 8px", fontFamily: "var(--mono)", fontSize: 11 }}>${"{" + k + "}"}</td>
                          <td style={{ padding: "4px 8px" }}>{v}</td>
                          <td style={{ padding: "4px 0" }}>
                            <button className="btn-ghost btn-sm" style={{ fontSize: 9, color: "var(--danger)" }} onClick={async () => {
                              const vars = { ...selectedDataSet.variables };
                              delete vars[k];
                              const updated = await api.updateDataSet(selectedDataSet.id, { variables: vars });
                              setSelectedDataSet(updated);
                            }}>×</button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
                    <input className="input-sm" placeholder="key" value={editingVarKey} onChange={e => setEditingVarKey(e.target.value)} style={{ width: 120, fontSize: 11 }} />
                    <input className="input-sm" placeholder="value" value={editingVarVal} onChange={e => setEditingVarVal(e.target.value)} style={{ flex: 1, fontSize: 11 }} />
                    <button className="btn-ghost btn-sm" disabled={!editingVarKey.trim()} onClick={async () => {
                      const vars = { ...selectedDataSet.variables, [editingVarKey.trim()]: editingVarVal };
                      const updated = await api.updateDataSet(selectedDataSet.id, { variables: vars });
                      setSelectedDataSet(updated);
                      setEditingVarKey("");
                      setEditingVarVal("");
                    }}>+ Add</button>
                  </div>
                </div>

                {/* Tabular Rows */}
                <div>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                    <div style={{ fontSize: 11, fontWeight: 600, color: "var(--muted)", textTransform: "uppercase", letterSpacing: ".5px" }}>
                      Data Rows ({selectedDataSet.rows.length})
                    </div>
                    <button className="btn-ghost btn-sm" style={{ fontSize: 10 }} onClick={async () => {
                      const cols = selectedDataSet.rows[0] ? Object.keys(selectedDataSet.rows[0]) : ["col1"];
                      const newRow: Record<string, string> = {};
                      cols.forEach(c => { newRow[c] = ""; });
                      const updated = await api.updateDataSet(selectedDataSet.id, { rows: [...selectedDataSet.rows, newRow] });
                      setSelectedDataSet(updated);
                    }}>+ Add Row</button>
                  </div>
                  {selectedDataSet.rows.length > 0 ? (
                    <div style={{ overflowX: "auto" }}>
                      <table style={{ width: "100%", fontSize: 11, borderCollapse: "collapse" }}>
                        <thead>
                          <tr style={{ borderBottom: "1px solid var(--border)" }}>
                            <th style={{ padding: "4px 6px", fontSize: 10, color: "var(--muted)", width: 30 }}>#</th>
                            {Object.keys(selectedDataSet.rows[0]).map(col => (
                              <th key={col} style={{ textAlign: "left", padding: "4px 6px", fontSize: 10, color: "var(--muted)" }}>{col}</th>
                            ))}
                            <th style={{ width: 40 }}></th>
                          </tr>
                        </thead>
                        <tbody>
                          {selectedDataSet.rows.map((row, idx) => (
                            <tr key={idx} style={{ borderBottom: "1px solid var(--border)" }}>
                              <td style={{ padding: "3px 6px", color: "var(--muted)", fontSize: 10 }}>{idx + 1}</td>
                              {Object.entries(row).map(([col, val]) => (
                                <td key={col} style={{ padding: "3px 6px" }}>
                                  <input className="input-sm" value={val || ""} style={{ width: "100%", fontSize: 11, border: "none", background: "transparent" }}
                                    onChange={e => {
                                      const newRows = [...selectedDataSet.rows];
                                      newRows[idx] = { ...newRows[idx], [col]: e.target.value };
                                      setSelectedDataSet({ ...selectedDataSet, rows: newRows });
                                    }}
                                    onBlur={async () => {
                                      await api.updateDataSet(selectedDataSet.id, { rows: selectedDataSet.rows });
                                    }}
                                  />
                                </td>
                              ))}
                              <td style={{ padding: "3px 0" }}>
                                <button className="btn-ghost btn-sm" style={{ fontSize: 9, color: "var(--danger)" }} onClick={async () => {
                                  const newRows = selectedDataSet.rows.filter((_, i) => i !== idx);
                                  const updated = await api.updateDataSet(selectedDataSet.id, { rows: newRows });
                                  setSelectedDataSet(updated);
                                }}>×</button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <div style={{ color: "var(--muted)", fontSize: 11, padding: 8 }}>No rows. Add columns first, then add rows.</div>
                  )}
                  <div style={{ display: "flex", gap: 6, marginTop: 8, alignItems: "center" }}>
                    <input className="input-sm" placeholder="column name" value={newRowCol} onChange={e => setNewRowCol(e.target.value)} style={{ width: 140, fontSize: 11 }} />
                    <button className="btn-ghost btn-sm" disabled={!newRowCol.trim()} onClick={async () => {
                      const colName = newRowCol.trim();
                      const newRows = selectedDataSet.rows.length > 0
                        ? selectedDataSet.rows.map(r => ({ ...r, [colName]: "" }))
                        : [{ [colName]: "" }];
                      const updated = await api.updateDataSet(selectedDataSet.id, { rows: newRows });
                      setSelectedDataSet(updated);
                      setNewRowCol("");
                    }}>+ Add Column</button>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </>}
    </>
  );
}
