from datetime import datetime
from zoneinfo import ZoneInfo
from flask_login import current_user

DEFAULT_TIMEZONE = "Africa/Nairobi"  # Your app default

def get_timezone():
    try:
        return ZoneInfo(current_user.timezone or DEFAULT_TIMEZONE)
    except Exception:
        return ZoneInfo(DEFAULT_TIMEZONE)

def now():
    return datetime.now(get_timezone())

def today():
    return now().date()
