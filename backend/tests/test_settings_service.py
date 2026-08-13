"""
Agent rules resolution: saved override → env var → code default.

These knobs decide how many irreversible applications a run may spend and
whether it runs unattended at all, so the layering has to be exact — in
particular, a saved `False` or `0` must win over a truthy env var rather than
being mistaken for "nothing saved".
"""

import pytest

from services.settings_service import RULES, _coerce, _from_env


class TestFromEnv:
    def test_missing_falls_back_to_default(self, monkeypatch):
        monkeypatch.delenv("SOME_KNOB", raising=False)
        assert _from_env("SOME_KNOB", int, 70) == 70

    def test_blank_falls_back_to_default(self, monkeypatch):
        # A key left empty in .env is "unset", not "zero".
        monkeypatch.setenv("SOME_KNOB", "   ")
        assert _from_env("SOME_KNOB", int, 70) == 70

    @pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "y", "on"])
    def test_truthy_spellings(self, monkeypatch, raw):
        monkeypatch.setenv("SOME_FLAG", raw)
        assert _from_env("SOME_FLAG", bool, False) is True

    @pytest.mark.parametrize("raw", ["0", "false", "no", "off", "anything else"])
    def test_falsy_spellings(self, monkeypatch, raw):
        monkeypatch.setenv("SOME_FLAG", raw)
        assert _from_env("SOME_FLAG", bool, True) is False

    def test_unparseable_int_falls_back(self, monkeypatch):
        monkeypatch.setenv("SOME_KNOB", "seventy")
        assert _from_env("SOME_KNOB", int, 70) == 70

    def test_int_is_read(self, monkeypatch):
        monkeypatch.setenv("SOME_KNOB", "55")
        assert _from_env("SOME_KNOB", int, 70) == 55

    def test_str_is_read(self, monkeypatch):
        monkeypatch.setenv("SOME_REGION", "us")
        assert _from_env("SOME_REGION", str, "india") == "us"


class TestCoerce:
    def test_absent_stays_absent(self):
        # None is the signal that means "not saved" — it must survive, or the
        # env layer below can never be reached.
        assert _coerce(None, int, 70) is None
        assert _coerce(None, bool, True) is None

    def test_saved_false_is_a_real_value(self):
        """
        The one that matters: turning auto-apply off in Setup writes False.
        If that coerced to None, the resolver would fall through to
        AUTO_APPLY_ENABLED=1 and the agent would keep applying after you had
        switched it off.
        """
        assert _coerce(False, bool, True) is False

    def test_saved_zero_is_a_real_value(self):
        assert _coerce(0, int, 20) == 0

    def test_unparseable_int_falls_back_to_default(self):
        assert _coerce("many", int, 20) == 20

    def test_int_from_string(self):
        assert _coerce("55", int, 70) == 55

    def test_str_is_stringified(self):
        assert _coerce(123, str, "india") == "123"


class TestRulesTable:
    def test_every_rule_is_well_formed(self):
        for key, entry in RULES.items():
            env_name, kind, default = entry
            assert isinstance(env_name, str) and env_name
            assert kind in (int, bool, str)
            assert isinstance(default, kind)

    def test_the_documented_knobs_are_present(self):
        # CLAUDE.md and the Setup screen both promise these.
        expected = {
            "min_score", "daily_cap", "per_run", "interval_minutes", "region",
            "auto_apply_enabled", "dry_run", "alerts_enabled", "sms_alerts",
        }
        assert expected <= set(RULES)

    def test_dangerous_switches_default_off(self):
        # Autonomous applying must never be on because nobody said otherwise.
        assert RULES["auto_apply_enabled"][2] is False

    def test_a_full_resolution_prefers_saved_over_env(self, monkeypatch):
        env_name, kind, default = RULES["min_score"]
        monkeypatch.setenv(env_name, "60")
        saved = _coerce(85, kind, default)
        resolved = saved if saved is not None else _from_env(env_name, kind, default)
        assert resolved == 85

    def test_a_full_resolution_falls_through_to_env(self, monkeypatch):
        env_name, kind, default = RULES["min_score"]
        monkeypatch.setenv(env_name, "60")
        saved = _coerce(None, kind, default)
        resolved = saved if saved is not None else _from_env(env_name, kind, default)
        assert resolved == 60
