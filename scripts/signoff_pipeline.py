#!/usr/bin/env python3
"""
QA AI Automation — CI/CD Sign-off Pipeline Script
Runs after test gate in GitHub Actions (nightly/manual only).
Collects metrics, generates report, publishes to Confluence and Slack.
"""

import os
import sys
import argparse
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.signoff_report import run_signoff_pipeline


def main():
    parser = argparse.ArgumentParser(
        description="Generate and publish QA sign-off report",
    )
    parser.add_argument(
        "--sprint-id",
        default=os.getenv("SPRINT_ID", ""),
        help="Sprint identifier",
    )
    parser.add_argument(
        "--fix-version",
        default=os.getenv("FIX_VERSION", ""),
        help="Release version string",
    )
    parser.add_argument(
        "--no-publish",
        action="store_true",
        help="Generate report but don't publish to Confluence/Slack",
    )
    args = parser.parse_args()

    if not args.sprint_id or not args.fix_version:
        print("❌ Both --sprint-id and --fix-version are required")
        print("   Set via args or env vars SPRINT_ID / FIX_VERSION")
        sys.exit(1)

    print("=" * 60)
    print("🚀 QA Sign-off Pipeline")
    print("=" * 60)
    print(f"   Sprint:  {args.sprint_id}")
    print(f"   Version: {args.fix_version}")
    print(f"   Publish: {'No' if args.no_publish else 'Yes'}")

    result = run_signoff_pipeline(
        sprint_id=args.sprint_id,
        fix_version=args.fix_version,
        publish=not args.no_publish,
    )

    print("\n" + "=" * 60)
    print("📋 Sign-off Result")
    print("=" * 60)
    print(f"   Verdict:    {result['verdict']}")
    print(f"   Confluence: {result.get('confluence_url', 'Not published')}")

    # Exit code based on verdict
    verdict = result.get("verdict", "")
    if "NOT APPROVED" in verdict or "❌" in verdict:
        print("\n❌ Release NOT APPROVED")
        sys.exit(1)

    print("\n✅ Report generated successfully")
    sys.exit(0)


if __name__ == "__main__":
    main()
