FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv

RUN groupadd -g 10001 bridge && useradd -u 10001 -g bridge -m -s /usr/sbin/nologin bridge

COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install . && pip cache purge

COPY app ./app

USER bridge:bridge
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
