#!/usr/bin/env bash
# Corre la suite de tests contra un PostgreSQL REAL y efimero (se levanta, se prueba y se borra solo).
# Uso:  bash tests/run_pg_tests.sh [argumentos de pytest]      (por defecto: toda la carpeta tests)
# Variables opcionales: PG_BIN (carpeta con initdb/pg_ctl), PG_PORT (default 55432), PG_TMP (carpeta de trabajo).
# Si queres probar contra un PostgreSQL ya levantado: TEST_DATABASE_URL=postgresql://... pytest tests
set -u
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"

PG_BIN="${PG_BIN:-}"
if [ -z "$PG_BIN" ]; then
  for d in /usr/lib/postgresql/*/bin /usr/local/pgsql/bin /opt/homebrew/opt/postgresql*/bin; do
    [ -x "$d/initdb" ] && PG_BIN="$d"
  done
fi
[ -x "$PG_BIN/initdb" ] || { echo "No encuentro PostgreSQL (initdb). Instalalo o defini PG_BIN."; exit 2; }

PORT="${PG_PORT:-55432}"
BASE="$(mktemp -d "${PG_TMP:-${TMPDIR:-/tmp}}/pgtest.XXXXXX")"
DATA="$BASE/data"; LOG="$BASE/pg.log"

# PostgreSQL no arranca como root: en ese caso corre como el usuario "claude" (o "nobody").
RUN=""
if [ "$(id -u)" = "0" ]; then
  U=claude; id "$U" >/dev/null 2>&1 || U=nobody
  chown "$U" "$BASE"; chmod 755 "$BASE"
  RUN="runuser -u $U --"
fi

parar() {
  $RUN "$PG_BIN/pg_ctl" -D "$DATA" -m immediate stop >/dev/null 2>&1
  rm -rf "$BASE"
}
trap parar EXIT

$RUN "$PG_BIN/initdb" -D "$DATA" -A trust -U postgres -E UTF8 >/dev/null || { echo "initdb fallo"; exit 2; }
$RUN "$PG_BIN/pg_ctl" -D "$DATA" -l "$LOG" -w -o "-p $PORT -k $BASE -c listen_addresses=127.0.0.1 -c fsync=off" start >/dev/null \
  || { echo "No arranco PostgreSQL:"; cat "$LOG"; exit 2; }
$RUN "$PG_BIN/createdb" -h 127.0.0.1 -p "$PORT" -U postgres peajecontrol_test || exit 2

export TEST_DATABASE_URL="postgresql://postgres@127.0.0.1:$PORT/peajecontrol_test"
cd "$RAIZ"
if [ $# -eq 0 ]; then set -- tests; fi
python3 -m pytest "$@"
RC=$?
exit $RC
