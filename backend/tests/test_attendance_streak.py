from datetime import date, timedelta

import pytest

from app.models import AttendanceDay
from app.services.attendance import get_current_streak

TODAY = date(2026, 10, 24)


@pytest.fixture
def learner_id(get_user) -> int:
    return get_user("learner2@baeugo.test").id


def add_days(db, learner_id: int, attended_by_offset: dict[int, bool]) -> None:
    """{오늘 기준 며칠 전: 출석 여부} 로 출석 행을 만든다."""
    for days_ago, is_attended in attended_by_offset.items():
        db.add(
            AttendanceDay(
                learner_id=learner_id,
                study_date=TODAY - timedelta(days=days_ago),
                target_minutes_snapshot=20,
                active_seconds=1200 if is_attended else 0,
                is_attended=is_attended,
            )
        )
    db.flush()


@pytest.mark.parametrize(
    ("attended_by_offset", "expected"),
    [
        ({}, 0),
        ({0: True, 1: True, 2: True}, 3),
        ({0: False, 1: True, 2: True}, 2),
        ({1: True, 2: True, 3: False}, 2),
        ({0: True, 2: True}, 1),
        ({0: False, 1: False, 2: True}, 0),
        ({2: True, 3: True}, 0),
        ({-1: True, 0: True}, 1),
    ],
    ids=[
        "no-rows",
        "attended-through-today",
        "today-not-yet-attended",
        "today-no-row",
        "gap-yesterday",
        "missed-yesterday",
        "last-attended-two-days-ago",
        "future-row-ignored",
    ],
)
def test_current_streak(db, learner_id, attended_by_offset, expected):
    add_days(db, learner_id, attended_by_offset)

    assert get_current_streak(db, learner_id, today=TODAY) == expected


def test_other_learners_days_are_not_counted(db, learner_id, get_user):
    add_days(db, get_user("learner1@baeugo.test").id, {0: True, 1: True})

    assert get_current_streak(db, learner_id, today=TODAY) == 0
