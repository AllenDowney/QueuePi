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

echo "== systemd units"
sudo install -m 0644 deploy/systemd/queuepi-server.service deploy/systemd/queuepi-display.service \
    /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now queuepi-server.service queuepi-display.service
sudo systemctl restart queuepi-server.service queuepi-display.service

echo "== status"
sleep 3
systemctl --no-pager --lines=0 status queuepi-server.service queuepi-display.service || true
curl -fsS http://127.0.0.1/healthz && echo
