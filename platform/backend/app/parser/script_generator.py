from __future__ import annotations

import io
import re
import uuid
import zipfile
from typing import Any


def safe_katalon_name(name: str) -> str:
    """'Login — valid credentials' → 'LoginValidCredentials'."""
    name = re.sub(r"[^a-zA-Z0-9\s]", " ", name)
    return "".join(word.capitalize() for word in name.split()) or "Test"


def _selector_to_object_path(step: dict[str, Any], screen_name: str) -> str:
    sel = step.get("selector", {}) or {}
    value = sel.get("value", "element") or "element"
    if ":" in str(value) and "/id/" in str(value):
        value = str(value).split("/id/")[-1]
    value = re.sub(r"[^a-zA-Z0-9_]", "_", str(value))
    sn = safe_katalon_name(screen_name)
    return f"Object Repository/Screen_{sn}/{value}"


def steps_to_groovy(
    test_name: str,
    steps: list[dict[str, Any]],
    screen_name: str = "General",
    source_hint: str = "",
) -> str:
    """Convert platform step dicts into Katalon Groovy test case source."""
    lines = [
        "import static com.kms.katalon.core.testobject.ObjectRepository.findTestObject",
        "import com.kms.katalon.core.mobile.keyword.MobileBuiltInKeywords as Mobile",
        "",
        f"// Test: {test_name}",
    ]
    if source_hint:
        lines.append(f"// Source: {source_hint}")
    lines.append("")

    for i, step in enumerate(steps):
        stype = step.get("type", "")
        sel = step.get("selector", {}) or {}
        text = step.get("text", "") or ""
        expect = step.get("expect", "") or ""
        ms = int(step.get("ms", 1000) or 1000)
        obj_path = _selector_to_object_path(step, screen_name) if sel.get("value") else None
        obj_ref = f"findTestObject('{obj_path}')" if obj_path else "null"
        text_esc = str(text).replace("\\", "\\\\").replace("'", "\\'")
        expect_esc = str(expect).replace("\\", "\\\\").replace("'", "\\'")

        if stype == "python_raw":
            lines.append(f"// Step {i + 1}: python_raw — paste Appium code manually")
            continue
        if stype == "gherkin_raw":
            lines.append(f"// Gherkin: {step.get('text', '')}")
            continue

        if stype == "tap":
            lines.append(f"Mobile.tap({obj_ref}, 10)")
        elif stype == "type":
            lines.append(f"Mobile.tap({obj_ref}, 10)")
            lines.append(f"Mobile.setText({obj_ref}, '{text_esc}', 10)")
        elif stype == "wait":
            sec = max(0.1, ms / 1000.0)
            lines.append(f"Mobile.delay({sec})")
        elif stype == "waitForVisible":
            sec = max(1, int(round(ms / 1000)) or 10)
            lines.append(f"Mobile.waitForElementPresent({obj_ref}, {sec})")
        elif stype == "assertText":
            lines.append(f"Mobile.verifyElementText({obj_ref}, '{expect_esc}', 10)")
        elif stype == "assertVisible":
            lines.append(f"Mobile.verifyElementExist({obj_ref}, 10)")
        elif stype == "keyboardAction":
            key = text or "return"
            lines.append(f"// keyboardAction: {key}")
            lines.append("Mobile.hideKeyboard()")
        elif stype == "hideKeyboard":
            lines.append("Mobile.hideKeyboard()")
        elif stype == "swipe":
            direction = (text or "up").lower()
            lines.append(f"// Swipe {direction} — implement with Mobile.swipe or TouchAction as needed")
        elif stype == "takeScreenshot":
            lines.append(f"// Screenshot step_{i + 1:03d}")
        else:
            lines.append(f"// Step {i + 1}: {stype} (manual review)")

    return "\n".join(lines) + "\n"


def object_repo_entry(step: dict[str, Any], screen_name: str) -> tuple[str, str] | None:
    sel = step.get("selector", {}) or {}
    if not sel.get("value"):
        return None

    obj_path = _selector_to_object_path(step, screen_name)
    obj_name = obj_path.split("/")[-1]
    using = sel.get("using", "accessibilityId")
    prop_map = {
        "accessibilityId": "accessibility id",
        "id": "resource-id",
        "xpath": "xpath",
        "className": "class",
        "name": "name",
    }
    prop = prop_map.get(using, "accessibility id")
    value = str(sel.get("value", "")).replace("&", "&amp;").replace("<", "&lt;")

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<WebElementEntity>
  <description></description>
  <name>{obj_name}</name>
  <tag></tag>
  <elementGuid>{uuid.uuid4()}</elementGuid>
  <selectorMethod>BASIC</selectorMethod>
  <webElementProperties>
    <isSelected>true</isSelected>
    <matchCondition>equals</matchCondition>
    <name>{prop}</name>
    <type>Main</type>
    <value>{value}</value>
  </webElementProperties>
</WebElementEntity>"""

    return (f"{obj_path}.rs", xml)


def generate_katalon_zip(project_name: str, test_cases: list[dict[str, Any]]) -> bytes:
    """Build a Katalon Studio–style project ZIP from test case dicts."""
    safe_proj = safe_katalon_name(project_name)
    buf = io.BytesIO()

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            f"{safe_proj}/.project",
            f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f"<projectDescription>\n  <name>{safe_proj}</name>\n  <comment></comment>\n"
            f"  <projects></projects>\n  <buildSpec></buildSpec>\n  <natures>\n"
            f"    <nature>com.kms.katalon.core.katalon</nature>\n  </natures>\n</projectDescription>",
        )
        zf.writestr(
            f"{safe_proj}/{safe_proj}.prj",
            f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f"<ProjectEntity>\n  <name>{safe_proj}</name>\n  <description></description>\n"
            f"  <type>MOBILE</type>\n  <defaultProfile>default</defaultProfile>\n</ProjectEntity>",
        )
        zf.writestr(f"{safe_proj}/settings/internal.properties", "com.kms.katalon.core.testcase.version=1\n")
        zf.writestr(
            f"{safe_proj}/Profiles/default.glbl",
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            "<GlobalVariableEntities>\n  <name>default</name>\n  <defaultProfile>true</defaultProfile>\n"
            "  <globalVariableEntities/>\n</GlobalVariableEntities>",
        )

        object_paths_seen: set[str] = set()

        for tc in test_cases:
            name = tc.get("name", "Unnamed Test")
            steps = [s for s in (tc.get("steps") or []) if s.get("type") != "python_raw"]
            suite_name = tc.get("suite_name") or tc.get("suggested_suite") or "Imported"
            source_hint = str(tc.get("source_file", "") or "")

            safe_suite = safe_katalon_name(suite_name)
            safe_tc = safe_katalon_name(name)
            screen_name = safe_suite
            tc_path = f"Test Cases/{safe_suite}/{safe_tc}"

            groovy = steps_to_groovy(name, steps, screen_name=screen_name, source_hint=source_hint)
            zf.writestr(f"{safe_proj}/{tc_path}.groovy", groovy)

            ac = (tc.get("acceptance_criteria") or "")[:200]
            zf.writestr(
                f"{safe_proj}/{tc_path}.tc",
                f'<?xml version="1.0" encoding="UTF-8"?>\n'
                f"<TestCaseEntity>\n  <description>{ac}</description>\n"
                f"  <name>{safe_tc}</name>\n  <tag></tag>\n  <comment>{name}</comment>\n"
                f"  <testCaseGuid>{uuid.uuid4()}</testCaseGuid>\n</TestCaseEntity>",
            )

            for step in steps:
                result = object_repo_entry(step, screen_name)
                if result:
                    obj_rel, xml_content = result
                    if obj_rel not in object_paths_seen:
                        object_paths_seen.add(obj_rel)
                        zf.writestr(f"{safe_proj}/{obj_rel}", xml_content)

        suites_seen: dict[str, list[str]] = {}
        for tc in test_cases:
            suite = safe_katalon_name(tc.get("suite_name") or tc.get("suggested_suite") or "Imported")
            tc_ref = f"Test Cases/{suite}/{safe_katalon_name(tc.get('name', 'Test'))}"
            suites_seen.setdefault(suite, []).append(tc_ref)

        for suite_name, tc_refs in suites_seen.items():
            tc_links = "".join(
                f"  <testSuiteTestCaseLink>\n    <testCaseId>{ref}</testCaseId>\n"
                f"    <runEnabled>true</runEnabled>\n    <usingDataBinding>false</usingDataBinding>\n"
                f"  </testSuiteTestCaseLink>\n"
                for ref in tc_refs
            )
            zf.writestr(f"{safe_proj}/Test Suites/{suite_name}.groovy", "")
            zf.writestr(
                f"{safe_proj}/Test Suites/{suite_name}.ts",
                f'<?xml version="1.0" encoding="UTF-8"?>\n'
                f"<TestSuiteEntity>\n  <name>{suite_name}</name>\n  <isRerun>false</isRerun>\n"
                f"  <rerunFailedTestCasesOnly>false</rerunFailedTestCasesOnly>\n"
                f"  <testSuiteGuid>{uuid.uuid4()}</testSuiteGuid>\n{tc_links}</TestSuiteEntity>",
            )

        zf.writestr(f"{safe_proj}/Keywords/.gitkeep", "")

    buf.seek(0)
    return buf.read()
