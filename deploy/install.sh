#!/usr/bin/env bash
# Idempotent install for the Telegram assistant on the Spark. Run from the
# repo root as giospark1: bash deploy/install.sh
set -euo pipefail

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -e '.[voice]'

mkdir -p knowledge data models

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "edit .env"
fi
chmod 600 .env

cat <<EOF

To install and start the systemd unit, run:
  sudo cp deploy/spark-assistant.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now spark-assistant
EOF
