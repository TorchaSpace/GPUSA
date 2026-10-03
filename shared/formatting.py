"""Display formatting shared by all three apps: database timestamps and
money amounts.

Every table stores timestamps as UTC ISO strings written by SQLite's
strftime('%Y-%m-%dT%H:%M:%fZ', 'now') - e.g. "2026-09-25T07:01:46.123Z".
These turn one into what a screen shows: a local clock time, a local
date+time, or a relative age ("14 min", "2 h") like the admin mockup's
approvals panel. Pure functions - no Qt, no database.
"""

from __future__ import annotations

from datetime import datetime, timezone

from shared.i18n import current_language, tr


def _swap_separators(text: str) -> str:
    """"1,234.56" -> "1.234,56" (Turkish digit grouping)."""
    return text.translate(str.maketrans(",.", ".,"))


def localize_number(text: str) -> str:
    """An English-formatted number ("1,234.56") in the current language's
    convention - unchanged in English, "1.234,56" in Turkish."""
    return _swap_separators(text) if current_language() == "tr" else text


def month_abbr(moment) -> str:
    """"Sep" / "Eyl" - the short month name of a date, in the current language."""
    return tr(f"format.month.{moment.month}")


def day_month_text(moment) -> str:
    """"25 Sep" / "25 Eyl"."""
    return f"{moment.day:02d} {month_abbr(moment)}"


def long_date_text(moment) -> str:
    """"Fri 25 Sep 2026" / "Cum 25 Eyl 2026"."""
    return f"{tr(f'format.weekday.{moment.weekday()}')} {day_month_text(moment)} {moment.year}"


def format_int(value: int | float | None) -> str:
    """A whole number with thousands separators ("12,345" / "12.345")."""
    if value is None:
        return "\u2014"
    return localize_number(f"{value:,}")


def signed_int(value: int) -> str:
    """"+5" / "-3" / "+0" with thousands separators ("+1,200" / "+1.200")."""
    return localize_number(f"{value:+,}")


def format_number(value: float | None, decimals: int = 1) -> str:
    """A decimal number for display ("1,234.5" / "1.234,5")."""
    if value is None:
        return "\u2014"
    return localize_number(f"{value:,.{decimals}f}")


def parse_db_timestamp(value: str) -> datetime:
    """Parse a database timestamp into an aware UTC datetime."""
    text = value.rstrip("Z")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"Unrecognised timestamp {value!r}")


def local_time_text(value: str | None) -> str:
    """"14:32" in the machine's local time zone, or "—"."""
    if not value:
        return "—"
    try:
        return parse_db_timestamp(value).astimezone().strftime("%H:%M")
    except ValueError:
        return "—"


def local_clock_text(value: str | None, now: datetime | None = None) -> str:
    """For a check-in/out stamp: "14:32" in local time when it is from the
    machine's current local day, else "25.09.2026 14:32" - so a shift left
    open since yesterday isn't mistaken for one that began this morning.
    "—" for nothing."""
    if not value:
        return "—"
    try:
        moment = parse_db_timestamp(value).astimezone()
    except ValueError:
        return "—"
    today = (now or datetime.now(timezone.utc)).astimezone().date()
    return local_time_text(value) if moment.date() == today else local_datetime_text(value)


def local_datetime_text(value: str | None) -> str:
    """"25.09.2026 14:32" in the machine's local time zone, or "—"."""
    if not value:
        return "—"
    try:
        return parse_db_timestamp(value).astimezone().strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return "—"


def age_text(value: str | None, now: datetime | None = None) -> str:
    """How long ago, compactly: "just now", "14 min", "5 h", "3 d"."""
    if not value:
        return "—"
    try:
        then = parse_db_timestamp(value)
    except ValueError:
        return "—"
    now = now or datetime.now(timezone.utc)
    seconds = max(0, int((now - then).total_seconds()))
    if seconds < 60:
        return tr("format.age_now")
    if seconds < 3600:
        return tr("format.age_min").format(n=seconds // 60)
    if seconds < 86400:
        return tr("format.age_hour").format(n=seconds // 3600)
    return tr("format.age_day").format(n=seconds // 86400)


def format_amount(value: float | None) -> str:
    """Money for display: thousands separators, two decimals ("1,612.00").
    No currency symbol - the app shows plain amounts everywhere else
    (receipts, reports, product prices), so this doesn't invent one.
    In Turkish the separators swap ("1.612,00")."""
    if value is None:
        return "—"
    return localize_number(f"{value:,.2f}")


# Largest amount (money or unit price) any screen or repository accepts.
# Far beyond any real document here, far below where float cents lose
# precision - it exists to stop typos like 1e300 poisoning every total.
MAX_AMOUNT = 1_000_000_000.0


def round_money(value, what: str = "Amount") -> float:
    """Validate a money value and round it half-up to cents. Raises
    ValueError for non-numbers, NaN/inf, anything that rounds to 0 or
    below (checked AFTER rounding, so 0.004 is refused) and anything over
    MAX_AMOUNT. Returns the rounded float."""
    from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError(f"{what} must be a number.")
    try:
        number = Decimal(str(value)) if isinstance(value, float) else Decimal(value)
    except InvalidOperation:
        raise ValueError(f"{what} must be a number.") from None
    if not number.is_finite():
        raise ValueError(f"{what} must be a finite number.")
    if abs(number) > Decimal(str(MAX_AMOUNT)) * 10:  # avoid quantize overflow on absurd input
        raise ValueError(f"{what} is too large.")
    cents = number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if cents <= 0:
        raise ValueError(f"{what} must be at least 0.01.")
    if cents > Decimal(str(MAX_AMOUNT)):
        raise ValueError(f"{what} can't be more than {MAX_AMOUNT:,.0f}.")
    return float(cents)


def parse_amount(text: str) -> float | None:
    """Read a typed money amount, accepting either decimal convention.

    Rules (Turkish "1.234,56" and English "1,234.56" both work):
    - Both "," and "." present: the LAST one is the decimal mark, the
      other is a thousands mark ("1.612,00", "1,612.00").
    - One kind of separator, repeated ("1.234.567", "1,234,567"): all
      thousands marks, groups must be exactly 3 digits.
    - One kind, once: exactly 3 digits after it and 1-3 non-zero-led
      digits before ("1,234", "1.234", "12.345") is a thousands mark;
      anything else ("742,50", "0.125", "1234,5") is a decimal mark.
    Returns None for blank, unparseable, malformed grouping, exponent
    notation, NaN/inf, or anything not finite - never raises."""
    import math

    cleaned = (text or "").strip()
    for ch in (" ", "\u00a0", "\u202f", "\u2009"):
        cleaned = cleaned.replace(ch, "")
    sign = ""
    if cleaned[:1] in "+-" and cleaned:
        sign, cleaned = cleaned[0], cleaned[1:]
    if not cleaned or not cleaned.isascii() or any(c not in "0123456789,." for c in cleaned):
        return None
    if not any(c.isdigit() for c in cleaned):
        return None

    def thousands_ok(whole: str, sep: str) -> bool:
        parts = whole.split(sep)
        return (
            1 <= len(parts[0]) <= 3
            and parts[0].isdigit()
            and not parts[0].startswith("0")
            and all(len(p) == 3 and p.isdigit() for p in parts[1:])
        )

    commas, dots = cleaned.count(","), cleaned.count(".")
    if commas and dots:
        decimal = "," if cleaned.rfind(",") > cleaned.rfind(".") else "."
        thousand = "." if decimal == "," else ","
        if cleaned.count(decimal) != 1:
            return None
        whole, _, frac = cleaned.partition(decimal)
        if not thousands_ok(whole, thousand) or not frac.isdigit():
            return None
        number = whole.replace(thousand, "") + "." + frac
    else:
        sep = "," if commas else "." if dots else ""
        if not sep:
            number = cleaned
        elif cleaned.count(sep) > 1:
            if not thousands_ok(cleaned, sep):
                return None
            number = cleaned.replace(sep, "")
        else:
            whole, _, frac = cleaned.partition(sep)
            if len(frac) == 3 and frac.isdigit() and thousands_ok(whole, sep):
                number = whole + frac
            elif not (whole + frac).isdigit():
                return None
            else:
                number = (whole or "0") + "." + (frac or "0")
    try:
        value = float(sign + number)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def to_db_timestamp(value) -> str:
    """A datetime -> the database's UTC ISO format ("...T07:01:46.123Z").
    A naive datetime is taken to be local time (what a date/time picker
    on this machine gives you)."""
    from datetime import timezone as _tz

    if value.tzinfo is None:
        value = value.astimezone()  # attach the machine's local zone
    utc = value.astimezone(_tz.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


def now_db_timestamp() -> str:
    from datetime import datetime as _dt, timezone as _tz

    return to_db_timestamp(_dt.now(_tz.utc))
