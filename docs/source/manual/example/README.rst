KWDagger Example
================

This folder contains a minimal two-stage example that shows how kwdagger wires
prediction and evaluation steps together. The "model" is intentionally simple
so the focus stays on how the pipeline is defined and executed.

The demo task is a node that predicts the sentiment of a review, and then
another node that evaluates that prediction. We will show how to run this
pipeline over a grid of different input datasets and algorithm parameters.

As the user you are responsible for writing:

1. The command line executable program for each node in your pipeline, where
   * all arguments can be specified as key/value pairs
   * a subset of these arguments define input files corresponding to the data to be ingested
   * outputs are written to an output file or directory whos path can be specified.
   * there is at least one output file (whos path can be specified) whos
     existence indicates the process has completed.

2. A pipeline file that defines
   * A `kwdagger.ProcessNode` for each process node that specifies the
     executuable, which CLI arguments correspond to input paths, output paths,
     algorithm parameters, and performance parameters (ones that do not impact the output).

   * For evaluation nodes you must also define a ``load_result`` that takes the
     written evaluation outputs and returns them in a form the aggregator can
     read (if you want to use the aggregator).

   * A pipeline function that constructs the DAG by creating an insteace of
     each node, and connecting the outputs of nodes to the inputs of others.

In this tutorial we have written toy examples for each of these.

What the pipeline does
----------------------
- ``keyword_sentiment_predict`` loads a labeled JSONL file of short reviews and
  predicts ``positive`` whenever a configurable keyword appears in the text.
- ``sentiment_evaluate`` compares those predictions to the labels in the same
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

    # From this folder (modify to where your copy is)
    cd ~/code/kwdagger/docs/source/manual/example/

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

Afterward, aggregate everything into a single report:

.. code:: bash

    kwdagger aggregate \
        --pipeline='example_user_module.pipelines.my_sentiment_pipeline()' \
        --target "
            - $EVAL_DPATH
        " \
        --output_dpath="$EVAL_DPATH/full_aggregate" \
        --resource_report=0 \
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

The output looks like:

.. code::

    Varied Basis: = {
        'params.keyword_sentiment_predict.src_fpath': {
            'data/toy_reviews_movies.jsonl': 3,
            'data/toy_reviews_food.jsonl': 3,
        },
        'params.keyword_sentiment_predict.keyword': {
            'boring': 2,
            'great': 2,
            'love': 2,
        },
    }
    Constant Params: {
        'params.sentiment_evaluate.workers': 0,
    }
    Varied Parameter LUT: {
        'hqlsinicoysl': {
            'params.keyword_sentiment_predict.src_fpath': 'data/toy_reviews_movies.jsonl',
            'params.keyword_sentiment_predict.keyword': 'boring',
        },
        'swwxxeqjxolm': {
            'params.keyword_sentiment_predict.src_fpath': 'data/toy_reviews_food.jsonl',
            'params.keyword_sentiment_predict.keyword': 'boring',
        },
        'hewqdumktcae': {
            'params.keyword_sentiment_predict.src_fpath': 'data/toy_reviews_movies.jsonl',
            'params.keyword_sentiment_predict.keyword': 'great',
        },
        'kykkmttusomr': {
            'params.keyword_sentiment_predict.src_fpath': 'data/toy_reviews_food.jsonl',
            'params.keyword_sentiment_predict.keyword': 'great',
        },
        'tllrdfysnzqy': {
            'params.keyword_sentiment_predict.src_fpath': 'data/toy_reviews_movies.jsonl',
            'params.keyword_sentiment_predict.keyword': 'love',
        },
        'imcujqctebkx': {
            'params.keyword_sentiment_predict.src_fpath': 'data/toy_reviews_food.jsonl',
            'params.keyword_sentiment_predict.keyword': 'love',
        },
    }
    ---
    Top 6 / 6 for sentiment_evaluate, unknown
    region_id param_hashid  accuracy  precision_positive  recall_positive
      unknown hqlsinicoysl  0.000000            0.000000         0.000000
      unknown swwxxeqjxolm  0.500000            0.666667         0.285714
      unknown hewqdumktcae  0.647059            0.750000         0.600000
      unknown kykkmttusomr  0.666667            0.800000         0.571429
      unknown tllrdfysnzqy  0.705882            1.000000         0.500000
      unknown imcujqctebkx  0.750000            1.000000         0.571429


This table shows the primary and display metrics configured in the pipeline
node definitions for your evaluation node. Note that listing all parameters in
this table would be cumbersome, so we use a trick where we link the hash of the
parameters to a table printed above the final metrics. This lets you quickly
look up what the parameters corresponding to each row are.


NOTE: Aggregation of "macro" metrics across multiple datasets is possible, but
we are currently working on updating the API for more streamlined usage.


Diving Deeper
-------------

Go back to ``kwdagger schedule``. the Before you run this, try setting ``--run=0``, so it will show you *what* it
will run, before it actually does it.

When you set ``--run=1``, it will dispatch the jobs using the selected backend,
and results will be written under the ``results/`` folder.  The resulting
folder structure is important to understand.

Using ``tree results/ -L 2``, we will see something like:

.. code::

    results/
    ├── keyword_sentiment_predict
    │   ├── keyword_sentiment_predict_id_279631ac
    │   ├── keyword_sentiment_predict_id_384273b8
    │   ├── keyword_sentiment_predict_id_44b2ec77
    │   ├── keyword_sentiment_predict_id_c1961dc4
    │   ├── keyword_sentiment_predict_id_e3227549
    │   └── keyword_sentiment_predict_id_f4ea8a15
    └── sentiment_evaluate
        ├── sentiment_evaluate_id_4b5c3932
        ├── sentiment_evaluate_id_68186920
        ├── sentiment_evaluate_id_b55860da
        ├── sentiment_evaluate_id_b611316e
        ├── sentiment_evaluate_id_ce18c51b
        └── sentiment_evaluate_id_dfcf2b44

Notice that there is a top-level folder for each node, which has a flat list of
child nodes suffixed with a hash. This hash indicates the unique configuration
that produced that specific result, and all results for that configuration are
written there.

Diving further into the directory structure with ``tree -a results/ -L 5`` will
show the contents of each directory.

A sample prediction output directory looks like:

.. code::

   └── keyword_sentiment_predict_id_f4ea8a15
       ├── invoke.sh
       ├── job_config.json
       ├── keyword_predictions.json
       └── .succ
           └── sentiment_evaluate
               └── sentiment_evaluate_id_b55860da -> ../../../../sentiment_evaluate/sentiment_evaluate_id_b55860da


And a sample evaluation output directory looks like:

.. code::

    └── sentiment_evaluate_id_b55860da
        ├── invoke.sh
        ├── job_config.json
        ├── .pred
        │   └── keyword_sentiment_predict
        │       └── keyword_sentiment_predict_id_f4ea8a15 -> ../../../../keyword_sentiment_predict/keyword_sentiment_predict_id_f4ea8a15
        ├── resolved_result_row_v012.json
        └── sentiment_metrics.json


Notice that each node has a ``job_config.json`` and an ``invoke.sh``. These are
files written by kwdagger that makes it easy to inspect:

1. What the known configuration for the job was when the job was defined
2. The exact commandline that will re-execute the job (very useful for debugging)

Also notice that the prediction node has the custom
``keyword_predictions.json``, which is just a result of the way the CLI was
define, kwdagger does not interact with this file, it is just the output of the
program. However, if you use ``kwutil.ProcessContext`` you can enrich your
output with extra information kwdagger can make use of, but this is optional.

Similarly the evaluation node has a ``sentiment_metrics.json``, which stores
the measured metrics. Again, it can be any format you want, but you do need to
provide a ``load_results`` function in your evaluation node definition that
lets kwdagger know how to load it into a format it understands if you want to
use the aggregation feature.

Lastly notice ``.pred`` and ``.succ``.  This is what implements the graph based
symlink structure allows for navigation of dependencies within a node. The
``.succ`` folder holds symlinks to successors (i.e. results that depend on the
current results), and ``.pred`` holds symlinks to folders of results that the
current folder depends on. This lets you navigate the path of a specific
configuration without relying on reconstructing the DAG that produced it.

NOTE: Aggregation features are completely optional, and the scheduling of
multiple runs and the easy-to-parse and easy-to-nagivate graph based output
structure can be used independently.
