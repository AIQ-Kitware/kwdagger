KWDagger Example
================

This folder contains a minimal two-stage example that shows how kwdagger wires
prediction and evaluation steps together. The "model" is intentionally simple
so the focus stays on how the pipeline is defined and executed.

What the pipeline does
----------------------
- :mod:`keyword_sentiment_predict` loads a labeled JSONL file of short reviews and
  predicts ``positive`` whenever a configurable keyword appears in the text.
- :mod:`sentiment_evaluate` compares those predictions to the labels in the same
  file and reports accuracy/precision/recall so you can see how the keyword
  choice affects quality.

Two tiny datasets live under ``data/`` so you can see the combinatorial sweep of
parameters:

``toy_reviews_movies.jsonl`` (17 movie reviews)
    Mixes positive and negative reactions such as "Great visuals but boring
    story." and "I love how the mystery unfolded."

``toy_reviews_food.jsonl`` (12 restaurant reviews)
    Short notes like "Great coffee and great ambiance." alongside "Soup was
    bland and arrived cold." that show how keyword choice drives different
    scores on another domain.

Defining the pipeline
---------------------
``example_user_module/pipelines.py`` builds the pipeline by connecting the
prediction output JSON to the evaluation input and reusing the dataset path as
both the prediction input and ground-truth input. Inline comments call out the
parts kwdagger needs: the CLI executable to run, required inputs/outputs,
parameters to sweep, and how to connect node IO together.

Running the demo
----------------
Set ``PYTHONPATH`` so the module can be imported, schedule the jobs, and then
aggregate the results. The matrix below sweeps keywords **and** datasets to show
how changing parameters drives downstream evaluation.

.. code:: bash

    # From this folder
    export PYTHONPATH=.
    EVAL_DPATH=$PWD/results

    kwdagger schedule \
        --params="
            pipeline: 'example_user_module.pipelines.my_sentiment_pipeline()'
            matrix:
                keyword_sentiment_predict.src_fpath:
                    - data/toy_reviews_movies.jsonl
                    - data/toy_reviews_food.jsonl
                keyword_sentiment_predict.keyword:
                    - great
                    - boring
                    - love
                sentiment_evaluate.workers: 0
        " \
        --root_dpath="${EVAL_DPATH}" \
        --tmux_workers=2 --backend=tmux --skip_existing=1 \
        --run=1

The scheduler will create output folders for each node under ``results/`` and
dispatch the tiny jobs. Afterward, aggregate everything into a single report:

.. code:: bash

    kwdagger aggregate \
        --pipeline='example_user_module.pipelines.my_sentiment_pipeline()' \
        --target "
            - $EVAL_DPATH
        " \
        --output_dpath="$EVAL_DPATH/full_aggregate" \
        --resource_report=1 \
        --io_workers=0 \
        --eval_nodes="
            - sentiment_evaluate
        " \
        --stdout_report="
            top_k: 10
            print_models: True
            concise: 1
        " \
        --cache_resolved_results=False

The stdout report will show the dataset/keyword sweep alongside metrics from
``sentiment_evaluate`` so you can quickly spot which keyword works best on each
set of reviews.
