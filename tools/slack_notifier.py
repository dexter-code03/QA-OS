"""
QA AI Automation — Slack Notification Client
Rich Block Kit messages for test results, blocker alerts, and digest cards.
"""

import json
import requests
from typing import Optional
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config


class SlackNotifier:
    """Sends structured Slack notifications via incoming webhook."""

    def __init__(self):
        self.webhook_url = config.SLACK_WEBHOOK_URL

    # ── Core ──────────────────────────────────

    def _post(self, payload: dict) -> bool:
        """Send a payload to the Slack webhook. Returns True on success."""
        if not self.webhook_url:
            print("[Slack] No webhook URL configured — skipping notification")
            return False

        response = requests.post(
            self.webhook_url,
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        if response.status_code != 200:
            print(f"[Slack] Error {response.status_code}: {response.text}")
            return False
        return True

    # ── Simple Messages ───────────────────────

    def post_message(self, text: str) -> bool:
        """Send a simple text message."""
        return self._post({"text": text})

    # ── Blocker Alert ─────────────────────────

    def post_blocker_alert(
        self,
        ticket_key: str,
        summary: str,
        component: str,
        assigned_team: str,
        confidence: str,
        jira_url: str,
    ) -> bool:
        """Send a high-urgency blocker alert with all context."""
        return self._post({
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": "🔴 BLOCKER Detected — Deployment Blocked",
                    },
                },
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"*{ticket_key}* · {summary}\n\n"
                            f"• *Component:* {component}\n"
                            f"• *Assigned to:* {assigned_team}\n"
                            f"• *Confidence:* {confidence}\n"
                        ),
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "⛔ *Deployment to staging is blocked until resolved.*",
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "View in Jira"},
                            "url": jira_url,
                            "style": "danger",
                        }
                    ],
                },
            ],
        })

    # ── Test Run Summary ──────────────────────

    def post_run_summary(
        self,
        pass_rate: float,
        total_tests: int,
        passed: int,
        failed: int,
        flaky: int,
        blockers: int,
        tickets_created: list[str],
        run_url: str = "",
    ) -> bool:
        """Post a summary card after a CI/CD test run."""
        emoji = "✅" if blockers == 0 and pass_rate >= config.PASS_RATE_THRESHOLD else "🔴"

        fields = [
            {"type": "mrkdwn", "text": f"*Pass rate*\n{pass_rate}%"},
            {"type": "mrkdwn", "text": f"*Tests run*\n{total_tests}"},
            {"type": "mrkdwn", "text": f"*Passed*\n{passed}"},
            {"type": "mrkdwn", "text": f"*Failed*\n{failed}"},
            {"type": "mrkdwn", "text": f"*Flaky*\n{flaky}"},
            {"type": "mrkdwn", "text": f"*Blockers*\n{blockers}"},
        ]

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{emoji} Katalon Test Run Complete",
                },
            },
            {"type": "divider"},
            {"type": "section", "fields": fields},
        ]

        if tickets_created:
            ticket_text = ", ".join(tickets_created[:10])
            if len(tickets_created) > 10:
                ticket_text += f" (+{len(tickets_created) - 10} more)"
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Tickets created:* {ticket_text}",
                },
            })

        if run_url:
            blocks.append({
                "type": "actions",
                "elements": [{
                    "type": "button",
                    "text": {"type": "plain_text", "text": "View CI Run"},
                    "url": run_url,
                }],
            })

        return self._post({"blocks": blocks})

    # ── Sign-off Digest ───────────────────────

    def post_digest_card(
        self,
        fix_version: str,
        pass_rate: float,
        open_bugs: int,
        critical_bugs: int,
        resolved_bugs: int,
        flaky_tests: int,
        verdict: str,
        report_url: str,
    ) -> bool:
        """Post the QA sign-off digest card to Slack."""
        if "APPROVED" in verdict and "CONDITIONAL" not in verdict:
            emoji = "✅"
        elif "CONDITIONAL" in verdict:
            emoji = "⚠️"
        else:
            emoji = "❌"

        return self._post({
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"{emoji} QA Sign-off — {fix_version}",
                    },
                },
                {"type": "divider"},
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Pass rate*\n{pass_rate}%"},
                        {"type": "mrkdwn", "text": f"*Open bugs*\n{open_bugs} ({critical_bugs} critical)"},
                        {"type": "mrkdwn", "text": f"*Resolved*\n{resolved_bugs}"},
                        {"type": "mrkdwn", "text": f"*Flaky tests*\n{flaky_tests}"},
                    ],
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Verdict:* {verdict}",
                    },
                },
                {
                    "type": "actions",
                    "elements": [{
                        "type": "button",
                        "text": {"type": "plain_text", "text": "View Full Report"},
                        "url": report_url,
                    }],
                },
            ],
        })

    # ── Follow-up Notifications ───────────────

    def post_stale_bug_alert(
        self,
        ticket_key: str,
        summary: str,
        assigned_team: str,
        hours_open: int,
        jira_url: str,
    ) -> bool:
        """Notify channel about a stale bug that needs attention."""
        return self._post({
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"🔔 *{ticket_key}* has had no activity for *{hours_open} hours*\n\n"
                            f"_{summary}_\n"
                            f"• Assigned: {assigned_team}\n\n"
                            f"Please update the ticket status or comment with an ETA."
                        ),
                    },
                },
                {
                    "type": "actions",
                    "elements": [{
                        "type": "button",
                        "text": {"type": "plain_text", "text": "View in Jira"},
                        "url": jira_url,
                    }],
                },
            ],
        })

    def post_retest_result(
        self,
        ticket_key: str,
        passed: bool,
        attempt: int,
        jira_url: str,
    ) -> bool:
        """Notify about a retest outcome."""
        if passed:
            text = f"✅ *{ticket_key}* — Retest *PASSED* (attempt {attempt}). Ticket closed."
        else:
            text = f"❌ *{ticket_key}* — Retest *FAILED* (attempt {attempt}). Ticket reopened."
            if attempt >= config.MAX_RETEST_FAILURES:
                text += "\n⚠️ *Escalated to QA lead — max retests reached.*"

        return self._post({
            "blocks": [
                {"type": "section", "text": {"type": "mrkdwn", "text": text}},
                {
                    "type": "actions",
                    "elements": [{
                        "type": "button",
                        "text": {"type": "plain_text", "text": "View in Jira"},
                        "url": jira_url,
                    }],
                },
            ],
        })
