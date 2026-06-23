FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Stream stdout/stderr (the worker is long-running; unbuffered logs surface progress live).
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY cli.py worker.py schema.sql ./

VOLUME ["/app/input", "/app/output"]

# Default to the CLI; the worker service overrides this with `python worker.py`.
ENTRYPOINT ["python", "cli.py"]
