FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /srv/industry
ENV PYTHONUTF8=1 PYTHONUNBUFFERED=1 PATH="/srv/industry/.venv/bin:$PATH"
COPY . .
RUN python -m venv .venv \
    && .venv/bin/python -m pip install --no-cache-dir -e '.[dev]' -e './产业研究建议助手' \
    && .venv/bin/python -m pip check
CMD ["python", "-m", "uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
