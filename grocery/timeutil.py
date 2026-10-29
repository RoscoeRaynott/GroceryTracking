"""All dates use Houston time, not the server's UTC clock."""

from datetime import datetime
from zoneinfo import ZoneInfo

HOUSTON = ZoneInfo("America/Chicago")


def now():
    return datetime.now(HOUSTON)


def now_str():
    return now().strftime("%Y-%m-%d %H:%M:%S")


def today_str():
    return now().strftime("%Y-%m-%d")
