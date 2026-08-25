#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

kwdagger schedule \
    --params=params.yaml \
    --root_dpath=results \
    --backend=serial \
    --run=1
