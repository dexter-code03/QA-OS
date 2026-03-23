from __future__ import annotations

import html as html_lib
from datetime import datetime
from io import BytesIO
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from ..db import SessionLocal, _classify_error
from ..helpers import steps_for_platform_record as _steps_for_platform_record
from ..models import (
    Build,
    Module,
    Project,
    Run,
    TestDefinition,
    TestSuite,
)
from ..settings import settings

router = APIRouter()


# ── Reports v2 — test-case-first endpoints ─────────────────────────────

def _run_window(db, suite_or_test_ids: list[int], days: int, platform: str, *, by_suite: bool = True):
    """Return runs within the time window, optionally filtered by platform."""
    from datetime import timedelta
    cutoff = datetime.utcnow() - timedelta(days=days)
    q = db.query(Run).filter(Run.started_at >= cutoff)
    if platform:
        q = q.filter(Run.platform == platform)
    if by_suite:
        q = q.filter(Run.test_id.in_(
            db.query(TestDefinition.id).filter(TestDefinition.suite_id.in_(suite_or_test_ids))
        ))
    else:
        q = q.filter(Run.test_id.in_(suite_or_test_ids))
    return q.order_by(Run.id.asc()).all()


def _test_health_row(t: TestDefinition, test_runs: list[Run], days: int, prereq_offset: int = 0, prereq_def: "Optional[TestDefinition]" = None) -> dict[str, Any]:
    """Build one test-case health row from its runs in the window.
    prereq_offset: fallback count; prereq_def: prerequisite test for platform-aware offset."""
    if not test_runs:
        has_android = bool(_steps_for_platform_record(t, "android"))
        has_ios = bool(_steps_for_platform_record(t, "ios_sim"))
        plat = "android" if has_android and not has_ios else "ios_sim" if has_ios and not has_android else "android"
        return {
            "id": t.id, "name": t.name, "status": "not_run",
            "steps_ran": 0, "steps_total": max(len(_steps_for_platform_record(t, "android")), len(_steps_for_platform_record(t, "ios_sim"))),
            "platform": plat,
            "pass_rate_pct": 0, "fail_streak": 0,
            "last_passed_at": None, "ai_fixes_count": len(t.fix_history or []),
            "run_history": [], "last_failed_run": None,
        }

    platforms_seen = set(r.platform for r in test_runs if r.platform)
    plat = "both" if len(platforms_seen) > 1 else (platforms_seen.pop() if platforms_seen else "android")

    passed = sum(1 for r in test_runs if r.status == "passed")
    total = len(test_runs)
    rate = round(100 * passed / total) if total else 0

    # fail streak (from newest)
    streak = 0
    for r in reversed(test_runs):
        if r.status in ("failed", "error"):
            streak += 1
        else:
            break

    # flaky: both pass and fail, no long consecutive fail streak
    has_pass = any(r.status == "passed" for r in test_runs)
    has_fail = any(r.status in ("failed", "error") for r in test_runs)
    if has_pass and has_fail and streak <= 2:
        status = "flaky"
    elif streak > 0 and not has_pass:
        status = "failing"
    elif streak > 0:
        status = "failing"
    else:
        status = "passing"

    last_passed = None
    for r in reversed(test_runs):
        if r.status == "passed":
            last_passed = r.finished_at.isoformat() if r.finished_at else None
            break

    # steps_total from test definition only (excludes prerequisite)
    steps_total = max(len(_steps_for_platform_record(t, "android")), len(_steps_for_platform_record(t, "ios_sim")))
    # steps_ran: subtract prerequisite steps from run data using the run's actual platform
    last = test_runs[-1]
    sr = last.summary or {}
    step_results = sr.get("stepResults") or []
    all_ran = sum(1 for s in step_results if s.get("status") in ("passed", "failed"))
    run_pf = (last.platform or "android").strip() or "android"
    actual_offset = len(_steps_for_platform_record(prereq_def, run_pf)) if prereq_def else prereq_offset
    steps_ran = max(0, all_ran - actual_offset)

    # last failed run detail (skip prerequisite steps)
    last_failed_run = None
    for r in reversed(test_runs):
        if r.status in ("failed", "error"):
            rsummary = r.summary or {}
            rsteps = rsummary.get("stepResults") or []
            arts = r.artifacts or {}
            screenshots = arts.get("screenshots") or []
            # Slice off prerequisite steps using this run's platform
            r_pf = (r.platform or "android").strip() or "android"
            r_offset = len(_steps_for_platform_record(prereq_def, r_pf)) if prereq_def else prereq_offset
            own_rsteps = rsteps[r_offset:]
            own_screenshots = screenshots[r_offset:]
            step_detail = []
            for si, sr_item in enumerate(own_rsteps):
                shot = own_screenshots[si] if si < len(own_screenshots) else None
                step_detail.append({
                    "index": si,
                    "type": (sr_item.get("step") or {}).get("type") or "",
                    "selector": (sr_item.get("step") or {}).get("selector") or sr_item.get("details", {}).get("selector") if isinstance(sr_item.get("details"), dict) else None,
                    "status": sr_item.get("status") or "pending",
                    "duration_ms": sr_item.get("duration_ms"),
                    "error": sr_item.get("details", {}).get("error") if isinstance(sr_item.get("details"), dict) else (sr_item.get("details") if sr_item.get("status") == "failed" else None),
                    "screenshot": shot,
                })
            fh = t.fix_history or []
            ai_fix = None
            if fh:
                latest = fh[-1]
                ai_fix = {"analysis": latest.get("analysis", ""), "fixed_steps": latest.get("fixed_steps", []), "changes": latest.get("changes", [])}
            last_failed_run = {
                "id": r.id, "error_message": r.error_message,
                "failure_category": getattr(r, "failure_category", "") or "",
                "step_results": step_detail,
                "ai_fix": ai_fix,
                "platform": r.platform,
                "started_at": r.started_at.isoformat() if r.started_at else None,
            }
            break

    run_history = [{"id": r.id, "status": r.status, "platform": r.platform} for r in test_runs[-14:]]

    return {
        "id": t.id, "name": t.name, "status": status,
        "acceptance_criteria": t.acceptance_criteria or "",
        "steps_ran": steps_ran, "steps_total": steps_total,
        "platform": plat, "pass_rate_pct": rate, "fail_streak": streak,
        "last_passed_at": last_passed,
        "ai_fixes_count": len(t.fix_history or []),
        "run_history": run_history,
        "last_failed_run": last_failed_run,
    }


@router.get("/api/suites/{suite_id}/health")
def suite_health(suite_id: int, days: int = 14, platform: str = "") -> dict[str, Any]:
    with SessionLocal() as db:
        suite = db.query(TestSuite).filter(TestSuite.id == suite_id).first()
        if not suite:
            raise HTTPException(status_code=404, detail="Suite not found")
        module = db.query(Module).filter(Module.id == suite.module_id).first()
        tests = db.query(TestDefinition).filter(TestDefinition.suite_id == suite_id).all()
        test_ids = [t.id for t in tests]
        prereq_ids = set(t.prerequisite_test_id for t in tests if t.prerequisite_test_id and t.prerequisite_test_id != t.id)
        prereq_map: dict = {}
        if prereq_ids:
            for p in db.query(TestDefinition).filter(TestDefinition.id.in_(prereq_ids)).all():
                prereq_map[p.id] = p
        runs = _run_window(db, test_ids, days, platform, by_suite=False) if test_ids else []
        runs_by_test: dict[int, list[Run]] = {}
        for r in runs:
            runs_by_test.setdefault(r.test_id, []).append(r)

        rows = [_test_health_row(t, runs_by_test.get(t.id, []), days, prereq_def=prereq_map.get(t.prerequisite_test_id)) for t in tests]
        # sort: failing first (by streak desc), flaky, passing, not_run
        order = {"failing": 0, "flaky": 1, "passing": 2, "not_run": 3}
        rows.sort(key=lambda r: (order.get(r["status"], 4), -r["fail_streak"], r["name"]))

        passing = sum(1 for r in rows if r["status"] == "passing")
        failing = sum(1 for r in rows if r["status"] == "failing")
        flaky = sum(1 for r in rows if r["status"] == "flaky")
        never_run = sum(1 for r in rows if r["status"] == "not_run")
        avg_steps = 0
        denom = sum(1 for r in rows if r["steps_total"] > 0)
        if denom:
            avg_steps = round(sum(r["steps_ran"] / r["steps_total"] * 100 for r in rows if r["steps_total"] > 0) / denom)

        last_run_at = None
        if runs:
            lr = max(runs, key=lambda r: r.finished_at or r.started_at or datetime.min)
            last_run_at = (lr.finished_at or lr.started_at or datetime.min).isoformat()

        suite_rate = round(100 * passing / len(rows)) if rows else 0

    return {
        "suite": {"id": suite.id, "name": suite.name, "module_name": module.name if module else "", "last_run_at": last_run_at, "pass_rate": suite_rate},
        "metrics": {"total": len(rows), "passing": passing, "failing": failing, "flaky": flaky, "never_run": never_run, "avg_steps_pct": avg_steps},
        "tests": rows,
    }


@router.get("/api/suites/{suite_id}/trend")
def suite_trend(suite_id: int, days: int = 14, platform: str = "") -> list[dict[str, Any]]:
    with SessionLocal() as db:
        tests = db.query(TestDefinition).filter(TestDefinition.suite_id == suite_id).all()
        test_ids = [t.id for t in tests]
        runs = _run_window(db, test_ids, days, platform, by_suite=False) if test_ids else []
        runs_by_test: dict[int, list[Run]] = {}
        for r in runs:
            runs_by_test.setdefault(r.test_id, []).append(r)
        out = []
        for t in tests:
            trs = runs_by_test.get(t.id, [])
            total = len(trs)
            passed = sum(1 for r in trs if r.status == "passed")
            out.append({
                "test_case_id": t.id, "test_name": t.name,
                "pass_count": passed, "total_runs": total,
                "pass_rate_pct": round(100 * passed / total) if total else 0,
            })
    return out


@router.get("/api/suites/{suite_id}/step-coverage")
def suite_step_coverage(suite_id: int, days: int = 14, platform: str = "") -> list[dict[str, Any]]:
    with SessionLocal() as db:
        tests = db.query(TestDefinition).filter(TestDefinition.suite_id == suite_id).all()
        test_ids = [t.id for t in tests]
        prereq_ids = set(t.prerequisite_test_id for t in tests if t.prerequisite_test_id and t.prerequisite_test_id != t.id)
        prereq_map: dict = {}
        if prereq_ids:
            for p in db.query(TestDefinition).filter(TestDefinition.id.in_(prereq_ids)).all():
                prereq_map[p.id] = p
        runs = _run_window(db, test_ids, days, platform, by_suite=False) if test_ids else []
        runs_by_test: dict[int, list[Run]] = {}
        for r in runs:
            runs_by_test.setdefault(r.test_id, []).append(r)
        out = []
        for t in tests:
            trs = runs_by_test.get(t.id, [])
            if not trs:
                total_steps = max(len(_steps_for_platform_record(t, "android")), len(_steps_for_platform_record(t, "ios_sim")))
                out.append({"test_case_id": t.id, "test_name": t.name, "avg_steps_ran": 0, "avg_steps_total": total_steps, "coverage_pct": 0})
                continue
            defined_total = max(len(_steps_for_platform_record(t, "android")), len(_steps_for_platform_record(t, "ios_sim")))
            prereq_def = prereq_map.get(t.prerequisite_test_id)
            ran_sum = 0
            for r in trs:
                sr = r.summary or {}
                step_results = sr.get("stepResults") or []
                all_ran = sum(1 for s in step_results if s.get("status") in ("passed", "failed"))
                r_pf = (r.platform or "android").strip() or "android"
                r_offset = len(_steps_for_platform_record(prereq_def, r_pf)) if prereq_def else 0
                ran_sum += max(0, all_ran - r_offset)
            n = len(trs)
            total_sum = defined_total * n
            avg_ran = round(ran_sum / n, 1)
            avg_total = float(defined_total)
            cov = round(100 * ran_sum / total_sum) if total_sum else 0
            out.append({"test_case_id": t.id, "test_name": t.name, "avg_steps_ran": avg_ran, "avg_steps_total": avg_total, "coverage_pct": cov})
    return out


@router.get("/api/suites/{suite_id}/triage")
def suite_triage(suite_id: int, days: int = 14, platform: str = "") -> dict[str, Any]:
    with SessionLocal() as db:
        tests = db.query(TestDefinition).filter(TestDefinition.suite_id == suite_id).all()
        test_map = {t.id: t for t in tests}
        test_ids = [t.id for t in tests]
        runs = _run_window(db, test_ids, days, platform, by_suite=False) if test_ids else []
        failed_runs = [r for r in runs if r.status in ("failed", "error")]
        cat_map: dict[str, list[dict]] = {}
        for r in failed_runs:
            cat = getattr(r, "failure_category", "") or _classify_error(r.error_message or "")
            cat_map.setdefault(cat, [])
            t = test_map.get(r.test_id)
            cat_map[cat].append({"id": t.id if t else 0, "name": t.name if t else f"Run #{r.id}", "error_message": r.error_message or ""})
        total_failures = len(failed_runs)
        categories = []
        for cat in ["selector_not_found", "element_timeout", "assertion_failure", "network_error", "app_crash", "other"]:
            items = cat_map.get(cat, [])
            if not items and cat not in cat_map:
                continue
            categories.append({
                "category": cat,
                "count": len(items),
                "pct": round(100 * len(items) / total_failures) if total_failures else 0,
                "affected_tests": items,
            })
    return {"categories": categories, "total_failures": total_failures}


@router.get("/api/collections/{collection_id}/health")
def collection_health(collection_id: int, days: int = 14, platform: str = "") -> dict[str, Any]:
    with SessionLocal() as db:
        module = db.query(Module).filter(Module.id == collection_id).first()
        if not module:
            raise HTTPException(status_code=404, detail="Collection not found")
        suites = db.query(TestSuite).filter(TestSuite.module_id == collection_id).all()
        all_tests = db.query(TestDefinition).filter(TestDefinition.suite_id.in_([s.id for s in suites])).all() if suites else []
        test_ids = [t.id for t in all_tests]
        prereq_ids = set(t.prerequisite_test_id for t in all_tests if t.prerequisite_test_id and t.prerequisite_test_id != t.id)
        prereq_map: dict = {}
        if prereq_ids:
            for p in db.query(TestDefinition).filter(TestDefinition.id.in_(prereq_ids)).all():
                prereq_map[p.id] = p
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(days=days)
        q = db.query(Run).filter(Run.started_at >= cutoff, Run.test_id.in_(test_ids)) if test_ids else None
        if platform and q is not None:
            q = q.filter(Run.platform == platform)
        runs = q.order_by(Run.id.asc()).all() if q is not None else []
        runs_by_test: dict[int, list[Run]] = {}
        for r in runs:
            runs_by_test.setdefault(r.test_id, []).append(r)

        # per-suite rollup
        suite_rows = []
        all_health = []
        for s in suites:
            s_tests = [t for t in all_tests if t.suite_id == s.id]
            s_rows = [_test_health_row(t, runs_by_test.get(t.id, []), days, prereq_def=prereq_map.get(t.prerequisite_test_id)) for t in s_tests]
            all_health.extend(s_rows)
            s_pass = sum(1 for r in s_rows if r["status"] == "passing")
            s_fail = sum(1 for r in s_rows if r["status"] == "failing")
            s_blocker = sum(1 for r in s_rows if r["fail_streak"] >= 5 or (r.get("last_failed_run") or {}).get("failure_category") == "app_crash")
            s_runs = [r for r in runs if r.test_id in [t.id for t in s_tests]]
            last_at = None
            if s_runs:
                lr = max(s_runs, key=lambda r: r.finished_at or r.started_at or datetime.min)
                last_at = (lr.finished_at or lr.started_at).isoformat() if (lr.finished_at or lr.started_at) else None
            rate = round(100 * s_pass / len(s_rows)) if s_rows else 0
            suite_rows.append({"id": s.id, "name": s.name, "pass_rate_pct": rate, "pass_count": s_pass, "fail_count": s_fail, "blocker_count": s_blocker, "last_run_at": last_at, "total": len(s_rows)})

        total = len(all_health)
        passing = sum(1 for h in all_health if h["status"] == "passing")
        failing = sum(1 for h in all_health if h["status"] == "failing")
        flaky = sum(1 for h in all_health if h["status"] == "flaky")
        never_run = sum(1 for h in all_health if h["status"] == "not_run")
        blockers = sum(1 for h in all_health if h["fail_streak"] >= 5 or (h.get("last_failed_run") or {}).get("failure_category") == "app_crash")
        rate = round(100 * passing / total) if total else 0

        # verdict
        if blockers > 0:
            verdict = "BLOCKED"
        elif rate < 90:
            verdict = "NOT_READY"
        else:
            verdict = "READY"

        # 30-day trend
        from datetime import timedelta as td
        cutoff_30 = datetime.utcnow() - td(days=30)
        trend_runs = [r for r in runs if r.started_at and r.started_at >= cutoff_30]
        day_buckets: dict[str, dict] = {}
        for r in trend_runs:
            day = r.started_at.strftime("%Y-%m-%d") if r.started_at else "?"
            day_buckets.setdefault(day, {"passed": 0, "total": 0})
            day_buckets[day]["total"] += 1
            if r.status == "passed":
                day_buckets[day]["passed"] += 1
        trend = [{"date": d, "pass_rate_pct": round(100 * v["passed"] / v["total"]) if v["total"] else 0} for d, v in sorted(day_buckets.items())]

    return {
        "collection": {"id": module.id, "name": module.name, "pass_rate": rate, "verdict": verdict},
        "metrics": {"total": total, "passing": passing, "failing": failing, "blockers": blockers, "flaky": flaky, "never_run": never_run},
        "suites": suite_rows,
        "trend_30d": trend,
    }


@router.get("/api/collections/{collection_id}/blockers")
def collection_blockers(collection_id: int, days: int = 14, platform: str = "") -> list[dict[str, Any]]:
    with SessionLocal() as db:
        suites = db.query(TestSuite).filter(TestSuite.module_id == collection_id).all()
        suite_map = {s.id: s.name for s in suites}
        all_tests = db.query(TestDefinition).filter(TestDefinition.suite_id.in_([s.id for s in suites])).all() if suites else []
        test_ids = [t.id for t in all_tests]
        prereq_ids = set(t.prerequisite_test_id for t in all_tests if t.prerequisite_test_id and t.prerequisite_test_id != t.id)
        prereq_map: dict = {}
        if prereq_ids:
            for p in db.query(TestDefinition).filter(TestDefinition.id.in_(prereq_ids)).all():
                prereq_map[p.id] = p
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(days=days)
        q = db.query(Run).filter(Run.started_at >= cutoff, Run.test_id.in_(test_ids)) if test_ids else None
        if platform and q is not None:
            q = q.filter(Run.platform == platform)
        runs = q.order_by(Run.id.asc()).all() if q is not None else []
        runs_by_test: dict[int, list[Run]] = {}
        for r in runs:
            runs_by_test.setdefault(r.test_id, []).append(r)

        out = []
        for t in all_tests:
            trs = runs_by_test.get(t.id, [])
            if not trs:
                continue
            h = _test_health_row(t, trs, days, prereq_def=prereq_map.get(t.prerequisite_test_id))
            is_blocker = h["fail_streak"] >= 5 or (h.get("last_failed_run") or {}).get("failure_category") == "app_crash"
            if not is_blocker:
                continue
            lfr = h.get("last_failed_run") or {}
            screenshots = lfr.get("step_results") or []
            shot = None
            for sr in reversed(screenshots):
                if sr.get("screenshot"):
                    shot = sr["screenshot"]
                    break
            out.append({
                "test_id": t.id, "test_name": t.name,
                "suite_name": suite_map.get(t.suite_id, ""),
                "error_message": lfr.get("error_message") or "",
                "fail_streak": h["fail_streak"],
                "run_id": lfr.get("id") or 0,
                "screenshot_path": shot,
                "ai_fix_available": bool(t.fix_history),
            })
    return out


# ── Report export endpoints ────────────────────────────────────────────

_REPORT_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Inter','Segoe UI',system-ui,sans-serif;background:#080b0f;color:#e8eaed;padding:32px 40px;line-height:1.6;max-width:1100px;margin:0 auto}
h1{font-size:22px;color:#fff;margin-bottom:2px;font-weight:700}
h2{font-size:15px;color:#a78bfa;margin:32px 0 14px;border-bottom:1px solid #1e2430;padding-bottom:8px;font-weight:600}
.subtitle{font-size:12px;color:#6b7280;margin-bottom:24px}
.stats{display:flex;gap:16px;margin-bottom:28px;flex-wrap:wrap}
.stat{background:#111820;border:1px solid #1e2430;border-radius:10px;padding:14px 20px;min-width:100px;text-align:center}
.stat-val{font-size:22px;font-weight:700;letter-spacing:-.5px}
.stat-lbl{font-size:9px;text-transform:uppercase;color:#6b7280;margin-top:3px;letter-spacing:.8px}
.badge{display:inline-block;padding:3px 10px;border-radius:20px;font-weight:600;font-size:11px}
.badge-pass{background:#0d2818;color:#00e5a0;border:1px solid #0f3d22}
.badge-fail{background:#2a0a10;color:#ff3b5c;border:1px solid #3d1118}
.badge-flaky{background:#2a1f06;color:#ffb020;border:1px solid #3d2c0a}
.badge-skip{background:#1a1a1a;color:#6b7280;border:1px solid #2a2a2a}
table{width:100%;border-collapse:collapse;font-size:12px;margin-bottom:24px}
thead th{background:#0f1318;color:#6b7280;text-transform:uppercase;font-size:9.5px;letter-spacing:.6px;text-align:left;padding:10px 12px;border-bottom:2px solid #1e2430;font-weight:600}
tbody td{padding:10px 12px;border-bottom:1px solid #151a22;vertical-align:top}
tbody tr:hover{background:rgba(139,92,246,.03)}
.step-row{display:grid;grid-template-columns:32px 10px 1fr 70px 60px;gap:8px;align-items:center;padding:7px 10px;border-radius:6px;font-size:12px;min-height:40px}
.step-row:nth-child(even){background:rgba(255,255,255,.015)}
.step-row--fail{background:rgba(255,59,92,.04);border-left:3px solid #ff3b5c}
.step-num{text-align:right;color:#555;font-size:11px;font-weight:600;font-variant-numeric:tabular-nums}
.dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.selector{color:#6b7280;font-size:10.5px;font-family:'SF Mono','Fira Code',monospace}
.dur{color:#555;font-size:10.5px;text-align:right;font-variant-numeric:tabular-nums}
.screenshot-thumb{width:48px;border-radius:4px;border:1px solid #1e2430;cursor:pointer}
.screenshot-thumb--fail{border-color:#ff3b5c}
.error-box{background:rgba(255,59,92,.06);border:1px solid rgba(255,59,92,.15);padding:14px 16px;border-radius:8px;margin:12px 0;font-size:12px;color:#ff7b93;line-height:1.5}
.fix-box{background:rgba(139,92,246,.06);border:1px solid rgba(139,92,246,.15);padding:14px 16px;border-radius:8px;margin:12px 0}
.fix-label{font-weight:700;color:#a78bfa;margin-bottom:6px;text-transform:uppercase;font-size:9.5px;letter-spacing:.8px}
.fix-text{font-size:12px;color:#c4b5fd;line-height:1.5}
.meta-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px;margin-bottom:24px}
.meta-item{font-size:11px;color:#6b7280}.meta-item strong{color:#c9d1d9}
.history-strip{display:flex;gap:2px;flex-wrap:wrap}
.history-dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}
.footer{margin-top:40px;border-top:1px solid #1e2430;padding-top:16px;font-size:10px;color:#3d4250}
.section{margin-bottom:28px}
img.screenshot-full{max-width:300px;border-radius:8px;border:1px solid #1e2430;margin-top:8px}
"""


def _esc(text: str) -> str:
    """HTML-escape text for safe embedding in reports."""
    return html_lib.escape(str(text)) if text else ""


def _screenshot_to_base64(project_id: int, run_id: int, filename: str) -> str:
    """Read a screenshot from disk and return as base64 data URI."""
    import base64
    fpath = settings.artifacts_dir / str(project_id) / str(run_id) / filename
    if not fpath.exists():
        return ""
    try:
        data = fpath.read_bytes()
        return f"data:image/png;base64,{base64.b64encode(data).decode()}"
    except Exception:
        return ""


def _render_step_rows_html(step_results: list, project_id: int, run_id: int) -> str:
    """Render step results as HTML rows with embedded screenshots."""
    rows = ""
    for sr in step_results:
        idx = sr.get("index", 0)
        st = sr.get("status", "pending")
        stype = _esc(sr.get("type") or "")
        sel = sr.get("selector")
        sel_str = ""
        if sel:
            if isinstance(sel, dict):
                sel_str = f'{_esc(sel.get("using", ""))}=&quot;{_esc(sel.get("value", ""))}&quot;'
            else:
                sel_str = _esc(str(sel))
        dur = sr.get("duration_ms")
        dur_str = f'{dur/1000:.1f}s' if dur is not None else ""
        shot = sr.get("screenshot", "")
        shot_html = ""
        if shot:
            b64 = _screenshot_to_base64(project_id, run_id, shot)
            if b64:
                fail_cls = " screenshot-thumb--fail" if st == "failed" else ""
                shot_html = f'<img src="{b64}" class="screenshot-thumb{fail_cls}" />'
        dot_color = "#00e5a0" if st == "passed" else "#ff3b5c" if st == "failed" else "#555"
        fail_cls = " step-row--fail" if st == "failed" else ""
        error_detail = ""
        if st == "failed" and sr.get("error"):
            err = sr["error"] if isinstance(sr["error"], str) else str(sr["error"])
            error_detail = f'<div style="grid-column:3/6;font-size:11px;color:#ff7b93;margin-top:2px">{_esc(err)}</div>'
        rows += f'''<div class="step-row{fail_cls}">
<div class="step-num">{idx + 1}</div>
<div class="dot" style="background:{dot_color}"></div>
<div><strong>{stype}</strong>{f' <span class="selector">{sel_str}</span>' if sel_str else ""}{" — <em style='color:#555'>skipped</em>" if st not in ("passed","failed") else ""}</div>
<div class="dur">{dur_str}</div>
<div>{shot_html}</div>
{error_detail}
</div>'''
    return rows


@router.get("/api/tests/{test_id}/export/html")
def export_test_html(test_id: int, days: int = 14) -> StreamingResponse:
    """Generate a rich self-contained HTML report for a single test case."""
    with SessionLocal() as db:
        test = db.query(TestDefinition).filter(TestDefinition.id == test_id).first()
        if not test:
            raise HTTPException(status_code=404, detail="Test not found")
        project = db.query(Project).filter(Project.id == test.project_id).first()
        runs = db.query(Run).filter(Run.test_id == test_id).order_by(Run.id.desc()).limit(50).all()

        # Build info from latest run
        build_info = ""
        latest_run = runs[0] if runs else None
        if latest_run and latest_run.build_id:
            build = db.query(Build).filter(Build.id == latest_run.build_id).first()
            if build:
                build_info = f"{build.file_name} ({build.platform})"

        passed = sum(1 for r in runs if r.status == "passed")
        total = len(runs)
        rate = round(100 * passed / total) if total else 0
        streak = 0
        for r in runs:
            if r.status in ("failed", "error"):
                streak += 1
            else:
                break

        last_passed_at = None
        for r in runs:
            if r.status == "passed":
                last_passed_at = r.finished_at.isoformat() if r.finished_at else None
                break

        # Get the last failed run with full detail
        last_failed = None
        for r in runs:
            if r.status in ("failed", "error"):
                last_failed = r
                break

        prereq_def = None
        if test.prerequisite_test_id and test.prerequisite_test_id != test.id:
            prereq_def = db.query(TestDefinition).filter(TestDefinition.id == test.prerequisite_test_id).first()
        step_html = ""
        error_html = ""
        fix_html = ""
        fail_screenshot_html = ""
        if last_failed:
            rsummary = last_failed.summary or {}
            rsteps = rsummary.get("stepResults") or []
            arts = last_failed.artifacts or {}
            screenshots = arts.get("screenshots") or []
            # Skip prerequisite steps using the run's actual platform
            run_pf = (last_failed.platform or "android").strip() or "android"
            poffset = len(_steps_for_platform_record(prereq_def, run_pf)) if prereq_def else 0
            own_rsteps = rsteps[poffset:]
            own_screenshots = screenshots[poffset:]
            step_detail = []
            for si, sr_item in enumerate(own_rsteps):
                shot = own_screenshots[si] if si < len(own_screenshots) else None
                step_detail.append({
                    "index": si,
                    "type": (sr_item.get("step") or {}).get("type") or "",
                    "selector": (sr_item.get("step") or {}).get("selector") or (sr_item.get("details", {}).get("selector") if isinstance(sr_item.get("details"), dict) else None),
                    "status": sr_item.get("status") or "pending",
                    "duration_ms": sr_item.get("duration_ms"),
                    "error": sr_item.get("details", {}).get("error") if isinstance(sr_item.get("details"), dict) else (sr_item.get("details") if sr_item.get("status") == "failed" else None),
                    "screenshot": shot,
                })

            step_html = _render_step_rows_html(step_detail, test.project_id, last_failed.id)

            if last_failed.error_message:
                error_html = f'<div class="error-box">{_esc(last_failed.error_message)}</div>'

            # Failed step screenshot (full size)
            for sd in step_detail:
                if sd["status"] == "failed" and sd.get("screenshot"):
                    b64 = _screenshot_to_base64(test.project_id, last_failed.id, sd["screenshot"])
                    if b64:
                        fail_screenshot_html = f'<h2>Failure Screenshot (Step {sd["index"]+1})</h2><img class="screenshot-full" src="{b64}" />'
                    break

            fh = test.fix_history or []
            if fh:
                latest_fix = fh[-1]
                fix_html = f'<div class="fix-box"><div class="fix-label">AI Fix Suggestion</div><div class="fix-text">{_esc(latest_fix.get("analysis",""))}</div></div>'

        # Run history strip
        history_html = ""
        for r in reversed(runs[:30]):
            c = "#00e5a0" if r.status == "passed" else "#ff3b5c" if r.status in ("failed","error") else "#555"
            ts = r.finished_at.strftime("%b %d %H:%M") if r.finished_at else ""
            history_html += f'<div class="history-dot" style="background:{c}" title="Run #{r.id} {r.status} {ts}"></div>'

        status_badge = "badge-pass" if streak == 0 and passed > 0 else "badge-fail" if streak > 0 else "badge-skip"
        status_label = "Passing" if streak == 0 and passed > 0 else "Failing" if streak > 0 else "No runs"

        steps_total = max(len(_steps_for_platform_record(test, "android")), len(_steps_for_platform_record(test, "ios_sim")))
        steps_ran = 0
        if last_failed:
            sr_list = (last_failed.summary or {}).get("stepResults") or []
            all_ran = sum(1 for s in sr_list if s.get("status") in ("passed", "failed"))
            steps_ran = max(0, all_ran - poffset)
        elif latest_run:
            sr_list = (latest_run.summary or {}).get("stepResults") or []
            all_ran = sum(1 for s in sr_list if s.get("status") in ("passed", "failed"))
            run_pf = (latest_run.platform or "android").strip() or "android"
            lr_offset = len(_steps_for_platform_record(prereq_def, run_pf)) if prereq_def else 0
            steps_ran = max(0, all_ran - lr_offset)

        dur_s = None
        if last_failed and last_failed.finished_at and last_failed.started_at:
            dur_s = round((last_failed.finished_at - last_failed.started_at).total_seconds(), 1)

        html = f'''<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>{test.name} — Test Report</title>
<style>{_REPORT_CSS}</style></head><body>
<h1>{test.name} <span class="badge {status_badge}">{status_label}</span></h1>
<div class="subtitle">{project.name if project else ""} · Generated {datetime.utcnow().strftime("%Y-%m-%d %H:%M")} UTC</div>

<div class="stats">
<div class="stat"><div class="stat-val" style="color:{"#00e5a0" if rate>=80 else "#ffb020" if rate>=50 else "#ff3b5c"}">{rate}%</div><div class="stat-lbl">Pass Rate</div></div>
<div class="stat"><div class="stat-val">{total}</div><div class="stat-lbl">Total Runs</div></div>
<div class="stat"><div class="stat-val" style="color:#00e5a0">{passed}</div><div class="stat-lbl">Passed</div></div>
<div class="stat"><div class="stat-val" style="color:#ff3b5c">{total - passed}</div><div class="stat-lbl">Failed</div></div>
<div class="stat"><div class="stat-val" style="color:{"#ff3b5c" if streak > 3 else "#ffb020" if streak > 0 else "#00e5a0"}">{streak}</div><div class="stat-lbl">Fail Streak</div></div>
<div class="stat"><div class="stat-val">{steps_ran}/{steps_total}</div><div class="stat-lbl">Steps Ran</div></div>
</div>

<div class="meta-grid">
<div class="meta-item">Build: <strong>{build_info or "—"}</strong></div>
<div class="meta-item">Platform: <strong>{last_failed.platform if last_failed else "—"}</strong></div>
<div class="meta-item">Device: <strong>{last_failed.device_target if last_failed else "—"}</strong></div>
<div class="meta-item">Last passed: <strong>{last_passed_at[:10] if last_passed_at else "Never"}</strong></div>
<div class="meta-item">Last run: <strong>{latest_run.finished_at.strftime("%Y-%m-%d %H:%M") if latest_run and latest_run.finished_at else "—"}</strong></div>
<div class="meta-item">Duration: <strong>{f"{dur_s}s" if dur_s else "—"}</strong></div>
<div class="meta-item">AI fixes used: <strong>{len(test.fix_history or [])}</strong></div>
<div class="meta-item">Failure category: <strong>{last_failed.failure_category if last_failed and hasattr(last_failed, "failure_category") else "—"}</strong></div>
</div>

{f'<div class="section"><div style="font-size:11px;color:#6b7280;margin-bottom:6px">ACCEPTANCE CRITERIA</div><div style="font-size:12px;color:#c9d1d9;padding:10px 14px;background:#111820;border-radius:6px;border:1px solid #1e2430">{_esc(test.acceptance_criteria)}</div></div>' if test.acceptance_criteria else ""}

<h2>Run History (last {min(len(runs), 30)} runs)</h2>
<div class="history-strip" style="margin-bottom:24px">{history_html}</div>

{f'<h2>Step Execution — Run #{last_failed.id}</h2>{step_html}' if step_html else '<div style="color:#6b7280;padding:20px">No failed run to display steps for.</div>'}

{error_html}
{fix_html}
{fail_screenshot_html}

<div class="footer">QA·OS Test Case Report · {test.name} · {datetime.utcnow().isoformat()}</div>
</body></html>'''

    safe_name = test.name.replace(" ", "_").replace("/", "_")[:60]
    return StreamingResponse(
        iter([html]), media_type="text/html",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}_report.html"'},
    )


@router.get("/api/suites/{suite_id}/export/csv")
def export_suite_csv(suite_id: int, days: int = 14, platform: str = ""):
    import csv as csvmod
    import io
    data = suite_health(suite_id, days, platform)
    buf = io.StringIO()
    w = csvmod.writer(buf)
    w.writerow(["test_name", "status", "pass_rate_pct", "steps_ran", "steps_total", "fail_streak", "last_passed", "error_message", "failure_category", "platform"])
    for t in data["tests"]:
        lfr = t.get("last_failed_run") or {}
        w.writerow([
            t["name"], t["status"], t["pass_rate_pct"],
            t["steps_ran"], t["steps_total"], t["fail_streak"],
            t.get("last_passed_at") or "", lfr.get("error_message") or "",
            lfr.get("failure_category") or "", t["platform"],
        ])
    buf.seek(0)
    sname = data["suite"]["name"].replace(" ", "_")
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="suite_{sname}_health.csv"'},
    )


@router.get("/api/suites/{suite_id}/export/html")
def export_suite_html(suite_id: int, days: int = 14, platform: str = ""):
    data = suite_health(suite_id, days, platform)
    s = data["suite"]
    m = data["metrics"]
    tests = data["tests"]

    # Find project_id for screenshot embedding
    with SessionLocal() as db:
        suite = db.query(TestSuite).filter(TestSuite.id == suite_id).first()
        mod = db.query(Module).filter(Module.id == suite.module_id).first() if suite else None
        project_id = mod.project_id if mod else 0

    def _badge(st):
        if st == "passing": return '<span class="badge badge-pass">Passing</span>'
        if st == "failing": return '<span class="badge badge-fail">Failing</span>'
        if st == "flaky": return '<span class="badge badge-flaky">Flaky</span>'
        return '<span class="badge badge-skip">No runs</span>'

    test_sections = ""
    for t in tests:
        strip = "".join(f'<div class="history-dot" style="background:{"#00e5a0" if rh["status"]=="passed" else "#ff3b5c" if rh["status"] in ("failed","error") else "#555"}" title="#{rh["id"]} {rh["status"]}"></div>' for rh in t["run_history"])
        rate_c = "#00e5a0" if t["pass_rate_pct"] >= 80 else "#ffb020" if t["pass_rate_pct"] >= 50 else "#ff3b5c"

        detail_html = ""
        if t.get("last_failed_run"):
            lfr = t["last_failed_run"]
            step_html = _render_step_rows_html(lfr.get("step_results", []), project_id, lfr["id"])
            err = f'<div class="error-box">{_esc(lfr["error_message"])}</div>' if lfr.get("error_message") else ""
            fix = ""
            if lfr.get("ai_fix"):
                fix = f'<div class="fix-box"><div class="fix-label">AI Fix Suggestion</div><div class="fix-text">{_esc(lfr["ai_fix"].get("analysis",""))}</div></div>'
            detail_html = f'{step_html}{err}{fix}'

        test_sections += f'''
<div style="background:#0d1117;border:1px solid #1e2430;border-radius:10px;padding:16px 20px;margin-bottom:16px">
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
<div style="font-size:14px;font-weight:600">{t["name"]} {_badge(t["status"])}</div>
<div style="display:flex;gap:16px;font-size:11px;color:#6b7280">
<span>Steps: <strong style="color:#e8eaed">{t["steps_ran"]}/{t["steps_total"]}</strong></span>
<span>Pass: <strong style="color:{rate_c}">{t["pass_rate_pct"]}%</strong></span>
<span>Streak: <strong style="color:{"#ff3b5c" if t["fail_streak"]>0 else "#e8eaed"}">{t["fail_streak"]}</strong></span>
</div>
</div>
<div class="history-strip" style="margin-bottom:10px">{strip}</div>
{detail_html}
</div>'''

    html = f'''<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>Suite Report — {s["name"]}</title>
<style>{_REPORT_CSS}</style></head><body>
<h1>Suite Report — {s["name"]}</h1>
<div class="subtitle">{s.get("module_name","")} · {days}-day window · Platform: {platform or "All"} · Generated {datetime.utcnow().strftime("%Y-%m-%d %H:%M")} UTC</div>
<div class="stats">
<div class="stat"><div class="stat-val" style="color:{"#00e5a0" if s["pass_rate"]>=80 else "#ffb020" if s["pass_rate"]>=50 else "#ff3b5c"}">{s["pass_rate"]}%</div><div class="stat-lbl">Pass Rate</div></div>
<div class="stat"><div class="stat-val">{m["total"]}</div><div class="stat-lbl">Total</div></div>
<div class="stat"><div class="stat-val" style="color:#00e5a0">{m["passing"]}</div><div class="stat-lbl">Passing</div></div>
<div class="stat"><div class="stat-val" style="color:#ff3b5c">{m["failing"]}</div><div class="stat-lbl">Failing</div></div>
<div class="stat"><div class="stat-val" style="color:#ffb020">{m["flaky"]}</div><div class="stat-lbl">Flaky</div></div>
<div class="stat"><div class="stat-val">{m["avg_steps_pct"]}%</div><div class="stat-lbl">Avg Steps</div></div>
</div>
<h2>Test Cases ({m["total"]})</h2>
{test_sections}
<div class="footer">QA·OS Suite Report · {s["name"]} · {datetime.utcnow().isoformat()}</div>
</body></html>'''

    return StreamingResponse(
        iter([html]), media_type="text/html",
        headers={"Content-Disposition": f'attachment; filename="suite_{s["name"].replace(" ","_")}_report.html"'},
    )


@router.get("/api/suites/{suite_id}/export/screenshots")
def export_suite_screenshots(suite_id: int, days: int = 14, platform: str = ""):
    import zipfile as zf
    data = suite_health(suite_id, days, platform)
    buf = BytesIO()
    total_size = 0
    MAX_SIZE = 15 * 1024 * 1024
    with zf.ZipFile(buf, "w", zf.ZIP_DEFLATED) as z:
        for t in data["tests"]:
            lfr = t.get("last_failed_run")
            if not lfr:
                continue
            for sr in lfr.get("step_results") or []:
                shot = sr.get("screenshot")
                if not shot:
                    continue
                # find the file on disk
                with SessionLocal() as db:
                    suite = db.query(TestSuite).filter(TestSuite.id == suite_id).first()
                    if not suite:
                        continue
                    mod = db.query(Module).filter(Module.id == suite.module_id).first()
                    if not mod:
                        continue
                    project_id = mod.project_id
                art_path = settings.artifacts_dir / str(project_id) / str(lfr["id"]) / shot
                if art_path.exists():
                    content = art_path.read_bytes()
                    total_size += len(content)
                    if total_size > MAX_SIZE:
                        break
                    arcname = f'{data["suite"]["name"]}/{t["name"]}/run_{lfr["id"]}_step_{sr["index"]}.png'
                    z.writestr(arcname, content)
            if total_size > MAX_SIZE:
                break
    buf.seek(0)
    sname = data["suite"]["name"].replace(" ", "_")
    return StreamingResponse(buf, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{sname}_screenshots.zip"'})


def _format_date(iso_str) -> str:
    """Format an ISO date string for reports, or return '—' for None/empty."""
    if not iso_str or iso_str == "None":
        return "—"
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        return dt.strftime("%b %d, %Y %H:%M")
    except Exception:
        return str(iso_str)[:16]


@router.get("/api/collections/{collection_id}/export/html")
def export_collection_html(collection_id: int, days: int = 14, platform: str = ""):
    data = collection_health(collection_id, days, platform)
    c = data["collection"]
    m = data["metrics"]

    verdict_map = {"READY": "badge-pass", "BLOCKED": "badge-fail", "AT RISK": "badge-flaky"}
    verdict_cls = verdict_map.get(c["verdict"], "badge-skip")

    suite_cards = ""
    for s in data["suites"]:
        bar_w = s["pass_rate_pct"]
        bar_c = "#00e5a0" if bar_w >= 80 else "#ffb020" if bar_w >= 50 else "#ff3b5c"
        last_run = _format_date(s.get("last_run_at"))
        suite_cards += f'''
<div style="background:#0d1117;border:1px solid #1e2430;border-radius:10px;padding:16px 20px;margin-bottom:12px">
<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
<div style="font-size:14px;font-weight:600">{_esc(s["name"])}</div>
<div style="font-size:11px;color:#6b7280">Last run: {last_run}</div>
</div>
<div style="display:flex;gap:20px;align-items:center;margin-bottom:8px">
<div style="flex:1;height:10px;background:#1e2430;border-radius:5px;overflow:hidden"><div style="height:100%;width:{bar_w}%;background:{bar_c};border-radius:5px"></div></div>
<div style="font-size:18px;font-weight:700;color:{bar_c};min-width:50px;text-align:right">{s["pass_rate_pct"]}%</div>
</div>
<div style="display:flex;gap:20px;font-size:11px;color:#6b7280">
<span>Pass: <strong style="color:#00e5a0">{s["pass_count"]}</strong></span>
<span>Fail: <strong style="color:#ff3b5c">{s["fail_count"]}</strong></span>
<span>Blockers: <strong style="color:#ff6b35">{s["blocker_count"]}</strong></span>
<span>Total: <strong style="color:#e8eaed">{s["pass_count"] + s["fail_count"]}</strong></span>
</div>
</div>'''

    # Build a proper trend chart with axes, gridlines, labels, and data points
    trend_html = ""
    pts = data.get("trend_30d") or []
    if len(pts) >= 2:
        w, h = 600, 160
        pad_l, pad_r, pad_t, pad_b = 45, 20, 10, 30
        cw = w - pad_l - pad_r
        ch = h - pad_t - pad_b
        # Y axis gridlines and labels (0%, 25%, 50%, 75%, 100%)
        grid_lines = ""
        for pct in [0, 25, 50, 75, 100]:
            y = pad_t + ch - (pct / 100 * ch)
            grid_lines += f'<line x1="{pad_l}" y1="{y}" x2="{w - pad_r}" y2="{y}" stroke="#1e2430" stroke-width="1"/>'
            grid_lines += f'<text x="{pad_l - 6}" y="{y + 3}" text-anchor="end" fill="#6b7280" font-size="9" font-family="system-ui">{pct}%</text>'
        # Data points and line
        coords = []
        dots = ""
        labels = ""
        for i, p in enumerate(pts):
            x = pad_l + (i * cw / max(len(pts) - 1, 1))
            y = pad_t + ch - (p["pass_rate_pct"] / 100 * ch)
            coords.append(f"{x},{y}")
            dot_c = "#00e5a0" if p["pass_rate_pct"] >= 50 else "#ff3b5c"
            dots += f'<circle cx="{x}" cy="{y}" r="4" fill="{dot_c}" stroke="#080b0f" stroke-width="2"/>'
            dots += f'<text x="{x}" y="{y - 10}" text-anchor="middle" fill="#e8eaed" font-size="9" font-weight="600" font-family="system-ui">{p["pass_rate_pct"]}%</text>'
            # X axis labels (show every Nth to avoid crowding)
            date_str = str(p.get("date", ""))
            if date_str and (i == 0 or i == len(pts) - 1 or len(pts) <= 7 or i % max(1, len(pts) // 5) == 0):
                labels += f'<text x="{x}" y="{h - 4}" text-anchor="middle" fill="#6b7280" font-size="8" font-family="system-ui">{date_str[5:]}</text>'
        poly = " ".join(coords)
        # Area fill under the line
        area_pts = f"{coords[0].split(',')[0]},{pad_t + ch} {poly} {coords[-1].split(',')[0]},{pad_t + ch}"
        trend_html = f'''<h2>30-Day Pass Rate Trend</h2>
<svg viewBox="0 0 {w} {h}" style="width:100%;max-width:700px;height:{h}px;margin-bottom:16px">
{grid_lines}
<polygon points="{area_pts}" fill="rgba(0,229,160,.06)"/>
<polyline points="{poly}" fill="none" stroke="#00e5a0" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
{dots}
{labels}
</svg>'''
    elif len(pts) == 1:
        trend_html = f'<h2>30-Day Trend</h2><div style="padding:16px;background:#0d1117;border:1px solid #1e2430;border-radius:8px;font-size:12px;color:#6b7280">Only 1 data point — {pts[0]["pass_rate_pct"]}% pass rate. More data points needed to render a trend.</div>'

    html = f'''<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>Collection Report — {_esc(c["name"])}</title>
<style>{_REPORT_CSS}</style></head><body>
<h1>{_esc(c["name"])} <span class="badge {verdict_cls}">{c["verdict"]}</span></h1>
<div class="subtitle">{days}-day window · Platform: {platform or "All"} · Generated {datetime.utcnow().strftime("%Y-%m-%d %H:%M")} UTC</div>
<div class="stats">
<div class="stat"><div class="stat-val">{m["total"]}</div><div class="stat-lbl">Total</div></div>
<div class="stat"><div class="stat-val" style="color:#00e5a0">{m["passing"]}</div><div class="stat-lbl">Passing</div></div>
<div class="stat"><div class="stat-val" style="color:#ff3b5c">{m["failing"]}</div><div class="stat-lbl">Failing</div></div>
<div class="stat"><div class="stat-val" style="color:#ff6b35">{m["blockers"]}</div><div class="stat-lbl">Blockers</div></div>
<div class="stat"><div class="stat-val" style="color:#ffb020">{m["flaky"]}</div><div class="stat-lbl">Flaky</div></div>
<div class="stat"><div class="stat-val">{m["never_run"]}</div><div class="stat-lbl">Never Ran</div></div>
</div>
<h2>Suites ({len(data["suites"])})</h2>
{suite_cards}
{trend_html}
<div class="footer">QA·OS Collection Report · {_esc(c["name"])} · {datetime.utcnow().isoformat()}</div>
</body></html>'''

    return StreamingResponse(
        iter([html]), media_type="text/html",
        headers={"Content-Disposition": f'attachment; filename="collection_{c["name"].replace(" ","_")}_report.html"'},
    )


@router.get("/api/projects/{project_id}/reports/hierarchy")
def reports_hierarchy(project_id: int) -> dict[str, Any]:
    """Collection → suite → test → recent runs for reports UI."""
    with SessionLocal() as db:
        modules = db.query(Module).filter(Module.project_id == project_id).all()
        mod_ids = [m.id for m in modules]
        all_suites = db.query(TestSuite).filter(TestSuite.module_id.in_(mod_ids)).all() if mod_ids else []
        all_tests = db.query(TestDefinition).filter(TestDefinition.project_id == project_id).all()
        all_runs = (
            db.query(Run)
            .filter(Run.project_id == project_id)
            .order_by(Run.id.desc())
            .limit(500)
            .all()
        )

    runs_by_test: dict[int, list[Run]] = {}
    for r in all_runs:
        if r.test_id is not None:
            runs_by_test.setdefault(r.test_id, []).append(r)

    def run_out(r: Run) -> dict[str, Any]:
        arts = r.artifacts or {}
        summary = r.summary or {}
        step_results = summary.get("stepResults", []) or []
        step_defs = summary.get("stepDefinitions", []) or []
        total_steps = len(step_defs) if step_defs else len(step_results)
        dur: float | None = None
        if r.finished_at and r.started_at:
            dur = round((r.finished_at - r.started_at).total_seconds(), 1)
        return {
            "id": r.id,
            "status": r.status,
            "platform": r.platform,
            "device_target": r.device_target,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "duration_s": dur,
            "error_message": r.error_message,
            "screenshots": arts.get("screenshots", []),
            "video": arts.get("video"),
            "step_results": step_results,
            "passed_steps": sum(1 for s in step_results if s and s.get("status") == "passed"),
            "total_steps": total_steps,
        }

    def test_out(t: TestDefinition) -> dict[str, Any]:
        test_runs = runs_by_test.get(t.id, [])[:5]
        latest = test_runs[0] if test_runs else None
        n_and = len(_steps_for_platform_record(t, "android"))
        n_ios = len(_steps_for_platform_record(t, "ios_sim"))
        return {
            "id": t.id,
            "name": t.name,
            "steps_count": max(n_and, n_ios),
            "acceptance_criteria": t.acceptance_criteria,
            "latest_status": latest.status if latest else "not_run",
            "latest_run_id": latest.id if latest else None,
            "runs": [run_out(r) for r in test_runs],
        }

    def suite_out(s: TestSuite) -> dict[str, Any]:
        tests = [t for t in all_tests if t.suite_id == s.id]
        test_outs = [test_out(t) for t in tests]
        passed = sum(1 for t in test_outs if t["latest_status"] == "passed")
        run_count = sum(1 for t in test_outs if t["latest_status"] != "not_run")
        return {
            "id": s.id,
            "name": s.name,
            "tests": test_outs,
            "total_tests": len(tests),
            "passed_count": passed,
            "failed_count": sum(1 for t in test_outs if t["latest_status"] in ("failed", "error")),
            "not_run_count": len(tests) - run_count,
            "pass_rate": round(passed / run_count * 100) if run_count > 0 else 0,
        }

    def module_out(m: Module) -> dict[str, Any]:
        suites = [s for s in all_suites if s.module_id == m.id]
        suite_outs = [suite_out(s) for s in suites]
        total = sum(s["total_tests"] for s in suite_outs)
        passed = sum(s["passed_count"] for s in suite_outs)
        run = total - sum(s["not_run_count"] for s in suite_outs)
        return {
            "id": m.id,
            "name": m.name,
            "suites": suite_outs,
            "total_tests": total,
            "passed_count": passed,
            "failed_count": sum(s["failed_count"] for s in suite_outs),
            "not_run_count": sum(s["not_run_count"] for s in suite_outs),
            "pass_rate": round(passed / run * 100) if run > 0 else 0,
        }

    collections = [module_out(m) for m in modules]
    total = sum(c["total_tests"] for c in collections)
    passed = sum(c["passed_count"] for c in collections)
    run = total - sum(c["not_run_count"] for c in collections)

    return {
        "collections": collections,
        "summary": {
            "total_tests": total,
            "executed": run,
            "passed": passed,
            "failed": sum(c["failed_count"] for c in collections),
            "not_run": total - run,
            "pass_rate": round(passed / run * 100) if run > 0 else 0,
        },
    }
