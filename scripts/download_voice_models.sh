#!/usr/bin/env bash
# Fetches the Kokoro-82M ONNX voice model files used for TTS (bot/voice/tts.py)
# into ./models (or $1), where docker-compose mounts them read-only at /models
# (KOKORO_MODEL_DIR).
#
# This script deliberately does NOT guess a download URL. The two files below
# are release assets of the `kokoro-onnx` project (built from the
# `hexgrad/Kokoro-82M` weights). Grab the current release assets named exactly
# as below and place them in the target directory:
#
#   kokoro-v1.0.onnx
#   voices-v1.0.bin
#
# Sources to check for the current release:
#   https://github.com/thewh1teagle/kokoro-onnx/releases
#   https://huggingface.co/hexgrad/Kokoro-82M
set -euo pipefail

TARGET_DIR="${1:-./models}"
ONNX_FILE="kokoro-v1.0.onnx"
VOICES_FILE="voices-v1.0.bin"

mkdir -p "$TARGET_DIR"

if [[ -f "$TARGET_DIR/$ONNX_FILE" && -f "$TARGET_DIR/$VOICES_FILE" ]]; then
  echo "Both voice model files already present in $TARGET_DIR:"
  echo "  $TARGET_DIR/$ONNX_FILE"
  echo "  $TARGET_DIR/$VOICES_FILE"
  exit 0
fi

cat <<EOF
This script does not fetch the Kokoro voice model files automatically,
to avoid pulling from a guessed URL.

Download these two files and place them in: $TARGET_DIR

  $ONNX_FILE
  $VOICES_FILE

from the kokoro-onnx project's release assets:
  https://github.com/thewh1teagle/kokoro-onnx/releases

(built from the hexgrad/Kokoro-82M weights: https://huggingface.co/hexgrad/Kokoro-82M)

Then re-run this script to confirm both files are in place, or just
verify manually with:
  ls $TARGET_DIR
EOF
exit 1
