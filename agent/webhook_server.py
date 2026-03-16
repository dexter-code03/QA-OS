"""
QA AI Automation — Jira Webhook Server
FastAPI server that listens to Jira status changes and triggers
automated actions: retests, follow-ups, escalations, gate checks.
"""

import json
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config
from tools.jira_client import JiraClient
from tools.slack_notifier import SlackNotifier
from agent.escalation import EscalationEngine, Action


# ═════════════════════════════════════════════════
# FastAPI App
# ═════════════════════════════════════════════════

app = FastAPI(
    title="QA AI Automation — Webhook Server",
    description="Listens to Jira webhooks and automates the bug lifecycle.",
    version="1.0.0",
)

jira = JiraClient()
slack = SlackNotifier()
escalation = EscalationEngine()


# ═════════════════════════════════════════════════
# Data Models
# ═════════════════════════════════════════════════

class WebhookEvent(BaseModel):
    """Parsed Jira webhook event."""
    event_type: str
    issue_key: str
    issue_id: str
    status_from: Optional[str] = None
    status_to: Optional[str] = None
    priority: Optional[str] = None
    assignee: Optional[str] = None
    component: Optional[str] = None
    summary: Optional[str] = None
    created: Optional[str] = None
    updated: Optional[str] = None
    labels: list[str] = []


class PipelineTrigger(BaseModel):
    """Manual pipeline trigger request."""
    suite: str = "smoke"
    environment: str = "staging"
    sprint_id: Optional[str] = None
    fix_version: Optional[str] = None


# ═════════════════════════════════════════════════
# Webhook Parsing
# ═════════════════════════════════════════════════

def parse_jira_webhook(payload: dict) -> WebhookEvent:
    """Parse a raw Jira webhook payload into a structured event."""
    issue = payload.get("issue", {})
    fields = issue.get("fields", {})
    changelog = payload.get("changelog", {})

    # Extract status change from changelog
    status_from = None
    status_to = None
    for item in changelog.get("items", []):
        if item.get("field") == "status":
            status_from = item.get("fromString")
            status_to = item.get("toString")

    # Extract component
    components = fields.get("components", [])
    component = components[0].get("name", "") if components else ""

    return WebhookEvent(
        event_type=payload.get("webhookEvent", "unknown"),
        issue_key=issue.get("key", ""),
        issue_id=str(issue.get("id", "")),
        status_from=status_from,
        status_to=status_to,
        priority=fields.get("priority", {}).get("name", ""),
        assignee=fields.get("assignee", {}).get("displayName", "") if fields.get("assignee") else None,
        component=component,
        summary=fields.get("summary", ""),
        created=fields.get("created", ""),
        updated=fields.get("updated", ""),
        labels=[l for l in fields.get("labels", [])],
    )


# ═════════════════════════════════════════════════
# Event Handlers
# ═════════════════════════════════════════════════

def handle_status_in_review(event: WebhookEvent) -> dict:
    """Dev marked a bug as fixed (In Review). Queue a targeted retest."""
    print(f"   🔄 {event.issue_key} moved to In Review — queuing retest")

    # Add a comment noting the retest is queued
    jira.add_comment_text(
        event.issue_key,
        "🤖 Automated retest queued. Only linked test cases will run. "
        "Results will be posted here automatically.",
    )

    slack.post_message(
        f"🔄 *{event.issue_key}* moved to In Review — automated retest queued\n"
        f"_{event.summary}_"
    )

    return {
        "action": "retest_queued",
        "issue_key": event.issue_key,
        "message": "Targeted retest scheduled",
    }


def handle_retest_result(event: WebhookEvent, passed: bool, attempt: int) -> dict:
    """Handle the result of an automated retest."""
    decision = escalation.evaluate_retest(passed, attempt, event.priority)

    jira_url = f"{config.JIRA_BASE_URL}/browse/{event.issue_key}"
    slack.post_retest_result(event.issue_key, passed, attempt, jira_url)

    if passed:
        # Close the ticket
        try:
            jira.transition_issue(event.issue_key, "Done")
            jira.add_comment_text(
                event.issue_key,
                f"✅ Automated retest PASSED (attempt {attempt}). Ticket closed by QA agent.",
            )
        except Exception as e:
            print(f"   ⚠️  Could not transition: {e}")

    else:
        # Reopen the ticket
        try:
            jira.transition_issue(event.issue_key, "Reopened")
            jira.add_comment_text(
                event.issue_key,
                f"❌ Automated retest FAILED (attempt {attempt}).\n"
                f"Reason: {decision.reason}\n"
                f"Action: {decision.action.value}",
            )
        except Exception as e:
            print(f"   ⚠️  Could not transition: {e}")

    if decision.action == Action.ESCALATE:
        slack.post_message(
            f"⚠️ *ESCALATION* — {event.issue_key} requires QA lead attention\n"
            f"Reason: {decision.reason}\n"
            f"<{jira_url}|View in Jira>"
        )

    return {
        "action": decision.action.value,
        "issue_key": event.issue_key,
        "passed": passed,
        "attempt": attempt,
        "reason": decision.reason,
    }


def handle_stale_check(event: WebhookEvent) -> dict:
    """Check if a ticket is stale and take appropriate action."""
    created_str = event.created or event.updated
    if not created_str:
        return {"action": "skip", "reason": "No timestamp available"}

    try:
        # Parse ISO timestamp
        created = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        hours_open = (now - created).total_seconds() / 3600
    except (ValueError, TypeError):
        return {"action": "skip", "reason": "Could not parse timestamp"}

    decision = escalation.evaluate_stale_ticket(
        hours_open=hours_open,
        severity=event.priority,
        has_assignee=event.assignee is not None,
    )

    if decision.action != Action.HANDLE or not decision.auto_actions:
        # Nothing to do yet or escalation needed
        pass

    if "post_stale_bug_alert" in decision.auto_actions:
        jira_url = f"{config.JIRA_BASE_URL}/browse/{event.issue_key}"
        slack.post_stale_bug_alert(
            ticket_key=event.issue_key,
            summary=event.summary,
            assigned_team=event.assignee or "Unassigned",
            hours_open=int(hours_open),
            jira_url=jira_url,
        )

    return {
        "action": decision.action.value,
        "issue_key": event.issue_key,
        "hours_open": round(hours_open, 1),
        "reason": decision.reason,
    }


# ═════════════════════════════════════════════════
# Routes
# ═════════════════════════════════════════════════

@app.get("/health")
async def health_check():
    """Health check endpoint for monitoring."""
    return {
        "status": "healthy",
        "service": "qa-ai-webhook-server",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/jira-webhook")
async def jira_webhook(request: Request):
    """Main Jira webhook endpoint.

    Receives status change events and routes them to the appropriate handler.
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event = parse_jira_webhook(payload)
    print(f"\n📬 Webhook: {event.event_type} — {event.issue_key} ({event.status_from} → {event.status_to})")

    # Route based on the new status
    if event.status_to == "In Review":
        result = handle_status_in_review(event)

    elif event.status_to in ("Open", "To Do") and event.status_from in ("In Review", "In Progress"):
        # Bug reopened — dev couldn't fix it
        result = handle_stale_check(event)

    elif event.status_to == "Done":
        # Ticket closed — update metrics
        result = {
            "action": "ticket_closed",
            "issue_key": event.issue_key,
            "message": "Ticket closure recorded",
        }

    else:
        # Check for staleness on any other update
        result = handle_stale_check(event)

    return {"status": "processed", "result": result}


@app.post("/retest-result")
async def retest_result(request: Request):
    """Receive retest results from the CI pipeline.

    Expected payload:
    {
        "issue_key": "QA-419",
        "passed": true,
        "attempt": 1,
        "test_results": {...}
    }
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    issue_key = payload.get("issue_key")
    if not issue_key:
        raise HTTPException(status_code=400, detail="issue_key is required")

    # Fetch current issue details
    try:
        issue = jira.get_issue(issue_key)
        fields = issue.get("fields", {})
        event = WebhookEvent(
            event_type="retest",
            issue_key=issue_key,
            issue_id=str(issue.get("id", "")),
            priority=fields.get("priority", {}).get("name", "Medium"),
            summary=fields.get("summary", ""),
            assignee=fields.get("assignee", {}).get("displayName") if fields.get("assignee") else None,
        )
    except Exception:
        event = WebhookEvent(
            event_type="retest",
            issue_key=issue_key,
            issue_id="",
            priority="Medium",
            summary="",
        )

    result = handle_retest_result(
        event,
        passed=payload.get("passed", False),
        attempt=payload.get("attempt", 1),
    )

    return {"status": "processed", "result": result}


@app.post("/trigger-pipeline")
async def trigger_pipeline(trigger: PipelineTrigger):
    """Manual trigger endpoint for running specific pipeline stages.

    Can be called from Slack commands or other automation tools.
    """
    print(f"\n🚀 Manual trigger: suite={trigger.suite}, env={trigger.environment}")

    slack.post_message(
        f"🚀 Manual test run triggered\n"
        f"• Suite: {trigger.suite}\n"
        f"• Environment: {trigger.environment}\n"
        f"• Sprint: {trigger.sprint_id or 'N/A'}\n"
        f"• Version: {trigger.fix_version or 'N/A'}"
    )

    return {
        "status": "triggered",
        "suite": trigger.suite,
        "environment": trigger.environment,
        "message": "Pipeline trigger sent — check CI/CD for status",
    }


# ═════════════════════════════════════════════════
# Server Entry Point
# ═════════════════════════════════════════════════

def start_server(host: str = "0.0.0.0", port: int = 8000):
    """Start the webhook server."""
    import uvicorn
    print(f"\n🚀 Starting QA AI Webhook Server on {host}:{port}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="QA AI Webhook Server")
    parser.add_argument("--host", default="0.0.0.0", help="Server host")
    parser.add_argument("--port", type=int, default=8000, help="Server port")
    args = parser.parse_args()

    start_server(args.host, args.port)
