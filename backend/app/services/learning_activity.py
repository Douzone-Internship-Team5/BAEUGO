import math
from datetime import date, datetime, timedelta
from typing import Literal

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.timezone import SEOUL_TZ
from app.models.attendance import AttendanceDay
from app.models.goal import LearningGoal
from app.models.learning import Enrollment, StudyActivitySession
from app.models.user import User

DEFAULT_DAILY_GOAL_MINUTES = 20
MAX_FUTURE_SKEW_SECONDS = 5


def lock_learning_activity(db: Session, learner_id: int) -> None:
    """영상·퀴즈가 출석을 갱신하기 전에 같은 수강자의 요청을 직렬화한다."""
    # Enrollment 등 다른 행을 잠그기 전에 호출하여 C-7과 Q-2 잠금 순서를 맞춘다.
    user_id = db.scalar(select(User.id).where(User.id == learner_id).with_for_update())
    if user_id is None:
        raise AppError(404, "NOT_FOUND", "존재하지 않는 사용자입니다.")


def record_learning_activity(
    db: Session,
    *,
    learner_id: int,
    enrollment_id: int,
    activity_type: Literal["VIDEO", "QUIZ"],
    session_key: str,
    started_at: datetime,
    ended_at: datetime,
    active_seconds: int,
    now: datetime,
    video_id: int | None = None,
    quiz_attempt_id: int | None = None,
    validation_message: str = "학습 활동 시간 또는 세션 정보를 확인해 주세요.",
) -> None:
    """C-7·Q-2 공통 활동 저장. 호출자가 사용자 잠금과 commit·rollback을 담당한다."""
    session_key = session_key.strip()
    now = _ensure_seoul_aware(now)
    started_at = _ensure_seoul_aware(started_at)
    ended_at = _ensure_seoul_aware(ended_at)
    if not session_key or len(session_key) > 64 or active_seconds < 0 or ended_at < started_at:
        raise AppError(400, "VALIDATION_ERROR", validation_message)

    max_allowed_time = now + timedelta(seconds=MAX_FUTURE_SKEW_SECONDS)
    if started_at > max_allowed_time or ended_at > max_allowed_time:
        raise AppError(400, "VALIDATION_ERROR", validation_message)

    earliest_start_date = now.date() - timedelta(days=1)
    if ended_at.date() != now.date() or started_at.date() < earliest_start_date:
        raise AppError(400, "VALIDATION_ERROR", validation_message)

    if active_seconds > _max_allowed_active_seconds(started_at, ended_at):
        raise AppError(400, "VALIDATION_ERROR", validation_message)

    existing_session = db.scalar(
        select(StudyActivitySession)
        .where(StudyActivitySession.session_key == session_key)
        .with_for_update()
    )
    if existing_session is not None:
        if (
            existing_session.enrollment_id != enrollment_id
            or existing_session.activity_type != activity_type
            or existing_session.video_id != video_id
            or existing_session.quiz_attempt_id != quiz_attempt_id
        ):
            raise AppError(400, "VALIDATION_ERROR", validation_message)

        existing_started = _ensure_seoul_aware(existing_session.started_at)
        existing_ended = _ensure_seoul_aware(existing_session.ended_at)
        # 요청의 시작 시각만 바꿔 오래된 세션을 오늘 활동으로 재사용할 수 없다.
        if existing_started.date() < earliest_start_date:
            raise AppError(400, "VALIDATION_ERROR", validation_message)
        eff_ended = max(ended_at, existing_ended)
        if eff_ended < existing_started:
            raise AppError(400, "VALIDATION_ERROR", validation_message)
        if active_seconds > _max_allowed_active_seconds(existing_started, eff_ended):
            raise AppError(400, "VALIDATION_ERROR", validation_message)

    # 학습 활동 세션(study_activity_sessions) 및 날짜별 출석(attendance_days) 갱신
    goal = db.get(LearningGoal, learner_id)
    target_minutes = (
        goal.daily_target_minutes
        if (goal is not None and goal.daily_target_minutes > 0)
        else DEFAULT_DAILY_GOAL_MINUTES
    )

    if existing_session is None:
        target_slices = _split_session_into_daily_slices(started_at, ended_at, active_seconds)
        affected_dates = sorted({d for d, _, _, _ in target_slices})
    else:
        existing_started = _ensure_seoul_aware(existing_session.started_at)
        existing_ended = _ensure_seoul_aware(existing_session.ended_at)
        eff_ended = max(ended_at, existing_ended)
        eff_active = max(active_seconds, existing_session.active_seconds)
        prev_slices = _split_session_into_daily_slices(
            existing_started, existing_ended, existing_session.active_seconds
        )
        prelim_new_slices = _split_session_into_daily_slices(
            existing_started, eff_ended, eff_active
        )
        affected_dates = sorted(
            {d for d, _, _, _ in prev_slices} | {d for d, _, _, _ in prelim_new_slices}
        )

    if not affected_dates:
        affected_dates = [now.date()]

    attendances_map: dict[date, AttendanceDay | None] = {}
    base_untracked_map: dict[date, int] = {}

    for d in affected_dates:
        prev_day_sessions = _get_learner_day_sessions(db, learner_id, d)
        prev_day_seconds = _compute_deduplicated_active_seconds(prev_day_sessions, study_date=d)
        att_d = db.get(
            AttendanceDay,
            (learner_id, d),
            with_for_update=True,
            populate_existing=True,
        )
        attendances_map[d] = att_d

        # 갱신 전후에 같은 분할 기준을 써야 재전송 시 차액을 새 학습 시간으로 더하지 않는다.
        base_untracked_map[d] = (
            max(0, (att_d.active_seconds or 0) - prev_day_seconds) if att_d is not None else 0
        )

    if existing_session is None:
        session_id = db.scalar(
            insert(StudyActivitySession)
            .values(
                enrollment_id=enrollment_id,
                activity_type=activity_type,
                video_id=video_id,
                quiz_attempt_id=quiz_attempt_id,
                session_key=session_key,
                study_date=affected_dates[0],
                started_at=started_at,
                ended_at=ended_at,
                active_seconds=active_seconds,
            )
            .on_conflict_do_nothing(index_elements=["session_key"])
            .returning(StudyActivitySession.id)
        )
        # 다른 사용자가 같은 키로 동시에 생성해도 일부 결과만 저장하거나 500을 내지 않는다.
        if session_id is None:
            raise AppError(400, "VALIDATION_ERROR", validation_message)
        db.flush()
    else:
        existing_ended = _ensure_seoul_aware(existing_session.ended_at)
        if active_seconds > existing_session.active_seconds:
            existing_session.active_seconds = active_seconds
        if ended_at > existing_ended:
            existing_session.ended_at = ended_at
        db.flush()

    for d in affected_dates:
        updated_day_sessions = _get_learner_day_sessions(db, learner_id, d)
        new_day_seconds = _compute_deduplicated_active_seconds(
            updated_day_sessions,
            study_date=d,
        )
        total_d_seconds = base_untracked_map[d] + new_day_seconds
        att_d = attendances_map[d]

        if att_d is None:
            db.execute(
                insert(AttendanceDay)
                .values(
                    learner_id=learner_id,
                    study_date=d,
                    target_minutes_snapshot=target_minutes,
                    active_seconds=total_d_seconds,
                    is_attended=(total_d_seconds >= target_minutes * 60),
                )
                .on_conflict_do_update(
                    index_elements=["learner_id", "study_date"],
                    set_={
                        "active_seconds": total_d_seconds,
                        "is_attended": (total_d_seconds >= target_minutes * 60),
                    },
                )
            )
            att_d = db.get(
                AttendanceDay,
                (learner_id, d),
                populate_existing=True,
            )
            assert att_d is not None
            attendances_map[d] = att_d
        else:
            att_d.active_seconds = total_d_seconds
            att_d.is_attended = att_d.active_seconds >= att_d.target_minutes_snapshot * 60

    db.flush()


def _ensure_seoul_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=SEOUL_TZ)
    return dt.astimezone(SEOUL_TZ)


def _effective_end_for_midnight_truncation(started_at: datetime, ended_at: datetime) -> datetime:
    """자정 직전 시각이 초 단위(23:59:59)로 절삭 표현된 경우 자정(24:00:00)까지로 보정한다."""
    if (
        ended_at > started_at
        and ended_at.hour == 23
        and ended_at.minute == 59
        and ended_at.second == 59
        and ended_at.microsecond == 0
    ):
        return ended_at + timedelta(seconds=1)
    return ended_at


def _max_allowed_active_seconds(started_at: datetime, ended_at: datetime) -> int:
    eff_end = _effective_end_for_midnight_truncation(started_at, ended_at)
    elapsed = max(0.0, (eff_end - started_at).total_seconds())
    return math.ceil(elapsed)


def _get_day_bounds(study_date: date) -> tuple[datetime, datetime]:
    day_start = datetime(
        study_date.year, study_date.month, study_date.day, 0, 0, 0, tzinfo=SEOUL_TZ
    )
    return day_start, day_start + timedelta(days=1)


def _split_session_into_daily_slices(
    started_at: datetime,
    ended_at: datetime,
    active_seconds: int,
) -> list[tuple[date, datetime, datetime, int]]:
    """누적 활동 시간을 Asia/Seoul 날짜별 경과 시간에 비례해 일관되게 분할한다."""
    st = _ensure_seoul_aware(started_at)
    et = _ensure_seoul_aware(ended_at)
    act_sec = max(0, int(active_seconds or 0))

    if st.date() == et.date() or et <= st:
        return [(st.date(), st, et, act_sec)]

    raw_segments: list[tuple[date, datetime, datetime, float]] = []
    cur_date = st.date()
    end_date = et.date()
    while cur_date <= end_date:
        day_start, day_end = _get_day_bounds(cur_date)
        seg_st = max(st, day_start)
        seg_et = min(et, day_end)
        if seg_et > seg_st:
            raw_segments.append((cur_date, seg_st, seg_et, (seg_et - seg_st).total_seconds()))
        cur_date += timedelta(days=1)

    if not raw_segments:
        return [(st.date(), st, et, act_sec)]

    if len(raw_segments) == 1:
        d, seg_st, seg_et, _ = raw_segments[0]
        return [(d, seg_st, seg_et, act_sec)]

    total_elapsed = sum(seg[3] for seg in raw_segments)
    result: list[tuple[date, datetime, datetime, int]] = []
    cum_elapsed = 0.0
    prev_allocated = 0
    for d, seg_st, seg_et, seg_elapsed in raw_segments:
        cum_elapsed += seg_elapsed
        target_cum = round(act_sec * cum_elapsed / total_elapsed)
        seg_act = target_cum - prev_allocated
        prev_allocated = target_cum
        result.append((d, seg_st, seg_et, seg_act))
    return result


def _compute_deduplicated_active_seconds(
    sessions: list[StudyActivitySession],
    *,
    study_date: date | None = None,
) -> int:
    """동일 날짜 학습 활동 세션에서 동시(중복) 시간 구간을 제외하고 인정 학습 시간을 합산한다."""
    if not sessions:
        return 0

    normalized: list[tuple[datetime, datetime, int]] = []
    for s in sessions:
        st = _ensure_seoul_aware(s.started_at)
        et = _ensure_seoul_aware(s.ended_at)
        act_sec = max(0, int(s.active_seconds or 0))

        if study_date is not None:
            slices = _split_session_into_daily_slices(st, et, act_sec)
            matched = next((sl for sl in slices if sl[0] == study_date), None)
            if matched is None:
                if s.study_date == study_date and st.date() == et.date():
                    seg_st, seg_et, seg_act = st, et, act_sec
                else:
                    continue
            else:
                _, seg_st, seg_et, seg_act = matched

            normalized.append((seg_st, seg_et, seg_act))
        else:
            normalized.append((st, et, act_sec))

    if not normalized:
        return 0

    normalized.sort(key=lambda x: (x[0], x[1]))

    # 엄격히 겹치는(next_start < comp_end) 세션들을 연결 요소로 묶어 동시 시청 중복 합산 방지
    components: list[tuple[datetime, datetime, int, int, int]] = []
    for st, et, act_sec in normalized:
        if not components or st >= components[-1][1]:
            components.append((st, et, act_sec, act_sec, 1))
        else:
            c_st, c_et, c_sum, c_max, c_cnt = components[-1]
            components[-1] = (
                c_st,
                max(c_et, et),
                c_sum + act_sec,
                max(c_max, act_sec),
                c_cnt + 1,
            )

    total = 0
    for c_st, c_et, c_sum, c_max, c_cnt in components:
        if c_cnt == 1:
            total += c_sum
        else:
            window_seconds = _max_allowed_active_seconds(c_st, c_et)
            total += min(c_sum, max(c_max, window_seconds))
    return total


def _get_learner_day_sessions(
    db: Session, learner_id: int, study_date: date
) -> list[StudyActivitySession]:
    day_start, day_end = _get_day_bounds(study_date)
    return list(
        db.scalars(
            select(StudyActivitySession)
            .join(Enrollment, Enrollment.id == StudyActivitySession.enrollment_id)
            .where(
                Enrollment.learner_id == learner_id,
                or_(
                    StudyActivitySession.study_date == study_date,
                    and_(
                        StudyActivitySession.started_at < day_end,
                        StudyActivitySession.ended_at > day_start,
                    ),
                ),
            )
        ).all()
    )
