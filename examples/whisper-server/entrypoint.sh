#!/bin/bash
set -euo pipefail

# ---- models: downloaded into the volume on the first start ----
: "${WHISPER_MODEL:=large-v3-turbo-q8_0}"
: "${MODELS_DIR:=/models}"
HF=https://huggingface.co
MODEL="${MODELS_DIR}/ggml-${WHISPER_MODEL}.bin"
VAD="${MODELS_DIR}/ggml-silero-v5.1.2.bin"

fetch() {
  [ -s "$2" ] && return 0
  echo "downloading $(basename "$2")..."
  curl -fL --retry 3 -o "$2.part" "$1" && mv "$2.part" "$2"
}

mkdir -p "$MODELS_DIR"
fetch "${HF}/ggerganov/whisper.cpp/resolve/main/ggml-${WHISPER_MODEL}.bin" "$MODEL"
fetch "${HF}/ggml-org/whisper-vad/resolve/main/ggml-silero-v5.1.2.bin" "$VAD"

# ---- server: the same flags c3parr uses locally (VAD, no context carried between chunks) ----
# -l auto: without it the server assumes English when the client sends no language, and returns
# the audio translated instead of transcribed.
exec whisper-server \
  --host 0.0.0.0 --port 9000 \
  --inference-path /v1/audio/transcriptions \
  -m "$MODEL" -mc 0 -l auto \
  --vad -vm "$VAD" -vsd 500 -vp 300 \
  "$@"
