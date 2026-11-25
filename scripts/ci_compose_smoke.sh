#!/usr/bin/env bash
# Проверка образа и Compose: сборка, миграции, healthz, закрытая страница, резервная копия и восстановление
# на PostgreSQL, разбор PDF в контейнере, отсутствие секретов в журнале контейнера.
# Все значения синтетические и создаются на лету. Запуск: bash scripts/ci_compose_smoke.sh
set -euo pipefail
cd "$(dirname "$0")/.."

rand() { head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n'; }
SECRET="ci-secret-$(rand)"
DBPASS="ci-db-$(rand)"
VISITOR_PASS="Smoke-test-pass-81"
HOST_NAME="dataroom.example.com"

# Compose читает env_file из корня репозитория (../.env относительно deploy/).
if [ -e .env ]; then echo "refusing to overwrite an existing .env" >&2; exit 1; fi
cat > .env <<ENV
SECRET_KEY=$SECRET
PUBLIC_BASE_URL=https://$HOST_NAME
DATABASE_URL=postgres://dataroom:$DBPASS@db:5432/dataroom
POSTGRES_DB=dataroom
POSTGRES_USER=dataroom
POSTGRES_PASSWORD=$DBPASS
TRUST_PROXY_HEADERS=1
DEFAULT_LOCALE=en
PRIVATE_MEDIA_ROOT=/data/private_media
ENV

export COMPOSE_PROJECT_NAME=dataroomci
COMPOSE=(docker compose --env-file .env -f deploy/docker-compose.yml)
LOG="${RUNNER_TEMP:-/tmp}/compose-web.log"

cleanup() {
  "${COMPOSE[@]}" logs --no-color web > "$LOG" 2>&1 || true
  "${COMPOSE[@]}" down -v > /dev/null 2>&1 || true
  rm -f .env
}
trap cleanup EXIT

fail() { echo "FAIL: $*" >&2; "${COMPOSE[@]}" logs --no-color --tail 60 web >&2 || true; exit 1; }
run() { "${COMPOSE[@]}" exec -T web "$@"; }

"${COMPOSE[@]}" build
"${COMPOSE[@]}" up -d db
"${COMPOSE[@]}" run --rm web python manage.py migrate --noinput
"${COMPOSE[@]}" up -d web

echo "waiting for the web healthcheck"
state=""
for _ in $(seq 1 60); do
  state=$("${COMPOSE[@]}" ps --format '{{.Health}}' web || true)
  [ "$state" = "healthy" ] && break
  sleep 2
done
[ "$state" = "healthy" ] || fail "web did not become healthy (state: $state)"

status() { curl -s -o /dev/null -w "%{http_code}" -H "Host: $HOST_NAME" -H "X-Forwarded-Proto: https" "http://127.0.0.1:8000$1"; }
[ "$(status /healthz/)" = "200" ] || fail "healthz is not 200"
code=$(status /login/)
{ [ "$code" = "200" ] || [ "$code" = "302" ]; } || fail "the sign-in page returned $code"
[ "$(status /documents/1/view/)" != "200" ] || fail "a document page opens without signing in"

echo "backup and restore on PostgreSQL inside the container"
run python manage.py shell -c "
from apps.accounts.models import User
User.objects.create_user('smoke@example.com', '$VISITOR_PASS')
"
run python manage.py backup_instance /tmp/smoke.tar.gz
run python manage.py shell -c "
from apps.accounts.models import User
User.objects.all().delete()
"
run python manage.py restore_instance /tmp/smoke.tar.gz --replace
count=$(run python manage.py shell -c "
from apps.accounts.models import User
print(User.objects.filter(email='smoke@example.com').count())
" | tail -1 | tr -d '\r')
[ "$count" = "1" ] || fail "the restore did not bring the user back (count: $count)"

echo "PDF parsing in a separate process with its limits (Linux)"
pages=$(run python manage.py shell -c "
from apps.core.demo_files import make_pdf
from apps.rooms import previews
print(previews.pages_from_bytes(make_pdf('Smoke', [{'lines': ['a']}, {'lines': ['b']}])))
" | tail -1 | tr -d '\r')
[ "$pages" = "2" ] || fail "the PDF worker returned '$pages' pages instead of 2"

echo "the container log must not contain secrets"
"${COMPOSE[@]}" logs --no-color web > "$LOG" 2>&1
for value in "$SECRET" "$DBPASS" "$VISITOR_PASS"; do
  if grep -qF "$value" "$LOG"; then fail "a secret value is present in the container log"; fi
done
echo "OK"
