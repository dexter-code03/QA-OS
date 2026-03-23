export type {
  Project,
  ModuleDef,
  SuiteDef,
  Build,
  TestDef,
  Run,
  BatchRunChild,
  BatchRun,
  DeviceList,
  TapDiagnosisOut,
  AiFixResponse,
  UploadProgressOpts,
  TestHealthRow,
  SuiteHealthResponse,
  SuiteTrendItem,
  StepCoverageItem,
  TriageResponse,
  CollectionHealthResponse,
  BlockerItem,
  ScreenFolder,
  ScreenEntry,
  ScreenEntryFull,
} from "../types";

import type {
  Project,
  ModuleDef,
  SuiteDef,
  Build,
  TestDef,
  Run,
  BatchRunChild,
  BatchRun,
  DeviceList,
  TapDiagnosisOut,
  AiFixResponse,
  UploadProgressOpts,
  SuiteHealthResponse,
  SuiteTrendItem,
  StepCoverageItem,
  TriageResponse,
  CollectionHealthResponse,
  BlockerItem,
  ScreenFolder,
  ScreenEntry,
  ScreenEntryFull,
} from "../types";

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
