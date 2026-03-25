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
  data_set_id?: number | null;
  data_row_index?: number | null;
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
  mode: "suite" | "collection" | "data-driven";
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

export type BugReport = {
  title: string;
  severity: "critical" | "major" | "minor";
  expected_screen: string;
  actual_screen: string;
  expected_behavior: string;
  actual_behavior: string;
  evidence: string;
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
  fix_type?: "step" | "data" | "both" | "bug";
  bug_report?: BugReport | null;
  data_fixes?: Record<string, string>;
  data_set_updated?: boolean;
};

export type ValidationIssue = {
  step_index: number;
  type: "selector_not_found" | "hardcoded_data" | "wrong_strategy";
  detail: string;
};

export type ValidationSuggestion = {
  step_index: number;
  suggested_selector: { using: string; value: string };
  confidence: number;
};

export type ValidateTestResponse = {
  valid: boolean;
  grounding_score: number;
  total_selectors: number;
  issues: ValidationIssue[];
  suggestions: ValidationSuggestion[];
};

export interface ManualTestCase {
  name: string;
  steps: string[];
  expected: string;
  priority: string | null;
}

export type UploadProgressOpts = { onUploadProgress?: (pct: number | null) => void };

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
  latest_run: {
    id: number;
    status: string;
    platform: string;
    started_at: string | null;
    finished_at: string | null;
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
  screen_type?: string | null;
  stale?: boolean;
  fresh_install?: boolean;
  build_changed?: boolean;
}

export interface ScreenEntryFull extends ScreenEntry {
  xml_snapshot: string;
}

// ── API Logs ────────────────────────────────────────────────────────

export interface ApiLog {
  id: string;
  run_id?: number;
  step_index?: number;
  timestamp: string;
  method: string;
  url: string;
  status_code: number;
  duration_ms: number;
  req_headers: Record<string, string>;
  req_body: string | null;
  res_headers: Record<string, string>;
  res_body: string | null;
}

// ── Data Layer ──────────────────────────────────────────────────────

export interface DataFolder {
  id: number;
  project_id: number;
  name: string;
  description: string;
  data_set_count: number;
  created_at: string | null;
}

export interface DataSet {
  id: number;
  project_id: number;
  folder_id: number | null;
  name: string;
  description: string;
  environment: string;
  variables: Record<string, string>;
  rows: Record<string, string>[];
  is_default: boolean;
  created_at: string | null;
  updated_at: string | null;
}
