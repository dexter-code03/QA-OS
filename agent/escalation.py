"""
QA AI Automation — Escalation Rules Engine
Configurable rules that determine when the agent handles things
autonomously vs when it escalates to the QA lead.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config


class Action(str, Enum):
    """What the agent should do with an event."""
    HANDLE = "handle"          # Agent resolves autonomously
    ESCALATE = "escalate"      # Notify QA lead
    BLOCK = "block"            # Block the release pipeline


class EscalationTarget(str, Enum):
    """Who gets notified on escalation."""
    QA_LEAD = "qa_lead"
    DEV_LEAD = "dev_lead"
    DEV_TEAM = "dev_team"
    OPS_TEAM = "ops_team"


@dataclass
class EscalationDecision:
    """Result of evaluating an event against escalation rules."""
    action: Action
    target: Optional[EscalationTarget]
    reason: str
    auto_actions: list[str]     # Actions the agent should take automatically


class EscalationEngine:
    """Evaluates events against configurable rules to decide agent vs human handling.

    Rules hierarchy:
    1. Pipeline-level rules (pass rate, blocker count)
    2. Ticket-level rules (severity, staleness, retest failures)
    3. System-level rules (env issues, flaky test thresholds)
    """

    def __init__(self):
        self.pass_rate_threshold = config.PASS_RATE_THRESHOLD
        self.max_open_blockers = config.BLOCKER_MAX_OPEN
        self.stale_warn_hours = config.STALE_BUG_HOURS_WARN
        self.stale_escalate_hours = config.STALE_BUG_HOURS_ESCALATE
        self.stale_critical_hours = config.STALE_BUG_HOURS_CRITICAL
        self.max_retest_failures = config.MAX_RETEST_FAILURES

    # ── Pipeline-Level Rules ──────────────────

    def evaluate_pipeline(
        self,
        pass_rate: float,
        blocker_count: int,
        critical_open: int,
    ) -> EscalationDecision:
        """Evaluate the overall pipeline health after a test run.

        Rules:
        - Any blockers → BLOCK pipeline
        - Pass rate below threshold → ESCALATE to QA lead
        - Critical bugs open → ESCALATE to QA lead
        - Otherwise → HANDLE (agent clears gate)
        """
        if blocker_count > 0:
            return EscalationDecision(
                action=Action.BLOCK,
                target=EscalationTarget.QA_LEAD,
                reason=f"{blocker_count} blocker(s) found — pipeline blocked",
                auto_actions=[
                    "post_blocker_alert",
                    "block_deploy_gate",
                    "create_jira_tickets",
                ],
            )

        if pass_rate < self.pass_rate_threshold:
            return EscalationDecision(
                action=Action.ESCALATE,
                target=EscalationTarget.QA_LEAD,
                reason=f"Pass rate {pass_rate}% below threshold {self.pass_rate_threshold}%",
                auto_actions=[
                    "create_jira_tickets",
                    "post_run_summary",
                ],
            )

        if critical_open > 0:
            return EscalationDecision(
                action=Action.ESCALATE,
                target=EscalationTarget.QA_LEAD,
                reason=f"{critical_open} critical bug(s) still open",
                auto_actions=["post_run_summary"],
            )

        return EscalationDecision(
            action=Action.HANDLE,
            target=None,
            reason="All criteria met — pipeline passed",
            auto_actions=["clear_deploy_gate", "post_run_summary"],
        )

    # ── Ticket-Level Rules ────────────────────

    def evaluate_retest(
        self,
        passed: bool,
        attempt_number: int,
        severity: str,
    ) -> EscalationDecision:
        """Evaluate what to do after a retest result.

        Rules:
        - Retest passes → HANDLE (close ticket)
        - First failure → HANDLE (reopen, notify dev)
        - Second+ failure → ESCALATE to QA lead
        - Critical severity + any failure → ESCALATE immediately
        """
        if passed:
            return EscalationDecision(
                action=Action.HANDLE,
                target=None,
                reason="Retest passed — closing ticket",
                auto_actions=[
                    "close_jira_ticket",
                    "update_signoff_report",
                    "post_retest_result",
                ],
            )

        if severity in ("Critical", "Highest") and attempt_number >= 1:
            return EscalationDecision(
                action=Action.ESCALATE,
                target=EscalationTarget.QA_LEAD,
                reason=f"Critical bug failed retest (attempt {attempt_number})",
                auto_actions=[
                    "reopen_jira_ticket",
                    "post_retest_result",
                ],
            )

        if attempt_number >= self.max_retest_failures:
            return EscalationDecision(
                action=Action.ESCALATE,
                target=EscalationTarget.QA_LEAD,
                reason=f"Max retest attempts ({self.max_retest_failures}) reached",
                auto_actions=[
                    "reopen_jira_ticket",
                    "post_retest_result",
                ],
            )

        return EscalationDecision(
            action=Action.HANDLE,
            target=EscalationTarget.DEV_TEAM,
            reason=f"Retest failed (attempt {attempt_number}) — notifying dev",
            auto_actions=[
                "reopen_jira_ticket",
                "notify_dev",
                "post_retest_result",
            ],
        )

    def evaluate_stale_ticket(
        self,
        hours_open: float,
        severity: str,
        has_assignee: bool,
    ) -> EscalationDecision:
        """Evaluate what to do with a stale (no activity) ticket.

        Rules:
        - < warn threshold → HANDLE (do nothing, still within SLA)
        - warn → escalate threshold → HANDLE (ping dev)
        - escalate → critical threshold → HANDLE (ping dev lead)
        - > critical threshold → ESCALATE to QA lead
        - Critical severity has halved thresholds
        """
        # Critical bugs have stricter SLAs
        multiplier = 0.5 if severity in ("Critical", "Highest") else 1.0
        warn = self.stale_warn_hours * multiplier
        escalate = self.stale_escalate_hours * multiplier
        critical = self.stale_critical_hours * multiplier

        if hours_open < warn:
            return EscalationDecision(
                action=Action.HANDLE,
                target=None,
                reason=f"Within SLA ({hours_open:.0f}h < {warn:.0f}h threshold)",
                auto_actions=[],
            )

        if hours_open < escalate:
            return EscalationDecision(
                action=Action.HANDLE,
                target=EscalationTarget.DEV_TEAM,
                reason=f"Stale for {hours_open:.0f}h — pinging assigned dev",
                auto_actions=["post_stale_bug_alert"],
            )

        if hours_open < critical:
            return EscalationDecision(
                action=Action.HANDLE,
                target=EscalationTarget.DEV_LEAD,
                reason=f"Stale for {hours_open:.0f}h — escalating to dev lead",
                auto_actions=["post_stale_bug_alert"],
            )

        return EscalationDecision(
            action=Action.ESCALATE,
            target=EscalationTarget.QA_LEAD,
            reason=f"Bug open for {hours_open:.0f}h with no resolution — QA lead intervention required",
            auto_actions=["post_stale_bug_alert"],
        )

    def evaluate_severity_change(
        self,
        old_severity: str,
        new_severity: str,
    ) -> EscalationDecision:
        """Evaluate whether a severity change needs human approval.

        Rules:
        - Downgrade from Critical/High → ESCALATE (needs QA approval)
        - Upgrade to Critical → ESCALATE (QA lead should know)
        - All other changes → HANDLE
        """
        high_severities = ("Critical", "Highest", "High")

        if old_severity in high_severities and new_severity not in high_severities:
            return EscalationDecision(
                action=Action.ESCALATE,
                target=EscalationTarget.QA_LEAD,
                reason=f"Severity downgrade from {old_severity} to {new_severity} needs approval",
                auto_actions=[],
            )

        if new_severity in ("Critical", "Highest") and old_severity not in ("Critical", "Highest"):
            return EscalationDecision(
                action=Action.ESCALATE,
                target=EscalationTarget.QA_LEAD,
                reason=f"Severity upgraded to {new_severity} — QA lead notified",
                auto_actions=["post_blocker_alert"],
            )

        return EscalationDecision(
            action=Action.HANDLE,
            target=None,
            reason=f"Severity changed from {old_severity} to {new_severity}",
            auto_actions=[],
        )

    # ── Release Gate ──────────────────────────

    def evaluate_release_gate(
        self,
        pass_rate: float,
        critical_open: int,
        major_open: int,
        blockers_open: int,
    ) -> EscalationDecision:
        """Final release gate evaluation.

        Rules:
        - Any blockers → BLOCK
        - Any critical bugs open → BLOCK
        - Pass rate below threshold → BLOCK
        - Major bugs > 3 → ESCALATE for decision
        - Otherwise → clear gate (HANDLE)
        """
        if blockers_open > 0:
            return EscalationDecision(
                action=Action.BLOCK,
                target=EscalationTarget.QA_LEAD,
                reason=f"{blockers_open} blocker(s) must be resolved before release",
                auto_actions=["block_deploy_gate"],
            )

        if critical_open > 0:
            return EscalationDecision(
                action=Action.BLOCK,
                target=EscalationTarget.QA_LEAD,
                reason=f"{critical_open} critical bug(s) must be resolved before release",
                auto_actions=["block_deploy_gate"],
            )

        if pass_rate < self.pass_rate_threshold:
            return EscalationDecision(
                action=Action.BLOCK,
                target=EscalationTarget.QA_LEAD,
                reason=f"Pass rate {pass_rate}% below required {self.pass_rate_threshold}%",
                auto_actions=["block_deploy_gate"],
            )

        if major_open > 3:
            return EscalationDecision(
                action=Action.ESCALATE,
                target=EscalationTarget.QA_LEAD,
                reason=f"{major_open} major bugs open — QA lead decision required",
                auto_actions=[],
            )

        return EscalationDecision(
            action=Action.HANDLE,
            target=None,
            reason="All release criteria met — gate cleared",
            auto_actions=["clear_deploy_gate", "generate_signoff_report"],
        )
