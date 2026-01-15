Scriptconfig Pipeline Tutorial
==============================

This tutorial mirrors the two-stage pipeline example, but it uses
``scriptconfig`` schemas to declare input/output paths and parameter groups.
The ``ProcessNode.params`` class variable automatically derives
``in_paths``, ``out_paths``, ``algo_params``, and ``perf_params`` from the
schema so your pipeline stays in sync with the CLI definitions.

Files in this tutorial
----------------------

* ``data/`` - two small JSONL datasets of movie and food reviews.
* ``example_user_module/cli`` - command line entry points for the prediction and
  evaluation nodes (scriptconfig schemas live here).
* ``example_user_module/pipelines.py`` - pipeline wiring that uses
  ``ProcessNode.params`` to derive node IO/params.
* ``run_pipeline.sh`` - copy/paste helper to schedule and aggregate.

How scriptconfig drives ProcessNode definitions
-----------------------------------------------

Each CLI class declares the node schema with tags:

* ``in_path`` / ``in``: input paths
* ``out_path`` / ``out``: output templates (non-empty defaults are used)
* ``algo_param`` / ``algo``: algorithm parameters that affect outputs
* ``perf_param`` / ``perf``: execution-only parameters

The ``primary`` tag on an ``out_path`` marks which output signals completion.
``ProcessNode`` uses these tags to populate the appropriate groups unless you
explicitly override them on the node class.

Here is the schema for the prediction node:

.. code:: python

    class KeywordSentimentPredictCLI(scfg.DataConfig):
        src_fpath = scfg.Value(None, tags=['in_path'])
        dst_fpath = scfg.Value('keyword_predictions.json', tags=['out_path', 'primary'])
        dst_dpath = scfg.Value('.', tags=['out_path'])

        keyword = scfg.Value('great', tags=['algo_param'])
        case_sensitive = scfg.Value(False, tags=['algo_param'])
        workers = scfg.Value(0, tags=['perf_param'])

The pipeline nodes simply point ``params`` at these schemas:

.. code:: python

    class KeywordSentimentPredict(kwdagger.ProcessNode):
        name = 'keyword_sentiment_predict'
        executable = f'python {EXAMPLE_DPATH}/cli/keyword_sentiment_predict.py'
        params = KeywordSentimentPredictCLI

    class SentimentEvaluate(kwdagger.ProcessNode):
        name = 'sentiment_evaluate'
        executable = f'python {EXAMPLE_DPATH}/cli/sentiment_evaluate.py'
        params = SentimentEvaluateCLI

Connecting the pipeline
-----------------------

The wiring is the same as the base tutorial: prediction outputs feed evaluation
inputs, and the labeled dataset feeds both nodes.

.. code:: python

    nodes = {
        'keyword_sentiment_predict': KeywordSentimentPredict(),
        'sentiment_evaluate': SentimentEvaluate(),
    }
    nodes['keyword_sentiment_predict'].outputs['dst_fpath'].connect(
        nodes['sentiment_evaluate'].inputs['pred_fpath']
    )
    nodes['keyword_sentiment_predict'].inputs['src_fpath'].connect(
        nodes['sentiment_evaluate'].inputs['true_fpath']
    )

Running the tutorial
--------------------

.. code:: bash

    # From this folder (modify to where your copy is)
    cd ~/code/kwdagger/docs/source/manual/tutorials/scriptconfig_pipeline/

    # Set the PYTHONPATH so kwdagger can see the custom module in this directory
    export PYTHONPATH=.

    # Define where you want the results to be written to
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
        --tmux_workers=2 --backend=serial --skip_existing=1 \
        --run=1

Once jobs complete, aggregate with:

.. code:: bash

    kwdagger aggregate \
        --params="
            pipeline: 'example_user_module.pipelines.my_sentiment_pipeline()'
            root_dpath: ${EVAL_DPATH}
        "
