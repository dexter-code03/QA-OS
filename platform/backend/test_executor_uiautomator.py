"""Sanity checks for Android UiAutomator locator strategy mapping."""
from appium.webdriver.common.appiumby import AppiumBy

from app.runner.executor import _by


def test_by_android_uiautomator_canonical():
    assert _by("-android uiautomator") == AppiumBy.ANDROID_UIAUTOMATOR


def test_by_android_uiautomator_aliases():
    assert _by("Android Uiautomator") == AppiumBy.ANDROID_UIAUTOMATOR
    assert _by("uiautomator") == AppiumBy.ANDROID_UIAUTOMATOR
    assert _by("-androiduiautomator") == AppiumBy.ANDROID_UIAUTOMATOR
