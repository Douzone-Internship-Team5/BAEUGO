"""출석 관련 공통 계산. OP-5 attendanceStreak와 G-2 currentStreakDays가 같은 함수를 쓴다.

현재 연속 출석일 기준:
- 출석한 날은 attendance_days.is_attended = true 인 날이다. 행이 없는 날은 출석하지 않은 날이다.
- 오늘(Asia/Seoul)까지 하루도 빠짐없이 이어진 출석일 수를 센다.
- 오늘은 아직 끝나지 않았으므로, 오늘 출석하지 않았으면(행 없음 또는 미달성) 어제부터 거꾸로 센다.
  즉 오늘 목표를 아직 못 채웠다는 이유만으로 연속이 끊기지 않는다.
- 오늘보다 뒤 날짜의 행은 세지 않는다.
- 예: 오늘 미달성, 어제·그제 출석, 사흘 전 결석 → 2
"""

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timezone import today_in_seoul
from app.models import AttendanceDay


def get_current_streak(db: Session, learner_id: int, today: date | None = None) -> int:
    today = today or today_in_seoul()
    attended_dates = set(
        db.scalars(
            select(AttendanceDay.study_date).where(
                AttendanceDay.learner_id == learner_id,
                AttendanceDay.is_attended.is_(True),
                AttendanceDay.study_date <= today,
            )
        )
    )

    day = today if today in attended_dates else today - timedelta(days=1)
    streak = 0
    while day in attended_dates:
        streak += 1
        day -= timedelta(days=1)
    return streak
