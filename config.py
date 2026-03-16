"""
QA AI Automation — Central Configuration
Loads all API tokens, base URLs, project keys, and thresholds from .env
"""

import os
from dotenv import load_dotenv

load_dotenv()


# ──────────────────────────────────────────────
# AI Provider (Gemini = free, Claude = paid)
# If GEMINI_API_KEY is set, Gemini is used. Otherwise Claude.
# ──────────────────────────────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")

AI_MAX_TOKENS = int(os.getenv("AI_MAX_TOKENS", "8192"))


# ──────────────────────────────────────────────
# Jira
# ──────────────────────────────────────────────
JIRA_BASE_URL = os.getenv("JIRA_BASE_URL", "")          # e.g. https://yourco.atlassian.net
JIRA_EMAIL = os.getenv("JIRA_EMAIL", "")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN", "")
JIRA_PROJECT_KEY = os.getenv("JIRA_PROJECT_KEY", "QA")
JIRA_BUG_ISSUE_TYPE = os.getenv("JIRA_BUG_ISSUE_TYPE", "Bug")
JIRA_STORY_ISSUE_TYPE = os.getenv("JIRA_STORY_ISSUE_TYPE", "Story")
JIRA_EPIC_LINK_FIELD = os.getenv("JIRA_EPIC_LINK_FIELD", "customfield_10014")


# ──────────────────────────────────────────────
# Confluence
# ──────────────────────────────────────────────
CONFLUENCE_BASE_URL = os.getenv("CONFLUENCE_BASE_URL", "")  # e.g. https://yourco.atlassian.net/wiki
CONFLUENCE_EMAIL = os.getenv("CONFLUENCE_EMAIL", "")
CONFLUENCE_API_TOKEN = os.getenv("CONFLUENCE_API_TOKEN", "")
CONFLUENCE_SPACE_KEY = os.getenv("CONFLUENCE_SPACE_KEY", "QA")
QA_REPORTS_PARENT_PAGE_ID = os.getenv("QA_REPORTS_PARENT_PAGE_ID", "")
FRD_PAGE_ID = os.getenv("FRD_PAGE_ID", "")


# ──────────────────────────────────────────────
# Figma
# ──────────────────────────────────────────────
FIGMA_TOKEN = os.getenv("FIGMA_TOKEN", "")
FIGMA_FILE_KEY = os.getenv("FIGMA_FILE_KEY", "")


# ──────────────────────────────────────────────
# Katalon TestOps
# ──────────────────────────────────────────────
KATALON_API_KEY = os.getenv("KATALON_API_KEY", "")
KATALON_PROJECT_ID = os.getenv("KATALON_PROJECT_ID", "")
KATALON_TESTOPS_URL = os.getenv("KATALON_TESTOPS_URL", "https://testops.katalon.io")


# ──────────────────────────────────────────────
# Slack
# ──────────────────────────────────────────────
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
SLACK_CHANNEL = os.getenv("SLACK_CHANNEL", "#qa-automation")


# ──────────────────────────────────────────────
# GitHub
# ──────────────────────────────────────────────
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "")               # e.g. yourco/your-repo


# ──────────────────────────────────────────────
# Thresholds & Rules
# ──────────────────────────────────────────────
PASS_RATE_THRESHOLD = float(os.getenv("PASS_RATE_THRESHOLD", "95.0"))
BLOCKER_MAX_OPEN = int(os.getenv("BLOCKER_MAX_OPEN", "0"))
STALE_BUG_HOURS_WARN = int(os.getenv("STALE_BUG_HOURS_WARN", "24"))
STALE_BUG_HOURS_ESCALATE = int(os.getenv("STALE_BUG_HOURS_ESCALATE", "48"))
STALE_BUG_HOURS_CRITICAL = int(os.getenv("STALE_BUG_HOURS_CRITICAL", "72"))
MAX_RETEST_FAILURES = int(os.getenv("MAX_RETEST_FAILURES", "2"))


# ──────────────────────────────────────────────
# Paths
# ──────────────────────────────────────────────
KATALON_PROJECT_PATH = os.getenv("KATALON_PROJECT_PATH", "")
SCRIPTS_OUTPUT_DIR = os.getenv("SCRIPTS_OUTPUT_DIR", "./generated_scripts")
REPORTS_DIR = os.getenv("REPORTS_DIR", "./reports")
