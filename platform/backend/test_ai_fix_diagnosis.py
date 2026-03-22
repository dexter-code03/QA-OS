"""Unit tests for AI fix failure classification."""
from app.runner.ai_fix_diagnosis import (
    CAUSE_COMPOSE_ID_UNRELIABLE,
    CAUSE_ELEMENT_NOT_DISPLAYED,
    CAUSE_ELEMENT_NOT_IN_XML,
    CAUSE_STALE_OR_STRATEGY,
    build_failure_diagnosis_block,
    classify_failure_for_ai_fix,
    parse_android_package,
)


def test_parse_android_package_dot_separator():
    assert parse_android_package("Liberty · com.myliberty.care") == "com.myliberty.care"


def test_parse_android_package_regex():
    assert parse_android_package("Build com.example.app") == "com.example.app"


def test_compose_recommends_uiautomator():
    xml = (
        '<?xml version="1.0"?><hierarchy>'
        '<node class="androidx.compose.ui.platform.ComposeView" '
        'resource-id="com.test:id/home_btn" displayed="true" clickable="true"/>'
        "</hierarchy>"
    )
    step = {"type": "tap", "selector": {"using": "id", "value": "home_btn"}}
    d = classify_failure_for_ai_fix(step, "", xml, xml, "android", "com.test", None)
    assert d["cause"] == CAUSE_COMPOSE_ID_UNRELIABLE
    assert d.get("recommended_strategy") == "-android uiautomator"
    assert d.get("recommended_value") == 'new UiSelector().resourceId("com.test:id/home_btn")'
    block = build_failure_diagnosis_block(d)
    assert "COMPOSE_ID_SELECTOR_UNRELIABLE" in block
    assert "UiSelector" in block


def test_not_displayed():
    xml = (
        "<hierarchy>"
        '<node class="android.view.View" resource-id="com.test:id/x" displayed="false"/>'
        "</hierarchy>"
    )
    step = {"type": "tap", "selector": {"using": "id", "value": "com.test:id/x"}}
    d = classify_failure_for_ai_fix(step, "not visible", xml, xml, "android", "com.test", None)
    assert d["cause"] == CAUSE_ELEMENT_NOT_DISPLAYED


def test_element_not_in_xml():
    xml = "<hierarchy><node class=\"android.view.View\"/></hierarchy>"
    step = {"type": "tap", "selector": {"using": "id", "value": "com.test:id/missing"}}
    d = classify_failure_for_ai_fix(step, "", xml, xml, "android", "com.test", None)
    assert d["cause"] == CAUSE_ELEMENT_NOT_IN_XML


def test_stale_strategy_mismatch():
    xml = (
        "<hierarchy>"
        '<node resource-id="com.test:id/x" displayed="true" clickable="true"/>'
        "</hierarchy>"
    )
    step = {"type": "tap", "selector": {"using": "id", "value": "com.test:id/x"}}
    d = classify_failure_for_ai_fix(
        step,
        "StaleElementReferenceException",
        xml,
        xml,
        "android",
        "com.test",
        None,
    )
    assert d["cause"] == CAUSE_STALE_OR_STRATEGY


def test_ios_unknown():
    step = {"type": "tap", "selector": {"using": "id", "value": "foo"}}
    d = classify_failure_for_ai_fix(step, "", "<hierarchy/>", "", "ios_sim", None, None)
    assert d["cause"] == "UNKNOWN"
    assert any("iOS" in e for e in d["evidence"])
