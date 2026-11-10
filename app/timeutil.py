"""Time helpers.

The database stores every time as naive UTC (see models.utc_now). Admins type
and read times in local time (Philippines = UTC+8 by default), so these
helpers convert in both directions. Change LOCAL_UTC_OFFSET_HOURS in .env if needed.
"""
from datetime import datetime, timedelta

from flask import current_app

INPUT_FORMAT = "%Y-%m-%dT%H:%M"      # what <input type="datetime-local"> sends


def _offset():
    return timedelta(hours=current_app.config.get("LOCAL_UTC_OFFSET_HOURS", 8))


def to_local(value):
    return value + _offset() if value else None


def local_dt(value, fmt="%b %d, %Y %I:%M %p"):
    """Jinja filter: UTC datetime -> readable local time."""
    local = to_local(value)
    return local.strftime(fmt) if local else "-"


def local_date(value):
    return local_dt(value, "%b %d, %Y")


def local_input(value):
    """Jinja filter: UTC datetime -> value for <input type="datetime-local">."""
    local = to_local(value)
    return local.strftime(INPUT_FORMAT) if local else ""


def parse_local_input(text):
    """'2026-11-12T08:00' (local) -> naive UTC datetime. Empty -> None. Bad -> ValueError."""
    text = (text or "").strip()
    if not text:
        return None
    return datetime.strptime(text, INPUT_FORMAT) - _offset()
