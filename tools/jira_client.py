"""
QA AI Automation — Jira REST API Client
Handles all Jira interactions: ticket creation, searching, commenting, attachments.
"""

import json
import requests
from pathlib import Path
from typing import Optional

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config


class JiraClient:
    """Wrapper for Jira REST API v3 with Atlassian Document Format support."""

    def __init__(self):
        self.base_url = config.JIRA_BASE_URL.rstrip("/")
        self.auth = (config.JIRA_EMAIL, config.JIRA_API_TOKEN)
        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    # ── Core API ──────────────────────────────

    def _request(self, method: str, endpoint: str, **kwargs) -> dict:
        """Make an authenticated request to the Jira API."""
        url = f"{self.base_url}/rest/api/3/{endpoint}"
        response = requests.request(
            method, url,
            auth=self.auth,
            headers=self.headers,
            **kwargs,
        )
        response.raise_for_status()
        return response.json() if response.text else {}

    # ── Issue Operations ──────────────────────

    def create_issue(
        self,
        summary: str,
        description_adf: dict,
        issue_type: str = "Bug",
        priority: str = "Medium",
        labels: Optional[list] = None,
        components: Optional[list] = None,
        epic_key: Optional[str] = None,
        extra_fields: Optional[dict] = None,
    ) -> dict:
        """Create a Jira issue with full ADF description.

        Returns:
            dict with 'id', 'key', 'self' of the created issue.
        """
        fields = {
            "project": {"key": config.JIRA_PROJECT_KEY},
            "issuetype": {"name": issue_type},
            "summary": summary,
            "description": description_adf,
            "priority": {"name": priority},
        }

        if labels:
            fields["labels"] = labels
        if components:
            fields["components"] = [{"name": c} for c in components]
        if epic_key and config.JIRA_EPIC_LINK_FIELD:
            fields[config.JIRA_EPIC_LINK_FIELD] = epic_key
        if extra_fields:
            fields.update(extra_fields)

        return self._request("POST", "issue", json={"fields": fields})

    def get_issue(self, issue_key: str) -> dict:
        """Fetch full issue details."""
        return self._request("GET", f"issue/{issue_key}")

    def search_issues(self, jql: str, max_results: int = 50) -> list:
        """Search issues using JQL. Returns list of issue dicts.

        Uses the new /search/jql endpoint (the old /search was deprecated
        and returns 410 on newer Jira Cloud instances).
        """
        result = self._request(
            "POST", "search/jql",
            json={"jql": jql, "maxResults": max_results},
        )
        return result.get("issues", [])

    def add_comment(self, issue_key: str, body_adf: dict) -> dict:
        """Add a comment to an issue using ADF format."""
        return self._request(
            "POST", f"issue/{issue_key}/comment",
            json={"body": body_adf},
        )

    def add_comment_text(self, issue_key: str, text: str) -> dict:
        """Add a plain-text comment (auto-wrapped in ADF)."""
        adf = self.text_to_adf(text)
        return self.add_comment(issue_key, adf)

    def transition_issue(self, issue_key: str, transition_name: str) -> dict:
        """Transition an issue to a new status by name."""
        # First get available transitions
        transitions = self._request("GET", f"issue/{issue_key}/transitions")
        target = next(
            (t for t in transitions["transitions"]
             if t["name"].lower() == transition_name.lower()),
            None,
        )
        if not target:
            available = [t["name"] for t in transitions["transitions"]]
            raise ValueError(
                f"Transition '{transition_name}' not found. "
                f"Available: {available}"
            )
        return self._request(
            "POST", f"issue/{issue_key}/transitions",
            json={"transition": {"id": target["id"]}},
        )

    def attach_file(self, issue_key: str, file_path: str) -> dict:
        """Attach a file (e.g. screenshot) to an issue."""
        url = f"{self.base_url}/rest/api/3/issue/{issue_key}/attachments"
        headers = {
            "X-Atlassian-Token": "no-check",
        }
        with open(file_path, "rb") as f:
            response = requests.post(
                url,
                auth=self.auth,
                headers=headers,
                files={"file": (Path(file_path).name, f)},
            )
        response.raise_for_status()
        return response.json()

    # ── Duplicate Detection ───────────────────

    def find_duplicate(
        self,
        summary: str,
        component: Optional[str] = None,
        max_results: int = 5,
    ) -> Optional[str]:
        """Check if a similar open bug already exists.

        Returns:
            Existing issue key if found, None otherwise.
        """
        # Use first 40 chars of summary for fuzzy matching
        search_text = summary[:40].replace('"', '\\"')
        jql = (
            f'project = {config.JIRA_PROJECT_KEY} '
            f'AND issuetype = {config.JIRA_BUG_ISSUE_TYPE} '
            f'AND status != Done '
            f'AND summary ~ "{search_text}"'
        )
        if component:
            jql += f' AND component = "{component}"'

        issues = self.search_issues(jql, max_results=max_results)
        return issues[0]["key"] if issues else None

    # ── ADF Helpers ───────────────────────────

    @staticmethod
    def text_to_adf(text: str) -> dict:
        """Convert plain text to minimal ADF document."""
        return {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": text}],
                }
            ],
        }

    @staticmethod
    def build_adf_document(sections: list[dict]) -> dict:
        """Build a rich ADF document from a list of section dicts.

        Each section: {"type": "heading"|"paragraph"|"code"|"list",
                       "text": str, "level": int (for headings),
                       "items": list (for lists)}
        """
        content = []
        for section in sections:
            s_type = section.get("type", "paragraph")

            if s_type == "heading":
                content.append({
                    "type": "heading",
                    "attrs": {"level": section.get("level", 2)},
                    "content": [{"type": "text", "text": section["text"]}],
                })

            elif s_type == "paragraph":
                content.append({
                    "type": "paragraph",
                    "content": [{"type": "text", "text": section["text"]}],
                })

            elif s_type == "code":
                content.append({
                    "type": "codeBlock",
                    "attrs": {"language": section.get("language", "")},
                    "content": [{"type": "text", "text": section["text"]}],
                })

            elif s_type == "list":
                items = []
                for item_text in section.get("items", []):
                    items.append({
                        "type": "listItem",
                        "content": [{
                            "type": "paragraph",
                            "content": [{"type": "text", "text": item_text}],
                        }],
                    })
                content.append({
                    "type": "orderedList" if section.get("ordered") else "bulletList",
                    "content": items,
                })

        return {"type": "doc", "version": 1, "content": content}

    @staticmethod
    def build_bug_description(
        root_cause: str,
        repro_steps: list[str],
        expected: str,
        actual: str,
        test_case_id: str = "",
        browser: str = "",
        build_number: str = "",
        environment: str = "",
        stack_trace: str = "",
    ) -> dict:
        """Build a structured ADF bug description with all required fields."""
        sections = [
            {"type": "heading", "text": "Root Cause", "level": 3},
            {"type": "paragraph", "text": root_cause},
            {"type": "heading", "text": "Steps to Reproduce", "level": 3},
            {"type": "list", "items": repro_steps, "ordered": True},
            {"type": "heading", "text": "Expected vs Actual", "level": 3},
            {"type": "paragraph", "text": f"✅ Expected: {expected}"},
            {"type": "paragraph", "text": f"❌ Actual: {actual}"},
            {"type": "heading", "text": "Test Environment", "level": 3},
            {"type": "paragraph", "text": (
                f"Test case: {test_case_id}  |  "
                f"Browser: {browser}  |  "
                f"Build: #{build_number}  |  "
                f"Environment: {environment}"
            )},
        ]

        if stack_trace:
            sections.extend([
                {"type": "heading", "text": "Stack Trace", "level": 3},
                {"type": "code", "text": stack_trace, "language": "text"},
            ])

        return JiraClient.build_adf_document(sections)
