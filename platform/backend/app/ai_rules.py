"""Contextual rule injection for AI prompts.

Universal rules are always prepended. Contextual rules are injected only
when the XML context or platform signals that the corresponding failure
mode is possible. The model receives 2-5 targeted rules per generation,
never a flat wall of 20.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Universal rules — always prepended to every generation/fix prompt
# ---------------------------------------------------------------------------

UNIVERSAL_RULES = """
ALWAYS:
- Prefer clickable=true parent over non-clickable child for all tap steps.
- Add waitForVisible before every tap step.
- Use the EXACT resource-id as it appears in the XML — copy it verbatim, character for character.

SELECTOR SOURCE OF TRUTH:
- The XML/DOM CONTEXT is the ONLY valid source for selector values.
- Screenshots are for VISUAL UNDERSTANDING ONLY — to see layout, screen state, and flow.
- NEVER extract text visible in a screenshot to use as a selector value.
- If an element is visible in the screenshot but absent from the XML, you CANNOT target it — skip it or note it as unresolvable.
- Every selector.value you output MUST match verbatim against an attribute in the provided XML.

RESOURCE-ID FORMAT (CRITICAL):
- If the XML shows resource-id="home_button", use exactly "home_button" — do NOT add a package prefix.
- If the XML shows resource-id="com.example:id/home_button", use exactly "com.example:id/home_button".
- NEVER invent or guess a package name. NEVER add com.xxx:id/ to a resource-id that appears without a package prefix in the XML.
- For UiSelector: resourceId("home_button") NOT resourceId("com.any.package:id/home_button") — unless the XML actually has that prefix.

DATA DISCIPLINE (ZERO TOLERANCE):
- EVERY value typed into a field (emails, passwords, names, phones, OTPs, URLs, amounts, dates, codes) MUST be a ${variable_name} reference.
- The test_data / data_fixes object MUST contain the actual value for every ${variable}.
- If you generate type or clearAndType, the text field MUST use ${variable_name}. Raw literal values are REJECTED.
- If you generate assertText or assertTextContains, the expect field MUST use ${variable_name} for any dynamic data.

NEVER:
- Type into TextView or StaticText — these are labels, not inputs. Only EditText (Android) or TextField/SecureTextField (iOS) are editable.
- Type into a wrapper element (View, FrameLayout, etc.) that CONTAINS a child EditText — always target the child EditText with .childSelector().
- Generate assertText on password fields or SecureTextField elements — masked fields always return empty or dots.
- Hardcode test data (emails, phones, passwords, OTPs, URLs, names, amounts) directly in step text/expect fields.
- Invent package prefixes for resource-ids. Use EXACTLY what the XML shows.
"""

# ---------------------------------------------------------------------------
# Contextual rules — injected when specific signals are detected
# ---------------------------------------------------------------------------

INPUT_RULE = """
INPUT FIELDS: type and clearAndType must target editable elements only.
Android: android.widget.EditText | iOS: XCUIElementTypeTextField or XCUIElementTypeSecureTextField.
Never target a label next to a field — use the field itself."""

COMPOSE_RULE = """
SELECTOR STRATEGY: This screen uses Jetpack Compose.
Use -android uiautomator for ALL steps on this screen.
Format: new UiSelector().resourceId("<exact resource-id from XML>")
Standard id selectors are unreliable on Compose nodes."""

SWIFTUI_RULE = """
SWIFTUI SCREEN: All elements are XCUIElementTypeOther.
Use inferred_type annotations: input_field=type, button=tap, label=assertText only.
Use -ios predicate string for all selectors on this screen."""

SCROLL_RULE = """
SCROLLABLE SCREEN: A ScrollView is present.
Add a scroll step before waitForVisible if target may be below viewport.
Do not assume all XML elements are currently visible."""

KEYBOARD_RULE = """
KEYBOARD: After every type step, add hideKeyboard before the next tap.
The software keyboard covers the lower 40%% of the screen after input."""

WRAPPER_INPUT_RULE = """
WRAPPER vs INPUT: Many input fields have a WRAPPER element (View, FrameLayout, or custom)
containing a child EditText (Android) or TextField (iOS). NEVER type into the wrapper.
- Android: if resource-id belongs to a wrapper, use .childSelector(new UiSelector().className("android.widget.EditText")).
  Pattern: tap wrapper → type into child EditText → hideKeyboard.
- iOS: if the element is XCUIElementTypeOther, target the child XCUIElementTypeTextField or XCUIElementTypeSecureTextField.
- If the XML shows a resource-id element with class != EditText that CONTAINS a child EditText, always target the child."""

_INPUT_KEYWORDS = frozenset(["enter", "type", "fill", "input", "login", "sign", "write", "submit", "form"])


def build_contextual_rules(
    description: str,
    xml_context: str,
    platform: str,
    screen_type: str,
) -> list[str]:
    """Return 0-4 contextual rules based on current generation context."""
    rules: list[str] = []

    has_inputs = (
        "EditText" in xml_context
        or "TextField" in xml_context
        or any(k in description.lower() for k in _INPUT_KEYWORDS)
    )
    if has_inputs:
        rules.append(INPUT_RULE)
        rules.append(KEYBOARD_RULE)
        rules.append(WRAPPER_INPUT_RULE)

    if platform == "android" and screen_type == "compose":
        rules.append(COMPOSE_RULE)

    if platform in ("ios_sim", "ios") and screen_type == "swiftui":
        rules.append(SWIFTUI_RULE)

    if 'scrollable="true"' in xml_context or "ScrollView" in xml_context:
        rules.append(SCROLL_RULE)

    priority_order = [COMPOSE_RULE, SWIFTUI_RULE, WRAPPER_INPUT_RULE, INPUT_RULE, KEYBOARD_RULE, SCROLL_RULE]
    ordered = [r for r in priority_order if r in rules]
    return ordered[:5]


# ---------------------------------------------------------------------------
# One-shot examples per platform/screen_type
# ---------------------------------------------------------------------------

EXAMPLE_STEPS: dict[str, str] = {
    "android_native": (
        'EXAMPLE – tapping a button:\n'
        '{"type":"tap","selector":{"using":"id","value":"<exact resource-id from XML>"},"description":"Tap login button"}\n\n'
        'EXAMPLE – typing into a wrapper-contained input (3-step pattern):\n'
        '{"type":"tap","selector":{"using":"-android uiautomator","value":"new UiSelector().resourceId(\\"com.pkg:id/email_field\\")"},"description":"Focus email field"}\n'
        '{"type":"type","selector":{"using":"-android uiautomator","value":"new UiSelector().resourceId(\\"com.pkg:id/email_field\\").childSelector(new UiSelector().className(\\"android.widget.EditText\\"))"},"text":"user@test.com","description":"Type email"}\n'
        '{"type":"hideKeyboard","description":"Dismiss keyboard"}'
    ),
    "android_compose": (
        'EXAMPLE – tapping a button:\n'
        '{"type":"tap","selector":{"using":"-android uiautomator","value":"new UiSelector().resourceId(\\"<exact resource-id from XML>\\")"},"description":"Tap login button"}\n\n'
        'EXAMPLE – typing into a Compose input (3-step pattern):\n'
        '{"type":"tap","selector":{"using":"-android uiautomator","value":"new UiSelector().resourceId(\\"com.pkg:id/email_field\\")"},"description":"Focus email field"}\n'
        '{"type":"type","selector":{"using":"-android uiautomator","value":"new UiSelector().resourceId(\\"com.pkg:id/email_field\\").childSelector(new UiSelector().className(\\"android.widget.EditText\\"))"},"text":"user@test.com","description":"Type email"}\n'
        '{"type":"hideKeyboard","description":"Dismiss keyboard"}'
    ),
    "ios_native": (
        'EXAMPLE – tapping a button:\n'
        '{"type":"tap","selector":{"using":"accessibility id","value":"btnLogin"},"description":"Tap login button"}\n\n'
        'EXAMPLE – typing into an input:\n'
        '{"type":"tap","selector":{"using":"accessibility id","value":"emailField"},"description":"Focus email field"}\n'
        '{"type":"type","selector":{"using":"-ios class chain","value":"**/XCUIElementTypeTextField[`name == \'emailField\'`]"},"text":"user@test.com","description":"Type email"}\n'
        '{"type":"hideKeyboard","description":"Dismiss keyboard"}'
    ),
    "ios_swiftui": (
        'EXAMPLE – tapping a button:\n'
        '{"type":"tap","selector":{"using":"-ios predicate string","value":"name == \'btnLogin\'"},"description":"Tap login button"}\n\n'
        'EXAMPLE – typing into a SwiftUI input:\n'
        '{"type":"tap","selector":{"using":"-ios predicate string","value":"name == \'emailField\'"},"description":"Focus email field"}\n'
        '{"type":"type","selector":{"using":"-ios predicate string","value":"type == \'XCUIElementTypeTextField\' AND name == \'emailField\'"},"text":"user@test.com","description":"Type email"}\n'
        '{"type":"hideKeyboard","description":"Dismiss keyboard"}'
    ),
}


def get_example_step(platform: str, screen_type: str) -> str:
    """Return a one-shot example step for the given platform/screen combination."""
    if platform in ("ios_sim", "ios"):
        key = "ios_swiftui" if screen_type == "swiftui" else "ios_native"
    else:
        key = "android_compose" if screen_type == "compose" else "android_native"
    return EXAMPLE_STEPS.get(key, "")


def build_rules_block(
    description: str,
    xml_context: str,
    platform: str,
    screen_type: str,
) -> str:
    """Build the complete rules block (universal + contextual + example)."""
    contextual = build_contextual_rules(description, xml_context, platform, screen_type)
    block = UNIVERSAL_RULES
    if contextual:
        block += "\nCONTEXTUAL RULES FOR THIS SCREEN:\n" + "\n".join(contextual)
    example = get_example_step(platform, screen_type)
    if example:
        block += "\n\n" + example
    return block
