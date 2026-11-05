#!/usr/bin/env bash
# Quick safety check for the server PC: is the database port open to the network, and is the API
# reachable only over plain HTTP? Prints OK / WARNING lines; exit code 1 if something is open.
#
#   server/scripts/check_security.sh
#
set -u
PORT="${GPUSA_DB_PORT:-5432}"
bad=0

if command -v ss >/dev/null 2>&1; then
  listeners="$(ss -H -ltn "sport = :$PORT" 2>/dev/null | awk '{print $4}')"
elif command -v netstat >/dev/null 2>&1; then
  listeners="$(netstat -ltn 2>/dev/null | awk -v p=":$PORT" '$4 ~ p"$" {print $4}')"
elif command -v lsof >/dev/null 2>&1; then
  listeners="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | awk 'NR>1 {print $9}')"
else
  echo "WARNING: neither ss, netstat nor lsof found; cannot check port $PORT."
  exit 1
fi

if [ -z "$listeners" ]; then
  echo "OK: nothing is listening on port $PORT here (database may run elsewhere or be stopped)."
else
  while read -r addr; do
    case "$addr" in
      127.0.0.1:*|\[::1\]:*|::1:*) echo "OK: database port listens on $addr (this PC only)." ;;
      *) echo "WARNING: database port listens on $addr - other computers can reach it."
         echo "         Fix: in postgresql.conf set listen_addresses = 'localhost', then restart PostgreSQL."
         bad=1 ;;
    esac
  done <<EOF
$listeners
EOF
fi

API_PORT="${GPUSA_PORT:-8000}"
if command -v ss >/dev/null 2>&1 && ss -H -ltn "sport = :$API_PORT" 2>/dev/null | grep -q .; then
  echo "NOTE: the API on port $API_PORT is plain HTTP; PINs travel unencrypted on the network. Put it behind HTTPS before using it outside a trusted LAN."
fi

[ "$bad" -eq 0 ] && echo "Done: no open database port found." || echo "Done: fix the warnings above."
exit "$bad"
