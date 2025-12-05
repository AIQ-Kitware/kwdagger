Two-Stage Pipeline Tutorial
==========================

This tutorial walks through a tiny two-stage pipeline so you can see how
``kwdagger`` wants you to describe your own processes. By the end you should
understand why each piece of a :class:`~kwdagger.pipeline.ProcessNode` exists,
how nodes communicate through the filesystem, and how to run or aggregate the
results.

Files in this tutorial
----------------------

* ``data/`` – two small JSONL datasets of movie and food reviews.
* ``example_user_module/cli`` – command line entry points for the prediction and
  evaluation nodes.
* ``example_user_module/pipelines.py`` – the pipeline wiring that connects the
  two nodes.
* ``run_pipeline.sh`` – a copy/paste friendly script that runs scheduling and
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

* ``name`` – human-friendly identifier used in folder names and reports.
* ``executable`` – the exact command to run. Here it directly invokes the local
  Python scripts so you can inspect them.
* ``in_paths`` / ``out_paths`` – which CLI arguments are treated as filesystem
  inputs and outputs. Outputs can specify default relative names; one of them
  must be marked ``primary_out_key`` to track completion.
* ``algo_params`` – parameters that change the logical work (e.g., the keyword
  the classifier looks for). These participate in parameter hashing.
* ``perf_params`` – execution-only toggles (e.g., number of workers) that do not
  affect outputs but are still recorded for transparency.
* ``load_result`` – how to read the node's output file into a flattened dict so
  aggregation can understand it. Prediction nodes can opt out if you only want
  evaluation metrics.
* Optional helpers such as ``default_metrics`` and ``default_vantage_points``
  describe how aggregation should score and visualize evaluation nodes.

Connecting the pipeline
-----------------------

``my_sentiment_pipeline()`` builds the DAG by creating both nodes and wiring
outputs to inputs. The prediction output JSON feeds ``sentiment_evaluate`` via
``pred_fpath``, and the labeled dataset path is reused as both the prediction
input and ground-truth input. ``Pipeline.build_nx_graphs()`` validates the
connections before scheduling.

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

* ``serial`` (default) – run jobs in the current process for quick iteration.
* ``tmux`` – launch jobs in tmux panes; useful for lightweight concurrency on a
  single machine.
* ``slurm`` – hand off work to a Slurm cluster using the options configured in
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
metrics, or rerun a single job via ``./invoke.sh``. Aggregation is optional—the
filesystem layout alone makes it easy to trace dependencies via ``.pred`` and
``.succ`` symlinks and to load JSON outputs directly.

Aggregation basics and options
------------------------------

Aggregation loads completed job folders, parses outputs via ``load_result``, and
produces tables and plots. Useful flags include:

* ``--eval_nodes`` – which evaluation nodes to include.
* ``--stdout_report`` – controls the printed summary (top-k rows, whether to
  show parameter columns, and how verbose to be).
* ``--plot_params`` – enable plotting and choose which parameters to visualize.
* ``--resource_report`` – include process context like runtime and hostname.
* ``--cache_resolved_results`` – reuse cached parsing results to speed up
  repeated aggregations while iterating on report options.

Aggregation can also combine metrics across runs, but macro aggregation across
multiple datasets is still being streamlined; expect improvements here.

Limitations and roadmap
-----------------------

* Node executables must accept key/value CLI arguments; positional-only tools
  are not supported today.
* Communication between nodes is file-based, so very large intermediates or
  streaming workloads may need custom handling.
* Macro-level aggregation is evolving. The current tutorial sticks to
  per-configuration reports, and broader aggregation workflows are planned for a
  future release.

