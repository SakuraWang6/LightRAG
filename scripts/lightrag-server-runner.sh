#!/usr/bin/env bash
# Run LightRAG in the configured Conda environment.  This is intentionally a
# small wrapper so the service manager and macOS launchd use the same command.

set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCAL_SETTINGS="$REPO_ROOT/scripts/lightrag-service.local.env"

if [[ -f "$LOCAL_SETTINGS" ]]; then
  # shellcheck disable=SC1090
  source "$LOCAL_SETTINGS"
fi

cd "$REPO_ROOT"

CONDA_EXE="${LIGHTRAG_CONDA_EXE:-}"
if [[ -z "$CONDA_EXE" && -x "/Users/sakura/miniconda3/bin/conda" ]]; then
  # Default for this local development machine. Set LIGHTRAG_CONDA_EXE in the
  # local settings file when Conda is installed somewhere else.
  CONDA_EXE="/Users/sakura/miniconda3/bin/conda"
fi
if [[ -z "$CONDA_EXE" ]]; then
  CONDA_EXE="$(command -v conda || true)"
fi

if [[ -z "$CONDA_EXE" || ! -x "$CONDA_EXE" ]]; then
  echo "LightRAG cannot start: Conda was not found." >&2
  echo "Set LIGHTRAG_CONDA_EXE in scripts/lightrag-service.local.env." >&2
  exit 1
fi

CONDA_ENV_NAME="${LIGHTRAG_CONDA_ENV:-lightrag-memory-eval}"
export PYTHONUNBUFFERED=1

# Pin tokenizer assets to project-local durable storage. This prevents a later
# document parse or test from trying to download tiktoken encodings at runtime.
export TIKTOKEN_CACHE_DIR="${LIGHTRAG_TIKTOKEN_CACHE_DIR:-$REPO_ROOT/var/tiktoken-cache}"
mkdir -p "$TIKTOKEN_CACHE_DIR"

# CairoSVG's CFFI loader does not search Homebrew's ARM prefix by default.
# Keep the system library visible to native DOCX/SVG parsing without changing
# the user's global shell configuration.
if [[ -d "/opt/homebrew/opt/cairo/lib" ]]; then
  # Conda deliberately strips DYLD_* from its outer environment. Preserve the
  # intent in a neutral variable and restore it inside the Conda child below.
  export LIGHTRAG_CAIRO_LIBRARY_DIR="/opt/homebrew/opt/cairo/lib"
fi

exec "$CONDA_EXE" run --no-capture-output -n "$CONDA_ENV_NAME" /bin/bash -lc '
  if [[ -n "${LIGHTRAG_CAIRO_LIBRARY_DIR:-}" ]]; then
    export DYLD_FALLBACK_LIBRARY_PATH="${LIGHTRAG_CAIRO_LIBRARY_DIR}${DYLD_FALLBACK_LIBRARY_PATH:+:$DYLD_FALLBACK_LIBRARY_PATH}"
  fi
  exec lightrag-server
'
