#!/usr/bin/env bash
# GPUSA sunucusunu tek seferde başlatır (sunucu PC, Linux):
#   Docker  ->  Postgres kapsayıcısı  ->  ayarlar  ->  tablolar  ->  API  ->  kontroller
# Çift tıklayarak açmak için once make_launcher.sh calistirilir.
#
# Ortam degiskenleri (hepsi istege bagli):
#   GPUSA_DB_CONTAINER  Postgres kapsayicisinin adi (varsayilan gpusa-db)
#   GPUSA_PORT          API portu (varsayilan 8000)
#   GPUSA_VENV          Python ortami (varsayilan <server>/.venv)
#   GPUSA_SKIP_DOCKER   1 ise Docker adimlari atlanir (Postgres baska yerde calisiyorsa)

set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONTAINER="${GPUSA_DB_CONTAINER:-gpusa-db}"
PORT="${GPUSA_PORT:-8000}"
export GPUSA_PORT="$PORT"   # the API announces this port to PCs looking for the server
VENV="${GPUSA_VENV:-$SERVER_DIR/.venv}"
LOG="$HOME/gpusa-server.log"
UVICORN_PID=""
FAILS=0

ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; FAILS=$((FAILS + 1)); }
step() { printf '\n\033[1m%s\033[0m\n' "$*"; }

finish() {
    if [ -n "$UVICORN_PID" ] && kill -0 "$UVICORN_PID" 2>/dev/null; then
        kill "$UVICORN_PID" 2>/dev/null
        wait "$UVICORN_PID" 2>/dev/null
        echo "API durduruldu."
    fi
    echo
    read -r -p "Pencereyi kapatmak için Enter'a bas..." _ || true
}
stop_here() { bad "$*"; echo; echo "Başlatma durdu. Yukarıdaki ✗ satırına bak."; finish; exit 1; }
trap 'finish; exit 0' INT TERM

echo "GPUSA sunucusu başlatılıyor ($SERVER_DIR)"

# --- 1. Docker ve Postgres ---------------------------------------------------
step "1/5  Docker ve veritabanı"
if [ "${GPUSA_SKIP_DOCKER:-0}" = "1" ]; then
    warn "GPUSA_SKIP_DOCKER=1: Docker adımları atlandı"
else
    command -v docker >/dev/null 2>&1 || stop_here "docker kurulu değil"
    if ! docker info >/dev/null 2>&1; then
        warn "Docker kapalı, açılıyor (şifre sorabilir)..."
        sudo systemctl start docker
        for _ in $(seq 1 20); do docker info >/dev/null 2>&1 && break; sleep 1; done
    fi
    docker info >/dev/null 2>&1 || stop_here "Docker açılamadı (sudo systemctl status docker ile bak)"
    ok "Docker çalışıyor"

    docker inspect "$CONTAINER" >/dev/null 2>&1 || stop_here "'$CONTAINER' adlı kapsayıcı yok (docker ps -a ile adına bak, GPUSA_DB_CONTAINER=ad ile ver)"
    if [ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER")" != "true" ]; then
        docker start "$CONTAINER" >/dev/null || stop_here "'$CONTAINER' başlatılamadı"
    fi
    ready=0
    for _ in $(seq 1 60); do
        if docker exec "$CONTAINER" pg_isready -U postgres >/dev/null 2>&1; then ready=1; break; fi
        sleep 1
    done
    [ "$ready" = "1" ] || stop_here "Postgres 60 saniyede hazır olmadı (docker logs $CONTAINER)"
    ok "Postgres kapsayıcısı '$CONTAINER' hazır"
    if ! docker port "$CONTAINER" 5432 >/dev/null 2>&1; then
        bad "Kapsayıcının 5432 portu bilgisayara açık değil, API bağlanamaz (kapsayıcıyı -p 127.0.0.1:5432:5432 ile yeniden oluştur)"
    elif docker port "$CONTAINER" 5432 2>/dev/null | grep -q '^0\.0\.0\.0:\|^\[::\]:'; then
        warn "Veritabanı portu (5432) ağdaki herkese açık. Sadece bu bilgisayardan erişilmeli: kapsayıcıyı -p 127.0.0.1:5432:5432 ile yeniden oluştur (README: 'Veritabanı portunu kapat'). Verin silinmez, volume aynı kalır."
    fi
fi

# --- 2. Python ortamı ve ayarlar --------------------------------------------
step "2/5  Python ortamı ve ayarlar"
if [ ! -x "$VENV/bin/python" ]; then
    warn "Python ortamı yok, kuruluyor (birkaç dakika sürebilir)..."
    python3 -m venv "$VENV" || stop_here "python3 -m venv başarısız"
    "$VENV/bin/pip" install -q -r "$SERVER_DIR/requirements.txt" || stop_here "pip install başarısız"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
"$VENV/bin/python" -c "import fastapi, uvicorn, psycopg, alembic, jwt" 2>/dev/null || {
    warn "Eksik paketler kuruluyor..."
    "$VENV/bin/pip" install -q -r "$SERVER_DIR/requirements.txt" || stop_here "pip install başarısız"
}
ok "Python ortamı hazır"

cd "$SERVER_DIR" || stop_here "$SERVER_DIR açılamadı"
[ -f .env ] && chmod 600 .env 2>/dev/null   # PIN gizli anahtarı ve kurulum kodu başkası tarafından okunmasın
[ -f .env ] || stop_here ".env dosyası yok ($SERVER_DIR/.env). README'deki 'Sign-in' adımlarına bak"
set -a; . ./.env; set +a
[ -n "${DATABASE_URL:-}" ] || stop_here ".env içinde DATABASE_URL yok"
ok ".env okundu (DATABASE_URL tanımlı)"
if [ -n "${GPUSA_JWT_SECRET:-}" ]; then ok "GPUSA_JWT_SECRET tanımlı"; else bad "GPUSA_JWT_SECRET yok: giriş çalışmaz (.env'ye ekle)"; fi

# --- 2b. Günlük yedek ve kurulum kodu ----------------------------------------
if [ "${GPUSA_SKIP_DOCKER:-0}" != "1" ] && [ "${GPUSA_SKIP_BACKUP:-0}" != "1" ]; then
    if bash "$SCRIPT_DIR/backup_db.sh" --daily >/dev/null 2>&1; then ok "Günlük yedek tamam (~/gpusa-backups)"; else warn "Yedek alınamadı (bash server/scripts/backup_db.sh ile elle dene)"; fi
fi
if [ -z "${GPUSA_SETUP_CODE:-}" ]; then
    # Yeni kurulumda ilk yöneticiyi sunucuya ulaşan herkes kuramasın: rastgele bir kurulum kodu .env'ye yazılır.
    GPUSA_SETUP_CODE="$("$VENV/bin/python" -c "import secrets;print(''.join(secrets.choice('ABCDEFGHJKMNPQRSTUVWXYZ23456789') for _ in range(8)))")"
    printf '\nGPUSA_SETUP_CODE=%s\n' "$GPUSA_SETUP_CODE" >> .env
    export GPUSA_SETUP_CODE
fi

# --- 3. Veritabanı bağlantısı ve tablolar ------------------------------------
step "3/5  Veritabanı bağlantısı ve tablolar"
if "$VENV/bin/python" -c "from gpusa_server import db; from gpusa_server.config import load_settings; db.ping(load_settings())" 2>/dev/null; then
    ok "API kodu veritabanına bağlanabiliyor"
else
    stop_here "Veritabanına bağlanılamadı (DATABASE_URL'deki şifre/port yanlış olabilir)"
fi
if "$VENV/bin/alembic" upgrade head >/dev/null 2>&1; then
    ok "Tablolar güncel: $("$VENV/bin/alembic" current 2>/dev/null | tail -1)"
else
    stop_here "alembic upgrade head başarısız (elle çalıştırıp hatayı gör)"
fi

# --- 4. API ------------------------------------------------------------------
step "4/5  API ($PORT portu)"
if curl -fsS -m 3 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
    warn "API zaten çalışıyor (başka bir pencerede?), tekrar başlatılmadı"
elif ss -tln 2>/dev/null | grep -q ":$PORT "; then
    stop_here "$PORT portunu başka bir program kullanıyor (ss -tlnp | grep $PORT)"
else
    : > "$LOG"
    "$VENV/bin/uvicorn" gpusa_server.main:app --host 0.0.0.0 --port "$PORT" >>"$LOG" 2>&1 &
    UVICORN_PID=$!
    up=0
    for _ in $(seq 1 30); do
        curl -fsS -m 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && { up=1; break; }
        kill -0 "$UVICORN_PID" 2>/dev/null || break
        sleep 1
    done
    if [ "$up" = "1" ]; then
        ok "API başlatıldı (kayıt dosyası: $LOG)"
    else
        echo "--- son log satırları ---"; tail -n 15 "$LOG"; echo "-------------------------"
        if grep -qi "address already in use" "$LOG"; then
            stop_here "$PORT portunu başka bir program kullanıyor (ss -tlnp | grep $PORT)"
        fi
        stop_here "API başlamadı"
    fi
fi

# --- 5. Kontroller -----------------------------------------------------------
step "5/5  Kontroller"
health="$(curl -fsS -m 3 "http://127.0.0.1:$PORT/health" 2>/dev/null)" && ok "/health: $health" || bad "/health cevap vermiyor"
dbh="$(curl -sS -m 5 "http://127.0.0.1:$PORT/health/db" 2>/dev/null)"
case "$dbh" in *'"database":"ok"'*) ok "/health/db: veritabanı ok" ;; *) bad "/health/db: $dbh" ;; esac
if ! command -v ufw >/dev/null 2>&1 || ! ufw status 2>/dev/null | grep -q "Status: active"; then
    :
else
    warn "ufw açık: başka bilgisayardan erişim için 'sudo ufw allow $PORT/tcp' ve otomatik bulma için 'sudo ufw allow 47808/udp' gerekebilir"
fi

echo
echo "İlk yönetici henüz yoksa Admin'in sorduğu KURULUM KODU: $GPUSA_SETUP_CODE"
echo "(Yönetici oluştuktan sonra gerekmez; kimseyle paylaşma.)"
echo
echo "Başka bilgisayarlar sunucuyu kendisi bulur; bulamazsa giriş ekranındaki \"Sunucu adresini gir\" kutusuna şunlardan birini yaz:"
ips="$(ip -4 -o addr show scope global 2>/dev/null | awk '{split($4,a,"/"); print a[1]}')"
if [ -n "$ips" ]; then for ip in $ips; do echo "    $ip:$PORT"; done; else warn "IP bulunamadı (ip -4 addr)"; fi

echo
if [ "$FAILS" -gt 0 ]; then
    echo "⚠ $FAILS sorun var (yukarıdaki ✗ satırlarına bak)."
else
    echo "✓ Her şey tamam. Bu pencere açık kaldığı sürece sunucu çalışır."
fi
if [ -n "$UVICORN_PID" ]; then
    echo "Kapatmak için bu pencerede Ctrl+C'ye bas."
    wait "$UVICORN_PID" 2>/dev/null
    echo "API beklenmedik şekilde durdu. Son log:"; tail -n 15 "$LOG"
fi
finish
