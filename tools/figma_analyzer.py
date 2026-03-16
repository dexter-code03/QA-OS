"""
QA AI Automation — Stage 3: Figma → UI Test Cases
Analyses Figma designs via API and Claude vision to generate
pixel-accurate UI test cases and visual regression checks.
"""

import json
import base64
from typing import Optional
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config
from tools.ai_client import ai_chat_with_images_json

import httpx


# ═════════════════════════════════════════════════
# Claude System Prompts
# ═════════════════════════════════════════════════

UI_TEST_GENERATION_PROMPT = """You are a senior UI QA engineer.
You receive a Figma frame screenshot AND its design token JSON.
Generate complete UI test cases covering ALL of the following categories:

1. Element visibility and presence (every visible component)
2. Typography (font-family, font-size, font-weight, line-height, colour)
3. Colour accuracy (exact hex from design tokens)
4. Spacing (margin, padding — ±2px tolerance)
5. Interactive states (hover, focus, disabled, error, loading)
6. Responsive behaviour at 375px, 768px, 1440px
7. Accessibility (WCAG AA contrast, aria-labels, tab order)

For each test case output:
{
  "id": "TC-UI-[FEATURE]-[NNN]",
  "title": "one clear sentence",
  "priority": "Critical | High | Medium | Low",
  "type": "Visual | Typography | Colour | Spacing | Interactive | Responsive | Accessibility",
  "css_selector": "button[data-testid='login-submit']",
  "assertion": {
    "property": "background-color",
    "expected": "rgb(79, 70, 229)",
    "tolerance": null
  },
  "viewport": "all | 375px | 768px | 1440px",
  "automatable": true
}

Rules:
- Use real CSS selectors from Figma component names (convert to kebab-case)
- Include exact values from the design tokens — no approximations
- Flag selectors you are uncertain about with: "selector_confidence": "low"
- Group related test cases by component
- Return ONLY valid JSON with key "test_cases" as an array."""


VISUAL_REGRESSION_PROMPT = """You are a visual QA specialist comparing a design spec against a live build.

Image 1 is the FIGMA DESIGN SPEC (the source of truth).
Image 2 is the LIVE BUILT UI (what was actually deployed).

Compare them element by element and list EVERY visual difference.

For each difference output:
{
  "element": "Primary CTA button",
  "difference": "Button padding is 12px in build vs 16px in design",
  "severity": "Critical | High | Medium | Low",
  "likely_css_cause": "padding shorthand missing px unit on right side",
  "category": "Spacing | Colour | Typography | Layout | Missing Element | Extra Element"
}

Rules:
- Be thorough — check every visible element
- Critical: missing elements, wrong text, broken layout
- High: wrong colours, wrong font size
- Medium: spacing off by >4px, wrong font weight
- Low: spacing off by 1-3px, sub-pixel rendering differences
- Return ONLY valid JSON with key "differences" as an array.
- If no differences found, return: {"differences": [], "verdict": "PIXEL PERFECT"}"""


# ═════════════════════════════════════════════════
# Figma API Functions
# ═════════════════════════════════════════════════

def get_figma_data(file_key: str, frame_id: str) -> tuple[dict, str]:
    """Fetch design tokens and PNG export from Figma.

    Args:
        file_key: Figma file key (from URL).
        frame_id: Node ID of the frame to analyse.

    Returns:
        Tuple of (design_tokens_dict, png_url_string).
    """
    headers = {"X-Figma-Token": config.FIGMA_TOKEN}
    base = "https://api.figma.com/v1"

    print(f"🎨 Fetching Figma data (file: {file_key}, frame: {frame_id})...")

    # Get design token tree
    nodes_resp = httpx.get(
        f"{base}/files/{file_key}/nodes",
        params={"ids": frame_id},
        headers=headers,
    )
    nodes_resp.raise_for_status()
    nodes = nodes_resp.json()
    design_tokens = nodes["nodes"][frame_id]["document"]

    # Get rendered PNG
    images_resp = httpx.get(
        f"{base}/images/{file_key}",
        params={"ids": frame_id, "format": "png", "scale": 2},
        headers=headers,
    )
    images_resp.raise_for_status()
    png_url = images_resp.json()["images"][frame_id]

    print(f"   → Design tokens retrieved ({len(json.dumps(design_tokens))} chars)")
    print(f"   → PNG export URL obtained")

    return design_tokens, png_url


def get_figma_styles(file_key: str) -> dict:
    """Fetch all styles (colors, typography, effects) from a Figma file.

    Returns:
        dict with 'styles' containing the full style definitions.
    """
    headers = {"X-Figma-Token": config.FIGMA_TOKEN}
    resp = httpx.get(
        f"https://api.figma.com/v1/files/{file_key}/styles",
        headers=headers,
    )
    resp.raise_for_status()
    return resp.json()


# ═════════════════════════════════════════════════
# Claude Vision Analysis
# ═════════════════════════════════════════════════

def generate_ui_tests(
    design_tokens: dict,
    png_url: str,
    feature_name: str,
) -> dict:
    """Generate UI test cases from Figma screenshot + design tokens.

    Uses Claude's vision capability to analyse the UI and produce
    test cases with exact CSS property assertions.

    Args:
        design_tokens: Figma node tree with component details.
        png_url: URL of the Figma frame PNG export.
        feature_name: Name of the feature for test case IDs.

    Returns:
        dict with 'test_cases' array.
    """
    print(f"🤖 Generating UI test cases for '{feature_name}'...")

    # Download the screenshot
    image_response = httpx.get(png_url)
    image_response.raise_for_status()

    result = ai_chat_with_images_json(
        UI_TEST_GENERATION_PROMPT,
        f"Feature: {feature_name}\n\nDesign tokens:\n{json.dumps(design_tokens, indent=2)[:8000]}",
        [image_response.content],
    )
    tc_count = len(result.get("test_cases", []))
    print(f"   → {tc_count} UI test cases generated")

    # Categorize results
    by_type = {}
    for tc in result.get("test_cases", []):
        t = tc.get("type", "Other")
        by_type[t] = by_type.get(t, 0) + 1
    for t, c in by_type.items():
        print(f"      {t}: {c}")

    return result


def visual_regression_check(
    figma_png_url: str,
    live_screenshot_b64: str,
) -> dict:
    """Compare a Figma design spec against a live build screenshot.

    Claude analyses both images and describes every visual difference
    element-by-element with severity and likely CSS cause.

    Args:
        figma_png_url: URL of the Figma frame PNG export.
        live_screenshot_b64: Base64-encoded screenshot of the live build.

    Returns:
        dict with 'differences' array (or 'verdict': 'PIXEL PERFECT').
    """
    print("🔍 Running visual regression check...")

    # Download Figma image
    figma_response = httpx.get(figma_png_url)
    figma_response.raise_for_status()

    # Decode live screenshot
    import base64
    live_bytes = base64.standard_b64decode(live_screenshot_b64)

    result = ai_chat_with_images_json(
        VISUAL_REGRESSION_PROMPT,
        "Image 1 is the Figma design spec. Image 2 is the live built UI. List every visual difference.",
        [figma_response.content, live_bytes],
    )
    diffs = result.get("differences", [])

    if diffs:
        print(f"   → {len(diffs)} differences found:")
        for d in diffs:
            print(f"      [{d['severity']}] {d['element']}: {d['difference']}")
    else:
        print(f"   → ✅ {result.get('verdict', 'No differences')}")

    return result


# ═════════════════════════════════════════════════
# Full Pipeline
# ═════════════════════════════════════════════════

def run_figma_analysis(
    file_key: Optional[str] = None,
    frame_id: Optional[str] = None,
    feature_name: str = "UI",
) -> dict:
    """Run the complete Figma → UI test case pipeline.

    1. Fetch design tokens and PNG from Figma
    2. Generate UI test cases via Claude vision

    Args:
        file_key: Figma file key.
        frame_id: Figma frame node ID.
        feature_name: Feature name for test case IDs.

    Returns:
        dict with keys: design_tokens, png_url, ui_tests
    """
    print("\n" + "=" * 60)
    print("🚀 Stage 3: Figma → UI Test Cases Pipeline")
    print("=" * 60)

    fk = file_key or config.FIGMA_FILE_KEY
    if not fk or not frame_id:
        raise ValueError("Figma file_key and frame_id are required")

    design_tokens, png_url = get_figma_data(fk, frame_id)
    ui_tests = generate_ui_tests(design_tokens, png_url, feature_name)

    print(f"\n✅ Figma analysis complete!")
    print(f"   UI test cases: {len(ui_tests.get('test_cases', []))}")

    return {
        "design_tokens": design_tokens,
        "png_url": png_url,
        "ui_tests": ui_tests,
    }


# ═════════════════════════════════════════════════
# CLI Entry Point
# ═════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Figma → UI Test Cases")
    parser.add_argument("--file-key", required=True, help="Figma file key")
    parser.add_argument("--frame-id", required=True, help="Figma frame node ID")
    parser.add_argument("--feature", default="UI", help="Feature name for test IDs")
    args = parser.parse_args()

    result = run_figma_analysis(
        file_key=args.file_key,
        frame_id=args.frame_id,
        feature_name=args.feature,
    )

    print("\n📊 UI Test Cases:")
    print(json.dumps(result["ui_tests"], indent=2)[:3000])
