#!/bin/zsh
# Start the local multilingual reranker for this LightRAG checkout.
# It intentionally binds to 127.0.0.1 so no API key or external access is needed.
set -euo pipefail

script_dir="${0:A:h}"
runtime="/Users/sakura/.venv-lightrag-reranker/bin/python3"
export HF_HUB_DISABLE_XET=1
export PYTORCH_ENABLE_MPS_FALLBACK=1

exec "$runtime" -m uvicorn local_reranker_service:app \
  --app-dir "$script_dir" \
  --host 127.0.0.1 \
  --port 8000
