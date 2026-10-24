FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Whisper가 CUDA용 PyTorch를 가져오지 않도록 CPU 빌드를 먼저 설치한다.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# 컨테이너 안에서 pytest·ruff를 돌릴 수 있도록 개발용 패키지까지 설치한다.
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt \
    && pip check \
    && python -c "import torch; assert torch.version.cuda is None, 'CPU-only PyTorch required'"

COPY . .

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"]
