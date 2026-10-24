import re
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.main import app
from tests.conftest import auth_header


class InstructorTestClient(TestClient):
    """강사 도메인 전용 TestClient.

    기본적으로 목업 강사(id=6)의 인증 토큰을 헤더에 부여하며,
    'NONE' 헤더 전달 시 비인증 요청으로 변환하고,
    'Bearer learner' 등 목업 토큰을 실제 JWT 토큰으로 변환한다.
    """

    def request(self, method: str, url: str, **kwargs):
        headers = dict(kwargs.get("headers") or {})
        auth_val = headers.get("Authorization") or headers.get("authorization")

        if auth_val is not None:
            if auth_val in ("NONE", "None", ""):
                headers.pop("Authorization", None)
                headers.pop("authorization", None)
            elif auth_val == "Bearer learner":
                headers["Authorization"] = auth_header(2)["Authorization"]
            elif auth_val == "Bearer instructor":
                headers["Authorization"] = auth_header(6)["Authorization"]
            elif auth_val == "Bearer operator":
                headers["Authorization"] = auth_header(1)["Authorization"]
            elif re.match(r"^Bearer user_(\d+)$", auth_val):
                uid = int(re.match(r"^Bearer user_(\d+)$", auth_val).group(1))
                headers["Authorization"] = auth_header(uid)["Authorization"]
        else:
            headers["Authorization"] = auth_header(6)["Authorization"]

        kwargs["headers"] = headers
        return super().request(method, url, **kwargs)


@pytest.fixture
def client(db: Session, monkeypatch) -> Generator[TestClient, None, None]:
    # API 계약 테스트의 가짜 영상은 실제 추론을 하지 않는다. 작업 저장은 별도 테스트한다.
    monkeypatch.setattr("app.services.processing.jobs.run_job", lambda job_id: None)
    monkeypatch.setattr("app.services.processing.jobs.run_section", lambda section_id: None)
    monkeypatch.setattr("app.services.storage.extract_video_duration", lambda path: 60)
    app.dependency_overrides[get_db] = lambda: db
    with InstructorTestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
