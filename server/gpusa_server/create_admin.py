"""Create (or reset) the first administrator, with the PIN typed at a prompt.

    python -m gpusa_server.create_admin B-001 "Ali Emre"

The PIN is asked for with ``getpass`` (not echoed, never on the command line
or in shell history) and must follow the desktop rule: digits only, at least 6
for an administrator.
"""

from __future__ import annotations

import getpass
import sys

from . import db, security
from .config import load_settings


def pin_problem(pin: str) -> str | None:
    if not pin.isdigit() or not pin.isascii():
        return "The PIN must be digits only."
    if len(pin) < 6:
        return "An administrator PIN needs at least 6 digits."
    if len(pin) > 12:
        return "The PIN can be at most 12 digits."
    if len(set(pin)) == 1 or pin in "0123456789012" or pin in "9876543210987":
        return "That PIN is too easy to guess (repeated or sequential digits)."
    return None


def create_admin(settings, badge: str, name: str, pin: str) -> str:
    badge = security.normalize_badge_id(badge)
    problem = pin_problem(pin)
    if not badge or not name.strip():
        raise ValueError("Badge and name are required.")
    if problem:
        raise ValueError(problem)
    pin_hash = security.hash_pin(pin)
    with db.connect(settings) as conn:
        row = conn.execute("SELECT id FROM employees WHERE upper(badge_id) = %s", (badge,)).fetchone()
        if row is None:
            row = conn.execute(
                "INSERT INTO employees (badge_id, name, title, role, location_type, location_name) "
                "VALUES (%s, %s, 'Administrator', 'Management', 'Warehouse', 'HQ') RETURNING id",
                (badge, name.strip()),
            ).fetchone()
        conn.execute("UPDATE accounts SET is_root = FALSE WHERE is_root AND employee_id <> %s", (row[0],))
        conn.execute(
            "INSERT INTO accounts (employee_id, role, is_root, pin_hash) VALUES (%s, 'admin', TRUE, %s) "
            "ON CONFLICT (employee_id) DO UPDATE SET role = 'admin', is_root = TRUE, pin_hash = EXCLUDED.pin_hash, "
            "is_active = TRUE, failed_attempts = 0, locked_until = NULL, updated_at = now()",
            (row[0], pin_hash),
        )
    return badge


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print('Usage: python -m gpusa_server.create_admin BADGE "Full Name"', file=sys.stderr)
        return 2
    pin = getpass.getpass("PIN (6-12 digits): ")
    if getpass.getpass("PIN again: ") != pin:
        print("The two PINs differ.", file=sys.stderr)
        return 1
    try:
        badge = create_admin(load_settings(), argv[1], argv[2], pin)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Administrator {badge} is ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
