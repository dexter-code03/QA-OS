"""Shared helpers used across multiple routers."""
from __future__ import annotations

import json
import os
import secrets
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException, Request, WebSocket

from .compose_detection import is_compose_screen
from .models import (
    BatchRun,
    Run,
    ScreenLibrary,
    TestDefinition,
)
from .schemas import (
    BatchRunChildOut,
    BatchRunOut,
    RunOut,
    TestOut,
)
from .settings import (
    ensure_dirs,
    load_encrypted_json,
    save_encrypted_json,
    settings,
)
from .swiftui_detection import is_swiftui_screen

# ── Constants ──────────────────────────────────────────────────────────

ALLOWED_ORIGINS = [
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:5174",
    "http://localhost:5174",
]
def utcnow() -> datetime:
    """Return current UTC time as a naive datetime (SQLite-compatible)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


AUTH_COOKIE_NAME = "qa_os_token"
AUTH_TOKEN_FILE = settings.data_dir / "token.txt"
SETTINGS_FILE = settings.data_dir / "settings.json"
ONBOARDING_FILE = settings.data_dir / "onboarding.json"

# ── Settings helpers ───────────────────────────────────────────────────


def load_settings() -> dict:
    return load_encrypted_json(SETTINGS_FILE)


def save_settings_file(data: dict) -> None:
    save_encrypted_json(SETTINGS_FILE, data)


def ai_creds(s: dict | None = None) -> tuple[str, str]:
    if s is None:
        s = load_settings()
    key = s.get("ai_api_key") or s.get("ai_key") or ""
    model = s.get("ai_model") or "gemini-2.5-flash"
    return key, model


def gemini_extract_text(data: dict[str, Any]) -> str:
    """Pull model text from generateContent JSON; raise HTTPException if unusable."""
    err = data.get("error")
    if err:
        if isinstance(err, dict):
            msg = str(err.get("message", json.dumps(err)[:400]))
        else:
            msg = str(err)
        raise HTTPException(status_code=502, detail=f"Gemini API error: {msg}")
    cands = data.get("candidates")
    if not cands:
        fb = data.get("promptFeedback")
        extra = json.dumps(fb)[:500] if fb else "(none)"
        raise HTTPException(
            status_code=502,
            detail=(
                "Gemini returned no candidates — often quota, safety block, or wrong model id. "
                f"Check Settings → AI model name and API key. promptFeedback={extra}"
            ),
        )
    first = cands[0]
    content = first.get("content") or {}
    parts = content.get("parts") or []
    if not parts:
        reason = first.get("finishReason", "")
        raise HTTPException(
            status_code=502,
            detail=f"Gemini returned no text (finishReason={reason}). Try again or shorten screenshot/XML payload.",
        )
    text = parts[0].get("text")
    if not text:
        raise HTTPException(status_code=502, detail="Gemini returned an empty text part.")
    return text


# ── Auth helpers ───────────────────────────────────────────────────────


def _write_private_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
    finally:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def get_auth_token() -> str:
    ensure_dirs()
    if AUTH_TOKEN_FILE.exists():
        token = AUTH_TOKEN_FILE.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_urlsafe(32)
    _write_private_text(AUTH_TOKEN_FILE, f"{token}\n")
    return token


def extract_bearer_token(value: str | None) -> str:
    if not value:
        return ""
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return token.strip()


def extract_request_token(request: Request) -> str:
    return (
        extract_bearer_token(request.headers.get("Authorization"))
        or request.cookies.get(AUTH_COOKIE_NAME, "")
    )


def extract_websocket_token(websocket: WebSocket) -> str:
    """WebSocket still accepts ?token= because browsers can't set custom headers on WS upgrade."""
    return (
        extract_bearer_token(websocket.headers.get("authorization"))
        or websocket.cookies.get(AUTH_COOKIE_NAME, "")
        or websocket.query_params.get("token", "")
    )


# ── Model converters ──────────────────────────────────────────────────


def run_to_out(r: Run) -> RunOut:
    return RunOut(
        id=r.id,
        project_id=r.project_id,
        build_id=r.build_id,
        test_id=r.test_id,
        batch_run_id=getattr(r, "batch_run_id", None),
        status=r.status,
        platform=r.platform,
        device_target=r.device_target,
        started_at=r.started_at,
        finished_at=r.finished_at,
        error_message=r.error_message,
        summary=r.summary or {},
        artifacts=r.artifacts or {},
        data_set_id=getattr(r, "data_set_id", None),
        data_row_index=getattr(r, "data_row_index", None),
    )


def steps_for_platform_record(t: TestDefinition, platform: str) -> list[dict]:
    ps = getattr(t, "platform_steps", None) or {}
    if isinstance(ps, dict) and platform in ps and ps[platform]:
        return list(ps[platform])
    return list(t.steps or [])


def test_out(t: TestDefinition) -> TestOut:
    ps = getattr(t, "platform_steps", None) or {}
    if not isinstance(ps, dict):
        ps = {}
    android_steps = list(ps.get("android") or t.steps or [])
    ios_steps = list(ps.get("ios_sim") or [])
    return TestOut(
        id=t.id,
        project_id=t.project_id,
        suite_id=t.suite_id,
        prerequisite_test_id=t.prerequisite_test_id,
        name=t.name,
        steps=android_steps,
        platform_steps={"android": android_steps, "ios_sim": ios_steps},
        acceptance_criteria=getattr(t, "acceptance_criteria", None),
        fix_history=getattr(t, "fix_history", None) or [],
        created_at=t.created_at,
    )


def batch_to_out(b: BatchRun, db) -> BatchRunOut:
    child_runs = db.query(Run).filter(Run.batch_run_id == b.id).order_by(Run.id).all()
    children: list[BatchRunChildOut] = []
    for cr in child_runs:
        t = db.query(TestDefinition).filter(TestDefinition.id == cr.test_id).first() if cr.test_id else None
        children.append(BatchRunChildOut(
            run_id=cr.id,
            test_id=cr.test_id or 0,
            test_name=t.name if t else f"Run #{cr.id}",
            status=cr.status,
            started_at=cr.started_at,
            finished_at=cr.finished_at,
            error_message=cr.error_message,
        ))
    first_child = child_runs[0] if child_runs else None
    return BatchRunOut(
        id=b.id,
        project_id=b.project_id,
        mode=b.mode,
        source_id=b.source_id,
        source_name=b.source_name,
        platform=b.platform,
        status=b.status,
        total=b.total,
        passed=b.passed,
        failed=b.failed,
        build_id=first_child.build_id if first_child else None,
        device_target=first_child.device_target if first_child else "",
        started_at=b.started_at,
        finished_at=b.finished_at,
        children=children,
    )




# ── Failure classification ────────────────────────────────────────────


def classify_failure_message(msg: str, platform: str | None) -> dict[str, Any]:
    s = (msg or "").lower()
    category = "other"
    if any(x in s for x in ("nosuchelement", "no such element", "unable to locate", "could not find", "not found")):
        category = "selector_not_found"
    elif any(x in s for x in ("timeout", "timed out", "wait")):
        category = "element_timeout"
    elif "assertion" in s or "expected" in s or "assert" in s:
        category = "assertion_failure"
    elif any(x in s for x in ("connection", "network", "unreachable", "econnrefused", "socket")):
        category = "network_error"
    elif any(x in s for x in ("crash", "anr", "instrumentation")):
        category = "app_crash"
    return {
        "category": category,
        "platform": platform or "",
        "summary": (msg or "")[:500],
    }


# ── Screen / XML helpers ─────────────────────────────────────────────


def compress_screenshot(fpath: Path, max_dim: int = 512) -> str:
    """Resize a screenshot to fit within max_dim and return base64 JPEG."""
    try:
        from PIL import Image
        import io, base64
        img = Image.open(fpath)
        img.thumbnail((max_dim, max_dim), Image.LANCZOS)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=60)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ""


def effective_screen_type(screen: ScreenLibrary) -> str:
    """Android: compose | native. iOS: swiftui | uikit."""
    st = getattr(screen, "screen_type", None)
    plat = (screen.platform or "").lower()
    if plat in ("ios_sim", "ios"):
        if st in ("swiftui", "uikit"):
            return st
        if st == "native":
            return "uikit"
        xml = screen.xml_snapshot or ""
        return "swiftui" if is_swiftui_screen(xml) else "uikit"
    if plat != "android":
        return "native"
    if st in ("compose", "native"):
        return st
    return "compose" if is_compose_screen(screen.xml_snapshot) else "native"


def screen_to_dict(s: ScreenLibrary, include_xml: bool = False) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": s.id, "project_id": s.project_id, "build_id": s.build_id,
        "folder_id": s.folder_id,
        "name": s.name, "platform": s.platform,
        "screenshot_path": s.screenshot_path,
        "captured_at": s.captured_at.isoformat() if s.captured_at else None,
        "captured_by": s.captured_by, "notes": s.notes,
        "auto_captured": bool(s.auto_captured),
        "xml_length": len(s.xml_snapshot) if s.xml_snapshot else 0,
        "screen_type": getattr(s, "screen_type", None),
    }
    if include_xml:
        d["xml_snapshot"] = s.xml_snapshot
    return d


def filter_screen_library_by_build(
    q: Any,
    build_ids: Optional[list[int]] = None,
    build_id_legacy: Optional[int] = None,
) -> Any:
    ids = [b for b in (build_ids or []) if b is not None]
    if ids:
        return q.filter(ScreenLibrary.build_id.in_(ids))
    if build_id_legacy is not None:
        return q.filter(ScreenLibrary.build_id == build_id_legacy)
    return q


def android_selector_generation_rules(screens: list[ScreenLibrary]) -> str:
    android_screens = [s for s in screens if s.platform == "android"]
    if not android_screens:
        return ""
    lines = [
        "ANDROID — PER-SCREEN SELECTOR MODE (must match each screen block header in DOM CONTEXT):",
    ]
    for s in android_screens:
        st = effective_screen_type(s)
        if st == "compose":
            lines.append(
                f'  • "{s.name}" [compose]: NEVER use selector.using "id". '
                'ALWAYS use "-android uiautomator" with UiSelector Java, e.g. '
                'new UiSelector().resourceId("<exact resource-id from XML>"), or .descriptionContains("..."), .textContains("...") '
                "when resource-id is absent."
            )
        else:
            lines.append(
                f'  • "{s.name}" [native]: prefer stable resource-id using Appium "id" strategy; '
                "then content-desc/accessibilityId, then text, then xpath as in the priority order."
            )
    return "\n".join(lines) + "\n\n"


def ios_selector_generation_rules(screens: list[ScreenLibrary]) -> str:
    ios_screens = [s for s in screens if (s.platform or "").lower() in ("ios_sim", "ios")]
    if not ios_screens:
        return ""
    lines = [
        "iOS — PER-SCREEN SELECTOR MODE (must match each screen block header in DOM CONTEXT):",
    ]
    for s in ios_screens:
        st = effective_screen_type(s)
        if st == "swiftui":
            lines.append(
                f'  • "{s.name}" [swiftui]: Prefer "-ios predicate string" with name (accessibility identifier) or label, '
                'e.g. {"using": "-ios predicate string", "value": "name == \'my_id\'"}. '
                "If multiple matches, use \"-ios class chain\" with **/XCUIElementType...[`name == '...'`]. "
                'Avoid bare "id" for taps; try "accessibility id" only when the XML shows a stable name= attribute.'
            )
        else:
            lines.append(
                '  • "' + s.name + '" [uikit]: Prefer {"using": "accessibility id", "value": "<name from XML>"} when name= is set; '
                'otherwise "-ios predicate string" on label or type; class chain if nested; xpath last resort.'
            )
    return "\n".join(lines) + "\n\n"


def build_xml_context(screens: list[ScreenLibrary], description: str = "") -> str:
    """Extract interactive elements from page source XML to reduce token usage.

    Delegates to the 3-pass pipeline in helpers_xml. When no description is
    provided, Pass 3 (relevance ranking) is skipped for backward compatibility.
    """
    from .helpers_xml import build_xml_context_v2
    return build_xml_context_v2(screens, description=description)
