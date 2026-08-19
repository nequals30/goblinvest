"""Calendar arithmetic for the month picker. Dates only — no money involved.

A month is identified in URLs by its ISO-ish key, `"2026-05"`, and shown as
`"May 2026"`.
"""

import calendar
from dataclasses import dataclass
from datetime import date

KEY_LENGTH = 7  # "YYYY-MM"


@dataclass(frozen=True, order=True)  # order= so min/max work in clamp()
class Month:
    year: int
    month: int

    @property
    def key(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def label(self) -> str:
        return f"{calendar.month_name[self.month]} {self.year}"

    @property
    def first_day(self) -> date:
        return date(self.year, self.month, 1)

    @property
    def last_day(self) -> date:
        return date(self.year, self.month, calendar.monthrange(self.year, self.month)[1])

    def shifted(self, months: int) -> "Month":
        index = (self.year * 12 + self.month - 1) + months
        return Month(index // 12, index % 12 + 1)


def of(day: date) -> Month:
    return Month(day.year, day.month)


def parse(key: str | None) -> Month | None:
    """A `"2026-05"` key as a Month, or None if it isn't one."""
    if not key or len(key) != KEY_LENGTH:
        return None
    year, sep, month = key[:4], key[4], key[5:]
    if sep != "-" or not (year.isdigit() and month.isdigit()):
        return None
    try:
        return of(date(int(year), int(month), 1))
    except ValueError:  # month 00 or 13
        return None


def names() -> list[tuple[int, str]]:
    """(1, "January") ... (12, "December") — the month dropdown's choices."""
    return [(n, calendar.month_name[n]) for n in range(1, 13)]


def clamp(month: Month, first: Month, last: Month) -> Month:
    """Pull a month back inside `[first, last]`.

    The year and month dropdowns are independent, so they can name a month the
    vault has no data for at all — December of the current year, say, when the
    ledger stops in May. Clamping means you land on the nearest month that has
    data instead of a blank page. Months *inside* the range are left alone even
    when they're empty; that's a real, informative answer.
    """
    return min(max(month, first), last)


def between(first: date | None, last: date | None) -> list[Month]:
    """Every month from `first` to `last`, oldest first.

    The range is contiguous: a month with no transactions in it still gets an
    entry, which is what makes this cheap — two dates from `summarize_vault()`
    instead of a scan over the ledger.
    """
    if first is None or last is None or last < first:
        return []
    start, end = of(first), of(last)
    span = (end.year * 12 + end.month) - (start.year * 12 + start.month)
    return [start.shifted(i) for i in range(span + 1)]
