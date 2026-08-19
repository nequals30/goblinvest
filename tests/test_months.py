"""Calendar arithmetic for the month picker."""

from datetime import date

import pytest

from goblinvest import months


def test_key_and_label():
    m = months.Month(2026, 5)
    assert m.key == "2026-05"
    assert m.label == "May 2026"
    assert m.first_day == date(2026, 5, 1)
    assert m.last_day == date(2026, 5, 31)


@pytest.mark.parametrize(
    "year,month,expected_last",
    [(2026, 2, 28), (2024, 2, 29), (2026, 4, 30), (2026, 12, 31)],
)
def test_last_day_handles_month_lengths_and_leap_years(year, month, expected_last):
    assert months.Month(year, month).last_day == date(year, month, expected_last)


def test_shifted_crosses_year_boundaries():
    assert months.Month(2026, 12).shifted(1) == months.Month(2027, 1)
    assert months.Month(2026, 1).shifted(-1) == months.Month(2025, 12)
    assert months.Month(2026, 5).shifted(24) == months.Month(2028, 5)
    assert months.Month(2026, 5).shifted(0) == months.Month(2026, 5)


@pytest.mark.parametrize(
    "bad", [None, "", "2026", "2026-13", "2026-00", "hello", "20260-5", "x-05"]
)
def test_parse_rejects_junk(bad):
    assert months.parse(bad) is None


def test_parse_accepts_a_key():
    assert months.parse("2026-05") == months.Month(2026, 5)


def test_between_is_inclusive_and_contiguous():
    got = months.between(date(2025, 11, 3), date(2026, 2, 20))
    assert [m.key for m in got] == ["2025-11", "2025-12", "2026-01", "2026-02"]


def test_between_single_month():
    assert months.between(date(2026, 5, 2), date(2026, 5, 28)) == [months.Month(2026, 5)]


def test_between_empty_when_dates_missing_or_reversed():
    assert months.between(None, date(2026, 5, 1)) == []
    assert months.between(date(2026, 5, 1), None) == []
    assert months.between(date(2026, 5, 1), date(2025, 1, 1)) == []
