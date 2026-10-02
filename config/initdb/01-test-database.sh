#!/bin/sh
# Се извршува само еднаш, при првото креирање на pgdata volume-от.
# Создава одделна база за тестови, за да не ги гази развојните податоци.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
  CREATE DATABASE "${POSTGRES_DB}_test" OWNER "$POSTGRES_USER";
SQL

echo "Базата ${POSTGRES_DB}_test е создадена."
