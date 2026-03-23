"""Artifact serving and Katalon single-run export."""
from __future__ import annotations

import re
import uuid
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from ..db import SessionLocal
from ..helpers import steps_for_platform_record
from ..models import Project, Run, TestDefinition
from ..settings import settings

router = APIRouter()


def _artifact_media_type(filename: str) -> str | None:
    lower = filename.lower()
    if lower.endswith(".mp4"):
        return "video/mp4"
    if lower.endswith(".mov"):
        return "video/quicktime"
    if lower.endswith(".webm"):
        return "video/webm"
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".jpg") or lower.endswith(".jpeg"):
        return "image/jpeg"
    if lower.endswith(".gif"):
        return "image/gif"
    if lower.endswith(".xml"):
        return "application/xml"
    return None


@router.get("/api/artifacts/{project_id}/{run_id}/{name}")
def get_artifact(project_id: int, run_id: int, name: str) -> FileResponse:
    path = settings.artifacts_dir / str(project_id) / str(run_id) / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Artifact not found")
    media = _artifact_media_type(name)
    headers = {"Cache-Control": "no-store, no-cache, must-revalidate"}
    fr_kw: dict[str, Any] = {"filename": name, "headers": headers}
    if media:
        fr_kw["media_type"] = media
    return FileResponse(str(path), **fr_kw)


def _safe_name(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)


def guessModule(name: str) -> str:
    parts = re.split(r"[_\s\-]+", name)
    return parts[0].capitalize() if parts else "General"


@router.get("/api/runs/{run_id}/katalon")
def export_katalon(run_id: int):
    with SessionLocal() as db:
        r = db.query(Run).filter(Run.id == run_id).first()
        if not r:
            raise HTTPException(status_code=404, detail="Run not found")
        t = db.query(TestDefinition).filter(TestDefinition.id == r.test_id).first() if r.test_id else None
        if not t:
            raise HTTPException(status_code=404, detail="Test not found")
        p = db.query(Project).filter(Project.id == r.project_id).first()
        proj_name = _safe_name(p.name if p else "QA_Project")
        tc_name = _safe_name(t.name)
        module = guessModule(t.name) if hasattr(t, "name") else "General"
        module_safe = _safe_name(module)
        tc_path = f"Test Cases/Mobile/{module_safe}/{tc_name}"

        groovy_lines = [
            "import static com.kms.katalon.core.testobject.ObjectRepository.findTestObject",
            "import com.kms.katalon.core.mobile.keyword.MobileBuiltInKeywords as Mobile",
            "",
            f"// Test: {t.name}",
            f"// Generated from QA·OS Run #{r.id}",
            "",
        ]
        obj_files: dict[str, str] = {}
        run_plat = (r.platform or "android") if r else "android"
        if run_plat not in ("android", "ios_sim"):
            run_plat = "android"
        export_steps = steps_for_platform_record(t, run_plat)

        for i, s in enumerate(export_steps):
            stype = s.get("type", "")
            sel = s.get("selector", {})
            using = sel.get("using", "accessibilityId")
            value = sel.get("value", "")
            text = s.get("text", "")
            ms = s.get("ms", 1000)
            obj_name = f"step_{i:03d}"
            obj_ref = f"'Object Repository/Screen_{module_safe}/{obj_name}'"

            if value:
                prop_map = {"accessibilityId": "accessibility id", "id": "resource-id", "xpath": "xpath", "className": "class"}
                prop = prop_map.get(using, "accessibility id")
                obj_files[f"Object Repository/Screen_{module_safe}/{obj_name}.rs"] = (
                    f'<?xml version="1.0" encoding="UTF-8"?>\n'
                    f"<WebElementEntity>\n"
                    f"  <description></description>\n"
                    f"  <name>{obj_name}</name>\n"
                    f"  <tag></tag>\n"
                    f"  <elementGuid>{uuid.uuid4()}</elementGuid>\n"
                    f"  <selectorMethod>BASIC</selectorMethod>\n"
                    f"  <useRalativeImagePath>false</useRalativeImagePath>\n"
                    f"  <webElementProperties>\n"
                    f"    <isSelected>true</isSelected>\n"
                    f"    <matchCondition>equals</matchCondition>\n"
                    f"    <name>{prop}</name>\n"
                    f"    <type>Main</type>\n"
                    f"    <value>{value}</value>\n"
                    f"  </webElementProperties>\n"
                    f"</WebElementEntity>"
                )

            if stype == "tap":
                groovy_lines.append(f"Mobile.tap(findTestObject({obj_ref}), 10)")
            elif stype == "type":
                groovy_lines.append(f"Mobile.tap(findTestObject({obj_ref}), 10)")
                groovy_lines.append(f"Mobile.setText(findTestObject({obj_ref}), '{text}', 10)")
            elif stype == "wait":
                groovy_lines.append(f"Mobile.delay({ms / 1000})")
            elif stype in ("waitForVisible", "assertVisible"):
                groovy_lines.append(f"Mobile.waitForElementPresent(findTestObject({obj_ref}), 10)")
            elif stype == "assertText":
                groovy_lines.append(f"Mobile.verifyElementText(findTestObject({obj_ref}), '{s.get('expect', '')}')")
            elif stype == "takeScreenshot":
                groovy_lines.append(f"Mobile.takeScreenshot('screenshots/step_{i:03d}.png')")
            elif stype == "swipe":
                groovy_lines.append(f"Mobile.swipe(100, 800, 100, 200)")
            else:
                groovy_lines.append(f"// Step {i}: {stype}")

        buf = BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"{proj_name}/.project",
                f'<?xml version="1.0" encoding="UTF-8"?>\n'
                f"<projectDescription>\n  <name>{proj_name}</name>\n  <comment></comment>\n"
                f"  <projects></projects>\n  <buildSpec></buildSpec>\n  <natures>\n"
                f"    <nature>com.kms.katalon.core.katalon</nature>\n  </natures>\n</projectDescription>")

            zf.writestr(f"{proj_name}/{proj_name}.prj",
                f'<?xml version="1.0" encoding="UTF-8"?>\n'
                f"<ProjectEntity>\n  <name>{proj_name}</name>\n  <description></description>\n"
                f"  <type>MOBILE</type>\n  <defaultProfile>default</defaultProfile>\n</ProjectEntity>")

            zf.writestr(f"{proj_name}/settings/internal.properties",
                "com.kms.katalon.core.testcase.version=1\n")

            zf.writestr(f"{proj_name}/Profiles/default.glbl",
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                "<GlobalVariableEntities>\n  <description></description>\n  <name>default</name>\n"
                "  <defaultProfile>true</defaultProfile>\n  <globalVariableEntities/>\n</GlobalVariableEntities>")

            zf.writestr(f"{proj_name}/{tc_path}.groovy", "\n".join(groovy_lines) + "\n")

            zf.writestr(f"{proj_name}/{tc_path}.tc",
                f'<?xml version="1.0" encoding="UTF-8"?>\n'
                f"<TestCaseEntity>\n  <description>Generated from QA·OS</description>\n"
                f"  <name>{tc_name}</name>\n  <tag></tag>\n  <comment>{t.name}</comment>\n"
                f"  <testCaseGuid>{uuid.uuid4()}</testCaseGuid>\n</TestCaseEntity>")

            for path, content in obj_files.items():
                zf.writestr(f"{proj_name}/{path}", content)

            zf.writestr(f"{proj_name}/Test Suites/smoke.groovy", "")
            zf.writestr(f"{proj_name}/Test Suites/smoke.ts",
                f'<?xml version="1.0" encoding="UTF-8"?>\n'
                f"<TestSuiteEntity>\n  <description></description>\n  <name>smoke</name>\n"
                f"  <tag></tag>\n  <isRerun>false</isRerun>\n  <mailRecipient></mailRecipient>\n"
                f"  <numberOfRerun>0</numberOfRerun>\n  <pageLoadTimeout>30</pageLoadTimeout>\n"
                f"  <rerunFailedTestCasesOnly>false</rerunFailedTestCasesOnly>\n"
                f"  <rerunImmediately>false</rerunImmediately>\n"
                f"  <testSuiteGuid>{uuid.uuid4()}</testSuiteGuid>\n"
                f"  <testSuiteTestCaseLink>\n    <testCaseId>{tc_path}</testCaseId>\n"
                f"    <runEnabled>true</runEnabled>\n    <usingDataBinding>false</usingDataBinding>\n"
                f"  </testSuiteTestCaseLink>\n</TestSuiteEntity>")

            zf.writestr(f"{proj_name}/Keywords/.gitkeep", "")

        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename={proj_name}_katalon.zip"},
        )
