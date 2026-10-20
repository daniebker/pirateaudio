#!/usr/bin/env bash
# Copy this project to the Pi and restart the player if its code changed.
# Usage: scripts/deploy.sh [--install]   (--install also re-runs install.sh and reboots)
# Set PI_HOST to deploy somewhere other than pirateaudio.local.
set -euo pipefail

HERE=$(cd "$(dirname "$0")/.." && pwd)
HOST=${PI_HOST:-pirateaudio.local}
DEST=pirateplayer

echo "==> Copying to $HOST:~/$DEST"
# --delete keeps the Pi in step with this folder; .venv is the Pi's own and must survive.
changes=$(rsync -rlptz --delete --itemize-changes \
    --exclude .git --exclude .venv --exclude __pycache__ --exclude '*.pyc' --exclude '.lgd-*' \
    "$HERE/" "$HOST:$DEST/")
[ -n "$changes" ] && echo "$changes" || echo "Already up to date"

# sudo on the Pi asks for a password, so these need a terminal (-t).
if [ "${1:-}" = "--install" ]; then
    echo "==> Running install.sh and rebooting"
    ssh -t "$HOST" "cd $DEST && ./install.sh && sudo reboot"
elif grep -q ' pirateplayer/' <<<"$changes"; then
    echo "==> Restarting pirateplayer"
    ssh -t "$HOST" "sudo systemctl restart pirateplayer && systemctl is-active pirateplayer"
fi
