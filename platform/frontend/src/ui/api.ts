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
  if (!res.ok) throw new Error(await res.text());
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
  createTest: (projectId: number, payload: { name: string; steps: any[]; suite_id?: number | null; prerequisite_test_id?: number | null; acceptance_criteria?: string | null }) =>
    http<TestDef>(`/api/projects/${projectId}/tests`, { method: "POST", body: JSON.stringify(payload) }),
  updateTest: (testId: number, payload: { name?: string; steps?: any[]; suite_id?: number | null; prerequisite_test_id?: number | null; acceptance_criteria?: string | null }) =>
    http<TestDef>(`/api/tests/${testId}`, { method: "PUT", body: JSON.stringify(payload) }),
  deleteTest: (testId: number) =>
    http<{ ok: boolean }>(`/api/tests/${testId}`, { method: "DELETE" }),
  getRelatedTests: (testId: number) =>
    http<{ dependents: TestDef[]; similar: { test: TestDef; shared_prefix_length: number }[] }>(`/api/tests/${testId}/related`),
  applyFixToRelated: (testId: number, payload: { fixed_steps: any[]; prefix_length: number; original_steps: any[]; test_ids?: number[] }) =>
    http<{ updated_test_ids: number[] }>(`/api/tests/${testId}/apply-fix-to-related`, { method: "POST", body: JSON.stringify(payload) }),
  appendFixHistory: (testId: number, payload: { analysis: string; fixed_steps: any[]; changes: any[]; run_id?: number; steps_before_fix?: any[] }) =>
    http<{ ok: boolean }>(`/api/tests/${testId}/append-fix-history`, { method: "POST", body: JSON.stringify(payload) }),
  undoLastFix: (testId: number) =>
    http<{ ok: boolean; steps: any[] }>(`/api/tests/${testId}/undo-last-fix`, { method: "POST" }),

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
  }) =>
    http<{ analysis: string; fixed_steps: any[]; changes: any[] }>("/api/ai/fix-steps", {
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
  }) =>
    http<{ analysis: string; fixed_steps: any[]; changes: any[] }>("/api/ai/refine-fix", {
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
