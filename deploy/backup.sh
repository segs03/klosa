#!/bin/bash
set -euo pipefail
set -a; . /etc/klosa/env; set +a
STAMP=$(date +%Y%m%d-%H%M); OUT=/var/backups/klosa; mkdir -p "$OUT"
PGPASSWORD="$DB_PASSWORD" pg_dump -h "$DB_HOST" -U "$DB_USER" -Fc "$DB_NAME" \
  | gpg --batch --yes --encrypt --recipient "$BACKUP_GPG_KEY" > "$OUT/db-$STAMP.dump.gpg"
tar czf - -C "$(dirname "$PRIVATE_MEDIA_ROOT")" "$(basename "$PRIVATE_MEDIA_ROOT")" \
  | gpg --batch --yes --encrypt --recipient "$BACKUP_GPG_KEY" > "$OUT/kyc-$STAMP.tgz.gpg"
find "$OUT" -type f -mtime +30 -delete