export type Project = { id: number; name: string; created_at: string };
export type ModuleDef = { id: number; project_id: number; name: string; created_at: string };
export type SuiteDef = { id: number; module_id: number; name: string; created_at: string };
export type Build = {
  id: number;
  project_id: number;
  platform: "android" | "ios_sim";
  file_name: string;
  created_at: string;
  metadata: Record<string, unknown>;
};
export type TestDef = {
  id: number;
  project_id: number;
  suite_id: number | null;
  prerequisite_test_id?: number | null;
  name: string;
  steps: Array<Record<string, unknown>>;
  platform_steps?: { android?: Array<Record<string, unknown>>; ios_sim?: Array<Record<string, unknown>> };
  acceptance_criteria?: string | null;
  fix_history?: Array<Record<string, unknown>>;
  created_at: string;
};
export type Run = {
  id: number;
  project_id: number;
  build_id: number | null;
  test_id: number | null;
  batch_run_id?: number | null;
  status: string;
  platform: "android" | "ios_sim";
  device_target: string;
  started_at: string | null;
  finished_at: string | null;
  error_message: string | null;
  summary: Record<string, unknown>;
  artifacts: Record<string, unknown>;
};
export type BatchRunChild = {
  run_id: number;
  test_id: number;
  test_name: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  error_message: string | null;
};
export type BatchRun = {
  id: number;
  project_id: number;
  mode: "suite" | "collection";
  source_id: number;
  source_name: string;
  platform: string;
  status: string;
  total: number;
  passed: number;
  failed: number;
  build_id: number | null;
  device_target: string;
  started_at: string | null;
  finished_at: string | null;
  children: BatchRunChild[];
};
export type DeviceList = {
  android: Array<Record<string, string>>;
  ios_simulators: Array<{ udid: string; name: string; state: string; runtime: string }>;
};

export type TapDiagnosisOut = {
  found: boolean;
  root_cause: string;
  root_cause_detail: string;
  is_clickable: boolean;
  is_visible: boolean;
  recommended_wait_ms: number;
  suggestions: { strategy: string; value: string; score: number; label: string }[];
};

export type AiFixResponse = {
  analysis: string;
  fixed_steps: any[];
  changes: any[];
  tap_diagnosis: TapDiagnosisOut | null;
  failure_diagnosis?: {
    cause?: string;
    evidence?: string[];
    recommended_fix?: string | null;
    recommended_strategy?: string | null;
    recommended_value?: string | null;
  } | null;
};

let authBootstrapped = false;
let authBootstrapPromise: Promise<string> | null = null;

async function bootstrapAuth(force = false): Promise<string> {
  if (authBootstrapped && !force) return "";
  if (authBootstrapPromise && !force) return authBootstrapPromise;

  authBootstrapPromise = fetch("/api/auth/token", { credentials: "same-origin" })
    .then(async (res) => {
      if (!res.ok) throw new Error(await res.text());
      const data = (await res.json()) as { token?: string };
      authBootstrapped = true;
      return data.token || "";
    })
    .finally(() => {
      authBootstrapPromise = null;
    });

  return authBootstrapPromise;
}

export type UploadProgressOpts = { onUploadProgress?: (pct: number | null) => void };

function errorFromResponseText(raw: string, status: number): Error {
  try {
    const j = JSON.parse(raw) as { detail?: unknown };
    if (j.detail != null) {
      return new Error(typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail));
    }
  } catch {
    /* not JSON */
  }
  return new Error(raw || `Request failed (${status})`);
}

/** Multipart POST with upload byte progress (0–100 when length-computable). */
async function xhrPostFormDataJson<T>(
  url: string,
  buildFormData: () => FormData,
  onUploadProgress?: (pct: number | null) => void,
): Promise<T> {
  await bootstrapAuth();
  const exec = () =>
    new Promise<{ status: number; text: string }>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", url);
      xhr.withCredentials = true;
      xhr.upload.onloadstart = () => {
        onUploadProgress?.(0);
      };
      xhr.upload.onprogress = (ev) => {
        if (!onUploadProgress) return;
        if (ev.lengthComputable && ev.total > 0) {
          onUploadProgress(Math.min(100, Math.round((100 * ev.loaded) / ev.total)));
        } else {
          onUploadProgress(null);
        }
      };
      xhr.onload = () => resolve({ status: xhr.status, text: xhr.responseText || "" });
      xhr.onerror = () => reject(new Error("Network error during upload"));
      if (onUploadProgress) {
        onUploadProgress(0);
      }
      xhr.send(buildFormData());
    });

  let { status, text } = await exec();
  if (status === 401) {
    await bootstrapAuth(true);
    ({ status, text } = await exec());
  }
  if (status < 200 || status >= 300) throw errorFromResponseText(text, status);
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new Error("Invalid JSON from server");
  }
}

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  if (path !== "/api/auth/token") {
    await bootstrapAuth();
  }

  const headers = new Headers(init?.headers || {});
  if (!headers.has("Content-Type") && init?.body && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }

  let res = await fetch(path, {
    ...init,
    headers,
    credentials: "same-origin",
  });
  if (res.status === 401 && path !== "/api/auth/token") {
    await bootstrapAuth(true);
    res = await fetch(path, {
      ...init,
      headers,
      credentials: "same-origin",
    });
  }
  if (!res.ok) {
    const raw = await res.text();
    let body: { detail?: unknown } | undefined;
    try {
      body = JSON.parse(raw) as { detail?: unknown };
    } catch {
      /* not JSON */
    }
    if (body?.detail != null) {
      throw new Error(typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail));
    }
    throw new Error(raw || `Request failed (${res.status})`);
  }
  return (await res.json()) as T;
}

export const api = {
  bootstrapAuth,
  health: () => http<{ status: string }>("/api/health"),

  // Projects
  listProjects: () => http<Project[]>("/api/projects"),
  createProject: (name: string) =>
    http<Project>("/api/projects", { method: "POST", body: JSON.stringify({ name }) }),

  // Modules
  listModules: (projectId: number) => http<ModuleDef[]>(`/api/projects/${projectId}/modules`),
  createModule: (projectId: number, name: string) =>
    http<ModuleDef>(`/api/projects/${projectId}/modules`, { method: "POST", body: JSON.stringify({ name }) }),
  renameModule: (moduleId: number, name: string) =>
    http<ModuleDef>(`/api/modules/${moduleId}`, { method: "PUT", body: JSON.stringify({ name }) }),
  deleteModule: (moduleId: number) =>
    http<{ ok: boolean }>(`/api/modules/${moduleId}`, { method: "DELETE" }),

  // Suites
  listSuites: (moduleId: number) => http<SuiteDef[]>(`/api/modules/${moduleId}/suites`),
  createSuite: (moduleId: number, name: string) =>
    http<SuiteDef>(`/api/modules/${moduleId}/suites`, { method: "POST", body: JSON.stringify({ name }) }),
  renameSuite: (suiteId: number, name: string) =>
    http<SuiteDef>(`/api/suites/${suiteId}`, { method: "PUT", body: JSON.stringify({ name }) }),
  deleteSuite: (suiteId: number) =>
    http<{ ok: boolean }>(`/api/suites/${suiteId}`, { method: "DELETE" }),

  // Builds
  listBuilds: (projectId: number) => http<Build[]>(`/api/projects/${projectId}/builds`),
  uploadBuild: async (projectId: number, platform: Build["platform"], file: File) => {
    await bootstrapAuth();
    const fd = new FormData();
    fd.append("file", file);
    let res = await fetch(`/api/projects/${projectId}/builds?platform=${platform}`, {
      method: "POST",
      body: fd,
      credentials: "same-origin",
    });
    if (res.status === 401) {
      await bootstrapAuth(true);
      res = await fetch(`/api/projects/${projectId}/builds?platform=${platform}`, {
        method: "POST",
        body: fd,
        credentials: "same-origin",
      });
    }
    if (!res.ok) throw new Error(await res.text());
    return (await res.json()) as Build;
  },
  deleteBuild: (buildId: number) =>
    http<{ ok: boolean }>(`/api/builds/${buildId}`, { method: "DELETE" }),

  // Tests
  listTests: (projectId: number) => http<TestDef[]>(`/api/projects/${projectId}/tests`),
  createTest: (
    projectId: number,
    payload: {
      name: string;
      steps?: any[];
      platform_steps?: { android?: any[]; ios_sim?: any[] };
      suite_id?: number | null;
      prerequisite_test_id?: number | null;
      acceptance_criteria?: string | null;
    },
  ) => http<TestDef>(`/api/projects/${projectId}/tests`, { method: "POST", body: JSON.stringify(payload) }),
  updateTest: (
    testId: number,
    payload: {
      name?: string;
      steps?: any[];
      platform?: "android" | "ios_sim";
      platform_steps?: { android?: any[]; ios_sim?: any[] };
      suite_id?: number | null;
      prerequisite_test_id?: number | null;
      acceptance_criteria?: string | null;
    },
  ) => http<TestDef>(`/api/tests/${testId}`, { method: "PUT", body: JSON.stringify(payload) }),
  deleteTest: (testId: number) =>
    http<{ ok: boolean }>(`/api/tests/${testId}`, { method: "DELETE" }),
  getRelatedTests: (testId: number) =>
    http<{ dependents: TestDef[]; similar: { test: TestDef; shared_prefix_length: number }[] }>(`/api/tests/${testId}/related`),
  applyFixToRelated: (testId: number, payload: { fixed_steps: any[]; prefix_length: number; original_steps: any[]; test_ids?: number[]; target_platform?: "android" | "ios_sim" }) =>
    http<{ updated_test_ids: number[] }>(`/api/tests/${testId}/apply-fix-to-related`, { method: "POST", body: JSON.stringify(payload) }),
  appendFixHistory: (testId: number, payload: { analysis: string; fixed_steps: any[]; changes: any[]; run_id?: number; steps_before_fix?: any[]; target_platform?: "android" | "ios_sim" }) =>
    http<{ ok: boolean }>(`/api/tests/${testId}/append-fix-history`, { method: "POST", body: JSON.stringify(payload) }),
  undoLastFix: (testId: number) =>
    http<{ ok: boolean; steps: any[]; target_platform?: string }>(`/api/tests/${testId}/undo-last-fix`, { method: "POST" }),

  // Script / sheet import (preview → confirm)
  importScript: async (
    projectId: number,
    suiteId: number,
    platform: string,
    file: File,
    opts?: UploadProgressOpts,
  ) => {
    const url = `/api/projects/${projectId}/import/script?suite_id=${suiteId}&platform=${encodeURIComponent(platform)}`;
    return xhrPostFormDataJson<{
      test_cases: any[];
      filename: string;
      warnings: string[];
      katalon_import_mode?: "ai" | "heuristic";
    }>(
      url,
      () => {
        const fd = new FormData();
        fd.append("file", file);
        return fd;
      },
      opts?.onUploadProgress,
    );
  },

  confirmScriptImport: (projectId: number, payload: { suite_id: number | null; test_cases: any[] }) =>
    http<{ created: number; tests: { id: number; name: string }[] }>(
      `/api/projects/${projectId}/import/script/confirm`,
      { method: "POST", body: JSON.stringify(payload) },
    ),

  importSheet: async (
    projectId: number,
    suiteId: number,
    platform: string,
    file: File,
    opts?: UploadProgressOpts,
  ) => {
    const url = `/api/projects/${projectId}/import/sheet?suite_id=${suiteId}&platform=${encodeURIComponent(platform)}`;
    return xhrPostFormDataJson<{
      test_cases: any[];
      filename: string;
      row_count: number;
      scripts?: { name: string; content: string }[];
      warnings?: string[];
    }>(url, () => {
      const fd = new FormData();
      fd.append("file", file);
      return fd;
    }, opts?.onUploadProgress);
  },

  getReportsHierarchy: (projectId: number) => http<any>(`/api/projects/${projectId}/reports/hierarchy`),

  importZip: async (projectId: number, platform: string, file: File, opts?: UploadProgressOpts & { folderId?: number | null; buildIds?: number[] }) => {
    let url = `/api/projects/${projectId}/import/zip?platform=${encodeURIComponent(platform)}`;
    if (opts?.folderId) url += `&folder_id=${opts.folderId}`;
    if (opts?.buildIds?.length) url += `&build_ids=${opts.buildIds.join(",")}`;
    return xhrPostFormDataJson<{
      groups: Record<string, any[]>;
      total_cases: number;
      total_files: number;
      warnings: string[];
      grounding: string;
      object_repo_count: number;
      collections: Record<string, string[]>;
      katalon_detected: boolean;
      files: { path: string; cases_count: number; status: string }[];
    }>(url, () => {
      const fd = new FormData();
      fd.append("file", file);
      return fd;
    }, opts?.onUploadProgress);
  },

  confirmZipImport: (
    projectId: number,
    payload: { suite_map?: Record<string, number>; module_id?: number; test_cases: any[]; platform: string; collections?: Record<string, string[]> },
  ) =>
    http<{ created: number; created_suites: string[]; created_modules?: string[]; tests: any[] }>(
      `/api/projects/${projectId}/import/zip/confirm`,
      { method: "POST", body: JSON.stringify(payload) },
    ),

  importFolder: async (
    projectId: number,
    platform: string,
    files: File[] | FileList,
    opts?: UploadProgressOpts & { folderId?: number | null; buildIds?: number[] },
  ) => {
    const list = Array.isArray(files) ? files : Array.from(files);
    if (!list.length) {
      throw new Error("No files to upload — folder picker returned empty (try Browse folder again).");
    }
    let url = `/api/projects/${projectId}/import/folder?platform=${encodeURIComponent(platform)}`;
    if (opts?.folderId) url += `&folder_id=${opts.folderId}`;
    if (opts?.buildIds?.length) url += `&build_ids=${opts.buildIds.join(",")}`;
    return xhrPostFormDataJson<{
      groups: Record<string, any[]>;
      total_cases: number;
      total_files: number;
      warnings: string[];
      grounding: string;
      object_repo_count: number;
      collections: Record<string, string[]>;
      katalon_detected: boolean;
      files: { path: string; cases_count: number; status: string }[];
    }>(
      url,
      () => {
        const fd = new FormData();
        for (const f of list) {
          const rel = ((f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name).replace(/\\/g, "/");
          fd.append("files", f, rel);
        }
        return fd;
      },
      opts?.onUploadProgress,
    );
  },

  downloadKatalonZip: async (
    projectId: number,
    payload: { test_case_ids?: number[]; test_cases?: any[]; project_name: string },
  ) => {
    await bootstrapAuth();
    let res = await fetch(`/api/projects/${projectId}/generate/katalon-zip`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(payload),
    });
    if (res.status === 401) {
      await bootstrapAuth(true);
      res = await fetch(`/api/projects/${projectId}/generate/katalon-zip`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify(payload),
      });
    }
    if (!res.ok) throw new Error(await res.text());
    const blob = await res.blob();
    const safe = (payload.project_name || "QA_Project").replace(/\s+/g, "_");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${safe}_katalon.zip`;
    a.click();
  },

  // Batch Runs
  createBatchRun: (payload: {
    project_id: number;
    build_id?: number | null;
    mode: "suite" | "collection";
    source_id: number;
    platform: Run["platform"];
    device_target?: string;
  }) => http<BatchRun>("/api/batch-runs", { method: "POST", body: JSON.stringify(payload) }),
  getBatchRun: (batchId: number) => http<BatchRun>(`/api/batch-runs/${batchId}`),
  listBatchRuns: (projectId: number) => http<BatchRun[]>(`/api/projects/${projectId}/batch-runs`),
  cancelBatchRun: (batchId: number) => http<{ ok: boolean; message?: string }>(`/api/batch-runs/${batchId}/cancel`, { method: "POST" }),

  // Runs
  listRuns: (projectId: number) => http<Run[]>(`/api/projects/${projectId}/runs`),
  createRun: (payload: {
    project_id: number;
    build_id?: number | null;
    test_id: number;
    platform: Run["platform"];
    device_target?: string;
  }) => http<Run>("/api/runs", { method: "POST", body: JSON.stringify(payload) }),
  getRun: (runId: number) => http<Run>(`/api/runs/${runId}`),
  cancelRun: (runId: number) => http<{ ok: boolean; message?: string }>(`/api/runs/${runId}/cancel`, { method: "POST" }),
  deleteRun: (runId: number) => http<{ ok: boolean }>(`/api/runs/${runId}`, { method: "DELETE" }),

  // Devices
  listDevices: () => http<DeviceList>("/api/devices"),

  // Settings
  getSettings: () => http<Record<string, any>>("/api/settings"),
  saveSettings: (data: Record<string, any>) =>
    http<Record<string, any>>("/api/settings", { method: "POST", body: JSON.stringify(data) }),

  // Onboarding
  getOnboarding: () => http<{ completed: boolean }>("/api/onboarding"),
  completeOnboarding: () =>
    http<{ completed: boolean }>("/api/onboarding/complete", { method: "POST" }),

  // AI
  generateSteps: (
    platform: string,
    prompt: string,
    pageSourceXml?: string,
    opts?: {
      screen_names?: string[];
      folder_id?: number | null;
      project_id?: number;
      build_id?: number | null;
      build_ids?: number[];
    },
  ) =>
    http<{ steps: any[]; grounded?: boolean; screens_used?: number }>("/api/ai/generate-steps", {
      method: "POST",
      body: JSON.stringify({ platform, prompt, page_source_xml: pageSourceXml || "", ...(opts || {}) }),
    }),
  generateSuite: (
    platform: string,
    prompt: string,
    projectId: number,
    suiteId: number,
    pageSourceXml?: string,
    folderId?: number | null,
    buildIds?: number[],
  ) =>
    http<{ created: number; test_cases: { id: number; name: string; steps_count: number }[] }>("/api/ai/generate-suite", {
      method: "POST",
      body: JSON.stringify({
        platform,
        prompt,
        project_id: projectId,
        suite_id: suiteId,
        page_source_xml: pageSourceXml || "",
        ...(folderId ? { folder_id: folderId } : {}),
        ...(buildIds && buildIds.length > 0 ? { build_ids: buildIds } : {}),
      }),
    }),
  editSteps: (platform: string, currentSteps: any[], instruction: string) =>
    http<{ steps: any[]; summary: string }>("/api/ai/edit-steps", {
      method: "POST",
      body: JSON.stringify({ platform, current_steps: currentSteps, instruction }),
    }),
  capturePageSource: () =>
    http<{ ok: boolean; xml: string; message?: string }>("/api/appium/page-source", { method: "POST" }),

  // AI Fix
  fixSteps: (payload: {
    platform: string;
    original_steps: any[];
    step_results: any[];
    failed_step_index: number;
    error_message: string;
    page_source_xml: string;
    /** Raw Appium page source for server tap diagnosis (strict XML). */
    page_source_xml_raw?: string;
    test_name: string;
    screenshot_base64: string;
    already_tried_fixes?: any[];
    acceptance_criteria?: string;
    app_context?: string;
    target_platform?: string;
  }) =>
    http<AiFixResponse>("/api/ai/fix-steps", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  refineFix: (payload: {
    platform: string;
    original_steps: any[];
    step_results: any[];
    failed_step_index: number;
    error_message: string;
    page_source_xml: string;
    page_source_xml_raw?: string;
    test_name: string;
    screenshot_base64: string;
    acceptance_criteria?: string;
    app_context?: string;
    fix_history?: any[];
    previous_analysis: string;
    previous_fixed_steps: any[];
    previous_changes: any[];
    user_suggestion: string;
    target_platform?: string;
  }) =>
    http<AiFixResponse>("/api/ai/refine-fix", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  // Connection tests
  testAppium: () => http<{ ok: boolean; message: string }>("/api/test-connection/appium", { method: "POST" }),
  testConfluence: () => http<{ ok: boolean; message: string }>("/api/test-connection/confluence", { method: "POST" }),
  testAI: () => http<{ ok: boolean; message: string }>("/api/test-connection/ai", { method: "POST" }),
  listFigmaComponents: () => http<{ names: string[] }>("/api/integrations/figma/components"),
  syncConfluenceProject: (projectId: number) =>
    http<{ ok: boolean; page_id?: string; page_url: string; space_key?: string; title: string }>(
      `/api/projects/${projectId}/confluence/sync`,
      { method: "POST" },
    ),

  // Katalon export
  exportKatalon: (runId: number) => {
    window.open(`/api/runs/${runId}/katalon`, "_blank");
  },

  // ── Screen Library ──────────────────────────────────
  listScreenFolders: (projectId: number) =>
    http<ScreenFolder[]>(`/api/screen-folders?project_id=${projectId}`),
  createScreenFolder: (body: { project_id: number; name: string }) =>
    http<ScreenFolder>("/api/screen-folders", { method: "POST", body: JSON.stringify(body) }),
  deleteScreenFolder: (id: number) =>
    http<{ ok: boolean }>(`/api/screen-folders/${id}`, { method: "DELETE" }),

  startScreenSession: (body: {
    project_id: number;
    folder_id: number;
    build_id: number;
    platform: string;
    device_target?: string;
  }) =>
    http<{ ok: boolean; started: boolean; reused?: boolean; flags: { fresh_install?: boolean; build_changed?: boolean } }>(
      "/api/screens/session/start",
      { method: "POST", body: JSON.stringify(body) },
    ),
  stopScreenSession: (body: {
    project_id: number;
    build_id: number;
    platform: string;
    device_target?: string;
  }) => http<{ ok: boolean; stopped: boolean }>("/api/screens/session/stop", { method: "POST", body: JSON.stringify(body) }),
  screenSessionStatus: (q: { project_id: number; build_id: number; platform: string; device_target?: string }) => {
    const params = new URLSearchParams({
      project_id: String(q.project_id),
      build_id: String(q.build_id),
      platform: q.platform,
    });
    if (q.device_target) params.set("device_target", q.device_target);
    return http<{ active: boolean; created_at?: string; last_used?: string }>(`/api/screens/session/status?${params}`);
  },
  captureScreen: (body: {
    project_id: number;
    build_id: number;
    folder_id: number;
    name: string;
    platform: string;
    notes?: string;
    /** Android serial or iOS simulator UDID — must match the device you are looking at */
    device_target?: string;
  }) => http<ScreenEntry>("/api/screens/capture", { method: "POST", body: JSON.stringify(body) }),
  listScreens: (projectId: number, opts?: { buildId?: number | null; folderId?: number | null; platform?: string }) => {
    const params = new URLSearchParams({ project_id: String(projectId) });
    if (opts?.buildId != null) params.set("build_id", String(opts.buildId));
    if (opts?.folderId != null) params.set("folder_id", String(opts.folderId));
    if (opts?.platform) params.set("platform", opts.platform);
    return http<ScreenEntry[]>(`/api/screens?${params}`);
  },
  getScreen: (id: number) => http<ScreenEntryFull>(`/api/screens/${id}`),
  updateScreen: (id: number, body: { name?: string; notes?: string; folder_id?: number | null }) =>
    http<ScreenEntry>(`/api/screens/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteScreen: (id: number) => http<{ ok: boolean }>(`/api/screens/${id}`, { method: "DELETE" }),
  screenScreenshotUrl: (id: number, cacheBust?: string | null) =>
    `/api/screens/${id}/screenshot${cacheBust != null && cacheBust !== "" ? `?v=${encodeURIComponent(cacheBust)}` : ""}`,

  // ── Reports v2 ──────────────────────────────────────
  getSuiteHealth: (suiteId: number, days = 14, platform = "") =>
    http<SuiteHealthResponse>(`/api/suites/${suiteId}/health?days=${days}&platform=${encodeURIComponent(platform)}`),
  getSuiteTrend: (suiteId: number, days = 14, platform = "") =>
    http<SuiteTrendItem[]>(`/api/suites/${suiteId}/trend?days=${days}&platform=${encodeURIComponent(platform)}`),
  getSuiteStepCoverage: (suiteId: number, days = 14, platform = "") =>
    http<StepCoverageItem[]>(`/api/suites/${suiteId}/step-coverage?days=${days}&platform=${encodeURIComponent(platform)}`),
  getSuiteTriage: (suiteId: number, days = 14, platform = "") =>
    http<TriageResponse>(`/api/suites/${suiteId}/triage?days=${days}&platform=${encodeURIComponent(platform)}`),
  getCollectionHealth: (collectionId: number, days = 14, platform = "") =>
    http<CollectionHealthResponse>(`/api/collections/${collectionId}/health?days=${days}&platform=${encodeURIComponent(platform)}`),
  getCollectionBlockers: (collectionId: number, days = 14, platform = "") =>
    http<BlockerItem[]>(`/api/collections/${collectionId}/blockers?days=${days}&platform=${encodeURIComponent(platform)}`),
  downloadSuiteHtml: (suiteId: number, days = 14, platform = "") => {
    window.open(`/api/suites/${suiteId}/export/html?days=${days}&platform=${encodeURIComponent(platform)}`, "_blank");
  },
  downloadSuiteCsv: (suiteId: number, days = 14, platform = "") => {
    window.open(`/api/suites/${suiteId}/export/csv?days=${days}&platform=${encodeURIComponent(platform)}`, "_blank");
  },
  downloadSuiteScreenshots: (suiteId: number, days = 14, platform = "") => {
    window.open(`/api/suites/${suiteId}/export/screenshots?days=${days}&platform=${encodeURIComponent(platform)}`, "_blank");
  },
  downloadCollectionHtml: (collectionId: number, days = 14, platform = "") => {
    window.open(`/api/collections/${collectionId}/export/html?days=${days}&platform=${encodeURIComponent(platform)}`, "_blank");
  },
};

// ── Report types ──────────────────────────────────────

export interface TestHealthRow {
  id: number;
  name: string;
  status: "passing" | "failing" | "flaky" | "not_run";
  acceptance_criteria: string;
  steps_ran: number;
  steps_total: number;
  platform: string;
  pass_rate_pct: number;
  fail_streak: number;
  last_passed_at: string | null;
  ai_fixes_count: number;
  run_history: { id: number; status: string; platform: string }[];
  last_failed_run: {
    id: number;
    error_message: string | null;
    failure_category: string;
    step_results: {
      index: number;
      type: string;
      selector: any;
      status: string;
      duration_ms: number | null;
      error: string | null;
      screenshot: string | null;
    }[];
    ai_fix: { analysis: string; fixed_steps: any[]; changes: any[] } | null;
    platform: string;
    started_at: string | null;
  } | null;
}

export interface SuiteHealthResponse {
  suite: { id: number; name: string; module_name: string; last_run_at: string | null; pass_rate: number };
  metrics: { total: number; passing: number; failing: number; flaky: number; never_run: number; avg_steps_pct: number };
  tests: TestHealthRow[];
}

export interface SuiteTrendItem {
  test_case_id: number;
  test_name: string;
  pass_count: number;
  total_runs: number;
  pass_rate_pct: number;
}

export interface StepCoverageItem {
  test_case_id: number;
  test_name: string;
  avg_steps_ran: number;
  avg_steps_total: number;
  coverage_pct: number;
}

export interface TriageResponse {
  categories: {
    category: string;
    count: number;
    pct: number;
    affected_tests: { id: number; name: string; error_message: string }[];
  }[];
  total_failures: number;
}

export interface CollectionHealthResponse {
  collection: { id: number; name: string; pass_rate: number; verdict: string };
  metrics: { total: number; passing: number; failing: number; blockers: number; flaky: number; never_run: number };
  suites: {
    id: number;
    name: string;
    pass_rate_pct: number;
    pass_count: number;
    fail_count: number;
    blocker_count: number;
    last_run_at: string | null;
    total: number;
  }[];
  trend_30d: { date: string; pass_rate_pct: number }[];
}

export interface BlockerItem {
  test_id: number;
  test_name: string;
  suite_name: string;
  error_message: string;
  fail_streak: number;
  run_id: number;
  screenshot_path: string | null;
  ai_fix_available: boolean;
}

export interface ScreenFolder {
  id: number;
  project_id: number;
  name: string;
  screen_count: number;
  created_at: string | null;
}

export interface ScreenEntry {
  id: number;
  project_id: number;
  build_id: number | null;
  folder_id: number | null;
  name: string;
  platform: string;
  screenshot_path: string | null;
  captured_at: string | null;
  captured_by: string | null;
  notes: string | null;
  auto_captured: boolean;
  xml_length: number;
  /** android: compose | native; ios: native; omitted on older rows */
  screen_type?: string | null;
  stale?: boolean;
  /** True when this was the first capture in an empty folder — backend uninstalled + reinstalled the build */
  fresh_install?: boolean;
  /** True when you selected a different build than existing screens in this folder — old app(s) removed and new build installed */
  build_changed?: boolean;
}

export interface ScreenEntryFull extends ScreenEntry {
  xml_snapshot: string;
}
