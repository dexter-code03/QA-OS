from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .script_parser import group_steps_into_test_cases, parse_groovy, parse_gherkin

SUPPORTED_EXTENSIONS = {".groovy", ".feature", ".java", ".py"}


@dataclass
class ParsedFile:
    path: str
    extension: str
    raw_text: str = ""
    test_cases: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


def extract_folder_name(path: str) -> str:
    """'Test Cases/Login/ValidLogin.groovy' → 'Login'."""
    parts = Path(path).parts
    if len(parts) >= 2:
        return parts[-2]
    return "Imported"


def _file_to_test_cases(path: str, ext: str, raw: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Return (test_case dicts, extra warnings)."""
    warnings: list[str] = []
    stem = Path(path).stem

    if ext in (".groovy", ".java"):
        steps = parse_groovy(raw)
        if not steps:
            warnings.append("No steps extracted — empty or unsupported lines")
            return [], warnings
        return group_steps_into_test_cases(steps, stem + ext), warnings

    if ext == ".feature":
        gh = parse_gherkin(raw)
        if not gh:
            warnings.append("No Gherkin lines extracted")
        return [
            {
                "name": stem,
                "steps": gh,
                "acceptance_criteria": "",
                "import": True,
            }
        ], warnings

    if ext == ".py":
        return [
            {
                "name": stem,
                "steps": [{"type": "python_raw", "source": raw, "name": stem}],
                "acceptance_criteria": "",
                "import": False,
            }
        ], [f"{path}: Python script needs AI completion — import disabled by default"]

    return [], warnings


def parse_zip(zip_bytes: bytes) -> list[ParsedFile]:
    """Walk a ZIP and parse supported script files."""
    results: list[ParsedFile] = []

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for entry in zf.infolist():
            if entry.is_dir():
                continue
            ext = Path(entry.filename).suffix.lower()
            if ext not in SUPPORTED_EXTENSIONS:
                continue
            if Path(entry.filename).name.startswith("._") or "__MACOSX" in entry.filename:
                continue

            pf = ParsedFile(path=entry.filename, extension=ext)
            try:
                raw = zf.read(entry.filename).decode("utf-8", errors="replace")
                pf.raw_text = raw
                cases, w = _file_to_test_cases(entry.filename, ext, raw)
                pf.test_cases = cases
                pf.warnings.extend(w)
                if not pf.test_cases and not pf.warnings:
                    pf.warnings.append("No test cases extracted — may need AI completion")
            except Exception as e:
                pf.error = str(e)

            results.append(pf)

    return results


def parse_folder_files(files: list[tuple[str, bytes]]) -> list[ParsedFile]:
    """Parse (relative_path, content) tuples (e.g. webkitdirectory upload)."""
    results: list[ParsedFile] = []
    for rel_path, content in files:
        ext = Path(rel_path).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            continue
        pf = ParsedFile(path=rel_path, extension=ext)
        try:
            raw = content.decode("utf-8", errors="replace")
            pf.raw_text = raw
            cases, w = _file_to_test_cases(rel_path, ext, raw)
            pf.test_cases = cases
            pf.warnings.extend(w)
            if not pf.test_cases and not pf.warnings:
                pf.warnings.append("No test cases extracted")
        except Exception as e:
            pf.error = str(e)
        results.append(pf)
    return results
