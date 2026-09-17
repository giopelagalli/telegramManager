#!/usr/bin/env bash
# Idempotent install for the Telegram assistant. Run from the repo root:
# bash deploy/install.sh
set -euo pipefail

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -e '.[voice]'

mkdir -p knowledge data models

if ! command -v ffmpeg >/dev/null; then
  echo "voice notes need ffmpeg: sudo apt install -y ffmpeg"
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "edit .env"
fi
chmod 600 .env

sed -e "s|__USER__|$USER|g" -e "s|__ROOT__|$(pwd)|g" \
  deploy/assistant.service > deploy/assistant.service.generated

cat <<MSG

To install and start the systemd unit, run:
  sudo cp $(pwd)/deploy/assistant.service.generated /etc/systemd/system/assistant.service && sudo systemctl daemon-reload && sudo systemctl enable --now assistant
MSG
