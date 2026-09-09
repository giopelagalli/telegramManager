#!/usr/bin/env bash
# Nightly encrypted backup of knowledge/ and data/ to a restic repository
# (Backblaze B2 or any S3-compatible target).
#
# Install once:
#   sudo apt install restic
#
# One-time repo init, after .restic.env is in place (see below):
#   set -a; source /home/<user>/telegramManager/.restic.env; set +a
#   restic init
#
# Config: create /home/<user>/telegramManager/.restic.env (mode 600) with
# RESTIC_REPOSITORY, RESTIC_PASSWORD (or RESTIC_PASSWORD_FILE), and the
# backend credentials (e.g. B2_ACCOUNT_ID / B2_ACCOUNT_KEY for Backblaze B2,
# or the S3-compatible equivalent). See docs/setup-checklist.md for the B2
# setup steps.
#
# Cron (nightly at 03:15):
#   15 3 * * * /home/<user>/telegramManager/deploy/backup.sh >> /home/<user>/telegramManager/data/backup.log 2>&1
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

set -a
source "$ROOT/.restic.env"
set +a

cd "$ROOT"
restic backup knowledge data --tag assistant
restic forget --keep-daily 14 --keep-weekly 8 --keep-monthly 12 --prune
