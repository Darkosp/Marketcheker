# backend

FastAPI апликацијата. Види го главниот [README](../README.md) за стартување.

```
app/
  core/      config.py (поставки од .env), logging.py, security.py (argon2)
  db/        base.py (Base, mixin-и), session.py (async engine/сесии)
  models/    домејн модели; сите се увезени во __init__.py за Alembic
  readers/   base.py = заеднички интерфејс; по еден модул на синџир
  api/       deps.py + routes/
  web/       Jinja2 шаблони и static (HTMX)
alembic/     миграции (само тие создаваат табели)
tests/       pytest; fixtures/ = зачувани примероци од ценовници
```

Внатре во контејнерот базата е на `db:5432`. За `pytest` директно на хостот:

```bash
POSTGRES_HOST=localhost POSTGRES_PORT=5434 pytest
```
