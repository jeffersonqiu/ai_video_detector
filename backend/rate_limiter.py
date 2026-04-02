from collections import defaultdict
from datetime import date

from config import settings

_daily_counts: dict[str, int] = defaultdict(int)
_count_date: date = date.today()


def check_and_increment_daily_limit() -> bool:
    """Returns True if request is allowed, False if daily limit exceeded."""
    global _count_date
    today = date.today()
    if today != _count_date:
        _daily_counts.clear()
        _count_date = today
    key = "gemini_calls"
    if _daily_counts[key] >= settings.daily_request_limit:
        return False
    _daily_counts[key] += 1
    return True
