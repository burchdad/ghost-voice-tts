FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements-railway.txt ./
RUN pip install --upgrade pip setuptools wheel && pip install -r requirements-railway.txt

COPY railway_app.py ./railway_app.py

EXPOSE 8000

CMD ["sh", "-c", "uvicorn railway_app:app --host 0.0.0.0 --port ${PORT:-8000}"]