Two-Stage Pipeline Tutorial
==========================

This tutorial walks through a tiny two-stage pipeline so you can see how
``kwdagger`` wants you to describe your own processes. By the end you should
understand why each piece of a ``kwdagger.pipeline.ProcessNode`` exists,
how nodes communicate through the filesystem, and how to run or aggregate the
results.

Files in this tutorial
----------------------

* ``data/`` - two small JSONL datasets of movie and food reviews.
* ``example_user_module/cli`` - command line entry points for the prediction and
  evaluation nodes.
* ``example_user_module/pipelines.py`` - the pipeline wiring that connects the
  two nodes.
* ``run_pipeline.sh`` - a copy/paste friendly script that runs scheduling and
  aggregation from this folder.

Why node CLIs use key/value arguments
-------------------------------------

Each node executable must accept arguments as explicit ``--key=value`` pairs.
The scheduler builds command lines automatically from the parameter grid, so
anything positional or context-dependent would be brittle. Key/value arguments
also serialize cleanly to JSON and YAML, which is how ``kwdagger`` persists
``job_config.json`` for reproducibility and hashing.

How nodes communicate (and why paths are relative)
--------------------------------------------------

``kwdagger`` treats every node run as an isolated working directory. Inputs and
outputs are specified in ``ProcessNode.in_paths`` and ``ProcessNode.out_paths``
and are always relative to that directory. The scheduler materializes the files
on disk, runs the executable, and then records symlinks under ``.pred`` and
``.succ`` so you can navigate dependencies later. Keeping paths relative makes
runs relocatable (you can move or copy the entire ``results/`` tree) and avoids
surprises when dispatching to remote backends where absolute paths may not
exist.

Understanding a ProcessNode
---------------------------

The tutorial nodes live in ``example_user_module/pipelines.py``.

* ``name`` - human-friendly identifier used in folder names and reports.
* ``executable`` - the exact command to run. Here it directly invokes the local
  Python scripts so you can inspect them.
* ``in_paths`` / ``out_paths`` - which CLI arguments are treated as filesystem
  inputs and outputs. Outputs can specify default relative names; one of them
  must be marked ``primary_out_key`` to track completion.
* ``algo_params`` - parameters that change the logical work (e.g., the keyword
  the classifier looks for). These participate in parameter hashing.
* ``perf_params`` - execution-only toggles (e.g., number of workers) that do not
  affect outputs but are still recorded for transparency.
* ``load_result`` - how to read the node's output file into a flattened dict so
  aggregation can understand it. Prediction nodes can opt out if you only want
  evaluation metrics.
* Optional helpers such as ``default_metrics`` and ``default_vantage_points``
  describe how aggregation should score and visualize evaluation nodes.


The following is a simplified version of these nodes to illustrate the structure:

.. code:: python

    class KeywordSentimentPredict(kwdagger.ProcessNode):
        """Define the prediction node (what to run and how data flows in/out)."""

        executable = 'python keyword_sentiment_predict.py'

        in_paths = {
            'src_fpath',
        }
        out_paths = {
            'dst_fpath': 'keyword_predictions.json',
            'dst_dpath': '.',
        }
        primary_out_key = 'dst_fpath'

        algo_params = {
            'keyword': 'great',
            'case_sensitive': False,
        }
        perf_params = {
            'workers': 0,
        }


    class SentimentEvaluate(kwdagger.ProcessNode):
        """Define the evaluation node and what artifacts it writes."""

        executable = 'python sentiment_evaluate.py'

        in_paths = {
            'true_fpath',
            'pred_fpath',
        }
        out_paths = {
            'out_fpath': 'sentiment_metrics.json',
        }
        primary_out_key = 'out_fpath'

        def load_result(self, node_dpath) -> dict:
            """
            Return results as items in a dictionary in the form:

                ``"metrics.<node_name>.<metric>": <value>``.
            """
            ...


Connecting the pipeline
-----------------------

The other important code in ``example_user_module/pipelines.py`` is a function
that builds the pipeline out of the defined nodes. In our case,
``my_sentiment_pipeline()`` builds the DAG by creating both nodes and wiring
outputs to inputs. The prediction output JSON feeds ``sentiment_evaluate`` via
``pred_fpath``, and the labeled dataset path is reused as both the prediction
input and ground-truth input. ``Pipeline.build_nx_graphs()`` validates the
connections before scheduling.

The code looks like this:

.. code:: python

    def my_sentiment_pipeline():
        """Connect the prediction and evaluation nodes into a pipeline."""
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

        return kwdagger.Pipeline(nodes)

Running the tutorial
--------------------

Set ``PYTHONPATH`` to this folder so the example module is importable, then run
scheduling and aggregation. The parameter matrix sweeps two datasets and three
keywords.

.. code:: bash

    # From this folder (modify to where your copy is)
    cd ~/code/kwdagger/docs/source/manual/tutorials/twostage_pipeline/

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


Go back to ``kwdagger schedule``. the Before you run this, try setting ``--run=0``, so it will show you *what* it
will run, before it actually does it.

When you set ``--run=1``, it will dispatch the jobs using the selected backend,
and results will be written under the ``results/`` folder.  The resulting
folder structure is important to understand.

Using ``tree results/ -L 2``, we will see something like:

.. code::

    results/
    ├── keyword_sentiment_predict
    │   ├── keyword_sentiment_predict_id_279631ac
    │   ├── keyword_sentiment_predict_id_384273b8
    │   ├── keyword_sentiment_predict_id_44b2ec77
    │   ├── keyword_sentiment_predict_id_c1961dc4
    │   ├── keyword_sentiment_predict_id_e3227549
    │   └── keyword_sentiment_predict_id_f4ea8a15
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

The ``kwdagger`` module ships with a command to aggregate these results and
summarize them in tables and plots. This is done with the ``kwdagger
aggregate`` command.

To aggregate everything into a single report run:

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


The output looks like this:

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


Where parameter hash IDs come from
----------------------------------

Each job folder name encodes a short hash of the resolved parameters for that
node (algo + perf params plus any injected context such as resolved input
paths). ``keyword_sentiment_predict_id_f4ea8a15`` and
``sentiment_evaluate_id_b55860da`` are examples you will see after running this
tutorial. ``job_config.json`` inside the folder contains the exact
configuration that produced the hash. Identical configurations reuse the same
directory, so reruns can skip existing work, while the hash provides a stable
key during aggregation (see the "Varied Parameter LUT" section in the example
output).

Backends
--------

``kwdagger schedule`` supports several dispatch strategies:

* ``serial`` (default) - run jobs in the current process for quick iteration.
* ``tmux`` - launch jobs in tmux panes; useful for lightweight concurrency on a
  single machine.
* ``slurm`` - hand off work to a Slurm cluster using the options configured in
  your ``cmd_queue`` setup.

All backends honor ``--skip_existing`` to avoid recomputing completed hashes and
will populate the same on-disk structure, so you can develop locally and then
scale out without changing the pipeline definition.

Working with outputs manually (without aggregation)
---------------------------------------------------

The ``results/`` tree groups runs by node, then by parameter hash. Each job
folder contains ``invoke.sh`` (the exact command that was run),
``job_config.json`` (the serialized parameters), and your node's outputs. You
can explore results with shell tools or Python to compare predictions, inspect
metrics, or rerun a single job via ``./invoke.sh``. Aggregation is optional-the
filesystem layout alone makes it easy to trace dependencies via ``.pred`` and
``.succ`` symlinks and to load JSON outputs directly.

To understand this, diver deeper into the directory structure with
``tree -a results/ -L 5``, which will show the contents of each directory.

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
        │   └── keyword_sentiment_predict
        │       └── keyword_sentiment_predict_id_f4ea8a15 -> ../../../../keyword_sentiment_predict/keyword_sentiment_predict_id_f4ea8a15
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


Aggregation basics and options
------------------------------

Aggregation loads completed job folders, parses outputs via ``load_result``, and
produces tables and plots. Useful flags include:

* ``--eval_nodes`` - which evaluation nodes to include.
* ``--stdout_report`` - controls the printed summary (top-k rows, whether to
  show parameter columns, and how verbose to be).
* ``--plot_params`` - enable plotting and choose which parameters to visualize.
* ``--resource_report`` - include process context like runtime and hostname.
* ``--cache_resolved_results`` - reuse cached parsing results to speed up
  repeated aggregations while iterating on report options.

Aggregation can also combine metrics across runs, but macro aggregation across
multiple datasets is still being streamlined; expect improvements here.

Limitations
-----------

The ``kwdagger`` module offers a powerful way to run pipelines such that the
workflow does not depend on ``kwdagger`` at all. Remember, you are simply
telling kwdagger how to construct the bash commands to run your code.  It does
nothing except provide an organization structure and the ability to forward the
job of running the code to some other DAG backend (which could just be running
the scripts in order - i.e. the naive way to do it).

Because we avoid any integration of kwdagger into the user code at all this
does introduce some limitations:


* Node executables must accept key/value CLI arguments; positional-only tools
  are not supported today.

* Communication between nodes is file-based, so very large intermediates,
  streaming workloads, scripts that continuously update results, may not work
  or require some custom handling.

* Macro-level aggregation is evolving. The current tutorial sticks to
  per-configuration reports, and broader aggregation workflows are in scope for
  future development.

* The tool was developed for a very specific use-case, and the API enables that
  use-case. We are exploring ways to make it more ergonomic and customizable.
  Input and collaberation is welcome.
