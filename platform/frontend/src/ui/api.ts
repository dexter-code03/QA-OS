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
  status: string;
  platform: "android" | "ios_sim";
  device_target: string;
  started_at: string | null;
  finished_at: string | null;
  error_message: string | null;
  summary: Record<string, unknown>;
  artifacts: Record<string, unknown>;
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

  importZip: async (projectId: number, platform: string, file: File, opts?: UploadProgressOpts) => {
    const url = `/api/projects/${projectId}/import/zip?platform=${encodeURIComponent(platform)}`;
    return xhrPostFormDataJson<{
      groups: Record<string, any[]>;
      total_cases: number;
      total_files: number;
      warnings: string[];
      files: { path: string; cases_count: number; status: string }[];
    }>(url, () => {
      const fd = new FormData();
      fd.append("file", file);
      return fd;
    }, opts?.onUploadProgress);
  },

  confirmZipImport: (
    projectId: number,
    payload: { suite_map?: Record<string, number>; module_id?: number; test_cases: any[]; platform: string },
  ) =>
    http<{ created: number; created_suites: string[]; tests: any[] }>(
      `/api/projects/${projectId}/import/zip/confirm`,
      { method: "POST", body: JSON.stringify(payload) },
    ),

  importFolder: async (
    projectId: number,
    platform: string,
    files: File[] | FileList,
    opts?: UploadProgressOpts,
  ) => {
    const list = Array.isArray(files) ? files : Array.from(files);
    if (!list.length) {
      throw new Error("No files to upload — folder picker returned empty (try Browse folder again).");
    }
    const url = `/api/projects/${projectId}/import/folder?platform=${encodeURIComponent(platform)}`;
    return xhrPostFormDataJson<{
      groups: Record<string, any[]>;
      total_cases: number;
      total_files: number;
      warnings: string[];
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
  generateSteps: (platform: string, prompt: string, pageSourceXml?: string) =>
    http<{ steps: any[] }>("/api/ai/generate-steps", {
      method: "POST",
      body: JSON.stringify({ platform, prompt, page_source_xml: pageSourceXml || "" }),
    }),
  generateSuite: (platform: string, prompt: string, projectId: number, suiteId: number, pageSourceXml?: string) =>
    http<{ created: number; test_cases: { id: number; name: string; steps_count: number }[] }>("/api/ai/generate-suite", {
      method: "POST",
      body: JSON.stringify({ platform, prompt, project_id: projectId, suite_id: suiteId, page_source_xml: pageSourceXml || "" }),
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

  // Katalon export
  exportKatalon: (runId: number) => {
    window.open(`/api/runs/${runId}/katalon`, "_blank");
  },
};
