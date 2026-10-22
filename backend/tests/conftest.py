"""DB가 필요한 테스트는 PostgreSQL 테스트 DB(기본: <개발 DB 이름>_test)를 쓴다.

세션 시작 때 스키마를 지우고 alembic upgrade head + 시드를 실행한다.
각 테스트는 트랜잭션 안에서 돌고 끝나면 롤백되므로 서로 영향을 주지 않는다.
"""

import os
from collections.abc import Callable, Generator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.config import Settings


def resolve_test_database_url() -> str:
    explicit_url = os.environ.get("TEST_DATABASE_URL")
    if explicit_url:
        return explicit_url
    url = make_url(Settings().database_url)
    return url.set(database=f"{url.database}_test").render_as_string(hide_password=False)


# app.core.database가 import되며 엔진을 만들기 전에 테스트 DB로 바꿔 둔다.
TEST_DATABASE_URL = resolve_test_database_url()
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

from fastapi.testclient import TestClient  # noqa: E402

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from app.core.database import SessionLocal, engine, get_db  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User  # noqa: E402
from app.seed import seed  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parent.parent


def create_database_if_missing(database_url: str) -> None:
    url = make_url(database_url)
    admin_engine = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        exists = connection.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": url.database}
        )
        if not exists:
            connection.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin_engine.dispose()


def reset_schema() -> None:
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))


def run_migrations() -> None:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(config, "head")


@pytest.fixture(scope="session")
def prepared_database() -> None:
    create_database_if_missing(TEST_DATABASE_URL)
    reset_schema()
    run_migrations()
    with SessionLocal() as db:
        seed(db)


@pytest.fixture
def db(prepared_database: None) -> Generator[Session, None, None]:
    connection = engine.connect()
    transaction = connection.begin()
    # 코드 안의 commit()은 savepoint까지만 반영되고, 테스트가 끝나면 전부 롤백된다.
    session = Session(
        bind=connection,
        join_transaction_mode="create_savepoint",
        autoflush=False,
        expire_on_commit=False,
    )
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def client(db: Session) -> Generator[TestClient, None, None]:
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def get_user(db: Session) -> Callable[[str], User]:
    def find_by_email(email: str) -> User:
        return db.scalars(select(User).where(User.email == email)).one()

    return find_by_email


def auth_header(user_id: int) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}
