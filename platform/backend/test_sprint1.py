#!/usr/bin/env python3
"""Sprint 1 API tests - run with backend on http://127.0.0.1:9001"""
from __future__ import annotations

import sys
from io import BytesIO

import httpx

BASE = "http://127.0.0.1:9001"
passed = 0
failed = 0


def get_auth_headers() -> dict:
    """Get auth token from /api/auth/token and return headers."""
    r = httpx.get(
        f"{BASE}/api/auth/token",
        headers={"Origin": "http://localhost:5173"},
        timeout=5,
    )
    if r.status_code != 200:
        return {}
    token = r.json().get("token", "")
    return {"Authorization": f"Bearer {token}"}


def ok(name: str, cond: bool, msg: str = ""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✓ {name}" + (f" — {msg}" if msg else ""))
    else:
        failed += 1
        print(f"  ✗ {name}" + (f" — {msg}" if msg else ""))


def test_health():
    print("\n1. Health check")
    r = httpx.get(f"{BASE}/api/health", timeout=5)
    ok("Health", r.status_code == 200 and r.json().get("status") == "ok")


def test_auth():
    print("\n2. Auth token")
    h = get_auth_headers()
    ok("Get auth token", bool(h.get("Authorization")))


def test_cancel_endpoint():
    print("\n3. Run cancellation endpoint")
    h = get_auth_headers()
    if not h:
        ok("Cancel (no auth)", True, "skipped")
        return
    # Cancel non-existent run → 404
    r = httpx.post(f"{BASE}/api/runs/99999/cancel", headers=h, timeout=5)
    ok("Cancel 404 for missing run", r.status_code == 404)
    # Cancel on completed run → 200 with "already X" message
    r = httpx.get(f"{BASE}/api/projects", headers=h, timeout=5)
    if r.status_code == 200 and r.json():
        pid = r.json()[0]["id"]
        r = httpx.get(f"{BASE}/api/projects/{pid}/runs", headers=h, timeout=5)
        if r.status_code == 200 and r.json():
            runs = [x for x in r.json() if x["status"] in ("passed", "failed", "error")]
            if runs:
                rid = runs[0]["id"]
                r = httpx.post(f"{BASE}/api/runs/{rid}/cancel", headers=h, timeout=5)
                ok("Cancel on completed run returns ok", r.status_code == 200)
                ok("Cancel message", "ok" in r.json() or "already" in str(r.json()).lower())
            else:
                ok("Cancel (no completed runs to test)", True, "skipped")
        else:
            ok("Cancel (no runs)", True, "skipped")
    else:
        ok("Cancel (no projects)", True, "skipped")


def test_file_upload_validation():
    print("\n4. File upload validation")
    h = get_auth_headers()
    r = httpx.get(f"{BASE}/api/projects", headers=h, timeout=5)
    if r.status_code != 200 or not r.json():
        ok("Upload validation (no projects)", True, "skipped")
        return
    pid = r.json()[0]["id"]

    # Invalid extension (.txt) → 400
    r = httpx.post(
        f"{BASE}/api/projects/{pid}/builds?platform=android",
        files={"file": ("bad.txt", BytesIO(b"x"), "text/plain")},
        headers=h,
        timeout=10,
    )
    ok("Reject .txt file", r.status_code == 400, f"got {r.status_code}")

    # Valid .apk (tiny) → 200 or 201
    r = httpx.post(
        f"{BASE}/api/projects/{pid}/builds?platform=android",
        files={"file": ("tiny.apk", BytesIO(b"PK\x03\x04"), "application/vnd.android.package-archive")},
        headers=h,
        timeout=10,
    )
    # May fail for other reasons (invalid APK) but should NOT be 400 "Invalid file type"
    ok("Accept .apk extension", r.status_code != 400 or "Invalid file type" not in (r.text or ""))


def test_runner_cancel_logic():
    print("\n5. Run engine cancel logic (import check)")
    try:
        from app.runner.engine import run_engine
        ok("RunEngine has request_cancel", hasattr(run_engine, "request_cancel"))
        ok("RunEngine has is_cancelled", hasattr(run_engine, "is_cancelled"))
    except Exception as e:
        ok("Run engine import", False, str(e))


def test_script_parser_unit():
    print("\n6. Script parser (unit, no server)")
    try:
        from app.parser.script_parser import parse_groovy, parse_gherkin, parse_test_sheet, sheet_row_combined_steps
    except Exception as e:
        ok("Parser import", False, str(e))
        return

    groovy = """
import com.kms.katalon.core.mobile.keyword.MobileBuiltInKeywords as Mobile
Mobile.tap(findTestObject('Object Repository/Login/btn_submit'), 0);
Mobile.setText(findTestObject('Object Repository/Login/input_email'), 'a@b.com', 0);
Mobile.waitForElementPresent(findTestObject('Object Repository/Home/title'), 10);
Mobile.delay(2);
"""
    steps = parse_groovy(groovy)
    ok("parse_groovy returns steps", len(steps) >= 4)
    ok("parse_groovy tap", any(s.get("type") == "tap" for s in steps))
    ok("parse_groovy type", any(s.get("type") == "type" for s in steps))
    ok("parse_groovy waitForVisible", any(s.get("type") == "waitForVisible" for s in steps))

    raw = parse_gherkin("Feature: X\n  Given I am on login\n  When I tap sign in\n")
    ok("parse_gherkin", len(raw) >= 2 and raw[0].get("type") == "gherkin_raw")

    csv_bytes = b"Test Name,Steps,Selector Value\nTC1,Tap button,my_btn\n"
    rows = parse_test_sheet(csv_bytes, "sheet.csv")
    ok("parse_test_sheet CSV", len(rows) == 1 and rows[0].get("name") == "TC1")

    steps_only = sheet_row_combined_steps(
        {"name": "x", "steps_description": "Click OK", "expected": "Dialog closes", "selector_value": "", "selector_strategy": "accessibilityId"}
    )
    ok("sheet_row_combined_steps from text", len(steps_only) >= 2 and any(s.get("type") == "assertText" for s in steps_only))


def _first_suite_id(h: dict, pid: int) -> int | None:
    r = httpx.get(f"{BASE}/api/projects/{pid}/modules", headers=h, timeout=5)
    if r.status_code != 200:
        return None
    for m in r.json():
        sr = httpx.get(f"{BASE}/api/modules/{m['id']}/suites", headers=h, timeout=5)
        if sr.status_code == 200 and sr.json():
            return int(sr.json()[0]["id"])
    return None


def test_reports_hierarchy_and_import():
    print("\n7. Reports hierarchy & import preview API")
    h = get_auth_headers()
    if not h:
        ok("Hierarchy (no auth)", True, "skipped")
        return
    r = httpx.get(f"{BASE}/api/projects", headers=h, timeout=5)
    if r.status_code != 200 or not r.json():
        ok("Hierarchy (no projects)", True, "skipped")
        return
    pid = int(r.json()[0]["id"])

    rh = httpx.get(f"{BASE}/api/projects/{pid}/reports/hierarchy", headers=h, timeout=10)
    ok("GET reports/hierarchy 200", rh.status_code == 200)
    if rh.status_code == 200:
        data = rh.json()
        ok("hierarchy has collections + summary", "collections" in data and "summary" in data)

    sid = _first_suite_id(h, pid)
    if sid is None:
        ok("Import script (no suite)", True, "skipped")
        ok("Import bad type (no suite)", True, "skipped")
        return

    r = httpx.post(
        f"{BASE}/api/projects/{pid}/import/script?suite_id={sid}&platform=android",
        headers=h,
        files={"file": ("bad.txt", BytesIO(b"noop"), "text/plain")},
        timeout=10,
    )
    ok("Import script rejects .txt", r.status_code == 400)

    groovy = b"Mobile.tap(findTestObject('Repo/Screen/foo'), 0);\n"
    r = httpx.post(
        f"{BASE}/api/projects/{pid}/import/script?suite_id={sid}&platform=android",
        headers=h,
        files={"file": ("mini.groovy", BytesIO(groovy), "text/plain")},
        timeout=15,
    )
    ok("Import script preview .groovy", r.status_code == 200)
    if r.status_code == 200:
        tc = r.json().get("test_cases", [])
        ok("Preview has test_cases", isinstance(tc, list) and len(tc) >= 1)
        ok("Preview has warnings key", "warnings" in r.json())

    csv = b"Test Name,Steps\nImportTC,Tap\n"
    r = httpx.post(
        f"{BASE}/api/projects/{pid}/import/sheet?suite_id={sid}&platform=android",
        headers=h,
        files={"file": ("t.csv", BytesIO(csv), "text/csv")},
        timeout=60,
    )
    ok("Import sheet preview CSV", r.status_code == 200)
    if r.status_code == 200:
        j = r.json()
        ok("Sheet row_count", j.get("row_count") == 1)
        ok("Sheet returns scripts + warnings keys", "scripts" in j and isinstance(j.get("scripts"), list) and "warnings" in j)


def main():
    print("Sprint 1 API Tests")
    print("=" * 40)
    try:
        # Unit tests first (no backend required)
        test_script_parser_unit()
        test_health()
        test_auth()
        test_cancel_endpoint()
        test_file_upload_validation()
        test_runner_cancel_logic()
        test_reports_hierarchy_and_import()
    except httpx.ConnectError:
        print(f"\n  ✗ Backend not reachable at {BASE}")
        print("  Start: cd platform/backend && uv run uvicorn app.main:app --port 9001")
        sys.exit(1)
    except httpx.TimeoutException:
        print(f"\n  ✗ Backend timed out at {BASE}")
        print("  Start: cd platform/backend && uv run uvicorn app.main:app --port 9001")
        sys.exit(1)
    except Exception as e:
        print(f"\n  ✗ {e}")
        sys.exit(1)

    print("\n" + "=" * 40)
    print(f"Result: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
