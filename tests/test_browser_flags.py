import pytest

from src.config import Settings
from src.main import apply_browser_overrides, main


def test_no_flags_changes_nothing():
    settings = Settings()
    assert apply_browser_overrides(settings, False, None) is settings


def test_show_browser_opens_a_visible_window_with_a_gentle_pace():
    result = apply_browser_overrides(Settings(), True, None)
    assert result.browser.headless is False
    assert result.browser.slow_mo_ms == 400


def test_show_browser_keeps_a_pace_already_set_in_the_config():
    result = apply_browser_overrides(Settings(browser={"slow_mo_ms": 250}), True, None)
    assert result.browser.slow_mo_ms == 250


def test_slow_mo_alone_changes_only_the_pace():
    result = apply_browser_overrides(Settings(), False, 700)
    assert result.browser.headless is True
    assert result.browser.slow_mo_ms == 700


def test_explicit_slow_mo_beats_the_default_pace():
    result = apply_browser_overrides(Settings(), True, 900)
    assert (result.browser.headless, result.browser.slow_mo_ms) == (False, 900)


def test_negative_pace_becomes_zero():
    assert apply_browser_overrides(Settings(), False, -5).browser.slow_mo_ms == 0


def test_the_original_settings_are_not_modified():
    settings = Settings()
    apply_browser_overrides(settings, True, 800)
    assert settings.browser.headless is True and settings.browser.slow_mo_ms == 0


def test_other_browser_settings_survive_the_override():
    settings = Settings(browser={"timeout_ms": 20000, "viewport_height": 1800})
    result = apply_browser_overrides(settings, True, None)
    assert result.browser.timeout_ms == 20000 and result.browser.viewport_height == 1800


def test_a_non_numeric_pace_is_refused_by_the_command_line():
    with pytest.raises(SystemExit):
        main(["--slow-mo", "fast"])