# 새 모델 파일을 만들면 여기에서 import해야 Alembic이 테이블을 인식한다.
# 예: from app.models.user import User  # noqa: F401
from app.models.base import Base

__all__ = ["Base"]
