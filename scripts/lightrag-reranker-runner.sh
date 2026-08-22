#!/usr/bin/env bash
# Run the locally configured reranker under launchd or lightrag-service.

set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCAL_SETTINGS="$REPO_ROOT/scripts/lightrag-service.local.env"

if [[ -f "$LOCAL_SETTINGS" ]]; then
  # shellcheck disable=SC1090
  source "$LOCAL_SETTINGS"
fi

if [[ -z "${LIGHTRAG_RERANK_COMMAND:-}" ]]; then
  echo "LIGHTRAG_RERANK_COMMAND is not configured." >&2
  exit 2
fi

cd "$REPO_ROOT"
exec /bin/bash -lc "$LIGHTRAG_RERANK_COMMAND"
