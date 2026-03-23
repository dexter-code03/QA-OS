"""Unit tests for the 3-pass XML intelligence pipeline."""

import pytest

from app.helpers_xml import (
    filter_by_relevance,
    is_actionable,
    preprocess_xml,
    score_element,
    select_relevant_screens,
    strip_attributes,
)

# ---------------------------------------------------------------------------
# Fixtures — sample XML for each platform variant
# ---------------------------------------------------------------------------

ANDROID_NATIVE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <android.widget.FrameLayout index="0" package="com.example.app" class="android.widget.FrameLayout"
    text="" resource-id="" checkable="false" checked="false" clickable="false" enabled="true"
    focusable="false" focused="false" scrollable="false" long-clickable="false" password="false"
    selected="false" bounds="[0,0][1080,2340]" displayed="true">
    <android.widget.EditText index="0" package="com.example.app" class="android.widget.EditText"
      text="" resource-id="com.example.app:id/input_email" checkable="false" checked="false"
      clickable="true" enabled="true" focusable="true" focused="false" scrollable="false"
      long-clickable="false" password="false" selected="false" bounds="[48,500][1032,580]" displayed="true"/>
    <android.widget.EditText index="1" package="com.example.app" class="android.widget.EditText"
      text="" resource-id="com.example.app:id/input_password" checkable="false" checked="false"
      clickable="true" enabled="true" focusable="true" focused="false" scrollable="false"
      long-clickable="false" password="true" selected="false" bounds="[48,620][1032,700]" displayed="true"/>
    <android.widget.Button index="2" package="com.example.app" class="android.widget.Button"
      text="Sign in" resource-id="com.example.app:id/btn_sign_in" checkable="false" checked="false"
      clickable="true" enabled="true" focusable="true" focused="false" scrollable="false"
      long-clickable="false" password="false" selected="false" bounds="[48,1420][1032,1532]" displayed="true"/>
    <android.widget.TextView index="3" package="com.example.app" class="android.widget.TextView"
      text="Forgot password?" resource-id="" checkable="false" checked="false"
      clickable="true" enabled="true" focusable="false" focused="false" scrollable="false"
      long-clickable="false" password="false" selected="false" bounds="[400,1600][680,1640]" displayed="true"/>
    <android.widget.LinearLayout index="4" package="com.example.app" class="android.widget.LinearLayout"
      text="" resource-id="" checkable="false" checked="false"
      clickable="false" enabled="true" focusable="false" focused="false" scrollable="false"
      long-clickable="false" password="false" selected="false" bounds="[0,0][1080,50]" displayed="true"/>
  </android.widget.FrameLayout>
</hierarchy>"""

ANDROID_COMPOSE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <android.widget.FrameLayout class="android.widget.FrameLayout" resource-id="" clickable="false">
    <androidx.compose.ui.platform.ComposeView class="androidx.compose.ui.platform.ComposeView" resource-id="" clickable="false">
      <android.view.View class="android.view.View" resource-id="com.app:id/username_field" content-desc="" text="" clickable="true"/>
      <android.view.View class="android.view.View" resource-id="com.app:id/password_field" content-desc="" text="" clickable="true"/>
      <android.view.View class="android.view.View" resource-id="" content-desc="Login button" text="" clickable="true"/>
      <android.view.View class="android.view.View" resource-id="" content-desc="" text="" clickable="false"/>
    </androidx.compose.ui.platform.ComposeView>
  </android.widget.FrameLayout>
</hierarchy>"""

IOS_UIKIT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<AppiumAUT>
  <XCUIElementTypeApplication type="XCUIElementTypeApplication" name="MyApp" label="MyApp">
    <XCUIElementTypeTextField type="XCUIElementTypeTextField" name="email_field" label="Email" value="" enabled="true" visible="true" x="20" y="200" width="335" height="44"/>
    <XCUIElementTypeSecureTextField type="XCUIElementTypeSecureTextField" name="password_field" label="Password" value="" enabled="true" visible="true" x="20" y="260" width="335" height="44"/>
    <XCUIElementTypeButton type="XCUIElementTypeButton" name="login_btn" label="Sign In" enabled="true" visible="true" x="20" y="340" width="335" height="44"/>
    <XCUIElementTypeStaticText type="XCUIElementTypeStaticText" name="" label="Welcome" value="Welcome" enabled="true" visible="true" x="100" y="100" width="200" height="30"/>
    <XCUIElementTypeOther type="XCUIElementTypeOther" name="" label="" enabled="true" visible="true" x="0" y="0" width="375" height="812"/>
  </XCUIElementTypeApplication>
</AppiumAUT>"""

IOS_SWIFTUI_XML = """<?xml version="1.0" encoding="UTF-8"?>
<AppiumAUT>
  <XCUIElementTypeApplication type="XCUIElementTypeApplication" name="SwiftApp" label="SwiftApp">
    <XCUIElementTypeOther type="XCUIElementTypeOther" name="login_view" label="" enabled="true" visible="true"/>
    <XCUIElementTypeOther type="XCUIElementTypeOther" name="" label="" enabled="true" visible="true"/>
    <XCUIElementTypeOther type="XCUIElementTypeOther" name="email_input" label="Email" enabled="true" visible="true"/>
    <XCUIElementTypeOther type="XCUIElementTypeOther" name="password_input" label="Password" enabled="true" visible="true"/>
    <XCUIElementTypeOther type="XCUIElementTypeOther" name="" label="" enabled="true" visible="true"/>
    <XCUIElementTypeButton type="XCUIElementTypeButton" name="sign_in_btn" label="Sign In" enabled="true" visible="true"/>
    <XCUIElementTypeStaticText type="XCUIElementTypeStaticText" name="" label="Forgot?" enabled="true" visible="true"/>
    <XCUIElementTypeOther type="XCUIElementTypeOther" name="" label="" enabled="true" visible="true"/>
    <XCUIElementTypeOther type="XCUIElementTypeOther" name="" label="" enabled="true" visible="true"/>
    <XCUIElementTypeOther type="XCUIElementTypeOther" name="" label="" enabled="true" visible="true"/>
  </XCUIElementTypeApplication>
</AppiumAUT>"""


# ---------------------------------------------------------------------------
# Pass 1 — strip_attributes
# ---------------------------------------------------------------------------

class TestStripAttributes:
    def test_android_keeps_selector_attrs(self):
        import xml.etree.ElementTree as ET
        root = ET.fromstring(ANDROID_NATIVE_XML)
        edit_text = list(root.iter())[2]  # first EditText
        attrs = strip_attributes(edit_text, "android")
        assert "resource-id" in attrs
        assert "class" in attrs
        assert "clickable" in attrs
        assert "bounds" not in attrs
        assert "package" not in attrs
        assert "checkable" not in attrs

    def test_ios_keeps_selector_attrs(self):
        import xml.etree.ElementTree as ET
        root = ET.fromstring(IOS_UIKIT_XML)
        text_field = list(root.iter())[1]  # first TextField
        attrs = strip_attributes(text_field, "ios_sim")
        assert "name" in attrs
        assert "label" in attrs
        assert "type" in attrs
        assert "x" not in attrs
        assert "y" not in attrs
        assert "width" not in attrs


# ---------------------------------------------------------------------------
# Pass 2 — is_actionable
# ---------------------------------------------------------------------------

class TestIsActionable:
    def test_android_button_is_actionable(self):
        attrs = {"class": "android.widget.Button", "resource-id": "com.app:id/btn", "text": "OK", "clickable": "true"}
        assert is_actionable(attrs, "android") is True

    def test_android_no_selector_not_actionable(self):
        attrs = {"class": "android.widget.LinearLayout", "clickable": "false"}
        assert is_actionable(attrs, "android") is False

    def test_compose_view_with_rid_is_actionable(self):
        attrs = {"class": "android.view.View", "resource-id": "com.app:id/field"}
        assert is_actionable(attrs, "android", is_compose=True) is True

    def test_compose_view_no_selector_not_actionable(self):
        attrs = {"class": "android.view.View"}
        assert is_actionable(attrs, "android", is_compose=True) is False

    def test_ios_uikit_button_is_actionable(self):
        attrs = {"type": "XCUIElementTypeButton", "name": "login_btn", "label": "Sign In"}
        assert is_actionable(attrs, "ios_sim") is True

    def test_ios_swiftui_other_with_name(self):
        attrs = {"type": "XCUIElementTypeOther", "name": "email_input", "label": "Email"}
        assert is_actionable(attrs, "ios_sim", is_swiftui=True) is True

    def test_ios_swiftui_other_without_name(self):
        attrs = {"type": "XCUIElementTypeOther", "name": "", "label": ""}
        assert is_actionable(attrs, "ios_sim", is_swiftui=True) is False


# ---------------------------------------------------------------------------
# Pass 3 — score_element and filter_by_relevance
# ---------------------------------------------------------------------------

class TestScoreElement:
    def test_direct_text_match(self):
        attrs = {"text": "Sign in", "class": "android.widget.Button", "resource-id": "com.app:id/btn_sign_in"}
        score = score_element(attrs, "User signs in with email", "android")
        assert score > 0

    def test_resource_id_suffix_match(self):
        attrs = {"resource-id": "com.app:id/btn_sign_in", "class": "android.widget.Button"}
        score = score_element(attrs, "sign in button", "android")
        assert score >= 5  # direct + suffix

    def test_no_match_scores_zero(self):
        attrs = {"text": "Help", "class": "android.widget.TextView"}
        score = score_element(attrs, "payment checkout", "android")
        assert score == 0

    def test_ios_name_match(self):
        attrs = {"name": "login_btn", "label": "Sign In", "type": "XCUIElementTypeButton"}
        score = score_element(attrs, "user login", "ios_sim")
        assert score > 0

    def test_empty_description(self):
        attrs = {"text": "Hello"}
        assert score_element(attrs, "", "android") == 0


class TestFilterByRelevance:
    def test_keeps_matched_elements_first(self):
        elements = [
            {"text": "Help", "class": "android.widget.TextView"},
            {"text": "Sign in", "class": "android.widget.Button", "resource-id": "com.app:id/btn_sign_in"},
            {"text": "Settings", "class": "android.widget.TextView"},
        ]
        result = filter_by_relevance(elements, "sign in", "android", max_elements=2)
        assert len(result) <= 2
        assert result[0]["text"] == "Sign in"

    def test_no_description_returns_first_n(self):
        elements = [{"text": f"item_{i}"} for i in range(30)]
        result = filter_by_relevance(elements, "", "android", max_elements=10)
        assert len(result) == 10


# ---------------------------------------------------------------------------
# Integration — preprocess_xml
# ---------------------------------------------------------------------------

class TestPreprocessXml:
    def test_android_native_reduces_elements(self):
        output = preprocess_xml(ANDROID_NATIVE_XML, "android", description="sign in with email")
        assert "android/native" in output
        assert "rid:" in output or "text:" in output
        assert "bounds" not in output
        assert "package" not in output

    def test_android_compose_detected(self):
        output = preprocess_xml(ANDROID_COMPOSE_XML, "android", description="login")
        assert "android/compose" in output

    def test_ios_uikit(self):
        output = preprocess_xml(IOS_UIKIT_XML, "ios_sim", description="login")
        assert "ios/" in output
        assert "name:" in output or "label:" in output

    def test_ios_swiftui_detected(self):
        output = preprocess_xml(IOS_SWIFTUI_XML, "ios_sim", description="sign in")
        assert "ios/swiftui" in output

    def test_empty_xml_returns_empty(self):
        assert preprocess_xml("", "android") == ""
        assert preprocess_xml("  ", "android") == ""

    def test_malformed_xml_returns_error(self):
        output = preprocess_xml("<not>valid<xml", "android")
        assert "parse error" in output.lower()

    def test_screen_name_in_header(self):
        output = preprocess_xml(ANDROID_NATIVE_XML, "android", screen_name="Login Screen")
        assert "Login Screen" in output

    def test_filtered_count_in_header(self):
        output = preprocess_xml(ANDROID_NATIVE_XML, "android", description="email sign in")
        assert "filtered from" in output


# ---------------------------------------------------------------------------
# Safety — detectors must run on raw XML
# ---------------------------------------------------------------------------

class TestDetectorSafety:
    def test_compose_detection_on_raw(self):
        from app.compose_detection import is_compose_screen
        assert is_compose_screen(ANDROID_COMPOSE_XML) is True
        assert is_compose_screen(ANDROID_NATIVE_XML) is False

    def test_swiftui_detection_on_raw(self):
        from app.swiftui_detection import is_swiftui_screen
        assert is_swiftui_screen(IOS_SWIFTUI_XML) is True


# ---------------------------------------------------------------------------
# select_relevant_screens
# ---------------------------------------------------------------------------

class _FakeScreen:
    def __init__(self, name: str, xml: str):
        self.name = name
        self.xml_snapshot = xml

class TestSelectRelevantScreens:
    def test_selects_by_name_overlap(self):
        screens = [
            _FakeScreen("Login Screen", ANDROID_NATIVE_XML),
            _FakeScreen("Settings Page", "<hierarchy></hierarchy>"),
            _FakeScreen("Payment Screen", "<hierarchy></hierarchy>"),
        ]
        result = select_relevant_screens(screens, "user login", max_screens=2)
        assert result[0].name == "Login Screen"

    def test_returns_at_least_one(self):
        screens = [_FakeScreen("Dashboard", "<hierarchy></hierarchy>")]
        result = select_relevant_screens(screens, "xyz totally unrelated", max_screens=4)
        assert len(result) >= 1
