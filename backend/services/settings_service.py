"""
Agent rules — the handful of knobs the Setup screen exposes.

These have always existed as env vars. The redesign puts them on screen, which
needs them writable at runtime, so they get a Mongo document that *overrides*
the env var. Nothing changes until something is actually saved: a missing
document, or a missing key inside it, falls straight back to the env var and
then to the same default the code used before.
"""

import os

RULES = {
    # key: (env var, type, default)
    "min_score": ("AUTO_APPLY_MIN_SCORE", int, 70),
    "daily_cap": ("AUTO_APPLY_DAILY_CAP", int, 20),
    "per_run": ("AUTO_APPLY_PER_RUN", int, 5),
    "interval_minutes": ("AUTO_APPLY_INTERVAL_MIN", int, 60),
    "region": ("AUTO_APPLY_REGION", str, "india"),
    "auto_apply_enabled": ("AUTO_APPLY_ENABLED", bool, False),
    "dry_run": ("APPLY_DRY_RUN", bool, False),
    "alerts_enabled": ("ALERTS_ENABLED", bool, True),
    "sms_alerts": ("SMS_ALERTS_ENABLED", bool, True),
}

_TRUTHY = ("1", "true", "yes", "y", "on")


def _from_env(env_name: str, kind, default):
    raw = os.environ.get(env_name)
    if raw is None or raw.strip() == "":
        return default
    if kind is bool:
        return raw.strip().lower() in _TRUTHY
    if kind is int:
        try:
            return int(raw)
        except ValueError:
            return default
    return raw


def _coerce(value, kind, default):
    if value is None:
        return None
    if kind is bool:
        return bool(value)
    if kind is int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    return str(value)


async def get_agent_rules() -> dict:
    """Saved overrides layered over env vars layered over defaults."""
    saved = {}
    try:
        from db.mongodb import get_settings
        saved = await get_settings() or {}
    except Exception:
        # No DB yet (or it is down) — env vars still answer every question.
        saved = {}

    rules = {}
    for key, (env_name, kind, default) in RULES.items():
        override = _coerce(saved.get(key), kind, default)
        rules[key] = override if override is not None else _from_env(env_name, kind, default)
    return rules


async def save_agent_rules(values: dict) -> dict:
    """Persist only the keys we recognise, then return the merged view."""
    from db.mongodb import save_settings

    clean = {}
    for key, (_env, kind, default) in RULES.items():
        if key in values:
            clean[key] = _coerce(values[key], kind, default)
    await save_settings(clean)
    return await get_agent_rules()
