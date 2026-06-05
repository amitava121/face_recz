#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f .env ]]; then
  eval "$(
    python3 - <<'PY'
from pathlib import Path
import shlex

for raw_line in Path(".env").read_text().splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    key = key.strip()
    if not key:
        continue
    if " #" in value:
        value = value.split(" #", 1)[0]
    print(f"export {key}={shlex.quote(value)}")
PY
  )"
fi

TARGET_DATABASE_URL="${1:-${TARGET_DATABASE_URL:-}}"
if [[ -z "${TARGET_DATABASE_URL}" ]]; then
  echo "Usage: $0 <target_database_url>"
  echo "Or set TARGET_DATABASE_URL in the environment."
  exit 1
fi

if [[ -n "${SOURCE_DATABASE_URL:-}" ]]; then
  SOURCE_DATABASE_URL="$SOURCE_DATABASE_URL"
elif [[ -n "${DATABASE_URL:-}" ]]; then
  SOURCE_DATABASE_URL="$DATABASE_URL"
else
  : "${DB_USER:?DB_USER is required when DATABASE_URL is not set}"
  : "${DB_PASS:?DB_PASS is required when DATABASE_URL is not set}"
  : "${DB_HOST:?DB_HOST is required when DATABASE_URL is not set}"
  : "${DB_PORT:?DB_PORT is required when DATABASE_URL is not set}"
  : "${DB_NAME:?DB_NAME is required when DATABASE_URL is not set}"
  SOURCE_DATABASE_URL="postgresql://${DB_USER}:${DB_PASS}@${DB_HOST}:${DB_PORT}/${DB_NAME}"
fi

if [[ "$TARGET_DATABASE_URL" != *"sslmode="* ]]; then
  if [[ "$TARGET_DATABASE_URL" == *"?"* ]]; then
    TARGET_DATABASE_URL="${TARGET_DATABASE_URL}&sslmode=require"
  else
    TARGET_DATABASE_URL="${TARGET_DATABASE_URL}?sslmode=require"
  fi
fi

TABLES=(admin attendance face_images students system_settings user_activities users)

echo "Checking source connection..."
psql "$SOURCE_DATABASE_URL" -v ON_ERROR_STOP=1 -Atc "select current_database(), current_user;"

echo "Checking Supabase target connection..."
psql "$TARGET_DATABASE_URL" -v ON_ERROR_STOP=1 -Atc "select current_database(), current_user;"

echo "Applying schema to Supabase..."
pg_dump "$SOURCE_DATABASE_URL" \
  --schema-only \
  --clean \
  --if-exists \
  --no-owner \
  --no-privileges \
  --quote-all-identifiers \
  | psql "$TARGET_DATABASE_URL" -v ON_ERROR_STOP=1

echo "Truncating migrated tables on Supabase before data load..."
for table in "${TABLES[@]}"; do
  psql "$TARGET_DATABASE_URL" -v ON_ERROR_STOP=1 -c "TRUNCATE TABLE public.\"${table}\" RESTART IDENTITY CASCADE;"
done

echo "Loading data into Supabase..."
pg_dump "$SOURCE_DATABASE_URL" \
  --data-only \
  --inserts \
  --quote-all-identifiers \
  $(printf -- '--table=public.%s ' "${TABLES[@]}") \
  | psql "$TARGET_DATABASE_URL" -v ON_ERROR_STOP=1

echo "Resetting sequences on Supabase..."
psql "$TARGET_DATABASE_URL" -v ON_ERROR_STOP=1 <<'SQL'
DO $$
DECLARE
  rec record;
BEGIN
  FOR rec IN
    SELECT
      c.relname AS table_name,
      a.attname AS column_name,
      s.relname AS sequence_name
    FROM pg_class s
    JOIN pg_depend d ON d.objid = s.oid AND d.deptype = 'a'
    JOIN pg_class c ON d.refobjid = c.oid
    JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = d.refobjsubid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE s.relkind = 'S'
      AND n.nspname = 'public'
      AND c.relname IN ('admin', 'attendance', 'face_images', 'students', 'system_settings', 'user_activities', 'users')
  LOOP
    EXECUTE format(
      'SELECT setval(%L, COALESCE((SELECT MAX(%I) FROM public.%I), 1), true)',
      'public.' || rec.sequence_name,
      rec.column_name,
      rec.table_name
    );
  END LOOP;
END $$;
SQL

echo "Verifying row counts on source..."
psql "$SOURCE_DATABASE_URL" -v ON_ERROR_STOP=1 -At <<'SQL'
select 'admin='||(select count(*) from public.admin);
select 'attendance='||(select count(*) from public.attendance);
select 'face_images='||(select count(*) from public.face_images);
select 'students='||(select count(*) from public.students);
select 'system_settings='||(select count(*) from public.system_settings);
select 'user_activities='||(select count(*) from public.user_activities);
select 'users='||(select count(*) from public.users);
SQL

echo "Verifying row counts on Supabase..."
psql "$TARGET_DATABASE_URL" -v ON_ERROR_STOP=1 -At <<'SQL'
select 'admin='||(select count(*) from public.admin);
select 'attendance='||(select count(*) from public.attendance);
select 'face_images='||(select count(*) from public.face_images);
select 'students='||(select count(*) from public.students);
select 'system_settings='||(select count(*) from public.system_settings);
select 'user_activities='||(select count(*) from public.user_activities);
select 'users='||(select count(*) from public.users);
SQL
