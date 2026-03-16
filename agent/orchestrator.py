"""
QA AI Automation — Agent Orchestrator
Claude tool-calling agent that chains all QA tools autonomously.
Receives high-level goals and decides which tools to call, in what order.
"""

import json
from typing import Optional
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config
from tools.ai_client import _get_provider
from tools.frd_extractor import (
    fetch_frd, extract_requirements, generate_test_plan,
    push_to_jira, publish_test_plan,
)
from tools.figma_analyzer import (
    get_figma_data, generate_ui_tests, visual_regression_check,
)
from tools.katalon_generator import (
    generate_katalon_tests, generate_custom_keywords,
    save_to_katalon_project,
)
from tools.bug_triage import classify_failures, create_bug_ticket, run_bug_pipeline
from tools.signoff_report import run_signoff_pipeline
from tools.jira_client import JiraClient
from tools.confluence_client import ConfluenceClient
from tools.slack_notifier import SlackNotifier
from agent.escalation import EscalationEngine, Action


# ═════════════════════════════════════════════════
# Tool Definitions (Claude Tool-Calling Schema)
# ═════════════════════════════════════════════════

TOOLS = [
    {
        "name": "fetch_frd",
        "description": "Fetch a Functional Requirements Document from Confluence by page ID. Returns the full text content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "page_id": {
                    "type": "string",
                    "description": "Confluence page ID of the FRD",
                },
            },
            "required": ["page_id"],
        },
    },
    {
        "name": "extract_requirements",
        "description": "Extract user stories, acceptance criteria (Given/When/Then), edge cases, risk levels, and flag ambiguous requirements from FRD text. Returns structured JSON.",
        "input_schema": {
            "type": "object",
            "properties": {
                "frd_text": {
                    "type": "string",
                    "description": "Raw text content of the FRD document",
                },
            },
            "required": ["frd_text"],
        },
    },
    {
        "name": "generate_test_plan",
        "description": "Generate a complete test plan with test cases, coverage matrix, and summary from acceptance criteria.",
        "input_schema": {
            "type": "object",
            "properties": {
                "acceptance_criteria": {
                    "type": "string",
                    "description": "JSON string of acceptance criteria array",
                },
            },
            "required": ["acceptance_criteria"],
        },
    },
    {
        "name": "push_test_plan_to_jira",
        "description": "Create Jira stories for each test case in a test plan, linked to an epic.",
        "input_schema": {
            "type": "object",
            "properties": {
                "test_plan": {
                    "type": "string",
                    "description": "JSON string of the test plan",
                },
                "epic_key": {
                    "type": "string",
                    "description": "Jira epic key to link stories to (e.g. QA-100)",
                },
            },
            "required": ["test_plan", "epic_key"],
        },
    },
    {
        "name": "fetch_figma_design",
        "description": "Fetch design tokens and PNG screenshot from a Figma frame.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_key": {
                    "type": "string",
                    "description": "Figma file key from the URL",
                },
                "frame_id": {
                    "type": "string",
                    "description": "Figma frame node ID",
                },
            },
            "required": ["file_key", "frame_id"],
        },
    },
    {
        "name": "generate_ui_tests",
        "description": "Generate UI test cases from Figma design tokens and screenshot using Claude vision.",
        "input_schema": {
            "type": "object",
            "properties": {
                "design_tokens": {
                    "type": "string",
                    "description": "JSON string of Figma design tokens",
                },
                "png_url": {
                    "type": "string",
                    "description": "URL of the Figma frame PNG export",
                },
                "feature_name": {
                    "type": "string",
                    "description": "Feature name for test case IDs",
                },
            },
            "required": ["design_tokens", "png_url", "feature_name"],
        },
    },
    {
        "name": "generate_katalon_scripts",
        "description": "Generate Katalon Groovy test scripts, CSV test data, and custom keywords from acceptance criteria.",
        "input_schema": {
            "type": "object",
            "properties": {
                "acceptance_criteria": {
                    "type": "string",
                    "description": "JSON string of acceptance criteria",
                },
                "figma_tokens": {
                    "type": "string",
                    "description": "Optional JSON string of Figma tokens for selector generation",
                },
                "feature_name": {
                    "type": "string",
                    "description": "Feature name",
                },
            },
            "required": ["acceptance_criteria", "feature_name"],
        },
    },
    {
        "name": "run_bug_triage",
        "description": "Run AI bug triage on test results: classify failures, deduplicate, create Jira tickets, notify Slack.",
        "input_schema": {
            "type": "object",
            "properties": {
                "results_path": {
                    "type": "string",
                    "description": "Path to Katalon results JSON file",
                },
                "build_number": {
                    "type": "string",
                    "description": "CI build number",
                },
                "environment": {
                    "type": "string",
                    "description": "Test environment (staging/production)",
                },
            },
            "required": ["results_path"],
        },
    },
    {
        "name": "generate_signoff_report",
        "description": "Generate QA sign-off report, publish to Confluence, and post Slack digest.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sprint_id": {
                    "type": "string",
                    "description": "Sprint identifier",
                },
                "fix_version": {
                    "type": "string",
                    "description": "Release version string",
                },
            },
            "required": ["sprint_id", "fix_version"],
        },
    },
    {
        "name": "notify_slack",
        "description": "Send a message to the QA Slack channel.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Message text to send",
                },
            },
            "required": ["message"],
        },
    },
    {
        "name": "publish_to_confluence",
        "description": "Publish content as a new Confluence page.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Page title",
                },
                "content": {
                    "type": "string",
                    "description": "Page content (will be converted to HTML)",
                },
                "parent_page_id": {
                    "type": "string",
                    "description": "Parent page ID for hierarchy",
                },
            },
            "required": ["title", "content"],
        },
    },
]


# ═════════════════════════════════════════════════
# Tool Executor
# ═════════════════════════════════════════════════

def execute_tool(tool_name: str, tool_input: dict) -> str:
    """Execute a tool and return its result as a JSON string.

    Maps Claude's tool calls to actual function implementations.
    """
    try:
        if tool_name == "fetch_frd":
            result = fetch_frd(tool_input["page_id"])
            return json.dumps({"status": "success", "content": result[:8000]})

        elif tool_name == "extract_requirements":
            result = extract_requirements(tool_input["frd_text"])
            return json.dumps({"status": "success", "data": result})

        elif tool_name == "generate_test_plan":
            acs = json.loads(tool_input["acceptance_criteria"])
            result = generate_test_plan(acs)
            return json.dumps({"status": "success", "data": result})

        elif tool_name == "push_test_plan_to_jira":
            plan = json.loads(tool_input["test_plan"])
            tickets = push_to_jira(plan, tool_input["epic_key"])
            return json.dumps({"status": "success", "tickets": tickets})

        elif tool_name == "fetch_figma_design":
            tokens, png_url = get_figma_data(
                tool_input["file_key"],
                tool_input["frame_id"],
            )
            return json.dumps({
                "status": "success",
                "design_tokens": tokens,
                "png_url": png_url,
            })

        elif tool_name == "generate_ui_tests":
            tokens = json.loads(tool_input["design_tokens"])
            result = generate_ui_tests(
                tokens,
                tool_input["png_url"],
                tool_input["feature_name"],
            )
            return json.dumps({"status": "success", "data": result})

        elif tool_name == "generate_katalon_scripts":
            acs = json.loads(tool_input["acceptance_criteria"])
            figma = json.loads(tool_input.get("figma_tokens") or "{}") or None
            scripts = generate_katalon_tests(acs, figma)
            for s in scripts:
                s["feature_name"] = tool_input.get("feature_name", "Feature")
            saved = save_to_katalon_project(
                scripts,
                katalon_project_path=config.KATALON_PROJECT_PATH,
                suite_name="smoke",
            )
            return json.dumps({
                "status": "success",
                "scripts_written_to_katalon": len(scripts),
                "files": saved,
            })

        elif tool_name == "run_bug_triage":
            result = run_bug_pipeline(
                tool_input["results_path"],
                build_info={
                    "number": tool_input.get("build_number", "agent"),
                    "env": tool_input.get("environment", "staging"),
                    "epic_key": None,
                    "run_url": "",
                    "browser": "chrome",
                },
            )
            return json.dumps({"status": "success", "data": {
                k: v for k, v in result.items()
                if k != "classifications"
            }})

        elif tool_name == "generate_signoff_report":
            result = run_signoff_pipeline(
                tool_input["sprint_id"],
                tool_input["fix_version"],
            )
            return json.dumps({
                "status": "success",
                "verdict": result["verdict"],
                "confluence_url": result["confluence_url"],
            })

        elif tool_name == "notify_slack":
            slack = SlackNotifier()
            slack.post_message(tool_input["message"])
            return json.dumps({"status": "success", "sent": True})

        elif tool_name == "publish_to_confluence":
            confluence = ConfluenceClient()
            page = confluence.create_page(
                title=tool_input["title"],
                body_html=f"<p>{tool_input['content']}</p>",
                parent_page_id=tool_input.get("parent_page_id"),
            )
            url = confluence.get_page_url(page)
            return json.dumps({"status": "success", "url": url})

        else:
            return json.dumps({"status": "error", "message": f"Unknown tool: {tool_name}"})

    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


# ═════════════════════════════════════════════════
# Agent Core
# ═════════════════════════════════════════════════

AGENT_SYSTEM_PROMPT = """You are a QA Automation Agent. Your job is to handle QA tasks autonomously.

You have access to tools that connect to:
- Confluence (read FRDs, publish reports)
- Jira (create tickets, manage bugs)
- Figma (analyse designs)
- Katalon (generate test scripts)
- Slack (send notifications)
- Claude AI (generate test plans, classify bugs, write reports)

When given a goal:
1. Break it down into the required steps
2. Call tools in the correct order
3. Handle errors gracefully — retry once, then report the failure
4. Always notify Slack when a major action completes
5. Never make irreversible decisions without confirming via Slack first

Escalation rules you MUST follow:
- If pass rate drops below the threshold → escalate to QA lead
- If a Critical/Blocker bug is found → escalate immediately
- If an FRD has major ambiguities → flag them, don't assume
- If a retest fails twice → escalate to QA lead
- Everything else → handle autonomously

Be concise in your reasoning. Focus on actions, not explanations."""


class QAAgent:
    """AI-powered agent that orchestrates all QA automation tools.

    Supports both Gemini (free) and Claude (paid) as the AI backend.
    """

    def __init__(self):
        self.provider = _get_provider()
        self.escalation = EscalationEngine()
        self.max_iterations = 20

    def run(self, goal: str) -> dict:
        """Execute a goal by letting the AI decide which tools to call.

        Args:
            goal: High-level goal description.

        Returns:
            dict with 'summary', 'actions_taken', 'result'.
        """
        print("\n" + "=" * 60)
        print(f"🤖 Agent Goal: {goal}")
        print(f"   Provider: {self.provider.capitalize()}")
        print("=" * 60)

        if self.provider == "gemini":
            return self._run_gemini(goal)
        else:
            return self._run_claude(goal)

    def _run_gemini(self, goal: str) -> dict:
        """Run agent loop using Gemini function calling."""
        import google.generativeai as genai

        genai.configure(api_key=config.GEMINI_API_KEY)

        # Convert tool schemas to Gemini format
        gemini_tools = []
        for tool in TOOLS:
            gemini_tools.append(genai.protos.Tool(
                function_declarations=[genai.protos.FunctionDeclaration(
                    name=tool["name"],
                    description=tool["description"],
                    parameters=genai.protos.Schema(
                        type=genai.protos.Type.OBJECT,
                        properties={
                            k: genai.protos.Schema(type=genai.protos.Type.STRING, description=v.get("description", ""))
                            for k, v in tool["input_schema"]["properties"].items()
                        },
                        required=tool["input_schema"].get("required", []),
                    ),
                )],
            ))

        model = genai.GenerativeModel(
            model_name=config.GEMINI_MODEL,
            system_instruction=AGENT_SYSTEM_PROMPT,
            tools=gemini_tools,
        )

        chat = model.start_chat()
        actions_taken = []
        iteration = 0

        response = chat.send_message(goal)

        while iteration < self.max_iterations:
            iteration += 1
            print(f"\n--- Agent iteration {iteration} ---")

            # Check for function calls
            has_function_call = False
            for part in response.parts:
                if part.function_call:
                    has_function_call = True
                    fn = part.function_call
                    tool_name = fn.name
                    tool_input = dict(fn.args)

                    print(f"   🔧 Calling: {tool_name}")
                    result = execute_tool(tool_name, tool_input)

                    actions_taken.append({
                        "tool": tool_name,
                        "input": tool_input,
                        "result_preview": result[:200],
                    })

                    # Send function response back
                    response = chat.send_message(
                        genai.protos.Content(
                            parts=[genai.protos.Part(
                                function_response=genai.protos.FunctionResponse(
                                    name=tool_name,
                                    response={"result": result},
                                )
                            )]
                        )
                    )

            if not has_function_call:
                # No function calls — agent is done
                final_text = response.text if response.text else "Agent completed."
                print(f"\n✅ Agent completed in {iteration} iterations")
                print(f"   Actions taken: {len(actions_taken)}")
                return {
                    "summary": final_text,
                    "actions_taken": actions_taken,
                    "iterations": iteration,
                    "status": "completed",
                }

        return {
            "summary": "Agent reached max iterations",
            "actions_taken": actions_taken,
            "iterations": iteration,
            "status": "max_iterations_reached",
        }

    def _run_claude(self, goal: str) -> dict:
        """Run agent loop using Claude tool calling."""
        import anthropic

        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        conversation = [{"role": "user", "content": goal}]
        actions_taken = []
        iteration = 0

        while iteration < self.max_iterations:
            iteration += 1
            print(f"\n--- Agent iteration {iteration} ---")

            response = client.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=config.AI_MAX_TOKENS,
                system=AGENT_SYSTEM_PROMPT,
                tools=TOOLS,
                messages=conversation,
            )

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        tool_name = block.name
                        tool_input = block.input
                        print(f"   🔧 Calling: {tool_name}")
                        result = execute_tool(tool_name, tool_input)
                        actions_taken.append({
                            "tool": tool_name,
                            "input": tool_input,
                            "result_preview": result[:200],
                        })
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })

                conversation.append({"role": "assistant", "content": response.content})
                conversation.append({"role": "user", "content": tool_results})

            elif response.stop_reason == "end_turn":
                final_text = ""
                for block in response.content:
                    if hasattr(block, "text"):
                        final_text += block.text
                print(f"\n✅ Agent completed in {iteration} iterations")
                return {
                    "summary": final_text,
                    "actions_taken": actions_taken,
                    "iterations": iteration,
                    "status": "completed",
                }
            else:
                break

        return {
            "summary": "Agent reached max iterations",
            "actions_taken": actions_taken,
            "iterations": iteration,
            "status": "max_iterations_reached",
        }


# ═════════════════════════════════════════════════
# Pre-built Goal Templates
# ═════════════════════════════════════════════════

def handle_new_frd(
    confluence_page_id: str,
    epic_key: Optional[str] = None,
    figma_file_key: Optional[str] = None,
    figma_frame_id: Optional[str] = None,
) -> dict:
    """Pre-built goal: Handle a new FRD publication end-to-end.

    The agent will:
    1. Fetch the FRD from Confluence
    2. Extract acceptance criteria
    3. Generate test plan
    4. Optionally analyse Figma designs
    5. Generate Katalon scripts
    6. Push everything to Jira
    7. Publish test plan to Confluence
    8. Notify Slack
    """
    agent = QAAgent()

    goal = (
        f"A new FRD has been published in Confluence (page ID: {confluence_page_id}). "
        f"Handle the complete QA setup:\n"
        f"1. Fetch the FRD content\n"
        f"2. Extract all acceptance criteria\n"
        f"3. Generate a complete test plan\n"
    )

    if epic_key:
        goal += f"4. Push all test cases to Jira under epic {epic_key}\n"

    if figma_file_key and figma_frame_id:
        goal += (
            f"5. Fetch the Figma design (file: {figma_file_key}, frame: {figma_frame_id})\n"
            f"6. Generate UI test cases from the design\n"
            f"7. Generate Katalon scripts including UI tests\n"
        )
    else:
        goal += "5. Generate Katalon scripts from the test plan\n"

    goal += (
        f"8. Publish the test plan to Confluence\n"
        f"9. Notify Slack that QA setup is complete\n"
    )

    return agent.run(goal)


def handle_test_results(
    results_path: str,
    sprint_id: Optional[str] = None,
    fix_version: Optional[str] = None,
) -> dict:
    """Pre-built goal: Handle test results after a CI run.

    The agent will:
    1. Run bug triage on the results
    2. Create Jira tickets for real bugs
    3. Post Slack notifications
    4. Optionally generate sign-off report
    """
    agent = QAAgent()

    goal = (
        f"Test execution just completed. Results are at: {results_path}\n"
        f"1. Run bug triage on the results\n"
        f"2. Create Jira tickets for any real bugs found\n"
        f"3. Post a summary to Slack\n"
    )

    if sprint_id and fix_version:
        goal += (
            f"4. Generate the QA sign-off report for sprint {sprint_id}, "
            f"version {fix_version}\n"
            f"5. Publish the report to Confluence\n"
        )

    return agent.run(goal)


# ═════════════════════════════════════════════════
# CLI Entry Point
# ═════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="QA AI Agent Orchestrator")
    subparsers = parser.add_subparsers(dest="command", help="Agent command")

    # Custom goal
    goal_parser = subparsers.add_parser("goal", help="Run a custom goal")
    goal_parser.add_argument("goal_text", help="Goal description")

    # New FRD
    frd_parser = subparsers.add_parser("new-frd", help="Handle a new FRD")
    frd_parser.add_argument("--page-id", required=True, help="Confluence page ID")
    frd_parser.add_argument("--epic-key", default=None, help="Jira epic key")
    frd_parser.add_argument("--figma-file", default=None, help="Figma file key")
    frd_parser.add_argument("--figma-frame", default=None, help="Figma frame ID")

    # Test results
    results_parser = subparsers.add_parser("results", help="Handle test results")
    results_parser.add_argument("--results-path", required=True, help="Results JSON path")
    results_parser.add_argument("--sprint-id", default=None, help="Sprint ID")
    results_parser.add_argument("--fix-version", default=None, help="Fix version")

    args = parser.parse_args()

    if args.command == "goal":
        agent = QAAgent()
        result = agent.run(args.goal_text)
    elif args.command == "new-frd":
        result = handle_new_frd(
            args.page_id, args.epic_key,
            args.figma_file, args.figma_frame,
        )
    elif args.command == "results":
        result = handle_test_results(
            args.results_path, args.sprint_id, args.fix_version,
        )
    else:
        parser.print_help()
        exit(1)

    print(f"\n📊 Agent Result:")
    print(json.dumps(result, indent=2, default=str)[:3000])
