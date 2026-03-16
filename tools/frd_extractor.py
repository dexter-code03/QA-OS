"""
QA AI Automation — Stage 1 & 2: FRD → Test Plan Pipeline
Reads FRD from Confluence, extracts ACs via Claude, generates test plan,
pushes to Jira and Confluence.
"""

import json
from typing import Optional
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config
from tools.confluence_client import ConfluenceClient
from tools.jira_client import JiraClient
from tools.ai_client import ai_chat_json


# ═════════════════════════════════════════════════
# Claude System Prompts
# ═════════════════════════════════════════════════

EXTRACT_REQUIREMENTS_PROMPT = """You are a senior QA engineer with 10+ years of experience.
Read the FRD (Functional Requirements Document) and extract EVERY testable requirement.

For each requirement, output:
{
  "user_stories": [
    {
      "id": "US-001",
      "as_a": "...",
      "i_want": "...",
      "so_that": "...",
      "acceptance_criteria": [
        {
          "id": "AC-001",
          "given": "...",
          "when": "...",
          "then": "...",
          "edge_cases": ["null input", "max length", "special characters", "concurrent access"],
          "risk_level": "Critical | High | Medium | Low",
          "test_types": ["Functional", "Negative", "Edge", "Performance", "Security", "Regression"]
        }
      ]
    }
  ],
  "ambiguous_requirements": [
    {
      "ac_id": "AC-003",
      "issue": "Does not specify error message text — confirm with PM",
      "question": "What exact error message should be shown when X happens?"
    }
  ],
  "missing_coverage": [
    "No requirement covers behaviour when user is already logged in and visits /login",
    "Password expiry flow not mentioned"
  ],
  "risk_summary": [
    "Session handling — if misconfigured, auth bypass is possible",
    "Rate limiting not addressed in FRD"
  ]
}

Rules:
- Be pessimistic — assume every input can be null, every API can fail
- Flag EVERY ambiguous AC as a clarification question
- Look for implicit requirements the FRD doesn't spell out but should
- Assign risk based on business impact: auth/payment = Critical, UI polish = Low
- Return ONLY valid JSON. No preamble, no markdown fencing."""


GENERATE_TEST_PLAN_PROMPT = """You are a senior QA engineer generating a COMPLETE test plan.

For every acceptance criterion, produce ALL necessary test cases:

{
  "test_cases": [
    {
      "id": "TC-[FEATURE]-[NUMBER]",
      "title": "one clear sentence describing what is tested",
      "priority": "Critical | High | Medium | Low",
      "type": "Functional | Negative | Edge | Performance | Security | Regression",
      "preconditions": ["User account exists", "User is on /login"],
      "steps": ["Enter valid email: user@test.com", "Enter valid password", "Click Submit"],
      "expected_result": "Redirected to /dashboard, auth token set in cookie",
      "test_data": {"email": "user@test.com", "password": "Pass1!"},
      "automatable": true,
      "automation_notes": "Standard WebUI flow, all selectors known",
      "linked_ac": "AC-004"
    }
  ],
  "coverage_matrix": {
    "AC-001": ["TC-LOGIN-001", "TC-LOGIN-002", "TC-LOGIN-003"],
    "AC-002": ["TC-LOGIN-004", "TC-LOGIN-005"]
  },
  "test_summary": {
    "total_test_cases": 25,
    "by_priority": {"Critical": 5, "High": 8, "Medium": 10, "Low": 2},
    "by_type": {"Functional": 10, "Negative": 6, "Edge": 4, "Performance": 3, "Security": 2},
    "automatable_count": 22,
    "manual_only_count": 3
  }
}

Rules:
- Generate happy path, negative, edge/boundary, security, and regression cases
- Use CONCRETE test data — actual values, not placeholders like "invalid email"
- Each test must have a unique ID following the pattern TC-[FEATURE]-[NNN]
- Flag steps that need manual verification with automatable: false
- Return ONLY valid JSON."""


# ═════════════════════════════════════════════════
# Core Functions
# ═════════════════════════════════════════════════

def fetch_frd(page_id: Optional[str] = None) -> str:
    """Fetch FRD text content from Confluence.

    Args:
        page_id: Confluence page ID. Defaults to config.FRD_PAGE_ID.

    Returns:
        Plain text content of the FRD page.
    """
    confluence = ConfluenceClient()
    pid = page_id or config.FRD_PAGE_ID
    print(f"📄 Fetching FRD from Confluence (page {pid})...")
    text = confluence.get_page_body_text(pid)
    print(f"   → {len(text)} characters retrieved")
    return text


def extract_requirements(frd_text: str) -> dict:
    """Use Claude to extract structured requirements from FRD text.

    Args:
        frd_text: Raw text content of the FRD.

    Returns:
        dict with user_stories, ambiguous_requirements, missing_coverage, risk_summary.
    """
    print("🤖 Extracting requirements via Claude...")

    result = ai_chat_json(
        EXTRACT_REQUIREMENTS_PROMPT,
        f"Extract all testable requirements from this FRD:\n\n{frd_text}",
    )
    stories = result.get("user_stories", [])
    acs = sum(len(s.get("acceptance_criteria", [])) for s in stories)
    ambiguous = len(result.get("ambiguous_requirements", []))
    gaps = len(result.get("missing_coverage", []))

    print(f"   → {len(stories)} user stories, {acs} ACs extracted")
    print(f"   → {ambiguous} ambiguous items flagged, {gaps} coverage gaps found")

    return result


def generate_test_plan(acceptance_criteria: list[dict]) -> dict:
    """Generate a complete test plan from acceptance criteria.

    Args:
        acceptance_criteria: List of AC dicts (with given/when/then).

    Returns:
        dict with test_cases, coverage_matrix, test_summary.
    """
    print("📋 Generating test plan via Claude...")

    result = ai_chat_json(
        GENERATE_TEST_PLAN_PROMPT,
        "Generate a complete test plan for these acceptance criteria:\n\n"
        + json.dumps(acceptance_criteria, indent=2),
    )
    tc_count = len(result.get("test_cases", []))
    print(f"   → {tc_count} test cases generated")

    return result


def push_to_jira(test_plan: dict, epic_key: str) -> list[str]:
    """Push test plan to Jira as individual stories linked to an epic.

    Args:
        test_plan: Output from generate_test_plan().
        epic_key: Jira epic key to link stories to (e.g. "QA-100").

    Returns:
        List of created Jira issue keys.
    """
    jira = JiraClient()
    created = []
    test_cases = test_plan.get("test_cases", [])

    print(f"📤 Pushing {len(test_cases)} test cases to Jira under {epic_key}...")

    for tc in test_cases:
        # Build structured description
        desc_sections = [
            {"type": "heading", "text": "Test Details", "level": 3},
            {"type": "paragraph", "text": f"ID: {tc['id']}  |  Type: {tc['type']}  |  Priority: {tc['priority']}"},
            {"type": "heading", "text": "Preconditions", "level": 3},
            {"type": "list", "items": tc.get("preconditions", ["None"])},
            {"type": "heading", "text": "Steps", "level": 3},
            {"type": "list", "items": tc.get("steps", []), "ordered": True},
            {"type": "heading", "text": "Expected Result", "level": 3},
            {"type": "paragraph", "text": tc.get("expected_result", "")},
        ]

        if tc.get("test_data"):
            desc_sections.extend([
                {"type": "heading", "text": "Test Data", "level": 3},
                {"type": "code", "text": json.dumps(tc["test_data"], indent=2), "language": "json"},
            ])

        if tc.get("automation_notes"):
            desc_sections.extend([
                {"type": "heading", "text": "Automation Notes", "level": 3},
                {"type": "paragraph", "text": tc["automation_notes"]},
            ])

        description_adf = JiraClient.build_adf_document(desc_sections)

        try:
            result = jira.create_issue(
                summary=f"[{tc['id']}] {tc['title']}",
                description_adf=description_adf,
                issue_type=config.JIRA_STORY_ISSUE_TYPE,
                priority=tc.get("priority", "Medium"),
                labels=[tc.get("type", "Functional"), "ai-generated"],
                epic_key=epic_key,
            )
            created.append(result["key"])
        except Exception as e:
            print(f"   ⚠️  Failed to create ticket for {tc['id']}: {e}")

    print(f"   → {len(created)} Jira tickets created")

    # Post coverage summary and flags as a comment on the epic
    if test_plan.get("test_summary"):
        summary_text = json.dumps(test_plan["test_summary"], indent=2)
        jira.add_comment_text(
            epic_key,
            f"📊 Test Plan Summary:\n{summary_text}\n\n"
            f"Test cases created: {', '.join(created)}",
        )

    return created


def publish_test_plan(
    test_plan: dict,
    requirements: dict,
    parent_page_id: Optional[str] = None,
    title: Optional[str] = None,
) -> str:
    """Publish the complete test plan as a Confluence page.

    Args:
        test_plan: Output from generate_test_plan().
        requirements: Output from extract_requirements().
        parent_page_id: Confluence parent page ID.
        title: Page title.

    Returns:
        URL of the published Confluence page.
    """
    confluence = ConfluenceClient()

    page_title = title or "AI-Generated Test Plan"
    test_cases = test_plan.get("test_cases", [])
    summary = test_plan.get("test_summary", {})

    # Build the page HTML
    html_parts = [
        f"<h1>{page_title}</h1>",
        '<ac:structured-macro ac:name="info"><ac:rich-text-body>',
        f"<p>Total test cases: <strong>{summary.get('total_test_cases', len(test_cases))}</strong> | "
        f"Automatable: <strong>{summary.get('automatable_count', 'N/A')}</strong></p>",
        "</ac:rich-text-body></ac:structured-macro>",
    ]

    # Ambiguous requirements section
    ambiguous = requirements.get("ambiguous_requirements", [])
    if ambiguous:
        html_parts.append("<h2>⚠️ Clarification Questions</h2><ul>")
        for item in ambiguous:
            html_parts.append(
                f"<li><strong>{item['ac_id']}</strong>: {item['question']}</li>"
            )
        html_parts.append("</ul>")

    # Missing coverage
    gaps = requirements.get("missing_coverage", [])
    if gaps:
        html_parts.append("<h2>🔍 Coverage Gaps</h2><ul>")
        for gap in gaps:
            html_parts.append(f"<li>{gap}</li>")
        html_parts.append("</ul>")

    # Test cases table
    html_parts.append('<h2>Test Cases</h2>')
    html_parts.append(
        '<table><tr><th>ID</th><th>Title</th><th>Priority</th>'
        '<th>Type</th><th>Automatable</th><th>Linked AC</th></tr>'
    )
    for tc in test_cases:
        html_parts.append(
            f"<tr><td>{tc['id']}</td><td>{tc['title']}</td>"
            f"<td>{tc['priority']}</td><td>{tc['type']}</td>"
            f"<td>{'✅' if tc.get('automatable') else '❌'}</td>"
            f"<td>{tc.get('linked_ac', '')}</td></tr>"
        )
    html_parts.append("</table>")

    # Risk summary
    risks = requirements.get("risk_summary", [])
    if risks:
        html_parts.append("<h2>⚡ Risk Areas</h2><ul>")
        for risk in risks:
            html_parts.append(f"<li>{risk}</li>")
        html_parts.append("</ul>")

    html_parts.append(
        "<hr/><p><em>Auto-generated by QA AI Automation pipeline</em></p>"
    )

    page = confluence.create_page(
        title=page_title,
        body_html="\n".join(html_parts),
        parent_page_id=parent_page_id or config.QA_REPORTS_PARENT_PAGE_ID,
    )

    url = confluence.get_page_url(page)
    print(f"📄 Test plan published: {url}")
    return url


# ═════════════════════════════════════════════════
# Full Pipeline Orchestrator
# ═════════════════════════════════════════════════

def run_frd_to_test_plan(
    page_id: Optional[str] = None,
    epic_key: Optional[str] = None,
    publish: bool = True,
) -> dict:
    """Run the complete FRD → Test Plan pipeline.

    1. Fetch FRD from Confluence
    2. Extract requirements via Claude
    3. Generate test plan via Claude
    4. Push test cases to Jira (if epic_key provided)
    5. Publish test plan to Confluence (if publish=True)

    Args:
        page_id: Confluence FRD page ID.
        epic_key: Jira epic key to link test stories to.
        publish: Whether to publish the test plan to Confluence.

    Returns:
        dict with keys: requirements, test_plan, jira_tickets, confluence_url
    """
    print("\n" + "=" * 60)
    print("🚀 Stage 1–2: FRD → Test Plan Pipeline")
    print("=" * 60)

    # Step 1: Fetch FRD
    frd_text = fetch_frd(page_id)

    # Step 2: Extract requirements
    requirements = extract_requirements(frd_text)

    # Step 3: Flatten all ACs for test plan generation
    all_acs = []
    for story in requirements.get("user_stories", []):
        for ac in story.get("acceptance_criteria", []):
            ac["story_id"] = story.get("id", "")
            ac["story_title"] = story.get("i_want", "")
            all_acs.append(ac)

    # Step 4: Generate test plan
    test_plan = generate_test_plan(all_acs)

    result = {
        "requirements": requirements,
        "test_plan": test_plan,
        "jira_tickets": [],
        "confluence_url": None,
    }

    # Step 5: Push to Jira
    if epic_key:
        result["jira_tickets"] = push_to_jira(test_plan, epic_key)

    # Step 6: Publish to Confluence
    if publish:
        result["confluence_url"] = publish_test_plan(
            test_plan, requirements, title="AI Test Plan — " + (epic_key or "Draft"),
        )

    print("\n✅ FRD → Test Plan pipeline complete!")
    print(f"   Test cases: {len(test_plan.get('test_cases', []))}")
    print(f"   Jira tickets: {len(result['jira_tickets'])}")
    print(f"   Confluence: {result['confluence_url'] or 'Not published'}")

    return result


# ═════════════════════════════════════════════════
# CLI Entry Point
# ═════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="FRD → Test Plan Pipeline")
    parser.add_argument("--page-id", default=None, help="Confluence FRD page ID")
    parser.add_argument("--epic-key", default=None, help="Jira epic key to link to")
    parser.add_argument("--no-publish", action="store_true", help="Skip Confluence publishing")
    args = parser.parse_args()

    result = run_frd_to_test_plan(
        page_id=args.page_id,
        epic_key=args.epic_key,
        publish=not args.no_publish,
    )

    print("\n📊 Requirements JSON:")
    print(json.dumps(result["requirements"], indent=2)[:2000])
