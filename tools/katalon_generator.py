"""
QA AI Automation — Stage 4: Katalon Script Generator
Generates complete Katalon Groovy test scripts, CSV test data,
and custom keywords from acceptance criteria and Figma tokens.
"""

import json
import os
import re
from typing import Optional
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config
from tools.ai_client import ai_chat_json


# ═════════════════════════════════════════════════
# Claude System Prompts
# ═════════════════════════════════════════════════

KATALON_SCRIPT_PROMPT = """You are a Katalon Studio MOBILE automation engineer.
You receive two inputs simultaneously:
1. Acceptance criteria from an FRD (the WHAT to test)
2. Figma design tokens and element names (the WHERE to find it in the UI)

Generate complete, ready-to-run Katalon Groovy test scripts for MOBILE apps.

Rules:
- Use Mobile.* keywords — NOT WebUI.*
- Mobile.tap() for button taps
- Mobile.setText() for text input
- Mobile.swipe() for scroll/swipe gestures
- Mobile.verifyElementExist() before any interaction
- Mobile.waitForElementPresent(..., 10) for waits
- Start each script with Mobile.startApplication() or assume app is open
- Include device/platform context: Android vs iOS
- Use TestObject references with mobile-compatible selectors (xpath or accessibility id)
- Every Mobile step MUST have a findTestObject() reference
- Generate matching TestData CSV rows for data-driven tests
- Flag any selector you are NOT confident about with: // VERIFY SELECTOR
- Include proper imports at the top of each script
- Use GlobalVariable references for app paths and credentials
- Add descriptive comments for each logical section

Output format (return as JSON):
{
  "groovy_script": "full Groovy mobile test script as a single string",
  "test_data_csv": "CSV content with header row and test data rows",
  "test_objects": [
    {
      "name": "Screen_Login/btn_login",
      "selector_type": "accessibility_id",
      "selector_value": "login-button",
      "confidence": "high | medium | low"
    }
  ],
  "verify_selectors": ["list of selectors marked for manual verification"]
}"""


CUSTOM_KEYWORD_PROMPT = """You are a Katalon Studio MOBILE automation engineer.
Generate reusable Custom Keywords (Groovy classes) for common MOBILE test operations.

Rules:
- Package: keywords
- Use Mobile.* keywords — NOT WebUI.*
- Mobile.tap(), Mobile.setText(), Mobile.swipe(), Mobile.verifyElementExist()
- Each keyword class should be focused on one functional area
- Include proper error handling and logging
- Use GlobalVariable for configuration (app paths, device profiles)
- Methods should be static for easy calling via CustomKeywords.'keywords.ClassName.methodName'()
- Include JSDoc-style comments
- Handle both Android and iOS where applicable

Return the complete Groovy class file content as a JSON:
{
  "class_name": "LoginHelper",
  "package": "keywords",
  "groovy_content": "full Groovy class content as a string",
  "methods": ["loginAs", "verifyLoggedIn", "logout"],
  "description": "Reusable mobile login/logout operations for all test suites"
}"""


# ═════════════════════════════════════════════════
# Format Learning
# ═════════════════════════════════════════════════

def load_format_examples(filepath: str) -> str:
    """Load existing test cases to teach Claude your team's format.

    Reads the first 5 rows from an Excel (.xlsx) or CSV file and
    returns a formatted string that can be injected into the system
    prompt so Claude matches your exact column structure.

    Args:
        filepath: Path to an existing test case sheet (.xlsx or .csv).

    Returns:
        Formatted string with column names and example rows.
    """
    try:
        import pandas as pd
    except ImportError:
        print("   ⚠️  pandas not installed — skipping format learning")
        print("   Install with: pip install pandas openpyxl")
        return ""

    try:
        if filepath.endswith(".xlsx") or filepath.endswith(".xls"):
            df = pd.read_excel(filepath)
        else:
            df = pd.read_csv(filepath)

        examples = df.head(5).to_dict(orient="records")
        return (
            "\n\nFORMAT REQUIREMENT — MATCH THIS EXACT FORMAT:\n"
            f"Columns: {list(df.columns)}\n"
            f"Example rows:\n{json.dumps(examples, indent=2, default=str)}"
        )
    except Exception as e:
        print(f"   ⚠️  Could not load format examples: {e}")
        return ""


# ═════════════════════════════════════════════════
# Core Functions
# ═════════════════════════════════════════════════

def generate_katalon_tests(
    ac_list: list[dict],
    figma_tokens: Optional[dict] = None,
    format_examples: str = "",
) -> list[dict]:
    """Generate Katalon Groovy test scripts from acceptance criteria.

    Processes each AC individually for maximum test case depth.

    Args:
        ac_list: List of acceptance criteria dicts.
        figma_tokens: Optional Figma component tree for selector generation.

    Returns:
        List of dicts, each containing groovy_script, test_data_csv,
        test_objects, and verify_selectors.
    """
    client_results = []

    print(f"🤖 Generating Katalon scripts for {len(ac_list)} acceptance criteria...")

    for i, ac in enumerate(ac_list):
        print(f"   [{i+1}/{len(ac_list)}] Processing AC: {ac.get('id', f'AC-{i+1}')}")

        user_content = f"""Acceptance Criteria:
ID: {ac.get('id', f'AC-{i+1}')}
Given: {ac.get('given', 'N/A')}
When: {ac.get('when', 'N/A')}
Then: {ac.get('then', 'N/A')}
Priority: {ac.get('risk_level', 'Medium')}
Edge Cases: {json.dumps(ac.get('edge_cases', []))}
"""

        if figma_tokens:
            components = figma_tokens.get("children", figma_tokens.get("components", []))
            user_content += f"\n\nFigma elements on this screen:\n{json.dumps(components, indent=2)[:4000]}"

        user_content += "\n\nGenerate the full Katalon Groovy test script + CSV test data."

        try:
            system_prompt = KATALON_SCRIPT_PROMPT
            if format_examples:
                system_prompt += format_examples

            result = ai_chat_json(system_prompt, user_content)
            result["ac_id"] = ac.get("id", f"AC-{i+1}")
            client_results.append(result)

            verify_count = len(result.get("verify_selectors", []))
            if verify_count:
                print(f"      ⚠️  {verify_count} selectors need manual verification")

        except Exception as e:
            print(f"      ❌ Error: {e}")
            client_results.append({
                "ac_id": ac.get("id", f"AC-{i+1}"),
                "error": str(e),
                "groovy_script": "",
                "test_data_csv": "",
                "test_objects": [],
                "verify_selectors": [],
            })

    successful = sum(1 for r in client_results if "error" not in r)
    print(f"   → {successful}/{len(ac_list)} scripts generated successfully")

    return client_results


def generate_custom_keywords(
    feature_name: str,
    common_flows: list[str],
) -> dict:
    """Generate reusable Katalon Custom Keywords for common operations.

    Args:
        feature_name: Name of the feature area (e.g., "Login", "Checkout").
        common_flows: List of common operations (e.g., ["login", "logout", "verify session"]).

    Returns:
        dict with class_name, groovy_content, methods, description.
    """
    print(f"🤖 Generating Custom Keywords for '{feature_name}'...")

    result = ai_chat_json(
        CUSTOM_KEYWORD_PROMPT,
        f"Feature: {feature_name}\nCommon flows to create keywords for:\n"
        + "\n".join(f"- {flow}" for flow in common_flows),
    )
    print(f"   → Keyword class: {result.get('class_name', 'Unknown')}")
    print(f"   → Methods: {', '.join(result.get('methods', []))}")

    return result


# ═════════════════════════════════════════════════
# File Output
# ═════════════════════════════════════════════════

def save_to_katalon_project(
    scripts: list[dict],
    keywords: Optional[dict] = None,
    katalon_project_path: Optional[str] = None,
    suite_name: str = "smoke",
) -> dict:
    """Write generated scripts directly into the Katalon Studio
    project folder. Studio auto-detects them on next refresh.
    No copy-pasting required.

    Writes:
      Test Cases/Mobile/{feature}/TC_XXX.groovy + .tc
      Object Repository/Screen_{feature}/element.rs (one per element)
      Test Data/TD_XXX.csv
      Keywords/ClassName.groovy
      Test Suites/{suite_name}.groovy + .ts

    Args:
        scripts: List of script dicts from generate_katalon_tests().
        keywords: Optional custom keyword dict from generate_custom_keywords().
        katalon_project_path: Path to Katalon project root folder.
        suite_name: Name for the test suite that groups all generated cases.

    Returns:
        dict with lists of created file paths.
    """
    project_path = Path(
        katalon_project_path or config.KATALON_PROJECT_PATH
    )

    if not project_path.exists():
        raise ValueError(
            f"Katalon project path not found: {project_path}\n"
            f"Check KATALON_PROJECT_PATH in your .env"
        )

    saved = {
        "test_cases": [],
        "test_objects": [],
        "test_data": [],
        "keywords": [],
        "suite": None,
    }
    tc_ids_for_suite = []

    for script in scripts:
        if script.get("error"):
            continue

        ac_id = script.get("ac_id", "unknown")
        safe_id = re.sub(r"[^a-zA-Z0-9_]", "_", ac_id)
        tc_name = f"TC_{safe_id}"
        feature = script.get("feature_name", "Mobile")
        safe_feat = re.sub(r"[^a-zA-Z0-9_]", "_", feature)

        # ── 1. Test case .groovy script ───────────────────
        tc_dir = project_path / "Test Cases" / "Mobile" / safe_feat
        tc_dir.mkdir(parents=True, exist_ok=True)

        groovy_content = script.get("groovy_script", "")
        if groovy_content:
            groovy_path = tc_dir / f"{tc_name}.groovy"
            groovy_path.write_text(groovy_content, encoding="utf-8")
            saved["test_cases"].append(str(groovy_path))
            tc_ids_for_suite.append(
                f"Test Cases/Mobile/{safe_feat}/{tc_name}"
            )

        # ── 2. Test case .tc metadata ─────────────────────
        tc_meta_path = tc_dir / f"{tc_name}.tc"
        tc_meta_path.write_text(
            _build_tc_metadata(tc_name, ac_id),
            encoding="utf-8",
        )

        # ── 3. Object Repository .rs files ────────────────
        obj_dir = project_path / "Object Repository" / f"Screen_{safe_feat}"
        obj_dir.mkdir(parents=True, exist_ok=True)

        for obj in script.get("test_objects", []):
            obj_name = _safe_name(obj.get("name", "element").split("/")[-1])
            rs_path = obj_dir / f"{obj_name}.rs"
            rs_path.write_text(
                _build_rs_file(
                    obj_name,
                    obj.get("selector_type", "accessibility_id"),
                    obj.get("selector_value", ""),
                    obj.get("confidence", "low"),
                ),
                encoding="utf-8",
            )
            saved["test_objects"].append(str(rs_path))

        # ── 4. Test data CSV ──────────────────────────────
        csv_content = script.get("test_data_csv", "")
        if csv_content:
            data_dir = project_path / "Test Data"
            data_dir.mkdir(parents=True, exist_ok=True)
            csv_path = data_dir / f"TD_{safe_id}.csv"
            csv_path.write_text(csv_content, encoding="utf-8")
            saved["test_data"].append(str(csv_path))

    # ── 5. Custom keywords ────────────────────────────────
    if keywords and keywords.get("groovy_content"):
        kw_dir = project_path / "Keywords"
        kw_dir.mkdir(parents=True, exist_ok=True)
        kw_path = kw_dir / f"{keywords['class_name']}.groovy"
        kw_path.write_text(keywords["groovy_content"], encoding="utf-8")
        saved["keywords"].append(str(kw_path))
        print(f"   💾 Keyword written: {kw_path.name}")

    # ── 6. Test suite ─────────────────────────────────────
    if tc_ids_for_suite:
        suite_dir = project_path / "Test Suites"
        suite_dir.mkdir(parents=True, exist_ok=True)

        # Suite .groovy
        suite_groovy = suite_dir / f"{suite_name}.groovy"
        suite_groovy.write_text(
            _build_suite_groovy(suite_name, tc_ids_for_suite),
            encoding="utf-8",
        )
        # Suite .ts metadata
        suite_ts = suite_dir / f"{suite_name}.ts"
        suite_ts.write_text(
            _build_suite_metadata(suite_name, tc_ids_for_suite),
            encoding="utf-8",
        )
        saved["suite"] = str(suite_groovy)

    # Summary
    print(f"\n   ✅ Written directly to Katalon project:")
    print(f"      Test cases  : {len(saved['test_cases'])}")
    print(f"      Test objects: {len(saved['test_objects'])}")
    print(f"      Test data   : {len(saved['test_data'])}")
    print(f"      Keywords    : {len(saved['keywords'])}")
    print(f"      Suite       : {suite_name}")
    print(f"\n   → Open Katalon Studio → right-click project → Refresh")
    print(f"      All files will appear immediately.")

    return saved


# ─────────────────────────────────────────────
# File builders — the exact XML formats
# Katalon Studio expects
# ─────────────────────────────────────────────

def _build_tc_metadata(tc_name: str, ac_id: str) -> str:
    """Build the .tc XML file that Katalon needs alongside every .groovy.
    Without this file, Studio doesn't recognise the script as a test case.
    """
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<TestCaseEntity>
   <description>Generated from {ac_id} by QA AI Automation</description>
   <name>{tc_name}</name>
   <tag></tag>
   <comment></comment>
   <testCaseGuid>{_generate_guid()}</testCaseGuid>
</TestCaseEntity>"""


def _build_rs_file(
    name: str,
    selector_type: str,
    selector_value: str,
    confidence: str,
) -> str:
    """Build the .rs XML file for a mobile element in Object Repository.
    selector_type: accessibility_id | xpath | id | class_name
    """
    # Map selector type to Katalon's internal property name
    property_map = {
        "accessibility_id": "accessibility id",
        "xpath": "xpath",
        "id": "resource-id",
        "class_name": "class",
    }
    prop_name = property_map.get(selector_type, "accessibility id")

    # Add a comment if selector needs verification
    comment = "VERIFY THIS SELECTOR IN DOM" if confidence == "low" else ""

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<WebElementEntity>
   <description>{comment}</description>
   <name>{name}</name>
   <tag></tag>
   <elementGuid>{_generate_guid()}</elementGuid>
   <selectorMethod>BASIC</selectorMethod>
   <useRalativeImagePath>false</useRalativeImagePath>
   <webElementProperties>
      <isSelected>true</isSelected>
      <matchCondition>equals</matchCondition>
      <name>{prop_name}</name>
      <type>Main</type>
      <value>{selector_value}</value>
   </webElementProperties>
</WebElementEntity>"""


def _build_suite_groovy(suite_name: str, tc_paths: list[str]) -> str:
    """Build the test suite .groovy file listing all test cases."""
    lines = [
        "import com.kms.katalon.core.testdata.TestDataFactory as TestDataFactory",
        "import com.kms.katalon.core.testcase.TestCaseFactory as TestCaseFactory",
        "",
    ]
    for tc_path in tc_paths:
        lines.append(f"// {tc_path}")
    return "\n".join(lines)


def _build_suite_metadata(suite_name: str, tc_paths: list[str]) -> str:
    """Build the .ts XML metadata file for a test suite."""
    tc_entries = ""
    for tc_path in tc_paths:
        tc_entries += f"""   <testSuiteTestCaseLink>
      <testCaseId>{tc_path}</testCaseId>
      <runEnabled>true</runEnabled>
      <usingDataBinding>false</usingDataBinding>
   </testSuiteTestCaseLink>
"""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<TestSuiteEntity>
   <description>Auto-generated by QA AI Automation</description>
   <name>{suite_name}</name>
   <tag></tag>
   <isRerun>false</isRerun>
   <mailRecipient></mailRecipient>
   <numberOfRerun>0</numberOfRerun>
   <pageLoadTimeout>30</pageLoadTimeout>
   <rerunFailedTestCasesOnly>false</rerunFailedTestCasesOnly>
   <rerunImmediately>false</rerunImmediately>
   <testSuiteGuid>{_generate_guid()}</testSuiteGuid>
{tc_entries}</TestSuiteEntity>"""


def _safe_name(name: str) -> str:
    """Sanitize a string for use as a filename."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)


def _generate_guid() -> str:
    """Generate a UUID4 for Katalon metadata files."""
    import uuid
    return str(uuid.uuid4())


# ═════════════════════════════════════════════════
# Full Pipeline
# ═════════════════════════════════════════════════

def run_katalon_generation(
    ac_list: list[dict],
    figma_tokens: Optional[dict] = None,
    feature_name: str = "Feature",
    common_flows: Optional[list[str]] = None,
    suite_name: str = "smoke",
    katalon_project_path: Optional[str] = None,
    format_file: Optional[str] = None,
) -> dict:
    """Run the complete Katalon script generation pipeline.

    1. Generate Groovy test scripts for each AC
    2. Generate custom keywords for common flows
    3. Write directly into Katalon project folder (not a temp directory)

    Args:
        ac_list: Acceptance criteria list.
        figma_tokens: Optional Figma design tokens for selector generation.
        feature_name: Feature name for keyword generation.
        common_flows: Common flows for keyword generation.
        suite_name: Test suite name to group all generated cases.
        katalon_project_path: Path to Katalon project root folder.
        format_file: Existing test case file (.xlsx/.csv) to match format.

    Returns:
        dict with scripts, keywords, saved_files.
    """
    print("\n" + "=" * 60)
    print("🚀 Stage 4: Katalon Mobile Script Generation Pipeline")
    print("=" * 60)

    # Load format examples if provided
    fmt = ""
    if format_file:
        print(f"📄 Loading format from: {format_file}")
        fmt = load_format_examples(format_file)

    # Generate test scripts
    scripts = generate_katalon_tests(ac_list, figma_tokens, format_examples=fmt)

    # Attach feature name to each script so folder is named correctly
    for s in scripts:
        s["feature_name"] = feature_name

    # Generate custom keywords
    keywords = None
    if common_flows:
        keywords = generate_custom_keywords(feature_name, common_flows)

    # Write directly into Katalon project — not to a temp folder
    saved = save_to_katalon_project(
        scripts,
        keywords=keywords,
        katalon_project_path=katalon_project_path,
        suite_name=suite_name,
    )

    # Summary
    print(f"\n✅ Katalon generation complete!")
    print(f"   Test cases : {len(saved['test_cases'])}")
    print(f"   Objects    : {len(saved['test_objects'])}")
    print(f"   Test data  : {len(saved['test_data'])}")

    return {
        "scripts": scripts,
        "keywords": keywords,
        "saved_files": saved,
    }


# ═════════════════════════════════════════════════
# CLI Entry Point
# ═════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Katalon Mobile Script Generator")
    parser.add_argument("--ac-file", required=True, help="JSON file with acceptance criteria")
    parser.add_argument("--figma-tokens", default=None, help="JSON file with Figma tokens")
    parser.add_argument("--feature", default="Feature", help="Feature name")
    parser.add_argument("--katalon-project", default=None, help="Katalon project path")
    parser.add_argument("--suite", default="smoke", help="Test suite name")
    parser.add_argument("--common-flows", nargs="*", default=None, help="Common flows for keywords")
    parser.add_argument("--format-file", default=None, help="Existing test case file (.xlsx/.csv) to match format")
    args = parser.parse_args()

    with open(args.ac_file) as f:
        acs = json.load(f)

    tokens = None
    if args.figma_tokens:
        with open(args.figma_tokens) as f:
            tokens = json.load(f)

    result = run_katalon_generation(
        ac_list=acs,
        figma_tokens=tokens,
        feature_name=args.feature,
        common_flows=args.common_flows,
        katalon_project_path=args.katalon_project,
        suite_name=args.suite,
        format_file=args.format_file,
    )
