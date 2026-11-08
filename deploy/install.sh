#!/usr/bin/env bash
# Set up QueuePi on a Raspberry Pi running Pi OS Lite 64-bit.
#
# Run on the Pi, as the user "queuepi", from the QueuePi checkout in
# ~/QueuePi (which scripts/deploy.sh keeps up to date):
#
#     ~/QueuePi/deploy/install.sh
#
# Safe to run again: it doesn't overwrite an existing /etc/queuepi/config.toml
# or anything in /var/lib/queuepi.
set -euo pipefail

repo=$(cd "$(dirname "$0")/.." && pwd)
cd "$repo"

echo "== apt packages"
# python3-pygame: Debian's build includes SDL's kmsdrm driver (the PyPI
# pygame wheel doesn't). It pulls in the GBM/EGL/GLES libraries that Pi OS
# Lite lacks.
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv python3-pygame git

echo "== venv (uses Debian's pygame through --system-site-packages)"
if [ ! -d .venv ]; then
    python3 -m venv --system-site-packages .venv
fi
.venv/bin/pip install -q -e . segno

echo "== config"
if [ ! -f /etc/queuepi/config.toml ]; then
    sudo install -D -m 0640 -g queuepi deploy/config.toml /etc/queuepi/config.toml
    echo "installed /etc/queuepi/config.toml"
else
    echo "keeping existing /etc/queuepi/config.toml"
fi

echo "== hotspot profile"
# From wifi_ssid / wifi_password in the config; skipped if they aren't set.
# The profile is named QueuePi whatever the network name (hotspot.sh uses it).
read_setting() {
    sudo python3 -c 'import sys, tomllib; print(tomllib.load(open("/etc/queuepi/config.toml", "rb")).get(sys.argv[1]) or "")' "$1"
}
ssid=$(read_setting wifi_ssid)
psk=$(read_setting wifi_password)
hotspot=no
if nmcli -t -f NAME connection show | grep -qx QueuePi; then
    echo "keeping existing NetworkManager connection QueuePi"
    hotspot=yes
elif [ -n "$ssid" ] && [ -n "$psk" ]; then
    sudo nmcli connection add type wifi ifname uap0 con-name QueuePi autoconnect no ssid "$ssid" \
        802-11-wireless.mode ap 802-11-wireless.band bg 802-11-wireless.channel 1 \
        ipv4.method shared ipv4.addresses 10.42.0.1/24 ipv6.method disabled \
        wifi-sec.key-mgmt wpa-psk wifi-sec.proto rsn wifi-sec.pairwise ccmp wifi-sec.group ccmp \
        wifi-sec.psk "$psk"
    hotspot=yes
else
    echo "no wifi_ssid / wifi_password in the config: no hotspot"
fi

echo "== systemd units"
sudo install -m 0644 deploy/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now queuepi-server.service queuepi-display.service
sudo systemctl restart queuepi-server.service queuepi-display.service
if [ "$hotspot" = yes ]; then
    sudo systemctl enable queuepi-hotspot.service
    echo "hotspot starts at boot (queuepi-hotspot.service)"
fi

echo "== status"
sleep 3
systemctl --no-pager --lines=0 status queuepi-server.service queuepi-display.service || true
curl -fsS http://127.0.0.1/healthz && echo
