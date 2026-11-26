FROM python:3.11-slim

# Unbuffered stdout so JSON log lines reach `docker compose logs` immediately
ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# Exec form: python is PID 1 and receives SIGTERM directly for graceful shutdown
CMD ["python", "main.py"]
