#!/usr/bin/env python3
"""
QA AI Automation — CI/CD Triage & Gate Script
Runs after Katalon tests in GitHub Actions.
Classifies failures, creates Jira tickets, posts Slack alerts,
and exits non-zero if blockers are found (blocking the merge/deploy).
"""

import os
import sys
import json
import glob
import argparse
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.bug_triage import run_bug_pipeline


def find_results_file(results_dir: str) -> str:
    """Find the most recent Katalon results JSON in the given directory.

    Searches recursively for common Katalon result file patterns.
    """
    patterns = [
        "**/execution.json",
        "**/report.json",
        "**/*result*.json",
        "**/*.json",
    ]

    for pattern in patterns:
        matches = sorted(
            glob.glob(os.path.join(results_dir, pattern), recursive=True),
            key=os.path.getmtime,
            reverse=True,
        )
        if matches:
            return matches[0]

    raise FileNotFoundError(f"No results JSON found in {results_dir}")


def main():
    parser = argparse.ArgumentParser(
        description="AI triage + pipeline gate for Katalon test results",
    )
    parser.add_argument(
        "--results-dir",
        default="Reports/",
        help="Directory containing Katalon result files",
    )
    parser.add_argument(
        "--results-file",
        default=None,
        help="Specific results file (overrides --results-dir search)",
    )
    parser.add_argument(
        "--pass-threshold",
        type=float,
        default=95.0,
        help="Minimum pass rate to clear the gate (default: 95)",
    )
    parser.add_argument(
        "--block-on-critical",
        action="store_true",
        help="Block pipeline if any critical bugs are found",
    )
    parser.add_argument(
        "--build-number",
        default=os.getenv("GITHUB_RUN_NUMBER", "local"),
        help="CI build number",
    )
    parser.add_argument(
        "--environment",
        default=os.getenv("ENVIRONMENT", "staging"),
        help="Test environment",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("🚀 QA AI Triage & Gate")
    print("=" * 60)

    # Find results file
    try:
        if args.results_file:
            results_path = args.results_file
        else:
            results_path = find_results_file(args.results_dir)
        print(f"📂 Results file: {results_path}")
    except FileNotFoundError as e:
        print(f"❌ {e}")
        sys.exit(1)

    # Build info from CI environment
    build_info = {
        "number": args.build_number,
        "env": args.environment,
        "epic_key": os.getenv("EPIC_KEY"),
        "run_url": (
            os.getenv("GITHUB_SERVER_URL", "https://github.com")
            + "/" + os.getenv("GITHUB_REPOSITORY", "")
            + "/actions/runs/" + os.getenv("GITHUB_RUN_ID", "")
        ),
        "browser": os.getenv("BROWSER", "chrome"),
    }

    # Run the full triage pipeline
    result = run_bug_pipeline(results_path, build_info)

    # Print gate decision
    print("\n" + "=" * 60)
    print("📋 Gate Decision")
    print("=" * 60)
    print(f"   Pass rate:      {result['pass_rate']}% (threshold: {args.pass_threshold}%)")
    print(f"   Total tests:    {result['total_tests']}")
    print(f"   Passed:         {result['passed']}")
    print(f"   Failed:         {result['failed']}")
    print(f"   Tickets:        {len(result['tickets_created'])}")
    print(f"   Blockers:       {len(result['blockers'])}")
    print(f"   Flaky:          {len(result['flaky_log'])}")
    print(f"   Env issues:     {len(result['env_issues'])}")
    print(f"   Verdict:        {result['verdict']}")

    # Exit appropriately
    if result["blockers"]:
        print(f"\n❌ GATE BLOCKED — {len(result['blockers'])} blocker(s):")
        for b in result["blockers"]:
            print(f"   · {b['ticket']}: {b['summary']}")
        sys.exit(1)

    if result["pass_rate"] < args.pass_threshold:
        print(f"\n❌ GATE FAILED — pass rate {result['pass_rate']}% below {args.pass_threshold}%")
        sys.exit(1)

    if args.block_on_critical and any(
        c.get("severity") in ("Critical", "Highest")
        for c in result.get("classifications", [])
        if c.get("needs_jira_ticket")
    ):
        print(f"\n❌ GATE FAILED — critical bug(s) found with --block-on-critical enabled")
        sys.exit(1)

    print(f"\n✅ GATE PASSED — all criteria met")
    sys.exit(0)


if __name__ == "__main__":
    main()
