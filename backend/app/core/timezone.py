from datetime import date, datetime
from zoneinfo import ZoneInfo

# 출석·조회수 등 날짜 기준은 모두 Asia/Seoul이다.
SEOUL_TZ = ZoneInfo("Asia/Seoul")


def today_in_seoul() -> date:
    return datetime.now(SEOUL_TZ).date()
