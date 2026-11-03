#!/usr/bin/env bash
# GPUSA: sunucu + Admin + POS + Depo, hepsi bu bilgisayarda (Linux sunucu PC).
#
#   bash start_all.sh              # sunucu + üç uygulama
#   bash start_all.sh admin pos    # sunucu + sadece istediklerin (admin, pos, depo)
#
# Sırayla:
#   1. Sunucuyu başlatır (server/scripts/start_server.sh: Docker, Postgres, .env, tablolar, API).
#      Sunucu zaten açıksa dokunmaz.
#   2. Masaüstü uygulamaları için Python ortamını hazırlar (<repo>/.venv, ilk seferde kurar).
#   3. Uygulamaları bu sunucuya bağlı açar (GPUSA_SERVER_URL=http://127.0.0.1:PORT).
# Ctrl+C: uygulamaları ve bu pencerenin açtığı sunucuyu kapatır.
#
# Ortam değişkenleri (hepsi isteğe bağlı):
#   GPUSA_PORT        API portu (varsayılan 8000)
#   GPUSA_PYTHON      Uygulama ortamını kuracak python (varsayılan python3)
#   GPUSA_APP_VENV    Uygulama ortamı (varsayılan <repo>/.venv)
#   GPUSA_SKIP_DOCKER 1 ise Docker adımları atlanır (start_server.sh'e geçer)

set -u
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${GPUSA_PORT:-8000}"
URL="http://127.0.0.1:$PORT"
APP_VENV="${GPUSA_APP_VENV:-$REPO_DIR/.venv}"
PY="${GPUSA_PYTHON:-python3}"
SERVER_LOG="$HOME/gpusa-server-start.log"
SERVER_PID=""
APP_PIDS=()

ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; }
step() { printf '\n\033[1m%s\033[0m\n' "$*"; }

stop_all() {
    for pid in "${APP_PIDS[@]}"; do kill "$pid" 2>/dev/null; done
    if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
        kill "$SERVER_PID" 2>/dev/null   # start_server.sh kendi API'sini kapatır
        wait "$SERVER_PID" 2>/dev/null
        echo "Sunucu durduruldu."
    fi
}
pause() { [ -t 0 ] && read -r -p "Pencereyi kapatmak için Enter'a bas..." _; true; }
die() { bad "$*"; echo; echo "Başlatma durdu."; stop_all; pause; exit 1; }
trap 'echo; stop_all; exit 0' INT TERM

# Hangi uygulamalar
APPS=("$@")
[ ${#APPS[@]} -eq 0 ] && APPS=(admin pos depo)
for a in "${APPS[@]}"; do
    case "$a" in admin|pos|depo|depot) ;; *) die "Bilinmeyen uygulama: $a (admin, pos ya da depo yaz)";; esac
done

echo "GPUSA hepsi bir arada ($REPO_DIR)"

# --- 1. Sunucu ---------------------------------------------------------------
step "1/3  Sunucu ($URL)"
if curl -fsS -m 3 "$URL/health" >/dev/null 2>&1; then
    ok "Sunucu zaten çalışıyor, dokunulmadı"
else
    if [ "${GPUSA_SKIP_DOCKER:-0}" != "1" ] && command -v docker >/dev/null 2>&1 && ! docker info >/dev/null 2>&1; then
        warn "Docker kapalı, açılıyor (şifre sorabilir)..."
        sudo systemctl start docker || die "Docker açılamadı"
    fi
    echo "  Başlatılıyor, ayrıntılar: $SERVER_LOG"
    GPUSA_PORT="$PORT" bash "$REPO_DIR/server/scripts/start_server.sh" </dev/null >"$SERVER_LOG" 2>&1 &
    SERVER_PID=$!
    up=0
    # İlk seferde sunucu ortamını kurduğu için birkaç dakika sürebilir: süre sınırı yok, script ölürse durur.
    while kill -0 "$SERVER_PID" 2>/dev/null; do
        curl -fsS -m 2 "$URL/health" >/dev/null 2>&1 && { up=1; break; }
        sleep 1
    done
    if [ "$up" != "1" ]; then
        echo "--- sunucu çıktısı ---"; sed 's/\x1b\[[0-9;]*m//g' "$SERVER_LOG" | tail -n 25; echo "----------------------"
        die "Sunucu başlamadı (yukarıdaki ✗ satırına bak)"
    fi
    ok "Sunucu açıldı"
fi
dbh="$(curl -sS -m 5 "$URL/health/db" 2>/dev/null)"
case "$dbh" in *'"database":"ok"'*) ok "Veritabanı cevap veriyor" ;; *) die "Veritabanı cevap vermiyor: $dbh" ;; esac

# --- 2. Uygulama ortamı ------------------------------------------------------
step "2/3  Uygulama ortamı"
if [ ! -x "$APP_VENV/bin/python" ]; then
    warn "Python ortamı yok, kuruluyor (ilk seferde birkaç dakika sürer)..."
    "$PY" -m venv "$APP_VENV" || die "$PY -m venv başarısız"
fi
if ! "$APP_VENV/bin/python" -c "import PySide6, reportlab, openpyxl" 2>/dev/null; then
    warn "Paketler kuruluyor (PySide6 vb.)..."
    "$APP_VENV/bin/pip" install -q -r "$REPO_DIR/requirements.txt" \
        || die "pip install başarısız. Python çok yeniyse PySide6 kurulmayabilir: GPUSA_PYTHON=python3.12 ile dene"
fi
ok "Python ortamı hazır ($("$APP_VENV/bin/python" --version 2>&1))"

# --- 3. Uygulamalar ----------------------------------------------------------
step "3/3  Uygulamalar"
export GPUSA_SERVER_URL="$URL"   # hepsi bu sunucuya girer
export GPUSA_SERVER_REQUIRED=1   # sunucu yoksa sessizce yerel dosyaya düşmesin
cd "$REPO_DIR" || die "$REPO_DIR açılamadı"
NAMES=()
for a in "${APPS[@]}"; do
    case "$a" in
        admin)      mod=admin_app; name="Admin" ;;
        pos)        mod=pos_app;   name="POS" ;;
        depo|depot) mod=depot_app; name="Depo" ;;
    esac
    log="$HOME/gpusa-$a.log"
    "$APP_VENV/bin/python" -m "$mod.main" >"$log" 2>&1 &
    APP_PIDS+=($!)
    NAMES+=("$name|$log")
done
sleep 4
alive=0
for i in "${!APP_PIDS[@]}"; do
    name="${NAMES[$i]%%|*}"; log="${NAMES[$i]#*|}"
    if kill -0 "${APP_PIDS[$i]}" 2>/dev/null; then
        ok "$name açıldı (kayıt: $log)"; alive=$((alive + 1))
    else
        bad "$name hemen kapandı. Son satırlar:"; tail -n 8 "$log" | sed 's/^/      /'
        if grep -qiE "xcb|libEGL|libGL|libxkb" "$log"; then
            warn "Qt'nin ekran kütüphanesi eksik. CachyOS/Arch: sudo pacman -S --needed xcb-util-cursor libglvnd mesa libxkbcommon-x11"
        fi
    fi
done

echo
echo "Hepsi $URL sunucusunu kullanıyor. İlk girişte sunucudaki rozet ve PIN'ini (örn. B-001) kullan."
ips="$(ip -4 -o addr show scope global 2>/dev/null | awk '{split($4,a,"/"); print a[1]}')"
if [ -n "$ips" ]; then
    echo "Başka bilgisayardan bağlanmak için adres:"; for ip in $ips; do echo "    $ip:$PORT"; done
fi
echo
if [ "$alive" -gt 0 ]; then
    echo "Uygulamaları kapatınca bu pencere sunucuyu açık tutar. Her şeyi kapatmak için Ctrl+C."
    wait "${APP_PIDS[@]}" 2>/dev/null
    echo "Uygulamalar kapandı."
fi
if [ -n "$SERVER_PID" ]; then
    echo "Sunucu hâlâ açık (başka bilgisayarlar bağlanabilir). Kapatmak için Ctrl+C."
    wait "$SERVER_PID" 2>/dev/null
    echo "Sunucu durdu. Son satırlar:"; sed 's/\x1b\[[0-9;]*m//g' "$SERVER_LOG" | tail -n 10
fi
pause
