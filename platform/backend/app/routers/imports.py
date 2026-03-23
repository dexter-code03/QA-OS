from __future__ import annotations

import asyncio
import json
import re
from io import BytesIO
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Body, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from ..db import SessionLocal
from ..helpers import (
    ai_creds,
    android_selector_generation_rules,
    build_xml_context,
    gemini_extract_text,
    ios_selector_generation_rules,
    load_settings,
    steps_for_platform_record,
)
from ..models import Module, Project, ScreenLibrary, TestDefinition, TestSuite
from ..parser.script_generator import generate_katalon_zip, safe_katalon_name, steps_to_groovy
from ..parser.script_parser import (
    group_steps_into_test_cases,
    katalon_or_leaves_and_aliases,
    parse_groovy,
    parse_gherkin,
    parse_test_sheet,
    sheet_row_combined_steps,
)
from ..parser.zip_importer import (
    ParsedFile,
    extract_folder_name,
    parse_folder_files,
    parse_katalon_project,
    parse_object_repo_from_files,
    parse_object_repo_from_zip,
    parse_zip,
    _normalize_katalon_path,
    _tc_id_from_groovy_path,
)
from ..schemas import TestOut  # noqa: F401
from ..settings import ensure_dirs, settings  # noqa: F401

router = APIRouter()

# ── Script / Sheet import & Reports hierarchy ─────────────────────────-

_NO_SELECTOR_STEP_TYPES = frozenset({"wait", "takeScreenshot", "hideKeyboard", "swipe", "gherkin_raw", "python_raw"})


def _validate_test_cases(test_cases: list[dict]) -> list[str]:
    warnings: list[str] = []
    for tc in test_cases:
        bad = 0
        for s in tc.get("steps", []) or []:
            t = s.get("type")
            if t in _NO_SELECTOR_STEP_TYPES:
                continue
            sel = s.get("selector") or {}
            if not (isinstance(sel, dict) and sel.get("value")):
                bad += 1
        if bad:
            name = tc.get("name", "?")
            warnings.append(f"'{name}': {bad} step(s) have empty selectors")
    return warnings


def _groovy_scripts_for_sheet_cases(test_cases: list[dict], filename: str) -> list[dict[str, Any]]:
    """Katalon Groovy source per test case; mutates each tc with groovy_script."""
    stem = safe_katalon_name(Path(filename).stem) if filename else "ImportedSheet"
    scripts: list[dict[str, Any]] = []
    for tc in test_cases:
        steps = tc.get("steps") or []
        name = tc.get("name", "Unnamed")
        g_content = steps_to_groovy(
            test_name=name,
            steps=steps,
            screen_name=stem,
            source_hint=f"Generated from {filename or 'sheet'}",
        )
        tc["groovy_script"] = g_content
        scripts.append({"name": safe_katalon_name(name) + ".groovy", "content": g_content})
    return scripts


async def _ai_complete_gherkin(
    raw_steps: list, platform: str, source: str, api_key: str, model: str
) -> list[dict]:
    if not api_key:
        return [{"name": "Imported from Gherkin", "steps": raw_steps, "import": True, "acceptance_criteria": ""}]
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    prompt = (
        "Convert this Gherkin feature file into Appium test cases. "
        f"Platform: {platform}. Return ONLY JSON: "
        '{"test_cases": [{"name": "...", "steps": [...], "acceptance_criteria": "..."}]}. '
        "Use accessibilityId selectors where possible.\n\n" + source[:6000]
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }
    async with httpx.AsyncClient(timeout=45) as client:
        r = await client.post(url, json=body)
        if r.status_code != 200:
            return [{"name": "Imported from Gherkin", "steps": raw_steps, "import": True, "acceptance_criteria": ""}]
        try:
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(text)
            return [dict(tc, **{"import": True}) for tc in parsed.get("test_cases", [])]
        except (KeyError, IndexError, json.JSONDecodeError, TypeError):
            return [{"name": "Imported from Gherkin", "steps": raw_steps, "import": True, "acceptance_criteria": ""}]


async def _ai_parse_python_script(source: str, platform: str, api_key: str, model: str) -> list[dict]:
    if not api_key:
        return []
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    prompt = (
        f"Parse this Appium Python test script into test cases for platform {platform}. "
        "Map driver.find_element+click to tap, send_keys to type, WebDriverWait+visibility to waitForVisible, assertions to assertText/assertVisible. "
        "Available step types: tap, doubleTap, longPress, tapByCoordinates, type, clear, clearAndType, "
        "wait, waitForVisible, waitForNotVisible, waitForEnabled, waitForDisabled, swipe, scroll, "
        "assertText, assertTextContains, assertVisible, assertNotVisible, assertEnabled, assertChecked, assertAttribute, "
        "pressKey, keyboardAction, hideKeyboard, launchApp, closeApp, resetApp, takeScreenshot, getPageSource.\n"
        'Return ONLY JSON: {"test_cases": [{"name": "...", "steps": [...], "acceptance_criteria": "..."}]}.\n\n'
        + source[:6000]
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }
    async with httpx.AsyncClient(timeout=45) as client:
        r = await client.post(url, json=body)
        if r.status_code != 200:
            return []
        try:
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(text)
            return [dict(tc, **{"import": True}) for tc in parsed.get("test_cases", [])]
        except (KeyError, IndexError, json.JSONDecodeError, TypeError):
            return []


def _normalize_ai_katalon_cases(
    parsed: dict[str, Any], stem: str, fallback_cases: list[dict[str, Any]]
) -> list[dict[str, Any]] | None:
    cases = parsed.get("test_cases")
    if not isinstance(cases, list) or not cases:
        return None
    out: list[dict[str, Any]] = []
    for i, tc in enumerate(cases):
        if not isinstance(tc, dict):
            return None
        name = str(tc.get("name") or "").strip() or stem
        steps = tc.get("steps")
        if not isinstance(steps, list) or len(steps) == 0:
            fb = (
                fallback_cases[i]
                if i < len(fallback_cases)
                else (fallback_cases[0] if fallback_cases else None)
            )
            if not fb:
                return None
            tc = {**fb, "name": name}
        else:
            tc["name"] = name
        tc["import"] = True
        tc.setdefault("acceptance_criteria", str(tc.get("acceptance_criteria") or "") or "")
        out.append(tc)
    return out


def _katalon_ai_locator_looks_like_ui_label(value: str, leaves: set[str]) -> bool:
    v = (value or "").strip()
    if not v:
        return True
    if v in leaves:
        return False
    if re.search(r"\s&\s", v) or "  " in v:
        return True
    # Visible copy: phrase with spaces, no OR-style token (underscore / camel prefix)
    if " " in v and "_" not in v:
        return True
    return False


def _merge_katalon_ai_steps_with_heuristic(
    ai_steps: list[dict[str, Any]],
    heuristic_steps: list[dict[str, Any]],
    leaves: set[str],
) -> list[dict[str, Any]]:
    """Keep AI step types/order where reasonable; restore OR leaf selectors from heuristic when AI used UI labels."""
    out: list[dict[str, Any]] = []
    for i, s in enumerate(ai_steps):
        merged: dict[str, Any] = dict(s)
        h = heuristic_steps[i] if i < len(heuristic_steps) else None
        hs = h.get("selector") if h else None
        hv = hs.get("value") if isinstance(hs, dict) else None
        sel = merged.get("selector")
        if isinstance(sel, dict) and isinstance(hs, dict) and hv and str(hv).strip():
            av = str(sel.get("value") or "").strip()
            if av != str(hv).strip():
                if _katalon_ai_locator_looks_like_ui_label(av, leaves) or (hv in leaves and av not in leaves):
                    merged["selector"] = {
                        **sel,
                        "using": hs.get("using") or sel.get("using") or "accessibilityId",
                        "value": hv,
                    }
        elif isinstance(sel, dict) and not str(sel.get("value") or "").strip() and hv:
            merged["selector"] = dict(hs) if isinstance(hs, dict) else merged.get("selector")
        out.append(merged)
    return out


def _apply_katalon_locator_snap(
    ai_cases: list[dict[str, Any]],
    fallback_cases: list[dict[str, Any]],
    source: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Snap AI selectors to Object Repository leaves; if AI invents far more steps than the script,
    trust the rule-based parse for this file (common model failure on callTestCase / imports).
    """
    notes: list[str] = []
    leaves, _ = katalon_or_leaves_and_aliases(source)
    out: list[dict[str, Any]] = []
    for ci, tc in enumerate(ai_cases):
        if not isinstance(tc, dict):
            out.append(tc)
            continue
        fb = (
            fallback_cases[ci]
            if ci < len(fallback_cases)
            else (fallback_cases[0] if fallback_cases else {})
        )
        h_steps = (fb.get("steps") or []) if isinstance(fb, dict) else []
        steps = tc.get("steps") or []
        if not isinstance(steps, list) or not h_steps:
            out.append(tc)
            continue
        n_ai, n_h = len(steps), len(h_steps)
        if n_h > 0 and n_ai > max(20, n_h * 3):
            name = str(tc.get("name") or "?")
            out.append({**tc, "steps": list(h_steps)})
            notes.append(
                f'"{name}": AI produced {n_ai} steps vs {n_h} from script; using rule-based parse '
                f"(set AI API key and re-import if you need AI — or shorten script for context)."
            )
            continue
        out.append({**tc, "steps": _merge_katalon_ai_steps_with_heuristic(steps, h_steps, leaves)})
    return out, notes


async def _ai_parse_katalon_source(
    source: str,
    platform: str,
    logical_path: str,
    api_key: str,
    model: str,
    fallback_cases: list[dict[str, Any]],
    *,
    object_repo: Optional[dict[str, dict[str, str]]] = None,
    xml_context: str = "",
    platform_selector_rules: str = "",
) -> tuple[list[dict[str, Any]] | None, list[str]]:
    """Parse Katalon Groovy/Java mobile scripts with Gemini; None = caller uses heuristic fallback."""
    extra_warnings: list[str] = []
    if not api_key or not source.strip():
        return None, extra_warnings
    stem = Path(logical_path).stem or "Imported"
    max_chars = 56_000
    src = source if len(source) <= max_chars else source[:max_chars] + "\n\n// ... [truncated for AI context] ...\n"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    system = (
        "You parse Katalon Studio Mobile test scripts (Groovy/Java calling Mobile.*) into JSON for Appium.\n"
        f"Target platform: {platform} (android = UiAutomator2, ios_sim = XCUITest).\n\n"
        "Output ONLY valid JSON:\n"
        '{"test_cases":[{"name":"...","steps":[...],"acceptance_criteria":"..."}]}\n\n'
        "Step object shape:\n"
        '{"type":"<step_type>",'
        '"selector":{"using":"accessibilityId|id|xpath|className","value":"..."},'
        '"text":"...","ms":1000,"expect":"...","meta":{...}}\n'
        "Available step types: tap, doubleTap, longPress, tapByCoordinates, type, clear, clearAndType, "
        "wait, waitForVisible, waitForNotVisible, waitForEnabled, waitForDisabled, swipe, scroll, "
        "assertText, assertTextContains, assertVisible, assertNotVisible, assertEnabled, assertChecked, assertAttribute, "
        "pressKey, keyboardAction, hideKeyboard, launchApp, closeApp, resetApp, takeScreenshot, getPageSource.\n"
        "Omit selector on: wait, takeScreenshot, tapByCoordinates, pressKey, keyboardAction, hideKeyboard, launchApp, closeApp, resetApp, getPageSource.\n\n"
        "SELECTOR RULES (mandatory):\n"
        "- For EVERY element, resolve TestObject/def variables: `TestObject x = findTestObject('Object Repository/.../leafName')`. "
        "The locator token is the substring AFTER the final '/' — e.g. txtBox_libiPrompt, btn_TextEnterButton. "
        "Set selector.value to that leaf EXACTLY: keep underscores, hyphens, camelCase; never replace with human-readable UI text.\n"
        "- Mobile.tap(txtPrompt, ...) means: look up txtPrompt's findTestObject binding and use THAT leaf as selector.value.\n"
        "- FORBIDDEN as selector.value: strings from base.logInfo, user-visible setText bodies, screenshot file names, "
        "or prettified labels like 'Selfcare & transactions' unless the script literally uses that exact token in findTestObject.\n"
        "- If the script only has callTestCase(findTestCase(...)) lines and no local Mobile.* steps, output minimal waits — "
        "do NOT invent a long unrelated checkout flow.\n"
        "- Resource ids in literals/comments: use using=id with full resource id.\n"
        "- For verifyElementText, set expect from the script's expected string.\n"
        "- Map waitForElementPresent / retryWaitForElementPresent to waitForVisible; timeout seconds → ms.\n"
        "- Map Mobile.delay / delay to wait with ms.\n"
        "- scrollToText: use swipe (direction in text, usually down) and waitForVisible/assertVisible for the target when you can name a selector; "
        "otherwise minimal swipe + wait.\n"
        "- callTestCase: use wait steps to reflect handoff, keep subsequent flow.\n"
        "- System keyboard keys: keyboardAction, not tap.\n\n"
        "Use one test_cases entry unless the file clearly defines multiple independent tests (e.g. several unrelated test methods). "
        f'Default name: "{stem}".\n'
        f"Source file path hint: {logical_path}\n"
    )

    # Append Object Repository locator table when available
    if object_repo:
        repo_lines = [f"  {leaf}: using={info['strategy']} value={info['value']}" for leaf, info in sorted(object_repo.items())]
        system += (
            "\n--- OBJECT REPOSITORY (leafName → real locator) ---\n"
            "When a findTestObject leaf matches an entry below, use its exact strategy and value "
            "instead of guessing accessibilityId:\n"
            + "\n".join(repo_lines[:500]) + "\n"
        )

    # Append Screen Library XML context as fallback grounding
    if xml_context:
        system += (
            "\n--- SCREEN LIBRARY XML (captured page sources) ---\n"
            "Use the elements below to ground selectors to real attributes from the device. "
            "Prefer resource-id, content-desc, or accessibility id over xpath.\n"
            + xml_context[:30_000] + "\n"
        )

    # Inject platform-specific Compose/SwiftUI selector rules when Screen Library context is present
    if platform_selector_rules:
        system += "\n" + platform_selector_rules

    body = {
        "contents": [{"parts": [{"text": system + "\n--- SOURCE ---\n" + src}]}],
        "generationConfig": {"temperature": 0.08, "responseMimeType": "application/json"},
    }
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(url, json=body)
        if r.status_code != 200:
            return None, extra_warnings
        text = gemini_extract_text(r.json())
        merged = _normalize_ai_katalon_cases(json.loads(text), stem, fallback_cases)
        if merged:
            merged, snap_notes = _apply_katalon_locator_snap(merged, fallback_cases, source)
            extra_warnings.extend(snap_notes)
            return merged, extra_warnings
    except (HTTPException, json.JSONDecodeError, TypeError, KeyError, IndexError, httpx.HTTPError, Exception):
        return None, extra_warnings
    return None, extra_warnings


async def _rewrite_katalon_parsed_files_with_ai(
    parsed_files: list[ParsedFile],
    platform: str,
    api_key: str,
    model: str,
    *,
    object_repo: Optional[dict[str, dict[str, str]]] = None,
    xml_context: str = "",
    platform_selector_rules: str = "",
) -> None:
    if not api_key:
        return
    sem = asyncio.Semaphore(4)

    async def one(pf: ParsedFile) -> None:
        if pf.error or pf.extension not in (".groovy", ".java"):
            return
        raw = (pf.raw_text or "").strip()
        if not raw:
            return
        async with sem:
            fallback = list(pf.test_cases)
            ai_cases, ai_notes = await _ai_parse_katalon_source(
                raw, platform, pf.path, api_key, model, fallback,
                object_repo=object_repo, xml_context=xml_context,
                platform_selector_rules=platform_selector_rules,
            )
        if ai_cases:
            pf.test_cases = ai_cases
            pf.warnings.extend(ai_notes)
            if not any("parsed with AI" in w for w in pf.warnings):
                pf.warnings.append("Katalon steps parsed with AI — verify selectors on a real device.")

    await asyncio.gather(*(one(pf) for pf in parsed_files))


def _sheet_fallback_chunk(chunk: list[dict]) -> list[dict]:
    return [
        {
            "name": r["name"],
            "steps": sheet_row_combined_steps(r),
            "acceptance_criteria": r.get("expected", "") or "",
            "import": True,
        }
        for r in chunk
    ]


async def _ai_complete_sheet_rows(rows: list, platform: str, api_key: str, model: str) -> list[dict]:
    if not rows:
        return []
    if not api_key:
        return _sheet_fallback_chunk(rows)

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    CHUNK = 12
    all_out: list[dict] = []

    for i in range(0, len(rows), CHUNK):
        chunk = rows[i : i + CHUNK]
        n = len(chunk)
        instruction = (
            f"Platform: {platform}. Convert manual test sheet rows into Appium mobile test steps (JSON).\n"
            f"Input: exactly {n} rows in order. Each row uses:\n"
            "- name: test case title\n"
            "- steps_description: free-text steps (may be multi-line)\n"
            "- expected: expected result (must drive a final assertion when it describes an outcome)\n"
            "- selector_value / selector_strategy / input_value: optional locators\n\n"
            "Output ONLY valid JSON:\n"
            '{"test_cases":[{"name":"...","steps":[...],"acceptance_criteria":"..."}]}\n\n'
            f"Rules:\n"
            f"- test_cases MUST have exactly {n} entries in the SAME ORDER as the rows below.\n"
            "- For each row, set acceptance_criteria from the Expected column (expected).\n"
            "- Expand steps_description into atomic steps: tap, doubleTap, longPress, tapByCoordinates, type, clear, clearAndType, "
            "wait, waitForVisible, waitForNotVisible, waitForEnabled, waitForDisabled, swipe, scroll, "
            "assertText, assertTextContains, assertVisible, assertNotVisible, assertEnabled, assertChecked, assertAttribute, "
            "pressKey, keyboardAction, hideKeyboard, launchApp, closeApp, resetApp, takeScreenshot, getPageSource.\n"
            "- Include selector_value in the right steps when it clearly identifies a control.\n"
            "- End with assertText or assertVisible reflecting the Expected outcome when applicable.\n"
            "- Each step: {\"type\":\"...\",\"selector\":{\"using\":\"accessibilityId|id|xpath\",\"value\":\"...\"},\"text\",\"ms\",\"expect\"}\n\n"
            "Rows JSON:\n" + json.dumps(chunk, indent=2)
        )
        body = {
            "contents": [{"parts": [{"text": instruction}]}],
            "generationConfig": {"temperature": 0.25, "responseMimeType": "application/json"},
        }
        parsed_ok = False
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(url, json=body)
            if resp.status_code == 200:
                text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
                parsed = json.loads(text)
                cases = parsed.get("test_cases")
                if isinstance(cases, list) and len(cases) == n:
                    for tc, row in zip(cases, chunk):
                        tc["name"] = row["name"]
                        steps = tc.get("steps")
                        if not isinstance(steps, list) or not steps:
                            tc["steps"] = sheet_row_combined_steps(row)
                        tc["acceptance_criteria"] = (tc.get("acceptance_criteria") or row.get("expected") or "") or ""
                        tc["import"] = True
                    all_out.extend(cases)
                    parsed_ok = True
        except (KeyError, IndexError, json.JSONDecodeError, TypeError, httpx.HTTPError, Exception):
            pass
        if not parsed_ok:
            all_out.extend(_sheet_fallback_chunk(chunk))

    return all_out


@router.post("/api/projects/{project_id}/import/script")
async def import_script(
    project_id: int,
    suite_id: int,
    platform: str,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Parse script; return preview test cases. Client calls confirm to persist."""
    content = await file.read()
    filename = (file.filename or "").lower()
    source = content.decode("utf-8", errors="replace")

    s = load_settings()
    api_key, model = ai_creds(s)

    katalon_import_mode: str | None = None
    katalon_import_notes: list[str] = []
    if filename.endswith(".groovy") or filename.endswith(".java"):
        raw_steps = parse_groovy(source)
        fallback_cases = group_steps_into_test_cases(raw_steps, file.filename or "script")
        if api_key:
            ai_cases, ai_notes = await _ai_parse_katalon_source(
                source, platform, file.filename or "script", api_key, model, fallback_cases
            )
            katalon_import_notes.extend(ai_notes)
            if ai_cases:
                test_cases = ai_cases
                katalon_import_mode = "ai"
            else:
                test_cases = fallback_cases
                katalon_import_mode = "heuristic"
        else:
            test_cases = fallback_cases
            katalon_import_mode = "heuristic"
    elif filename.endswith(".feature"):
        raw_steps = parse_gherkin(source)
        test_cases = await _ai_complete_gherkin(raw_steps, platform, source, api_key, model)
    elif filename.endswith(".py"):
        test_cases = await _ai_parse_python_script(source, platform, api_key, model)
    else:
        raise HTTPException(status_code=400, detail="Unsupported file type (use .groovy, .java, .feature, .py)")

    _ = suite_id  # reserved for validation / future use
    with SessionLocal() as db:
        p = db.query(Project).filter(Project.id == project_id).first()
        if not p:
            raise HTTPException(status_code=404, detail="Project not found")

    warn = _validate_test_cases(test_cases) + katalon_import_notes
    out: dict[str, Any] = {
        "test_cases": test_cases,
        "filename": file.filename or "",
        "warnings": warn,
    }
    if katalon_import_mode is not None:
        out["katalon_import_mode"] = katalon_import_mode
    return out


@router.post("/api/projects/{project_id}/import/script/confirm")
def confirm_script_import(project_id: int, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Persist selected test cases after user reviews the parsed preview."""
    suite_id = payload.get("suite_id")
    if suite_id is not None:
        try:
            suite_id = int(suite_id)
        except (TypeError, ValueError):
            suite_id = None
    selected_cases = payload.get("test_cases", [])

    created: list[dict] = []
    with SessionLocal() as db:
        p = db.query(Project).filter(Project.id == project_id).first()
        if not p:
            raise HTTPException(status_code=404, detail="Project not found")

        for tc in selected_cases:
            if not tc.get("import", True):
                continue
            name = tc.get("name")
            steps = tc.get("steps")
            if not name or not isinstance(steps, list):
                continue
            if steps and all(s.get("type") == "python_raw" for s in steps):
                continue
            st = list(steps)
            t = TestDefinition(
                project_id=project_id,
                suite_id=suite_id,
                name=str(name)[:200],
                steps=st,
                platform_steps={"android": st, "ios_sim": []},
                acceptance_criteria=tc.get("acceptance_criteria") or None,
            )
            db.add(t)
            db.flush()
            created.append({"id": t.id, "name": t.name})
        db.commit()

    return {"created": len(created), "tests": created}


@router.post("/api/projects/{project_id}/import/sheet")
async def import_sheet(
    project_id: int,
    suite_id: int,
    platform: str,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Parse Excel/CSV: structured steps from Steps + Expected columns, plus Katalon Groovy per case."""
    content = await file.read()
    filename = file.filename or ""

    with SessionLocal() as db:
        p = db.query(Project).filter(Project.id == project_id).first()
        if not p:
            raise HTTPException(status_code=404, detail="Project not found")

    fn_lower = filename.lower()
    if not (fn_lower.endswith(".csv") or fn_lower.endswith(".xlsx")):
        raise HTTPException(status_code=400, detail="Unsupported file (use .csv or .xlsx)")

    try:
        rows = parse_test_sheet(content, filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    s = load_settings()
    api_key, model = ai_creds(s)

    test_cases = await _ai_complete_sheet_rows(rows, platform, api_key, model)
    scripts = _groovy_scripts_for_sheet_cases(test_cases, filename)
    _ = suite_id
    warnings = _validate_test_cases(test_cases)
    return {
        "test_cases": test_cases,
        "scripts": scripts,
        "filename": filename,
        "row_count": len(rows),
        "warnings": warnings,
    }


@router.post("/api/projects/{project_id}/import/zip")
async def import_zip(
    project_id: int,
    platform: str,
    file: UploadFile = File(...),
    folder_id: Optional[int] = None,
    build_ids: Optional[str] = None,
) -> dict[str, Any]:
    """Parse a ZIP of scripts; preview grouped by folder (suite). Does not persist."""
    with SessionLocal() as db:
        if not db.query(Project).filter(Project.id == project_id).first():
            raise HTTPException(status_code=404, detail="Project not found")

    content = await file.read()
    fn = (file.filename or "").lower()
    if not fn.endswith(".zip"):
        raise HTTPException(status_code=400, detail="Expected a .zip file")

    parsed_files = parse_zip(content)
    obj_repo = parse_object_repo_from_zip(content)
    katalon = parse_katalon_project(content)

    xml_ctx = ""
    sel_rules = ""
    if not obj_repo and folder_id:
        _bid_list = [int(b) for b in (build_ids or "").split(",") if b.strip().isdigit()]
        with SessionLocal() as db:
            q = db.query(ScreenLibrary).filter(
                ScreenLibrary.folder_id == folder_id,
                ScreenLibrary.xml_snapshot.isnot(None),
            )
            if _bid_list:
                q = q.filter(ScreenLibrary.build_id.in_(_bid_list))
            screens = q.all()
            if screens:
                xml_ctx = build_xml_context(screens)
                if platform == "android":
                    sel_rules = android_selector_generation_rules(screens)
                elif platform == "ios_sim":
                    sel_rules = ios_selector_generation_rules(screens)

    s = load_settings()
    zip_ai_key, zip_model = ai_creds(s)
    await _rewrite_katalon_parsed_files_with_ai(
        parsed_files, platform, zip_ai_key, zip_model,
        object_repo=obj_repo or None, xml_context=xml_ctx,
        platform_selector_rules=sel_rules,
    )

    # Build tc_id → parsed test case lookup
    tc_by_id: dict[str, dict[str, Any]] = {}
    for pf in parsed_files:
        norm = _normalize_katalon_path(pf.path)
        tc_id = _tc_id_from_groovy_path(norm)
        for tc in pf.test_cases:
            steps = tc.get("steps") or []
            if any(s.get("type") == "python_raw" for s in steps):
                tc["import"] = False
            else:
                tc.setdefault("import", True)
            tc["source_file"] = pf.path
            # Enrich with .tc metadata
            meta = katalon.tc_metadata.get(tc_id)
            if meta:
                if meta.description and not tc.get("acceptance_criteria"):
                    tc["acceptance_criteria"] = meta.description
                if meta.tags:
                    tc["tags"] = meta.tags
                if meta.comment and meta.comment != tc.get("name"):
                    tc.setdefault("comment", meta.comment)
            tc_by_id[tc_id] = tc

    # Group by .ts suites if available, else fall back to folder hierarchy
    groups: dict[str, list[dict[str, Any]]] = {}
    warnings: list[str] = []
    assigned_tc_ids: set[str] = set()

    if katalon.suites:
        for suite in katalon.suites:
            suite_cases: list[dict[str, Any]] = []
            for ref in suite.tc_ids:
                tc = tc_by_id.get(ref)
                if tc:
                    tc["suggested_suite"] = suite.name
                    suite_cases.append(tc)
                    assigned_tc_ids.add(ref)
            if suite_cases:
                groups[suite.name] = suite_cases

    # Anything not claimed by a .ts suite goes into folder-derived groups
    for pf in parsed_files:
        norm = _normalize_katalon_path(pf.path)
        tc_id = _tc_id_from_groovy_path(norm)
        if tc_id in assigned_tc_ids:
            continue
        tc = tc_by_id.get(tc_id)
        if not tc:
            continue
        suite_name = extract_folder_name(pf.path)
        tc["suggested_suite"] = suite_name
        groups.setdefault(suite_name, []).append(tc)
        assigned_tc_ids.add(tc_id)

    total_cases = sum(len(tcs) for tcs in groups.values())

    for pf in parsed_files:
        if pf.error:
            warnings.append(f"{pf.path}: {pf.error}")
        warnings.extend(pf.warnings)

    # Build collection → suite mapping for the frontend
    collections_map: dict[str, list[str]] = {}
    suite_names = {s.name for s in katalon.suites}
    for coll in katalon.collections:
        coll_suites = [ref.split("/")[-1] for ref in coll.suite_refs if ref.split("/")[-1] in suite_names]
        if coll_suites:
            collections_map[coll.name] = coll_suites

    grounding = "object_repo" if obj_repo else ("screen_library" if xml_ctx else "none")
    if obj_repo:
        warnings.insert(0, f"Object Repository: {len(obj_repo)} locator(s) extracted from .rs files")
    if katalon.suites:
        warnings.insert(0, f"Katalon project: {len(katalon.suites)} suite(s), {len(katalon.collections)} collection(s), {len(katalon.tc_metadata)} .tc metadata file(s)")

    return {
        "groups": groups,
        "total_cases": total_cases,
        "total_files": len(parsed_files),
        "warnings": warnings,
        "grounding": grounding,
        "object_repo_count": len(obj_repo),
        "collections": collections_map,
        "katalon_detected": bool(katalon.suites or katalon.collections),
        "files": [
            {
                "path": pf.path,
                "cases_count": len(pf.test_cases),
                "status": "error" if pf.error else ("warn" if pf.warnings else "ok"),
            }
            for pf in parsed_files
        ],
    }


@router.post("/api/projects/{project_id}/import/folder")
async def import_folder(
    project_id: int,
    platform: str,
    files: list[UploadFile] = File(...),
    folder_id: Optional[int] = None,
    build_ids: Optional[str] = None,
) -> dict[str, Any]:
    """Multiple files from webkitdirectory upload."""
    with SessionLocal() as db:
        if not db.query(Project).filter(Project.id == project_id).first():
            raise HTTPException(status_code=404, detail="Project not found")

    if not files:
        raise HTTPException(
            status_code=400,
            detail="No files received. Each part must use the multipart field name 'files' (folder picker).",
        )

    file_data: list[tuple[str, bytes]] = []
    for f in files:
        raw = await f.read()
        rel = f.filename or "file"
        file_data.append((rel, raw))

    parsed_files = parse_folder_files(file_data)
    obj_repo = parse_object_repo_from_files(file_data)

    xml_ctx = ""
    sel_rules = ""
    if not obj_repo and folder_id:
        _bid_list = [int(b) for b in (build_ids or "").split(",") if b.strip().isdigit()]
        with SessionLocal() as db:
            q = db.query(ScreenLibrary).filter(
                ScreenLibrary.folder_id == folder_id,
                ScreenLibrary.xml_snapshot.isnot(None),
            )
            if _bid_list:
                q = q.filter(ScreenLibrary.build_id.in_(_bid_list))
            screens = q.all()
            if screens:
                xml_ctx = build_xml_context(screens)
                if platform == "android":
                    sel_rules = android_selector_generation_rules(screens)
                elif platform == "ios_sim":
                    sel_rules = ios_selector_generation_rules(screens)

    s = load_settings()
    folder_ai_key, folder_model = ai_creds(s)
    await _rewrite_katalon_parsed_files_with_ai(
        parsed_files, platform, folder_ai_key, folder_model,
        object_repo=obj_repo or None, xml_context=xml_ctx,
        platform_selector_rules=sel_rules,
    )

    groups: dict[str, list[dict[str, Any]]] = {}
    warnings: list[str] = []
    total_cases = 0

    for pf in parsed_files:
        suite_name = extract_folder_name(pf.path)
        groups.setdefault(suite_name, [])
        for tc in pf.test_cases:
            steps = tc.get("steps") or []
            if any(s.get("type") == "python_raw" for s in steps):
                warnings.append(f"{pf.path}: Python script needs AI completion")
                tc["import"] = False
            else:
                tc.setdefault("import", True)
            tc["source_file"] = pf.path
            tc["suggested_suite"] = suite_name
            groups[suite_name].append(tc)
            total_cases += 1
        if pf.error:
            warnings.append(f"{pf.path}: {pf.error}")
        warnings.extend(pf.warnings)

    grounding = "object_repo" if obj_repo else ("screen_library" if xml_ctx else "none")
    if obj_repo:
        warnings.insert(0, f"Object Repository: {len(obj_repo)} locator(s) extracted from .rs files")

    return {
        "groups": groups,
        "total_cases": total_cases,
        "total_files": len(parsed_files),
        "warnings": warnings,
        "grounding": grounding,
        "object_repo_count": len(obj_repo),
        "files": [
            {
                "path": pf.path,
                "cases_count": len(pf.test_cases),
                "status": "error" if pf.error else ("warn" if pf.warnings else "ok"),
            }
            for pf in parsed_files
        ],
    }


@router.post("/api/projects/{project_id}/import/zip/confirm")
def confirm_zip_import(project_id: int, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Persist ZIP/folder preview. Optional suite_map, auto-create suites under module_id.
    When `collections` is provided (from a Katalon project), modules are created per collection
    and suites are placed underneath the appropriate module."""
    suite_map_raw = payload.get("suite_map") or {}
    suite_map: dict[str, int] = {}
    for k, v in suite_map_raw.items():
        if v is None:
            continue
        try:
            suite_map[str(k)] = int(v)
        except (TypeError, ValueError):
            continue

    mod_raw = payload.get("module_id")
    module_id: Optional[int] = None
    if mod_raw is not None:
        try:
            module_id = int(mod_raw)
        except (TypeError, ValueError):
            module_id = None

    collections_raw: dict[str, list[str]] = payload.get("collections") or {}

    test_cases: list[dict[str, Any]] = payload.get("test_cases", [])
    created_modules: dict[str, int] = {}
    created_suites: dict[str, int] = {}
    created_tests: list[dict[str, Any]] = []

    with SessionLocal() as db:
        p = db.query(Project).filter(Project.id == project_id).first()
        if not p:
            raise HTTPException(status_code=404, detail="Project not found")

        # Pre-create modules from Katalon collections
        suite_to_module: dict[str, int] = {}
        if collections_raw:
            for coll_name, coll_suites in collections_raw.items():
                mod = Module(project_id=project_id, name=str(coll_name)[:200])
                db.add(mod)
                db.flush()
                created_modules[coll_name] = mod.id
                for s_name in coll_suites:
                    suite_to_module[s_name] = mod.id

        # If no collections but a module_id is given, use a single fallback module
        # for suites not claimed by any collection
        fallback_module_id = module_id

        # Also create a default "Imported" module for orphan suites when collections exist
        if collections_raw:
            orphan_mod = Module(project_id=project_id, name="Imported (uncategorised)")
            db.add(orphan_mod)
            db.flush()
            fallback_module_id = orphan_mod.id

        for tc in test_cases:
            if not tc.get("import", True):
                continue
            steps = tc.get("steps")
            if not isinstance(steps, list) or not steps:
                continue
            if all(s.get("type") == "python_raw" for s in steps):
                continue
            name = tc.get("name") or "Imported test"
            suite_name = str(tc.get("suggested_suite", "Imported"))[:200]

            suite_id = suite_map.get(suite_name)
            if suite_id is None:
                suite_id = created_suites.get(suite_name)

            if suite_id is None:
                target_module_id = suite_to_module.get(suite_name, fallback_module_id)
                if target_module_id is not None:
                    new_suite = TestSuite(module_id=target_module_id, name=suite_name)
                    db.add(new_suite)
                    db.flush()
                    created_suites[suite_name] = new_suite.id
                    suite_id = new_suite.id

            ac = tc.get("acceptance_criteria")
            st = list(steps)
            t = TestDefinition(
                project_id=project_id,
                suite_id=suite_id,
                name=str(name)[:200],
                steps=st,
                platform_steps={"android": st, "ios_sim": []},
                acceptance_criteria=str(ac) if ac else None,
            )
            db.add(t)
            db.flush()
            created_tests.append({"id": t.id, "name": t.name, "suite": suite_name})

        # Remove the orphan module if nothing was placed in it
        if collections_raw and fallback_module_id and not any(
            db.query(TestSuite).filter(TestSuite.module_id == fallback_module_id).first()
            for _ in [1]
        ):
            orphan = db.query(Module).filter(Module.id == fallback_module_id).first()
            if orphan:
                db.delete(orphan)

        db.commit()

    return {
        "created": len(created_tests),
        "created_suites": list(created_suites.keys()),
        "created_modules": list(created_modules.keys()),
        "tests": created_tests,
    }


@router.post("/api/projects/{project_id}/generate/katalon-zip")
def generate_katalon_zip_endpoint(project_id: int, payload: dict[str, Any] = Body(...)) -> StreamingResponse:
    """Build Katalon project ZIP from saved test IDs and/or inline test case dicts."""
    test_case_ids = payload.get("test_case_ids") or []
    inline_cases = payload.get("test_cases") or []
    project_name = str(payload.get("project_name") or "QA_Project")

    cases_to_export: list[dict[str, Any]] = list(inline_cases)

    with SessionLocal() as db:
        for tc_id in test_case_ids:
            try:
                tid = int(tc_id)
            except (TypeError, ValueError):
                continue
            t = (
                db.query(TestDefinition)
                .filter(TestDefinition.id == tid, TestDefinition.project_id == project_id)
                .first()
            )
            if not t:
                continue
            suite_name = "Exported"
            if t.suite_id:
                su = db.query(TestSuite).filter(TestSuite.id == t.suite_id).first()
                if su:
                    suite_name = su.name
            cases_to_export.append(
                {
                    "name": t.name,
                    "steps": steps_for_platform_record(t, "android"),
                    "acceptance_criteria": t.acceptance_criteria or "",
                    "suite_name": suite_name,
                }
            )

    if not cases_to_export:
        raise HTTPException(status_code=400, detail="No test cases to export")

    zip_bytes = generate_katalon_zip(project_name, cases_to_export)
    safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", project_name)[:80] or "katalon"

    return StreamingResponse(
        BytesIO(zip_bytes),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}_katalon.zip"'},
    )
