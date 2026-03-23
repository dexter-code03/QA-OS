from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .settings import settings, ensure_dirs
from .models import (  # noqa: F401 — re-exported for backward compat
    Base,
    Project,
    Module,
    TestSuite,
    Build,
    TestDefinition,
    BatchRun,
    Run,
    ScreenFolder,
    ScreenLibrary,
)


def _db_url() -> str:
    ensure_dirs()
    return f"sqlite+pysqlite:///{settings.db_path}"


engine = create_engine(_db_url(), connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)
    import sqlite3
    con = sqlite3.connect(str(settings.db_path))
    cur = con.cursor()
    cols = [row[1] for row in cur.execute("PRAGMA table_info(tests)").fetchall()]
    if "suite_id" not in cols:
        cur.execute("ALTER TABLE tests ADD COLUMN suite_id INTEGER REFERENCES test_suites(id) ON DELETE SET NULL")
        con.commit()
    if "prerequisite_test_id" not in cols:
        cur.execute("ALTER TABLE tests ADD COLUMN prerequisite_test_id INTEGER REFERENCES tests(id) ON DELETE SET NULL")
        con.commit()
    if "fix_history" not in cols:
        cur.execute("ALTER TABLE tests ADD COLUMN fix_history JSON DEFAULT '[]'")
        con.commit()
    if "acceptance_criteria" not in cols:
        cur.execute("ALTER TABLE tests ADD COLUMN acceptance_criteria TEXT")
        con.commit()
    if "platform_steps" not in cols:
        cur.execute("ALTER TABLE tests ADD COLUMN platform_steps JSON DEFAULT '{}'")
        con.commit()
        import json as _json

        rows = cur.execute("SELECT id, steps FROM tests WHERE steps IS NOT NULL").fetchall()
        for test_id, steps_raw in rows:
            try:
                if isinstance(steps_raw, str):
                    steps = _json.loads(steps_raw)
                else:
                    steps = steps_raw
                if not steps:
                    continue
                platform_steps = _json.dumps({"android": steps, "ios_sim": []})
                cur.execute("UPDATE tests SET platform_steps = ? WHERE id = ?", (platform_steps, test_id))
            except Exception:
                pass
        con.commit()
    # -- screen_library: folder_id --
    sl_cols = [row[1] for row in cur.execute("PRAGMA table_info(screen_library)").fetchall()]
    if sl_cols and "folder_id" not in sl_cols:
        cur.execute("ALTER TABLE screen_library ADD COLUMN folder_id INTEGER REFERENCES screen_folders(id) ON DELETE SET NULL")
        con.commit()

    sl_cols2 = [row[1] for row in cur.execute("PRAGMA table_info(screen_library)").fetchall()]
    if sl_cols2 and "screen_type" not in sl_cols2:
        cur.execute("ALTER TABLE screen_library ADD COLUMN screen_type VARCHAR(20)")
        con.commit()

    sl_cols3 = [row[1] for row in cur.execute("PRAGMA table_info(screen_library)").fetchall()]
    if sl_cols3 and "screen_type" in sl_cols3:
        try:
            cur.execute(
                "UPDATE screen_library SET screen_type = 'uikit' "
                "WHERE platform IN ('ios', 'ios_sim') AND (screen_type = 'native' OR screen_type IS NULL OR screen_type = '')"
            )
            con.commit()
        except Exception:
            pass

    # -- runs: failure_category --
    run_cols = [row[1] for row in cur.execute("PRAGMA table_info(runs)").fetchall()]
    if "failure_category" not in run_cols:
        cur.execute("ALTER TABLE runs ADD COLUMN failure_category VARCHAR(50) DEFAULT ''")
        con.commit()
        _backfill_failure_categories(cur)
        con.commit()

    # -- runs: batch_run_id --
    run_cols2 = [row[1] for row in cur.execute("PRAGMA table_info(runs)").fetchall()]
    if "batch_run_id" not in run_cols2:
        cur.execute("ALTER TABLE runs ADD COLUMN batch_run_id INTEGER REFERENCES batch_runs(id) ON DELETE SET NULL")
        con.commit()

    con.close()


def _classify_error(msg: str) -> str:
    """Pattern-match an error message to a failure category."""
    if not msg:
        return "other"
    low = msg.lower()
    if any(k in low for k in ("nosuchelement", "no such element", "element not found", "selector")):
        return "selector_not_found"
    if any(k in low for k in ("timeout", "timed out", "waited")):
        return "element_timeout"
    if any(k in low for k in ("assert", "expected", "mismatch")):
        return "assertion_failure"
    if any(k in low for k in ("connectionerror", "httperror", "5xx", "502", "503", "504", "network")):
        return "network_error"
    if any(k in low for k in ("fatal", "anr", "crash", "nullpointer", "segfault")):
        return "app_crash"
    return "other"


def _backfill_failure_categories(cur) -> None:
    """One-time: classify existing failed/error runs."""
    rows = cur.execute(
        "SELECT id, error_message FROM runs WHERE status IN ('failed','error') AND (failure_category IS NULL OR failure_category = '')"
    ).fetchall()
    for run_id, err in rows:
        cat = _classify_error(err or "")
        cur.execute("UPDATE runs SET failure_category = ? WHERE id = ?", (cat, run_id))
