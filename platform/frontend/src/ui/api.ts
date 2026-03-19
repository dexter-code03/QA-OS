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
  name: string;
  steps: Array<Record<string, unknown>>;
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

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  if (!res.ok) throw new Error(await res.text());
  return (await res.json()) as T;
}

export const api = {
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
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(`/api/projects/${projectId}/builds?platform=${platform}`, { method: "POST", body: fd });
    if (!res.ok) throw new Error(await res.text());
    return (await res.json()) as Build;
  },

  // Tests
  listTests: (projectId: number) => http<TestDef[]>(`/api/projects/${projectId}/tests`),
  createTest: (projectId: number, payload: { name: string; steps: any[]; suite_id?: number | null }) =>
    http<TestDef>(`/api/projects/${projectId}/tests`, { method: "POST", body: JSON.stringify(payload) }),
  updateTest: (testId: number, payload: { name?: string; steps?: any[]; suite_id?: number | null }) =>
    http<TestDef>(`/api/tests/${testId}`, { method: "PUT", body: JSON.stringify(payload) }),
  deleteTest: (testId: number) =>
    http<{ ok: boolean }>(`/api/tests/${testId}`, { method: "DELETE" }),

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
  }) =>
    http<{ analysis: string; fixed_steps: any[]; changes: any[] }>("/api/ai/fix-steps", {
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
