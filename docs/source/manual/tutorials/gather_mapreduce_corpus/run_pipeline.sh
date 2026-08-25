#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# The YAML and Python files are alternative pipeline definitions. The YAML form
# is the default; pass 'pipelines.py::build_pipeline()' as argv[1] to run the
# same matrix through the Python API instead.
PIPELINE="${1:-./pipeline.yaml}"

rm -rf results

kwdagger schedule \
    --pipeline="$PIPELINE" \
    --params=params.yaml \
    --root_dpath=results \
    --backend=serial \
    --run=1

python verify.py --results=results
