#!/bin/sh
# Дневна резервна копија на базата.
#
# Зошто воопшто: прочитаните ценовници од денес може да се прочитаат пак, но
# историјата на цени од пред шест месеци не се враќа од никаде. Таа е целата
# вредност на статистиките и на идната корпа.
#
# Копијата оди во ./backups на хостот. Тоа НЕ е резервна копија надвор од
# серверот - ако дискот пропадне, пропаѓа и таа. Кога ќе има што да се губи,
# истата датотека треба да се носи и настрана (rsync, S3, што било).
set -eu

KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"

while true; do
	stamp="$(date +%Y-%m-%d)"
	target="/backups/marketchecker-${stamp}.sql.gz"

	if pg_dump -h db -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner \
		| gzip -9 > "${target}.tmp"; then
		# Преименувањето е последно: недовршена копија никогаш не го носи
		# вистинското име, па враќањето не може да фати половина датотека.
		mv "${target}.tmp" "$target"
		echo "[backup] $(date -Iseconds) готово: $target ($(du -h "$target" | cut -f1))"
		find /backups -name 'marketchecker-*.sql.gz' -mtime "+${KEEP_DAYS}" -delete
	else
		rm -f "${target}.tmp"
		echo "[backup] $(date -Iseconds) ПАДНА" >&2
	fi

	sleep 86400
done
