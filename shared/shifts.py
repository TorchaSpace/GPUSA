"""Which of the three working shifts a moment falls in. The Floor header shows it
next to the clock, so it never goes stale the way a typed-in label would.

A 06:00-14:00, B 14:00-22:00, C 22:00-06:00 (local time)."""

from __future__ import annotations

from datetime import datetime, time

SHIFTS = (("A", time(6, 0), time(14, 0)), ("B", time(14, 0), time(22, 0)))


def shift_for(moment: datetime) -> str:
    """"A", "B" or "C" for the local wall-clock time of `moment`."""
    clock = moment.time()
    for name, start, end in SHIFTS:
        if start <= clock < end:
            return name
    return "C"
