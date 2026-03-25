# AI Integration Overhaul — Changes & Test Plan

**Date:** 2026-03-25  
**Scope:** 14 changes across 8 backend files + 3 frontend files  
**Objective:** Perfect AI generation, editing, and fixing — eliminate selector hallucination, enforce data discipline, and detect app bugs instead of hiding them.

---

## Summary of All Changes

### Phase 1 — Core Fixes

| # | Change | Files Modified | What Changed |
|---|--------|---------------|-------------|
| 1.1 | Edit-steps grounding | `routers/ai.py`, `api/index.ts` | `edit-steps` endpoint now accepts `project_id`, `folder_id`, `screen_names`, `build_ids`, `page_source_xml`. Loads Screen Library XML + screenshots for grounded editing. |
| 1.2 | XML-over-screenshot priority | `ai_rules.py` | Added "SELECTOR SOURCE OF TRUTH" and "DATA DISCIPLINE" blocks to `UNIVERSAL_RULES`. AI is now explicitly told XML is the ONLY valid selector source, screenshots are visual-only. |
| 1.3 | Selector validation | `helpers_xml.py` | New `validate_selectors_against_xml()` function checks every generated selector against raw XML using `tap_debugger` matching. Returns `grounding_score`. |
| 1.4 | System instruction split | `routers/ai.py` | New `_build_gemini_body()` helper separates system prompt into Gemini's `system_instruction` field instead of merging it into `contents`. All 5 endpoints updated. |
| 1.5 | Validate Test endpoint | `routers/ai.py`, `types/index.ts`, `api/index.ts` | New `POST /api/ai/validate-test` checks selectors against Screen Library XML, flags hardcoded data, suggests alternatives. Frontend API wired. |
| 1.6 | Data layer enforcement | `helpers_data_extraction.py`, `ai_rules.py` | Completely rewritten extractor with 30+ keyword mappings. New `enforce_data_layer()` runs on ALL AI responses (not fallback). Prompt rules enforce `${variable}` syntax. |
| 1.7 | Bug detection overhaul | `runner/ai_fix_diagnosis.py`, `routers/ai.py`, `ExecutionPage.tsx`, `types/index.ts` | New `fix_type: "bug"` with structured `bug_report`. Pre-AI `check_screen_identity()` verifies acceptance_criteria against XML. Frontend shows bug card, blocks "Apply Fix". |

### Phase 2 — Context Quality

| # | Change | Files Modified | What Changed |
|---|--------|---------------|-------------|
| 2.1 | Screenshot resolution | `helpers.py` | `compress_screenshot` max_dim 512→768, quality 60→75. AI gets clearer images. |
| 2.2 | XML caps expanded | `helpers_xml.py` | `max_elements_per_screen` 20→35, `preprocess_live_xml` max 30→50. Added 10 Android container classes (RecyclerView, ScrollView, ViewGroup, etc.) + 8 iOS types (Table, Cell, TabBar, etc.). |
| 2.3 | Fix relevance scoring | `routers/ai.py` | `preprocess_live_xml` in fix-steps and refine-fix now uses `test_name + error_message` as description for better relevance filtering. |
| 2.4 | Temperatures lowered | `routers/ai.py` | Generate: 0.15→0.1, Suite: 0.15→0.1, Fix: 0.2→0.1, Refine: 0.2→0.15, Edit: 0.2→0.1. |

### Phase 3 — Advanced Improvements

| # | Change | Files Modified | What Changed |
|---|--------|---------------|-------------|
| 3.1 | Two-pass self-correction | `routers/ai.py` | When `grounding_score < 80%` on grounded generation, an automatic correction pass re-sends ungrounded selectors + XML to Gemini at temperature 0.05. |
| 3.2 | Flow-aware screen selection | `helpers_xml.py` | `select_relevant_screens()` detects flow language ("after", "then", "navigate to") and includes adjacent screens in folder order for multi-screen flows. |
| 3.3 | Figma component injection | `routers/ai.py` | New `_figma_hint()` fetches component names from Figma API and injects them as naming/understanding hints into generate-steps and generate-suite prompts. |

---

## Test Plan

### Prerequisites

- Backend running on `http://127.0.0.1:9001`
- Frontend running on `http://localhost:5173`
- A project with at least one Screen Library folder containing 2+ captured screens with XML snapshots
- An Appium session connected (for live testing)
- AI API key configured in Settings

---

### TEST 1: XML-over-Screenshot Priority (Phase 1.2)

**What changed:** AI now has explicit rules that XML is the ONLY valid selector source.

**Steps:**
1. Go to a test definition with Screen Library screens attached
2. Click "AI Generate" and provide a test objective like "Login with email and password"
3. Let AI generate the steps

**Verify:**
- Every generated selector's `value` exists verbatim in the Screen Library XML
- No selectors derived from text visible only in screenshots (e.g., display labels used as IDs)
- The response includes a `grounding_score` object with `matched` and `total` counts

---

### TEST 2: Selector Validation / Grounding Score (Phase 1.3)

**What changed:** Every AI response is now validated against raw XML.

**Steps:**
1. Generate steps for a test case with Screen Library screens
2. Observe the response JSON (check browser DevTools Network tab)

**Verify:**
- Response contains `grounding_score: { matched: N, total: M }`
- `matched/total` should be high (>80%) for grounded generation
- Each step has `_grounded: true/false` field

---

### TEST 3: Two-Pass Self-Correction (Phase 3.1)

**What changed:** If initial generation scores below 80%, a correction pass auto-runs.

**Steps:**
1. Use a complex prompt with ambiguous element references against Screen Library screens
2. Generate steps

**Verify:**
- Check backend logs for two Gemini API calls (the second one is the correction pass)
- Final grounding score should be higher than it would have been otherwise
- Steps should use valid XML selectors

---

### TEST 4: Data Layer Enforcement (Phase 1.6)

**What changed:** All hardcoded test data is now auto-extracted into `${variable}` references.

**Steps:**
1. Generate steps for "Login with email test@example.com and password MyP@ss123"
2. Examine the returned `steps` and `test_data`

**Verify:**
- Step text fields use `${email}`, `${password}` — NOT raw values
- `test_data` contains: `{ "email": "test@example.com", "password": "MyP@ss123" }`
- If a DataSet is auto-created, check it in the project's Data Layer

**Additional check:**
1. Generate steps for "Enter OTP 4567 and verify welcome message shows Hello John"
2. Verify `${otp}` and `${expected_name}` (or similar) are used, not raw "4567" or "Hello John"

---

### TEST 5: Edit-Steps with Grounding (Phase 1.1)

**What changed:** The edit-steps endpoint now accepts Screen Library context.

**Steps:**
1. Open a test case that already has steps
2. Use "AI Edit" with an instruction like "Add a step to verify the dashboard title is visible"
3. In the browser DevTools Network tab, check the request payload

**Verify:**
- Request body now includes `project_id`, `folder_id` (if applicable)
- Response includes `grounded: true` and `grounding_score`
- Edited steps use valid XML selectors from Screen Library
- New data values (if any) use `${variable}` syntax with `data_fixes` populated

---

### TEST 6: System Instruction Split (Phase 1.4)

**What changed:** System prompt is now in `system_instruction` field, not merged into `contents`.

**Steps:**
1. Trigger any AI generation (generate, edit, fix, refine, suite)
2. Check backend logs or add a temporary `print(json.dumps(body, indent=2))` in `_build_gemini_body`

**Verify:**
- The Gemini request body has: `{ "system_instruction": { "parts": [...] }, "contents": [...] }`
- The system prompt is NOT concatenated at the top of the `contents[0].parts[0].text`
- This is a structural improvement — behavior should be noticeably better for following rules

---

### TEST 7: Validate Test Endpoint (Phase 1.5)

**What changed:** New `/api/ai/validate-test` endpoint for on-demand test validation.

**Steps:**
1. Open browser DevTools Console
2. Run the following (adjust IDs):
```javascript
const res = await fetch('/api/ai/validate-test', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    platform: 'android',
    steps: [
      { type: 'tap', selector: { using: 'id', value: 'com.example:id/real_button' }, description: 'Tap real button' },
      { type: 'tap', selector: { using: 'id', value: 'fake_nonexistent_id' }, description: 'Tap fake' },
      { type: 'clearAndType', selector: { using: 'id', value: 'com.example:id/email' }, text: 'test@test.com', description: 'Type email' }
    ],
    project_id: YOUR_PROJECT_ID,
    folder_id: YOUR_FOLDER_ID,
  })
});
console.log(await res.json());
```

**Verify:**
- `valid: false` (because of fake ID and hardcoded email)
- `issues` array contains:
  - `type: "selector_not_found"` for the fake ID
  - `type: "hardcoded_data"` for the raw email
- `suggestions` array may contain alternative selectors
- `grounding_score` and `total_selectors` are populated

---

### TEST 8: Bug Detection in AI Fix (Phase 1.7)

**What changed:** AI can now report `fix_type: "bug"` when the app is on the wrong screen.

**Steps to simulate a bug scenario:**
1. Create a test case with acceptance_criteria: "On Login screen (login_title visible), user enters credentials, taps Login, and lands on Home Dashboard (dashboard_container visible)"
2. Run the test
3. If a step fails because the app navigated to the wrong screen (e.g., a Sign Up page instead of Login), let AI Fix analyze it
4. Alternatively, manually trigger the fix-steps endpoint with a `page_source_xml` that doesn't match the acceptance_criteria

**Verify:**
- If the XML has none of the acceptance_criteria terms (like "login_title", "dashboard_container"), the fix response should return `fix_type: "bug"`
- A `bug_report` object should be present with: `title`, `severity`, `expected_screen`, `actual_screen`, `expected_behavior`, `actual_behavior`, `evidence`
- In the UI, a red "Bug Detected" badge appears (not a step/data fix badge)
- The bug report card is rendered with severity, expected vs actual screen, and evidence
- "Apply Fix & Rerun" button is replaced with "Fix not applicable — Bug detected"
- "Download Bug Report" button still works

**Key behavioral check:**
- The `fixed_steps` should be a copy of `original_steps` UNCHANGED
- AI should NOT have adapted the test to work on the wrong screen

---

### TEST 9: Screenshot Resolution Improvement (Phase 2.1)

**What changed:** Screenshots now 768px max at 75% quality (was 512px at 60%).

**Steps:**
1. Capture a screen in the Screen Library
2. Generate steps using that screen
3. Check the Gemini request body (backend logs) to see the image data

**Verify:**
- The base64 image is larger than before (better quality)
- Visual AI understanding should be improved for fine UI details
- No performance regression (request still completes in reasonable time)

---

### TEST 10: Expanded XML Element Caps (Phase 2.2)

**What changed:** More elements pass through the XML filter to the AI.

**Steps:**
1. Capture a screen with a complex layout (RecyclerView, ScrollView, tabs, etc.)
2. Generate steps for interacting with items in a scrollable list

**Verify:**
- The preprocessed XML sent to AI includes container elements (RecyclerView, ScrollView, TabWidget, etc.)
- Previously filtered-out elements now appear in the DOM CONTEXT
- AI can generate scroll/swipe steps referencing these containers

---

### TEST 11: Temperature Reduction (Phase 2.4)

**What changed:** All generation temperatures lowered for more deterministic output.

**Steps:**
1. Generate the same test steps 3 times with identical inputs

**Verify:**
- Results should be more consistent across runs
- Fewer hallucinated or creative selectors
- Steps should follow rules more strictly

---

### TEST 12: Flow-Aware Screen Selection (Phase 3.2)

**What changed:** When prompts mention flow (after, then, navigate to), adjacent screens are included.

**Steps:**
1. Have a Screen Library folder with screens in order: "Splash", "Login", "Dashboard", "Profile"
2. Generate a suite with: "After logging in, navigate to the Profile screen and verify user details"

**Verify:**
- The AI should receive Login, Dashboard, and Profile screens (not just Profile)
- Steps should cover the full flow from Login → Dashboard → Profile
- Adjacent screens are included even if they don't score highest on keyword matching

---

### TEST 13: Figma Component Injection (Phase 3.3)

**What changed:** Figma component names are injected into generation prompts.

**Prerequisites:** Figma token and file key configured in Settings.

**Steps:**
1. Ensure Figma integration is configured (Settings → Integrations → Figma)
2. Generate steps for any test case

**Verify:**
- In the Gemini request, the user message contains a "FIGMA COMPONENT NAMES" section
- Component names are listed as naming hints
- Step descriptions should reference meaningful component names
- Figma names are NOT used as selectors — only for understanding and naming

---

### TEST 14: Fix Relevance Scoring (Phase 2.3)

**What changed:** Fix-steps XML filtering now uses test_name + error_message for relevance.

**Steps:**
1. Run a test that fails
2. Trigger AI Fix

**Verify:**
- The filtered XML sent to the fix endpoint prioritizes elements related to the test name and error
- The fix should be more targeted because the AI sees the most relevant elements first

---

## Quick Regression Checks

| Area | What to Check |
|------|--------------|
| AI Generate (grounded) | Steps generate correctly, `test_data` populated, `grounding_score` returned |
| AI Generate (ungrounded) | Steps generate without Screen Library, still uses data layer |
| AI Edit | Existing edit functionality works, now with optional grounding |
| AI Fix | Fix suggestions still work for normal failures (COMPOSE_ID, KEYBOARD_COVERING, etc.) |
| AI Refine Fix | Refine with user suggestions still works, data_fixes applied |
| AI Suite Generation | Suite generation produces multiple test cases with acceptance_criteria |
| Screen Library | Capture, browse, delete screens all still work |
| Data Layer | Variables auto-created from AI responses, applied to DataSets |
| Execution | Full test execution unaffected — these changes only touch AI prompt/response flow |

---

## Files Changed (Full List)

### Backend
| File | Type of Change |
|------|---------------|
| `app/ai_rules.py` | Added SELECTOR SOURCE OF TRUTH + DATA DISCIPLINE to UNIVERSAL_RULES |
| `app/helpers.py` | `compress_screenshot` resolution 512→768, quality 60→75 |
| `app/helpers_data_extraction.py` | Complete rewrite — expanded detection, new `enforce_data_layer()` |
| `app/helpers_xml.py` | Expanded interactive classes, increased caps, added `validate_selectors_against_xml()`, flow-aware screen selection |
| `app/routers/ai.py` | System instruction split, `_build_gemini_body()`, `_load_screen_context()`, `_gemini_call()`, `_figma_hint()`, temperatures lowered, two-pass correction, edit-steps grounding, validate-test endpoint, bug detection in fix-steps, data enforcement on all endpoints |
| `app/runner/ai_fix_diagnosis.py` | Added `CAUSE_WRONG_SCREEN_BUG`, `check_screen_identity()`, bug detection rules in `AI_FIX_CLASSIFICATION_RULES` |

### Frontend
| File | Type of Change |
|------|---------------|
| `src/ui/types/index.ts` | Added `BugReport`, `ValidationIssue`, `ValidationSuggestion`, `ValidateTestResponse` types. Extended `AiFixResponse` with `bug_report` and `fix_type: "bug"` |
| `src/ui/api/index.ts` | `editSteps()` now accepts optional context params, added `validateTest()` |
| `src/ui/pages/ExecutionPage.tsx` | Bug report display card, bug detection badge, blocked "Apply Fix" for bug fix_type |
