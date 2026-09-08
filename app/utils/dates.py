from calendar import monthrange
from datetime import date as datetime


def add_months(date: datetime, months: int) -> datetime:
    total = date.month - 1 + months
    year, month = date.year + total // 12, total % 12 + 1

    return datetime(year, month, min(date.day, monthrange(year, month)[1]))
