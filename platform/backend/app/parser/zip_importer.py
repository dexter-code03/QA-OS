from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .script_parser import group_steps_into_test_cases, parse_groovy, parse_gherkin

SUPPORTED_EXTENSIONS = {".groovy", ".feature", ".java", ".py"}


def _parse_rs_xml(xml_text: str) -> dict[str, str] | None:
    """Parse a Katalon .rs (Object Repository) XML into {"strategy": ..., "value": ...}."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    name_el = root.find("name")
    if name_el is None or not (name_el.text or "").strip():
        return None
    leaf_name = name_el.text.strip()

    PROP_TO_STRATEGY = {
        "accessibility id": "accessibilityId",
        "resource-id": "id",
        "xpath": "xpath",
        "class": "className",
        "name": "name",
        "id": "id",
    }
    for prop in root.iter("webElementProperties"):
        selected = prop.findtext("isSelected", "false").strip().lower() == "true"
        if not selected:
            continue
        prop_name = (prop.findtext("name") or "").strip().lower()
        prop_value = (prop.findtext("value") or "").strip()
        strategy = PROP_TO_STRATEGY.get(prop_name)
        if strategy and prop_value:
            return {"leaf": leaf_name, "strategy": strategy, "value": prop_value}
    return None


def parse_object_repo_from_zip(zip_bytes: bytes) -> dict[str, dict[str, str]]:
    """Extract {leafName → {strategy, value}} from .rs files in a ZIP."""
    repo: dict[str, dict[str, str]] = {}
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for entry in zf.infolist():
            if entry.is_dir():
                continue
            if not entry.filename.lower().endswith(".rs"):
                continue
            if "__MACOSX" in entry.filename or Path(entry.filename).name.startswith("._"):
                continue
            try:
                xml_text = zf.read(entry.filename).decode("utf-8", errors="replace")
                parsed = _parse_rs_xml(xml_text)
                if parsed:
                    repo[parsed["leaf"]] = {"strategy": parsed["strategy"], "value": parsed["value"]}
            except Exception:
                continue
    return repo


def parse_object_repo_from_files(files: list[tuple[str, bytes]]) -> dict[str, dict[str, str]]:
    """Extract {leafName → {strategy, value}} from .rs files in a file list."""
    repo: dict[str, dict[str, str]] = {}
    for rel_path, content in files:
        if not rel_path.lower().endswith(".rs"):
            continue
        try:
            xml_text = content.decode("utf-8", errors="replace")
            parsed = _parse_rs_xml(xml_text)
            if parsed:
                repo[parsed["leaf"]] = {"strategy": parsed["strategy"], "value": parsed["value"]}
        except Exception:
            continue
    return repo


@dataclass
class KatalonTcMeta:
    """Metadata from a .tc file."""
    tc_id: str  # e.g. "Test Cases/Login/ValidLogin"
    name: str
    description: str = ""
    tags: str = ""
    comment: str = ""


@dataclass
class KatalonSuite:
    """Parsed from a .ts file."""
    name: str
    tc_ids: list[str] = field(default_factory=list)  # references like "Test Cases/Login/ValidLogin"


@dataclass
class KatalonCollection:
    """Parsed from a .tsc file."""
    name: str
    suite_refs: list[str] = field(default_factory=list)  # references like "Test Suites/SmokeTests"


@dataclass
class KatalonProjectStructure:
    """Full structure extracted from a Katalon project ZIP."""
    suites: list[KatalonSuite] = field(default_factory=list)
    collections: list[KatalonCollection] = field(default_factory=list)
    tc_metadata: dict[str, KatalonTcMeta] = field(default_factory=dict)
    folder_hierarchy: dict[str, list[str]] = field(default_factory=dict)  # module path → [tc_ids]


def _parse_tc_xml(xml_text: str, entry_path: str) -> KatalonTcMeta | None:
    """Parse a Katalon .tc file for description, tags, comment."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    name = (root.findtext("name") or "").strip()
    if not name:
        name = Path(entry_path).stem
    tc_id = str(Path(entry_path).with_suffix(""))  # strip .tc → path like "ProjName/Test Cases/Login/ValidLogin"
    return KatalonTcMeta(
        tc_id=tc_id,
        name=name,
        description=(root.findtext("description") or "").strip(),
        tags=(root.findtext("tag") or "").strip(),
        comment=(root.findtext("comment") or "").strip(),
    )


def _parse_ts_xml(xml_text: str) -> KatalonSuite | None:
    """Parse a Katalon .ts (Test Suite) file for its name and test case references."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    name = (root.findtext("name") or "").strip()
    if not name:
        return None
    tc_ids: list[str] = []
    for link in root.iter("testSuiteTestCaseLink"):
        enabled = (link.findtext("runEnabled") or "true").strip().lower()
        if enabled != "true":
            continue
        tc_id = (link.findtext("testCaseId") or "").strip()
        if tc_id:
            tc_ids.append(tc_id)
    return KatalonSuite(name=name, tc_ids=tc_ids)


def _parse_tsc_xml(xml_text: str) -> KatalonCollection | None:
    """Parse a Katalon .tsc (Test Suite Collection) file."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    name = (root.findtext("name") or "").strip()
    if not name:
        return None
    suite_refs: list[str] = []
    for conf in root.iter("TestSuiteRunConfiguration"):
        enabled = (conf.findtext("runEnabled") or "true").strip().lower()
        if enabled != "true":
            continue
        ref = (conf.findtext("testSuiteEntity") or "").strip()
        if ref:
            suite_refs.append(ref)
    return KatalonCollection(name=name, suite_refs=suite_refs)


def _normalize_katalon_path(entry_path: str) -> str:
    """Strip leading project-name folder: 'MyProj/Test Cases/Login/TC.groovy' → 'Test Cases/Login/TC.groovy'."""
    parts = Path(entry_path).parts
    if len(parts) >= 2 and parts[1] in ("Test Cases", "Test Suites", "Object Repository", "Keywords", "Profiles"):
        return str(Path(*parts[1:]))
    return entry_path


def _tc_id_from_groovy_path(norm_path: str) -> str:
    """'Test Cases/Login/ValidLogin.groovy' → 'Test Cases/Login/ValidLogin'."""
    return str(Path(norm_path).with_suffix(""))


def _derive_module_path(tc_id: str) -> str:
    """
    Derive a module (collection) path from a test case ID.
    'Test Cases/ModA/SubMod/TC' → 'ModA/SubMod'
    'Test Cases/ModA/TC' → 'ModA'
    'Test Cases/TC' → 'Imported'
    """
    p = tc_id
    if p.startswith("Test Cases/"):
        p = p[len("Test Cases/"):]
    parts = Path(p).parts
    if len(parts) >= 2:
        return str(Path(*parts[:-1]))
    return "Imported"


def parse_katalon_project(zip_bytes: bytes) -> KatalonProjectStructure:
    """Walk a Katalon ZIP and extract suites, collections, and test case metadata."""
    structure = KatalonProjectStructure()

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for entry in zf.infolist():
            if entry.is_dir():
                continue
            if "__MACOSX" in entry.filename or Path(entry.filename).name.startswith("._"):
                continue

            norm = _normalize_katalon_path(entry.filename)
            ext = Path(entry.filename).suffix.lower()

            try:
                raw = zf.read(entry.filename).decode("utf-8", errors="replace")
            except Exception:
                continue

            if ext == ".tc" and norm.startswith("Test Cases"):
                meta = _parse_tc_xml(raw, norm)
                if meta:
                    tc_key = str(Path(norm).with_suffix(""))
                    meta.tc_id = tc_key
                    structure.tc_metadata[tc_key] = meta

            elif ext == ".ts" and norm.startswith("Test Suites"):
                suite = _parse_ts_xml(raw)
                if suite:
                    structure.suites.append(suite)

            elif ext == ".tsc" and norm.startswith("Test Suites"):
                coll = _parse_tsc_xml(raw)
                if coll:
                    structure.collections.append(coll)

            elif ext in SUPPORTED_EXTENSIONS and norm.startswith("Test Cases"):
                tc_id = _tc_id_from_groovy_path(norm)
                mod = _derive_module_path(tc_id)
                structure.folder_hierarchy.setdefault(mod, []).append(tc_id)

    return structure


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
