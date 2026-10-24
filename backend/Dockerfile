FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Whisper가 CUDA용 PyTorch를 가져오지 않도록 CPU 빌드를 먼저 설치한다.
RUN pip install --no-cache-dir torch==2.13.0+cpu --index-url https://download.pytorch.org/whl/cpu

# 컨테이너 안에서 pytest·ruff를 돌릴 수 있도록 개발용 패키지까지 설치한다.
COPY requirements.txt requirements-dev.txt ./
# Whisper는 Linux에서 Triton을 필수 의존성으로 선언하지만 CPU 실행에는 사용하지 않는다.
# 전체 의존성 검사 후 같은 레이어에서 제거해야 이미지에도 남지 않는다.
# 제거 후 pip check의 openai-whisper -> triton 누락 경고는 의도된 CPU 전용 예외다.
RUN pip install --no-cache-dir -r requirements-dev.txt \
    && pip check \
    && pip uninstall -y triton \
    && python -c "import importlib.util, torch, whisper; assert torch.__version__ == '2.13.0+cpu'; assert torch.version.cuda is None, 'CPU-only PyTorch required'; assert importlib.util.find_spec('triton') is None"

COPY . .

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"]
