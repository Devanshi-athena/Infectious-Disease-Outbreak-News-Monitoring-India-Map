# One image for both services in docker-compose.yml: the dashboard and the scheduled collector.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY outbreak_monitor/ outbreak_monitor/
COPY config/ config/
COPY reference/ reference/
COPY evaluation/ evaluation/
COPY tests/ tests/
COPY .streamlit/ .streamlit/
COPY app.py README.md ./

# Database and logs live in /app/data (a volume in docker-compose.yml).
RUN useradd --create-home monitor && mkdir -p data && chown monitor data
USER monitor

EXPOSE 8501
CMD ["streamlit", "run", "app.py"]
