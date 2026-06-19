Heatmap Detection Tutorial
==========================

This tutorial walks through a four-stage computer vision pipeline that stays
totally in ``kwcoco`` format. It consumes an existing dataset with polygon or
segmentation annotations, writes out auxiliary saliency maps, extracts
connected-component boxes, and scores both stages using kwcoco's built-in
metrics CLIs.

Prerequisites
-------------

* Install ``kwcoco`` in your current environment.

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

The scheduler takes the pipeline plus a parameter matrix. Run everything from
this tutorial folder and expose the example package on ``PYTHONPATH`` so the
module-based CLIs resolve. ``run_pipeline.sh`` generates a small kwcoco demo
dataset and runs the full schedule + aggregate; the core command is::

    cd docs/source/manual/tutorials/heatmap_detection
    export PYTHONPATH=.

    DATA=$PWD/demo_data/demo_vidshapes.kwcoco.json   # see run_pipeline.sh
    WORKDIR=$PWD/results

    kwdagger schedule \
        --params="
            pipeline: 'heatmap_example.pipelines.heatmap_detection_pipeline()'
            matrix:
                predict_heatmap.coco_fpath: [$DATA]
                predict_heatmap.sigma: [1.0, 7.0]
                extract_boxes.threshold: [0.25, 0.3]
                score_boxes.iou_thresh: [0.1, 0.5]
        " \
        --root_dpath "$WORKDIR" --backend=serial --run=1 --skip_existing=1

All four nodes invoke their CLIs via ``python -m``:

* ``python -m heatmap_example.cli.predict_heatmap``
* ``python -m heatmap_example.cli.extract_boxes``
* ``python -m kwcoco.metrics.segmentation_metrics``
* ``python -m kwcoco evaluate_detections``

Defining the pipeline in YAML
-----------------------------

This folder also ships ``pipeline.yaml``: the same four-node DAG expressed as
data, with no Python. Pass it to ``--pipeline`` instead of the ``module.func()``
string (everything else is identical)::

    kwdagger schedule --pipeline ./pipeline.yaml --params "
        matrix:
            predict_heatmap.coco_fpath: [$DATA]
            predict_heatmap.sigma: [1.0, 7.0]
            extract_boxes.threshold: [0.25, 0.3]
            score_boxes.iou_thresh: [0.1, 0.5]
    " --root_dpath "$WORKDIR" --backend=serial --run=1 --skip_existing=1

This is the realistic case where the topology is data but the *scoring* is not:
the two ``score_*`` nodes call kwcoco's metrics CLIs and parse their bespoke
output. The YAML topology, parameters, and metric metadata are declarative,
while those two nodes opt into a Python result loader via ``load_result:``,
which points right back at the ``ScoreHeatmap.load_result`` /
``ScoreBoxes.load_result`` methods already defined in ``pipelines.py`` -- one
source of truth, reused by both the Python and YAML pipelines. See the
:doc:`YAML pipeline specification </manual/technical/yaml_pipeline_spec>` for the
full schema.

``run_pipeline_fully_inline_version.sh`` takes this one step further: it inlines
the entire pipeline *and* the matrix into a single ``--params`` block (nothing
is hidden in a Python function), and then aggregates **without** repeating the
pipeline. ``kwdagger schedule`` serializes the pipeline it ran to
``<root_dpath>/_kwdagger_schedule/most_recent_run.json``, and ``kwdagger
aggregate`` (whose ``--pipeline`` now defaults to ``auto``) recovers it from the
target directory automatically. The run becomes self-describing: the only reason
``PYTHONPATH=.`` is still needed at aggregate time is the two ``load_result``
references; a pipeline whose nodes all use the generic loader would need nothing
on the path.

Inspecting results
------------------

After scheduling completes, each node's output directory under ``$WORKDIR``
will contain:

* ``heatmap.kwcoco.json`` – the predicted dataset with auxiliary saliency PNGs
  stored under ``assets/heatmaps/``.
* ``pred_boxes.kwcoco.json`` – the same imagery plus extracted bounding boxes
  with a confidence score per detection.
* ``heatmap_eval`` / ``heatmap_metrics.json`` – metrics produced by
  ``kwcoco.metrics.segmentation_metrics``.
* ``detection_eval`` / ``box_metrics.json`` – AP/mAP metrics from
  ``kwcoco evaluate_detections``.

You can load either kwcoco file in Python to visualize assets and detections::

    import kwcoco
    dset = kwcoco.CocoDataset('pred_boxes.kwcoco.json')
    print(dset.basic_stats())
