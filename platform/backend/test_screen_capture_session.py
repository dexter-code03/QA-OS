"""Registry behavior for long-lived screen capture Appium sessions."""
from app.runner.screen_capture_session import (
    _reset_for_tests,
    make_session_key,
    session_active_and_alive,
    set_session_driver,
    stop_session,
    with_session_driver,
)


class _MockDriver:
    @property
    def session_id(self) -> str:
        return "mock-session"


def test_screen_capture_session_registry():
    _reset_for_tests()
    try:
        key = make_session_key(1, "android", "emulator-5554", 42)
        assert session_active_and_alive(key) is False

        set_session_driver(key, _MockDriver())
        assert session_active_and_alive(key) is True

        def grab(d):
            return d.session_id, "shot"

        assert with_session_driver(key, grab) == ("mock-session", "shot")

        assert stop_session(key) is True
        assert session_active_and_alive(key) is False
    finally:
        _reset_for_tests()
