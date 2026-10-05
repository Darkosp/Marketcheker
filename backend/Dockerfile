# syntax=docker/dockerfile:1

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Europe/Skopje

WORKDIR /app

# curl за healthcheck-от во docker compose; tzdata за Europe/Skopje.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl tzdata \
    && rm -rf /var/lib/apt/lists/*

# Зависностите во одделен слој: кодот се менува често, зависностите ретко.
#
# Се инсталира ЗАКЛУЧЕНИОТ список, не долните граници. Со `requirements.txt`
# секое повторно градење можеше тивко да повлече нова верзија: едно такво
# градење донесе selectolax 1.0, кој го отстрани разложувачот што го
# користевме, и читачите паднаа при увоз. На сервер тоа би се случило при
# првото пуштање.
#
# Нова верзија се зема намерно: се менува requirements.txt и се регенерира
# requirements.lock.txt (`docker compose exec api pip freeze`).
COPY requirements.txt requirements-dev.txt requirements.lock.txt ./
RUN pip install -r requirements.lock.txt

FROM base AS dev
# Развојните алатки се веќе во заклучениот список; ова е за случај кога
# requirements-dev.txt добил нешто ново пред да се регенерира lock.
RUN pip install -r requirements-dev.txt
COPY backend/ /app/
# Не работи како root.
RUN useradd --create-home --uid 1000 app && chown -R app:app /app
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

FROM base AS prod
COPY backend/ /app/
RUN useradd --create-home --uid 1000 app && chown -R app:app /app
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
