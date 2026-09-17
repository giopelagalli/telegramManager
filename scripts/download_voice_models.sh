#!/usr/bin/env bash
# Fetches the two Kokoro-82M ONNX files used for voice notes (bot/voice/tts.py)
# into ./models (or $1). They are release assets of the kokoro-onnx project,
# built from the hexgrad/Kokoro-82M weights:
#   https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0
set -euo pipefail

TARGET_DIR="${1:-./models}"
BASE_URL="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"

mkdir -p "$TARGET_DIR"
for f in kokoro-v1.0.onnx voices-v1.0.bin; do
  if [[ -f "$TARGET_DIR/$f" ]]; then
    echo "already have $TARGET_DIR/$f"
    continue
  fi
  echo "downloading $f ..."
  curl -fL --progress-bar -o "$TARGET_DIR/$f.part" "$BASE_URL/$f"
  mv "$TARGET_DIR/$f.part" "$TARGET_DIR/$f"
done
ls -lh "$TARGET_DIR"
