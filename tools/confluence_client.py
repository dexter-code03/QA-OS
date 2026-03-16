"""
QA AI Automation — Confluence REST API Client
Handles page reading, creation, and updating for FRDs and reports.
"""

import re
import requests
from typing import Optional
from bs4 import BeautifulSoup
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config


class ConfluenceClient:
    """Wrapper for the Confluence REST API."""

    def __init__(self):
        self.base_url = config.CONFLUENCE_BASE_URL.rstrip("/")
        if not self.base_url.endswith("/wiki"):
            self.base_url += "/wiki"
        self.auth = (config.CONFLUENCE_EMAIL, config.CONFLUENCE_API_TOKEN)
        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    # ── Core API ──────────────────────────────

    def _request(self, method: str, endpoint: str, **kwargs) -> dict:
        """Make an authenticated request to the Confluence API."""
        url = f"{self.base_url}/rest/api/{endpoint}"
        response = requests.request(
            method, url,
            auth=self.auth,
            headers=self.headers,
            **kwargs,
        )
        response.raise_for_status()
        return response.json() if response.text else {}

    # ── Read Operations ───────────────────────

    def get_page(self, page_id: str, expand: str = "body.storage,version") -> dict:
        """Fetch a Confluence page by ID.

        Returns:
            dict with page metadata and body content.
        """
        return self._request("GET", f"content/{page_id}", params={"expand": expand})

    def get_page_body_html(self, page_id: str) -> str:
        """Get the raw HTML body of a Confluence page."""
        page = self.get_page(page_id)
        return page["body"]["storage"]["value"]

    def get_page_body_text(self, page_id: str) -> str:
        """Get the plain text content of a Confluence page.

        Strips all HTML tags and returns clean text for Claude processing.
        """
        html = self.get_page_body_html(page_id)
        return self._html_to_text(html)

    def get_page_title(self, page_id: str) -> str:
        """Get the title of a Confluence page."""
        page = self._request(
            "GET", f"content/{page_id}",
            params={"expand": "version"},
        )
        return page["title"]

    # ── Write Operations ──────────────────────

    def create_page(
        self,
        title: str,
        body_html: str,
        parent_page_id: Optional[str] = None,
        space_key: Optional[str] = None,
    ) -> dict:
        """Create a new Confluence page.

        Args:
            title: Page title.
            body_html: Page body in Confluence storage format (HTML).
            parent_page_id: Optional parent page ID for hierarchy.
            space_key: Space key (defaults to config value).

        Returns:
            dict with created page metadata including '_links.webui'.
        """
        payload = {
            "type": "page",
            "title": title,
            "space": {"key": space_key or config.CONFLUENCE_SPACE_KEY},
            "body": {
                "storage": {
                    "value": body_html,
                    "representation": "storage",
                },
            },
        }

        if parent_page_id:
            payload["ancestors"] = [{"id": parent_page_id}]

        result = self._request("POST", "content", json=payload)
        return result

    def update_page(
        self,
        page_id: str,
        title: str,
        body_html: str,
    ) -> dict:
        """Update an existing Confluence page.

        Automatically increments the version number.

        Returns:
            dict with updated page metadata.
        """
        # Get current version
        current = self.get_page(page_id)
        current_version = current["version"]["number"]

        payload = {
            "type": "page",
            "title": title,
            "version": {"number": current_version + 1},
            "body": {
                "storage": {
                    "value": body_html,
                    "representation": "storage",
                },
            },
        }

        return self._request("PUT", f"content/{page_id}", json=payload)

    def get_page_url(self, page_data: dict) -> str:
        """Extract the web URL from a page response dict."""
        base = self.base_url.replace("/rest/api", "")
        return base + page_data.get("_links", {}).get("webui", "")

    # ── Report Publishing ─────────────────────

    def publish_report(
        self,
        title: str,
        report_text: str,
        metrics_summary: str = "",
        parent_page_id: Optional[str] = None,
    ) -> str:
        """Publish a QA report as a Confluence page.

        Args:
            title: Report page title.
            report_text: Full report narrative (plain text/markdown).
            metrics_summary: One-line summary for the info panel.
            parent_page_id: Where to nest the page.

        Returns:
            URL of the published Confluence page.
        """
        # Build storage-format HTML
        html_parts = [f"<h1>{title}</h1>"]

        if metrics_summary:
            html_parts.append(
                '<ac:structured-macro ac:name="info">'
                "<ac:rich-text-body>"
                f"<p>{metrics_summary}</p>"
                "</ac:rich-text-body>"
                "</ac:structured-macro>"
            )

        # Convert markdown-like report to basic HTML
        html_body = self._report_to_html(report_text)
        html_parts.append(html_body)

        html_parts.append(
            "<hr/>"
            f"<p><em>Auto-generated by QA AI Automation pipeline</em></p>"
        )

        full_html = "\n".join(html_parts)
        page = self.create_page(
            title=title,
            body_html=full_html,
            parent_page_id=parent_page_id or config.QA_REPORTS_PARENT_PAGE_ID,
        )

        return self.get_page_url(page)

    # ── Helpers ───────────────────────────────

    @staticmethod
    def _html_to_text(html: str) -> str:
        """Strip HTML tags and return clean text."""
        soup = BeautifulSoup(html, "html.parser")

        # Remove script and style elements
        for element in soup(["script", "style"]):
            element.decompose()

        text = soup.get_text(separator="\n")
        # Collapse multiple newlines
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def _report_to_html(report_text: str) -> str:
        """Convert a structured report text to basic HTML.

        Handles markdown-style headings, bullet points, and tables.
        """
        lines = report_text.split("\n")
        html_lines = []

        for line in lines:
            stripped = line.strip()

            if not stripped:
                html_lines.append("<br/>")
            elif stripped.startswith("# "):
                html_lines.append(f"<h2>{stripped[2:]}</h2>")
            elif stripped.startswith("## "):
                html_lines.append(f"<h3>{stripped[3:]}</h3>")
            elif stripped.startswith("### "):
                html_lines.append(f"<h4>{stripped[4:]}</h4>")
            elif stripped.startswith("- ") or stripped.startswith("• "):
                html_lines.append(f"<li>{stripped[2:]}</li>")
            elif stripped.startswith("|"):
                # Basic table row passthrough
                html_lines.append(f"<p><code>{stripped}</code></p>")
            elif stripped.startswith("✅") or stripped.startswith("⚠️") or stripped.startswith("❌"):
                html_lines.append(
                    f'<p><strong>{stripped}</strong></p>'
                )
            else:
                html_lines.append(f"<p>{stripped}</p>")

        return "\n".join(html_lines)
