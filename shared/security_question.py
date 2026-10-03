"""The security question: the everyday way back into Admin when the
administrator PIN is forgotten - pick a question only you can answer,
answer it once, and answer it again on the sign-in screen.

Answers are compared loosely (case, accents, spaces and punctuation are
ignored - "Şişli " matches "sisli") and stored only as a salted hash. Pure
functions - no Qt, no database.
"""

from __future__ import annotations

from shared.i18n import UserError
from shared.search import fold

MIN_QUESTION_LENGTH = 8
MAX_QUESTION_LENGTH = 120
MIN_ANSWER_LENGTH = 3

# Suggestions for the picker (translated in shared/i18n.py by these keys);
# the person may also write their own question.
PRESET_KEYS = (
    "question.preset.pet",
    "question.preset.school",
    "question.preset.street",
    "question.preset.teacher",
    "question.preset.city",
)


def normalize_answer(text: str | None) -> str:
    """Folded to lower case without accents, keeping only letters and digits."""
    return "".join(ch for ch in fold(text) if ch.isalnum())


def validate(question: str, answer: str) -> tuple[str, str]:
    """(clean question, clean answer text) or ValueError with a message fit to show."""
    question = " ".join((question or "").split())
    if len(question) < MIN_QUESTION_LENGTH:
        raise UserError("err.question_short", n=MIN_QUESTION_LENGTH)
    if len(question) > MAX_QUESTION_LENGTH:
        raise UserError("err.question_long", n=MAX_QUESTION_LENGTH)
    if len(normalize_answer(answer)) < MIN_ANSWER_LENGTH:
        raise UserError("err.answer_short", n=MIN_ANSWER_LENGTH)
    return question, " ".join((answer or "").split())
