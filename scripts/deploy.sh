#!/usr/bin/env bash
# Deploy the committed master branch to the Pi.
#
# Pushes to the git remote "pi" (the Pi's ~/QueuePi, a non-bare repo with
# receive.denyCurrentBranch=updateInstead, so the push updates its files).
# The Pi's venv has an editable install, so code changes take effect on
# restart; it is reinstalled only when pyproject.toml changes. Installs the
# systemd units when they change, and restarts the QueuePi services if they
# are installed. /etc/queuepi/config.toml is never touched.
#
# Only committed work is deployed; uncommitted changes stay on the laptop.
#
# One-time setup on the laptop:
#   git remote add pi queuepi@queuepi.local:QueuePi
set -euo pipefail

cd "$(dirname "$0")/.."
remote_url=$(git remote get-url pi)
host=${remote_url%%:*}
dir=${remote_url#*:}

if ! git diff --quiet HEAD; then
    echo "Note: uncommitted changes are not deployed." >&2
fi

old=$(ssh "$host" "git -C $dir rev-parse HEAD 2>/dev/null || echo none")
git push pi master

ssh "$host" bash -s -- "$dir" "$old" <<'EOF'
set -euo pipefail
cd "$1"
old=$2
if [ "$old" = none ] || ! git diff --quiet "$old" HEAD -- pyproject.toml; then
    echo "pyproject.toml changed: reinstalling"
    .venv/bin/pip install -q -e .
fi
if [ "$old" = none ] || ! git diff --quiet "$old" HEAD -- deploy/systemd; then
    echo "systemd units changed: installing"
    sudo install -m 0644 deploy/systemd/*.service /etc/systemd/system/
    sudo systemctl daemon-reload
fi
if ! git diff --quiet "$old" HEAD -- deploy/config.toml 2>/dev/null; then
    echo "Note: deploy/config.toml changed; /etc/queuepi/config.toml is not updated automatically."
fi
for unit in queuepi-server queuepi-display; do
    if systemctl is-enabled --quiet "$unit" 2>/dev/null; then
        sudo systemctl restart "$unit"
        echo "restarted $unit"
    fi
done
echo "Pi now at: $(git log --oneline -1)"
EOF
