Heatmap Detection Tutorial
==========================

This tutorial walks through a four-stage computer vision pipeline that stays
totally in ``kwcoco`` format. It consumes an existing dataset with polygon or
segmentation annotations, writes out auxiliary saliency maps, extracts
connected-component boxes, and scores both stages using kwcoco's built-in
metrics CLIs.

Prerequisites
-------------

* Install ``kwcoco`` and friends (``kwimage``, ``kwdagger``) in your current
  environment.
* Generate a static dataset with the ``kwcoco toydata`` CLI before scheduling
  the pipeline. For example::

      kwcoco toydata --key=shapes8 --dst=./toydata.kwcoco.json --verbose=1

  The pipeline assumes the dataset already exists; it will not create new data
  during execution.

Pipeline structure
------------------

All tutorial code lives in ``heatmap_example`` inside this folder. The pieces
match the layout used by the two-stage tutorial: small CLIs under ``cli/`` and
pipeline wiring in ``pipelines.py``.

The DAG contains exactly four nodes:

* ``predict_heatmap`` (CLI: ``cli/predict_heatmap.py``) – loads each image and
  its ground truth polygons, creates a blurred saliency heatmap, saves it as an
  auxiliary channel named ``saliency``, and emits a new kwcoco file containing
  those assets.
* ``extract_boxes`` (CLI: ``cli/extract_boxes.py``) – thresholds the saliency
  channel, runs connected-component labeling, converts each component to an
  ``xywh`` bounding box, and writes the detections back into another kwcoco
  file.
* ``score_heatmap`` – calls ``python -m kwcoco.metrics.segmentation_metrics`` to
  compute segmentation-style metrics between the ground truth dataset and the
  saliency predictions.
* ``score_boxes`` – calls ``python -m kwcoco evaluate_detections`` to measure
  detection AP/mAP between the ground truth dataset and the predicted boxes.

Scheduling the run
------------------

``HeatmapDetectionPipelineConfig`` builds the parameter matrix used by the
scheduler. Run everything from this tutorial folder and expose the example
package on ``PYTHONPATH`` so the module-based CLIs resolve::

    cd docs/source/manual/tutorials/heatmap_detection
    export PYTHONPATH=.

    DATA=$PWD/../../../../../toydata.kwcoco.json
    WORKDIR=$PWD/heatmap_tutorial_run

    kwdagger schedule \
        --config heatmap_example.pipelines.HeatmapDetectionPipelineConfig \
        coco_fpath=$DATA workdir=$WORKDIR \
        --root_dpath "$WORKDIR" --backend=serial --run=1 --skip_existing=1

The two executable nodes referenced by the pipeline use ``python -m``:

* ``python -m heatmap_example.cli.predict_heatmap``
* ``python -m heatmap_example.cli.extract_boxes``

Inspecting results
------------------

After scheduling completes, the ``workdir`` will contain:

* ``pred_saliency.kwcoco.json`` – the predicted dataset with auxiliary
  saliency PNGs stored under ``saliency/``.
* ``pred_boxes.kwcoco.json`` – the same imagery plus extracted bounding boxes
  with a confidence score per detection.
* ``segmentation_eval`` – JSON/YAML metrics produced by
  ``kwcoco.metrics.segmentation_metrics``.
* ``detection_eval`` – AP/mAP metrics from ``kwcoco evaluate_detections``.

You can load either kwcoco file in Python to visualize assets and detections::

    import kwcoco
    dset = kwcoco.CocoDataset('pred_boxes.kwcoco.json')
    print(dset.basic_stats())
