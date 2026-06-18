#!/usr/bin/env bash
__doc__="
Fully-inline version of run_pipeline.sh.

Everything that defines this experiment -- the pipeline (nodes + edges) AND the
parameter matrix -- lives in a single '--params' block below. Nothing about the
DAG is hidden in a Python function; the schedule command is entirely
self-describing.

Two things make this possible:

  1. The 'pipeline:' key in '--params' accepts a declarative pipeline (the same
     'nodes:' / 'edges:' schema as a pipeline.yaml file), so it can sit right
     beside the 'matrix:' it is swept over.

  2. 'kwdagger schedule' serializes the pipeline it ran to
     <root_dpath>/_kwdagger_schedule/most_recent_run.json. 'kwdagger aggregate'
     then auto-discovers it from the target directory, so we do NOT repeat the
     pipeline definition when aggregating (note: no --pipeline below).

Caveat: the two score_* nodes parse kwcoco's bespoke metric output, so they opt
into a Python result loader via 'load_result:'. That dotted path is imported at
aggregate time, which is why PYTHONPATH=. is still exported below. A pipeline
whose nodes all use the generic loader would need nothing on PYTHONPATH at all.
"

# Copy/paste friendly: set SCRIPT_DIR to this folder (edit if you run elsewhere).
if [[ -n "${BASH_SOURCE[0]}" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
else
    SCRIPT_DIR="$HOME/code/kwdagger/docs/source/manual/tutorials/heatmap_detection"
fi
cd "$SCRIPT_DIR"

# Expose the example package so the 'python -m heatmap_example.*' CLIs resolve,
# and so aggregate can import the score_* load_result methods referenced inline.
export PYTHONPATH=.

# ----------------------------------------------------------------------
# Generate a small kwcoco demo dataset to run on.
# ----------------------------------------------------------------------
DATA_DPATH="${DATA_DPATH:-$PWD/demo_data}"
mkdir -p "$DATA_DPATH"
DEMO_COCO_FPATH="$DATA_DPATH/demo_vidshapes.kwcoco.json"
echo "Generating kwcoco demo dataset at: $DEMO_COCO_FPATH"
kwcoco toydata --key=vidshapes8 --dst="$DEMO_COCO_FPATH" --verbose=1
kwcoco stats "$DEMO_COCO_FPATH"

EVAL_DPATH=${EVAL_DPATH:-$PWD/results_inline}
echo "EVAL_DPATH = $EVAL_DPATH"

# --------------------------------------------------------------------
# Schedule: the pipeline and the matrix are BOTH defined here inline.
# --------------------------------------------------------------------
kwdagger schedule \
    --params="
        pipeline:
          nodes:
            predict_heatmap:
              executable: 'python -m heatmap_example.cli.predict_heatmap'
              in_paths: [coco_fpath]
              out_paths: {dst_coco_fpath: 'heatmap.kwcoco.json', asset_dpath: 'assets/heatmaps'}
              primary_out_key: dst_coco_fpath
              algo_params: {sigma: 7.0, thresh: 0.0, heatmap_channel: 'salient'}
            extract_boxes:
              executable: 'python -m heatmap_example.cli.extract_boxes'
              in_paths: [coco_fpath]
              out_paths: {dst_coco_fpath: 'pred_boxes.kwcoco.json'}
              primary_out_key: dst_coco_fpath
              algo_params: {threshold: 0.5, min_area: 4, heatmap_channel: 'salient'}
            score_heatmap:
              executable: 'python -m kwcoco.metrics.segmentation_metrics'
              in_paths: [true_dataset, pred_dataset]
              out_paths: {eval_dpath: 'heatmap_eval', eval_fpath: 'heatmap_metrics.json'}
              primary_out_key: eval_fpath
              algo_params: {salient_channel: 'salient'}
              perf_params: {workers: 'auto'}
              load_result: 'heatmap_example.pipelines.ScoreHeatmap.load_result'
              metrics:
                - {metric: ap,  objective: maximize, primary: true}
                - {metric: auc, objective: maximize, primary: true}
            score_boxes:
              executable: 'python -m kwcoco evaluate_detections'
              in_paths: [true_dataset, pred_dataset]
              out_paths: {out_dpath: 'detection_eval', out_fpath: 'box_metrics.json'}
              primary_out_key: out_fpath
              algo_params: {compat: 'all', iou_thresh: 0.1}
              load_result: 'heatmap_example.pipelines.ScoreBoxes.load_result'
              metrics:
                - {metric: ap,        objective: maximize, primary: true}
                - {metric: auc,       objective: maximize, primary: true}
                - {metric: f1,        objective: maximize, display: true}
                - {metric: precision, objective: maximize, display: true}
                - {metric: recall,    objective: maximize, display: true}
                - {metric: thresh,    objective: maximize, display: true}
          edges:
            - predict_heatmap.dst_coco_fpath -> extract_boxes.coco_fpath
            - predict_heatmap.dst_coco_fpath -> score_heatmap.pred_dataset
            - predict_heatmap.coco_fpath -> score_heatmap.true_dataset
            - extract_boxes.dst_coco_fpath -> score_boxes.pred_dataset
            - predict_heatmap.coco_fpath -> score_boxes.true_dataset
        matrix:
            predict_heatmap.coco_fpath:
                - $DEMO_COCO_FPATH
            predict_heatmap.sigma: [1.0, 7.0]
            extract_boxes.threshold: [0.25, 0.3]
            extract_boxes.min_area: [16]
            score_boxes.iou_thresh: [0.1, 0.5]
    " \
    --root_dpath="${EVAL_DPATH}" \
    --tmux_workers=4 \
    --backend=tmux --skip_existing=1 \
    --run=1

# --------------------------------------------------------------------
# Aggregate: NO --pipeline. It is auto-discovered from the schedule
# metadata under ${EVAL_DPATH}/_kwdagger_schedule/most_recent_run.json.
# --------------------------------------------------------------------
kwdagger aggregate \
    --target="
        - ${EVAL_DPATH}
    " \
    --output_dpath="${EVAL_DPATH}/full_aggregate" \
    --resource_report=0 \
    --io_workers=0 \
    --eval_nodes="
        - score_heatmap
        - score_boxes
    " \
    --stdout_report="
        top_k: 10
        print_models: True
        concise: split
    " \
    --plot_params="
        enabled: 0
    " \
    --cache_resolved_results=False
