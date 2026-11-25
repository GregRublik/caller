#!/usr/bin/env bash
# Native deployment of the caller GPU stack on a vast.ai instance (no Docker:
# vast containers forbid Docker-in-Docker). Installs three Python environments,
# downloads the models, and registers supervisor services.
#
# Run on the instance as root:
#   /workspace/caller/deploy/gpu-node/install.sh
#
# Assumes /workspace/caller is a copy of this repository and /workspace/caller/.env
# exists (see env.gpu.example).
set -euo pipefail

ROOT=${ROOT:-/workspace/caller}
VENVS="$ROOT/venvs"
APP_PY="$VENVS/app/bin/python"
LLM_PY="$VENVS/llm/bin/python"
TTS_PY="$VENVS/tts/bin/python"
export HF_HOME=${HF_HOME:-/workspace/.hf_home}
mkdir -p "$HF_HOME" "$VENVS" "$ROOT/logs"

echo "== free space before install"
df -h / | tail -1

echo "== apt packages"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
  ffmpeg libsndfile1 libgomp1 sox libsox-fmt-all

echo "== app venv (stt + tts + agent)"
uv venv --allow-existing --python 3.12 "$VENVS/app"
uv pip install --python "$APP_PY" \
  --index-url https://download.pytorch.org/whl/cu128 \
  torch==2.8.0 torchaudio==2.8.0 torchvision==0.23.0
uv pip install --python "$APP_PY" \
  transformers==4.57.3 accelerate==1.12.0 \
  hydra-core 'omegaconf>=2.3,<3' 'sentencepiece>=0.2,<1' \
  'fastapi>=0.115,<1' 'uvicorn[standard]>=0.30,<1' 'numpy>=1.26,<3' \
  'librosa>=0.10,<1' 'soundfile>=0.12,<1' 'sox>=1.4,<2' \
  'onnxruntime>=1.17,<2' 'einops>=0.7,<1' 'huggingface_hub>=0.30,<2' \
  livekit-agents==1.3.11 livekit-plugins-openai==1.3.11 livekit-plugins-silero==1.3.11 \
  'httpx>=0.27,<1' 'python-dotenv>=1.0,<2'
# GigaAM's remote code imports pyannote.audio (for VAD). Installing it without
# constraints upgrades torch (and leaves torchaudio mismatched), so pin the
# whole trio while resolving pyannote's dependencies.
printf 'torch==2.8.0\ntorchaudio==2.8.0\ntorchvision==0.23.0\n' \
  > /tmp/caller-torch-constraints.txt
uv pip install --python "$APP_PY" -c /tmp/caller-torch-constraints.txt \
  --index-url https://download.pytorch.org/whl/cu128 \
  --extra-index-url https://pypi.org/simple pyannote.audio
# Rust downloader for huggingface_hub: several times faster on multi-GB models.
uv pip install --python "$APP_PY" hf-transfer

echo "== llm venv (vLLM)"
uv venv --allow-existing --python 3.12 "$VENVS/llm"
if ! uv pip install --python "$LLM_PY" --torch-backend=cu128 vllm; then
  echo "torch-backend install failed, falling back to explicit cu128 torch"
  uv pip install --python "$LLM_PY" \
    --index-url https://download.pytorch.org/whl/cu128 torch torchaudio
  uv pip install --python "$LLM_PY" vllm
fi

echo "== tts venv (ESpeech / f5-tts)"
# Dedicated environment: f5-tts pulls its own heavy dependency tree (gradio,
# datasets, torchcodec, ...), which must not disturb the STT/agent env.
uv venv --allow-existing --python 3.12 "$VENVS/tts"
uv pip install --python "$TTS_PY" \
  --index-url https://download.pytorch.org/whl/cu128 \
  torch==2.8.0 torchaudio==2.8.0 torchvision==0.23.0
printf 'torch==2.8.0\ntorchaudio==2.8.0\ntorchvision==0.23.0\n' \
  > /tmp/caller-torch-constraints.txt
if ! uv pip install --python "$TTS_PY" -c /tmp/caller-torch-constraints.txt \
    'f5-tts==1.1.22' 'ruaccent==1.6.0'; then
  echo "f5-tts resolution with pinned torch failed; installing it unpinned"
  uv pip install --python "$TTS_PY" 'f5-tts==1.1.22' 'ruaccent==1.6.0'
fi

echo "== free space before model download"
df -h / | tail -1

if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/.env"
  set +a
fi

echo "== models"
HF="$VENVS/app/bin/hf"
export HF_HUB_ENABLE_HF_TRANSFER=${HF_HUB_ENABLE_HF_TRANSFER:-1}
download_model() {
  local repo=$1
  shift
  if ! "$HF" download "$repo" "$@"; then
    echo "download of $repo failed; retrying without hf_transfer"
    HF_HUB_ENABLE_HF_TRANSFER=0 "$HF" download "$repo" "$@"
  fi
}
download_model "${LLM_MODEL_ID:-Qwen/Qwen3-14B-FP8}"
download_model "${STT_MODEL_ID:-ai-sage/GigaAM-v3}" --revision "${STT_MODEL_REVISION:-e2e_rnnt}"
download_model "${TTS_MODEL_REPO:-ESpeech/ESpeech-TTS-1_RL-V2}"

echo "== tts assets (vocoder, RUAccent, voice reference)"
"$TTS_PY" - <<'PY' || true
from f5_tts.infer.utils_infer import load_vocoder
load_vocoder()
from ruaccent import RUAccent
acc = RUAccent()
acc.load(omograph_model_size="turbo3.1", use_dictionary=True, tiny_mode=False)
print("tts assets ready")
PY
mkdir -p "$ROOT/voice"
if [ ! -f "$ROOT/voice/ref.wav" ]; then
  echo "WARNING: voice reference is missing."
  echo "         Put a 5-15 s clean recording at $ROOT/voice/ref.wav"
  echo "         and set TTS_REF_TEXT (its exact transcript) in $ROOT/.env"
fi

echo "== supervisor units"
install -m 644 "$ROOT"/deploy/gpu-node/supervisor/caller-*.conf /etc/supervisor/conf.d/
supervisorctl reread
supervisorctl update

echo "== done; free space:"
df -h / | tail -1
supervisorctl status | grep -E 'caller-|^$' || true
