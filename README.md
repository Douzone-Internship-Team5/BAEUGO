# 배우고 (BAEUGO)

직무 교육 숏폼 학습 플랫폼. 더존 ICT 인턴십 5조. **백엔드 레포**

프론트엔드는 별도 레포에서 관리한다: (프론트 레포 주소)

| 영역 | 기술 |
| --- | --- |
| Backend | FastAPI, SQLAlchemy 2, Alembic |
| Database | PostgreSQL 16 |
| 실행 환경 | Docker Compose |

## 폴더 구조

```
backend/
  app/
    main.py            FastAPI 앱, 라우터 등록
    core/              설정, DB 연결, 에러 응답 형식
    routers/           엔드포인트 (auth.py, users.py, admin.py, courses.py ...)
    services/          처리 로직
    schemas/           요청·응답 형식 (Pydantic)
    models/            DB 테이블 (SQLAlchemy)
  alembic/             DB 마이그레이션
  tests/
docker-compose.yml     db + api
```

## 실행

### 1. 환경 변수

```bash
cp .env.example .env
```

### 2. 백엔드 + DB (Docker)

```bash
docker compose up --build
```

- API: http://localhost:8000/api/v1/health
- API 문서: http://localhost:8000/docs
- 컨테이너가 뜰 때 `alembic upgrade head`가 자동 실행됨

### 3. 시드 데이터 (목업 계정, 직무·주제)

```bash
docker compose exec api python -m app.seed
```

- 없는 데이터만 넣으므로 여러 번 실행해도 중복되지 않는다
- 목업 계정 비밀번호는 모두 `baeugo1234`

| 이메일 | 상태 |
| --- | --- |
| learner1@baeugo.test | 일반 회원, 역할 없음 (역할 선택·온보딩 시연) |
| learner2@baeugo.test | 수강자, 온보딩 완료 |
| instructor1@baeugo.test | 강사, 온보딩 완료 |
| instructor2@baeugo.test | 강사, 온보딩 완료 |
| admin@baeugo.test | 운영자 |

### Docker 없이 백엔드만 실행할 때

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
alembic upgrade head
python -m app.seed
uvicorn app.main:app --reload
```

- Docker의 DB는 호스트 `5433` 포트로 열린다 (PC에 설치된 PostgreSQL의 5432와 겹치지 않게)

## 테스트·린트

PR 전에 컨테이너 안에서 실행한다 (CI 없음).

```bash
docker compose exec api sh -c "ruff check . && ruff format --check . && python -m pytest -q"
```

- DB가 필요한 테스트는 `<DB 이름>_test` DB(기본 `baeugo_test`)를 자동으로 만들어 쓴다. 개발 DB는 건드리지 않는다
- 다른 DB로 테스트하려면 `TEST_DATABASE_URL`을 지정한다

## DB 마이그레이션

모델(`backend/app/models/`)을 추가하거나 바꾼 뒤:

```bash
docker compose exec api alembic revision --autogenerate -m "add users"
docker compose exec api alembic upgrade head
```

- 새 모델 파일은 `app/models/__init__.py`에 import해야 인식됨
- 마이그레이션 파일은 한 사람씩 만든다 (동시에 만들면 충돌)

## 백엔드 작성 규칙

- 성공 응답은 `DataResponse[...]`로 감싼다 → `{ "data": ... }`
- 목록은 `DataResponse[PageData[...]]` → `{ "data": { "items", "page", "size", "total" } }`
- 에러는 `AppError(상태코드, "에러코드", "메시지")`를 raise → `{ "error": { "code", "message" } }`
- 요청·응답 스키마는 `CamelModel`을 상속 → 파이썬은 snake_case, JSON은 camelCase
- DB 세션은 `db: DbSession`으로 받는다

```python
from fastapi import APIRouter

from app.core.database import DbSession
from app.core.errors import AppError
from app.schemas.common import CamelModel, DataResponse

router = APIRouter(prefix="/users", tags=["users"])


class RoleRequest(CamelModel):
    role: str


@router.post("/me/role", response_model=DataResponse[RoleRequest])
def select_role(body: RoleRequest, db: DbSession):
    if body.role not in ("LEARNER", "INSTRUCTOR"):
        raise AppError(400, "VALIDATION_ERROR", "역할 값을 확인해 주세요.")
    return DataResponse(data=body)
```

## Git 규칙

### 브랜치
- `main`에 직접 push하지 않는다. 기능 브랜치에서 작업하고 PR로 합친다
- 이름: `종류/내용` (영어 소문자, 단어는 `-`로 연결)
  - `feat/auth-login`, `feat/course-upload`, `fix/quiz-score`, `chore/docker`

### 커밋 메시지
- 형식: `[종류] 내용` (한글 가능, 마침표 없이)
- 종류
  - `feat`: 새 기능
  - `fix`: 버그 수정
  - `refactor`: 동작은 같고 구조만 개선
  - `docs`: 문서 수정
  - `test`: 테스트 추가·수정
  - `chore`: 설정, 패키지, 빌드 등 기타
- 예: `[feat] 목업 로그인 API 추가`, `[fix] 온보딩 주제 0개 검증 누락 수정`

### PR
- 제목은 커밋 메시지 형식과 같게 쓴다
- 본문에 작업 내용, 관련 엔드포인트 번호(A-1, B-3 등), 확인 방법을 적는다 (템플릿 자동 적용)
- 팀원 1명이 확인한 뒤 merge한다
- `.env`는 올리지 않는다 (`.env.example`만 올린다)

## 코드 규칙

### 공통
- 이름만 보고 역할을 알 수 있게 작성한다. `data`, `temp`, `a`, `b` 같은 모호한 이름은 지양한다
- 클래스명은 PascalCase (`CourseService`, `User`), 상수는 대문자와 언더바 (`MAX_UPLOAD_SIZE`)
- 함수는 가능한 한 하나의 기능만 수행하고, 길어지면 분리한다
- 조건문이 길거나 복잡하면 의미 있는 변수나 함수로 분리한다
- 중첩 if문이 깊어지지 않도록 조기 반환(early return)을 활용한다
- Boolean 값은 true/false와 직접 비교하지 않는다
- 주석은 코드만으로 알 수 없는 "이유"를 설명할 때만 작성한다
- 예외 처리는 구체적으로 작성하고, 에러 응답은 엔드포인트 명세의 에러 코드를 따른다
- 같은 로직이 반복되면 공통 함수로 분리한다
- 디버깅용 출력문(`print`)은 커밋 전에 지운다
- 짧은 코드보다 읽기 쉬운 코드를 우선한다
- 들여쓰기, 공백, 줄바꿈, 미사용 import는 포매터로 자동 정리한다 (저장 시 자동, `.vscode/settings.json`)

### 백엔드 (Python / FastAPI)
- 변수·함수명은 snake_case (`user_id`, `create_course()`)
- Boolean 예: `if is_published:` / None 비교는 `is None`
- 역할별로 파일을 나눈다: `routers/` 엔드포인트, `services/` 처리 로직, `schemas/` 요청·응답 형식, `models/` DB 테이블
- 요청·응답 스키마는 `CamelModel`을 상속한다 (JSON 필드는 camelCase로 자동 변환)
- 포매터·린터: ruff (`ruff format .` / `ruff check .`)
