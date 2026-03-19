"""
QA AI Automation — Stage 6: AI Bug Triage & Jira Auto-Creation
Classifies test failures, deduplicates, auto-creates Jira tickets,
and escalates blockers via Slack.
"""

import json
import os
from typing import Optional
from pathlib import Path
from datetime import datetime

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config
from tools.jira_client import JiraClient
from tools.slack_notifier import SlackNotifier
from tools.ai_client import ai_chat_json


# ═════════════════════════════════════════════════
# Claude Classification Prompt
# ═════════════════════════════════════════════════

CLASSIFY_PROMPT = """You are a senior QA lead triaging automated test failures.

Analyse each failure and classify it as EXACTLY ONE of:
- REAL_BUG: reproducible defect in the application code — consistent failure, assertion errors
- FLAKY: intermittent failure — timing issues, race conditions, selector instability, passes sometimes
- ENV_ISSUE: infrastructure problem — network timeout, DB connection failure, missing test data, not an app bug
- BLOCKER: critical path failure (authentication, payment, data loss, security) that MUST block the release

For every failure return a JSON object:
{
  "classification": "REAL_BUG | FLAKY | ENV_ISSUE | BLOCKER",
  "confidence": "high | medium | low",
  "severity": "Critical | Major | Minor | Trivial",
  "priority": "Highest | High | Medium | Low",
  "summary": "one crisp sentence — bug title for Jira, max 80 chars, feature-prefixed",
  "root_cause": "one sentence — what likely caused this",
  "repro_steps": ["step 1", "step 2", "..."],
  "expected": "what should have happened",
  "actual": "what actually happened",
  "component": "which part of the app is affected",
  "suggested_assignee_team": "which dev team should own this",
  "needs_jira_ticket": true
}

Rules:
- FLAKY and ENV_ISSUE → needs_jira_ticket: false (log only)
- REAL_BUG and BLOCKER → needs_jira_ticket: true
- If failure mentions timeout/connection/network → lean ENV_ISSUE
- If assertion fails on a core user journey (login, checkout, payment) → lean BLOCKER
- If error is in element not found / stale element → lean FLAKY
- Write concise bug summaries prefixed with [COMPONENT]: e.g. "[AUTH] Login fails for + emails"

Return a JSON array of classification objects, one per failure.
Return ONLY valid JSON."""


# ═════════════════════════════════════════════════
# Core Functions
# ═════════════════════════════════════════════════

def classify_failures(test_results: dict) -> list[dict]:
    """Use Claude to classify each test failure.

    Args:
        test_results: dict with 'testCases' array from Katalon results.

    Returns:
        List of classification dicts, one per failure.
    """
    # Extract only failed tests
    failures = [
        tc for tc in test_results.get("testCases", [])
        if tc.get("status", "").upper() in ("FAILED", "ERROR")
    ]

    if not failures:
        print("   ✅ No failures to classify")
        return []

    print(f"🤖 Classifying {len(failures)} failures...")

    classifications = ai_chat_json(
        CLASSIFY_PROMPT,
        f"Triage these test failures:\n\n{json.dumps(failures, indent=2)}",
    )

    # Summary
    by_class = {}
    for c in classifications:
        cls = c.get("classification", "UNKNOWN")
        by_class[cls] = by_class.get(cls, 0) + 1
    for cls, count in by_class.items():
        print(f"   {cls}: {count}")

    return classifications


def create_bug_ticket(
    failure: dict,
    triage: dict,
    build_info: dict,
    screenshot_path: Optional[str] = None,
) -> Optional[str]:
    """Create a Jira bug ticket from a classified failure.

    Checks for duplicates first, links to existing if found.

    Args:
        failure: Original test failure dict from Katalon.
        triage: Classification dict from Claude.
        build_info: dict with 'number', 'env', 'epic_key', 'run_url'.
        screenshot_path: Optional path to failure screenshot.

    Returns:
        Jira issue key (e.g. "QA-419") or None if skipped.
    """
    jira = JiraClient()

    summary = triage.get("summary", failure.get("testCaseName", "Unknown failure"))
    component = triage.get("component", "")

    # Check for duplicates
    existing = jira.find_duplicate(summary, component)
    if existing:
        print(f"   🔗 Linked to existing ticket: {existing}")
        # Add a comment with new occurrence details
        jira.add_comment_text(
            existing,
            f"🔄 Failure reoccurred\n"
            f"Build: #{build_info.get('number', 'N/A')}\n"
            f"Environment: {build_info.get('env', 'N/A')}\n"
            f"Test: {failure.get('testCaseId', 'N/A')}\n"
            f"Time: {datetime.now().isoformat()}",
        )
        return existing

    # Build structured description
    description = JiraClient.build_bug_description(
        root_cause=triage.get("root_cause", "Under investigation"),
        repro_steps=triage.get("repro_steps", ["See test case for details"]),
        expected=triage.get("expected", "N/A"),
        actual=triage.get("actual", "N/A"),
        test_case_id=failure.get("testCaseId", ""),
        browser=failure.get("browser", build_info.get("browser", "")),
        build_number=build_info.get("number", ""),
        environment=build_info.get("env", ""),
        stack_trace=failure.get("stackTrace", ""),
    )

    # Create the ticket
    try:
        result = jira.create_issue(
            summary=summary,
            description_adf=description,
            issue_type=config.JIRA_BUG_ISSUE_TYPE,
            priority=triage.get("priority", "Medium"),
            labels=["ai-detected", "automated", triage.get("classification", "").lower()],
            components=[component] if component else None,
            epic_key=build_info.get("epic_key"),
        )
        ticket_key = result["key"]
        print(f"   🎫 Created: {ticket_key} — {summary}")

        # Attach screenshot if available
        if screenshot_path and Path(screenshot_path).exists():
            try:
                jira.attach_file(ticket_key, screenshot_path)
                print(f"      📎 Screenshot attached")
            except Exception as e:
                print(f"      ⚠️  Failed to attach screenshot: {e}")

        return ticket_key

    except Exception as e:
        print(f"   ❌ Failed to create ticket: {e}")
        return None


# ═════════════════════════════════════════════════
# Full Pipeline
# ═════════════════════════════════════════════════

def run_bug_pipeline(
    results_path: str,
    build_info: Optional[dict] = None,
) -> dict:
    """Run the complete bug triage pipeline.

    1. Load test results
    2. Classify all failures via Claude
    3. Deduplicate against existing Jira tickets
    4. Create Jira tickets for real bugs and blockers
    5. Post Slack notifications
    6. Return pipeline verdict (pass/fail)

    Args:
        results_path: Path to Katalon results JSON file.
        build_info: dict with 'number', 'env', 'epic_key', 'run_url', 'browser'.

    Returns:
        dict with keys: classifications, tickets_created, blockers, pass_rate, verdict
    """
    print("\n" + "=" * 60)
    print("🚀 Stage 6: AI Bug Triage Pipeline")
    print("=" * 60)

    # Load results
    with open(results_path) as f:
        test_results = json.load(f)

    build = build_info or {
        "number": os.getenv("GITHUB_RUN_NUMBER", "local"),
        "env": os.getenv("ENVIRONMENT", "unknown"),
        "epic_key": None,
        "run_url": os.getenv("GITHUB_SERVER_URL", "")
        + "/" + os.getenv("GITHUB_REPOSITORY", "")
        + "/actions/runs/" + os.getenv("GITHUB_RUN_ID", ""),
        "browser": "chrome",
    }

    all_tests = test_results.get("testCases", [])
    total = len(all_tests)
    passed = sum(1 for t in all_tests if t.get("status", "").upper() == "PASSED")
    failed = total - passed
    pass_rate = round((passed / total) * 100, 1) if total else 0

    print(f"\n📊 Results: {total} total, {passed} passed, {failed} failed ({pass_rate}%)")

    # Classify failures
    classifications = classify_failures(test_results)

    # Process each classification
    tickets_created = []
    blockers = []
    flaky_log = []
    env_issues = []

    for i, triage in enumerate(classifications):
        if not triage.get("needs_jira_ticket", False):
            # Log but don't create ticket
            cls = triage.get("classification", "UNKNOWN")
            if cls == "FLAKY":
                flaky_log.append(triage)
            elif cls == "ENV_ISSUE":
                env_issues.append(triage)
            print(f"   ⚠️  Skipped ({cls}): {triage.get('summary', 'Unknown')}")
            continue

        # Get the corresponding failure data
        failures = [
            tc for tc in all_tests
            if tc.get("status", "").upper() in ("FAILED", "ERROR")
        ]
        failure = failures[i] if i < len(failures) else {}

        screenshot = failure.get("screenshotPath", "")
        ticket = create_bug_ticket(failure, triage, build, screenshot)

        if ticket:
            tickets_created.append(ticket)
            if triage.get("classification") == "BLOCKER":
                blockers.append({
                    "ticket": ticket,
                    "summary": triage.get("summary", ""),
                    "component": triage.get("component", ""),
                    "team": triage.get("suggested_assignee_team", ""),
                })

    # Post Slack notifications
    slack = SlackNotifier()
    jira_base = config.JIRA_BASE_URL

    # Post blocker alerts
    for blocker in blockers:
        slack.post_blocker_alert(
            ticket_key=blocker["ticket"],
            summary=blocker["summary"],
            component=blocker["component"],
            assigned_team=blocker["team"],
            confidence="high",
            jira_url=f"{jira_base}/browse/{blocker['ticket']}",
        )

    # Post run summary
    slack.post_run_summary(
        pass_rate=pass_rate,
        total_tests=total,
        passed=passed,
        failed=failed,
        flaky=len(flaky_log),
        blockers=len(blockers),
        tickets_created=tickets_created,
        run_url=build.get("run_url", ""),
    )

    # Determine verdict
    if blockers:
        verdict = "BLOCKED"
    elif pass_rate < config.PASS_RATE_THRESHOLD:
        verdict = "BELOW_THRESHOLD"
    else:
        verdict = "PASSED"

    print(f"\n{'🔴' if verdict != 'PASSED' else '✅'} Pipeline verdict: {verdict}")
    print(f"   Tickets created: {len(tickets_created)}")
    print(f"   Blockers: {len(blockers)}")
    print(f"   Flaky: {len(flaky_log)}")
    print(f"   Env issues: {len(env_issues)}")

    return {
        "classifications": classifications,
        "tickets_created": tickets_created,
        "blockers": blockers,
        "flaky_log": flaky_log,
        "env_issues": env_issues,
        "pass_rate": pass_rate,
        "total_tests": total,
        "passed": passed,
        "failed": failed,
        "verdict": verdict,
    }


# ═════════════════════════════════════════════════
# CLI Entry Point
# ═════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="AI Bug Triage Pipeline")
    parser.add_argument("--results", required=True, help="Path to Katalon results JSON")
    parser.add_argument("--build-number", default=None, help="CI build number")
    parser.add_argument("--environment", default="staging", help="Test environment")
    parser.add_argument("--epic-key", default=None, help="Jira epic key")
    args = parser.parse_args()

    build = {
        "number": args.build_number or "local",
        "env": args.environment,
        "epic_key": args.epic_key,
        "run_url": "",
        "browser": "chrome",
    }

    result = run_bug_pipeline(args.results, build)

    if result["verdict"] != "PASSED":
        print(f"\n❌ Exiting with error code 1 — {result['verdict']}")
        exit(1)