#!/usr/bin/env bash
# Installerer venteliste-monitor som en systemd-tjeneste.
#
# Koeres paa en frisk Ubuntu-maskine (fx Oracle Cloud Always Free):
#   curl -sSL https://raw.githubusercontent.com/emilbernekilde-star/venteliste-monitor/main/deploy/setup.sh | sudo bash
#
# Kan koeres igen uden problemer - den opdaterer blot en eksisterende installation.

set -euo pipefail

REPO="https://github.com/emilbernekilde-star/venteliste-monitor.git"
DIR="/opt/venteliste-monitor"
BRUGER="venteliste"

if [ "$(id -u)" -ne 0 ]; then
    echo "Kør med sudo: sudo bash setup.sh" >&2
    exit 1
fi

echo "==> Installerer systempakker"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git ca-certificates

echo "==> Opretter brugeren '$BRUGER' (uden login, uden hjemmemappe)"
if ! id "$BRUGER" >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$BRUGER"
fi

echo "==> Henter koden til $DIR"
# Mappen ejes af '$BRUGER', men git koerer som root. Uden denne undtagelse
# afviser git at roere repoet med "detected dubious ownership" - hvilket kun
# rammer ved OPDATERING, ikke ved foerste installation.
git config --global --add safe.directory "$DIR" 2>/dev/null || true

if [ -d "$DIR/.git" ]; then
    git -C "$DIR" fetch --quiet origin main
    git -C "$DIR" reset --hard --quiet origin/main
else
    rm -rf "$DIR"
    git clone --quiet "$REPO" "$DIR"
fi

echo "==> Opsætter Python-miljø"
python3 -m venv "$DIR/venv"
"$DIR/venv/bin/pip" install --quiet --upgrade pip
"$DIR/venv/bin/pip" install --quiet -r "$DIR/requirements.txt"

chown -R "$BRUGER:$BRUGER" "$DIR"

echo "==> Installerer systemd-tjenesten"
cp "$DIR/deploy/venteliste-monitor.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now venteliste-monitor

echo
echo "==> Færdig. Status:"
systemctl --no-pager --lines=0 status venteliste-monitor || true
echo
echo "Se loggen live med:   sudo journalctl -u venteliste-monitor -f"
echo "Genstart med:         sudo systemctl restart venteliste-monitor"
