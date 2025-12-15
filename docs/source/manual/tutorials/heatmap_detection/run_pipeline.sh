#!/usr/bin/env bash

# Copy/paste friendly: set SCRIPT_DIR to this folder (edit if you run elsewhere).
if [[ -n "${BASH_SOURCE[0]}" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
else
    # Fallback: edit this to wherever your tutorial lives
    SCRIPT_DIR="$HOME/code/kwdagger/docs/source/manual/tutorials/heatmap_detection"
fi
cd "$SCRIPT_DIR"

# Set PYTHONPATH to ensure Python can see the example package.
export PYTHONPATH=.

# ----------------------------------------------------------------------
# Generate a kwcoco demo dataset *in the current working directory*
# ----------------------------------------------------------------------
DATA_DPATH="${DATA_DPATH:-$PWD/demo_data}"
mkdir -p "$DATA_DPATH"

DEMO_COCO_FPATH="$DATA_DPATH/demo_vidshapes.kwcoco.json"

echo "Generating kwcoco demo dataset at: $DEMO_COCO_FPATH"
kwcoco toydata \
    --key=vidshapes8 \
    --dst="$DEMO_COCO_FPATH" \
    --verbose=1

# Verify it generated correctly
kwcoco stats "$DEMO_COCO_FPATH"

# ----------------------------------------------------------------------
# Run the kwdagger pipeline
# ----------------------------------------------------------------------
EVAL_DPATH=${EVAL_DPATH:-$PWD/results}
echo "EVAL_DPATH = $EVAL_DPATH"

# --------------------------------------------------------------------
# Schedule runs for the heatmap->boxes pipeline
# --------------------------------------------------------------------
kwdagger schedule \
    --params="
        pipeline: 'heatmap_example.pipelines.heatmap_detection_pipeline()'
        matrix:
            # EDIT THESE to point at your kwcoco datasets
            predict_heatmap.coco_fpath:
                - $DEMO_COCO_FPATH

            # Example hyper-params you might want to sweep
            predict_heatmap.sigma:
                - 3.0
                - 7.0

            extract_boxes.threshold:
                - 0.3
                - 0.5

            extract_boxes.min_area:
                - 4
                - 16

            score_boxes.__enabled__: False
    " \
    --root_dpath="${EVAL_DPATH}" \
    --tmux_workers=2 \
    --backend=tmux --skip_existing=1 \
    --print-commands=True \
    --run=1

# --------------------------------------------------------------------
# Aggregate metrics for the pipeline
# --------------------------------------------------------------------
export PYTHONPATH=.
kwdagger aggregate \
    --pipeline='heatmap_example.pipelines.heatmap_detection_pipeline()' \
    --target="
        - ${EVAL_DPATH}
    " \
    --output_dpath="${EVAL_DPATH}/full_aggregate" \
    --resource_report=0 \
    --io_workers=0 \
    --eval_nodes="
        - score_heatmap
        #- score_boxes
    " \
    --stdout_report="
        top_k: 10
        print_models: True
        concise: 1
    " \
    --plot_params="
        enabled: 0
    " \
    --cache_resolved_results=False

