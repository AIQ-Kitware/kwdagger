Kwdagger
========

|Pypi| |PypiDownloads| |GitlabCIPipeline| |GitlabCICoverage| |ReadTheDocs|

+-----------------+-----------------------------------------------------+
| Read the Docs   | http://kwdagger.readthedocs.io/en/latest/           |
+-----------------+-----------------------------------------------------+
| Gitlab (main)   | https://gitlab.kitware.com/computer-vision/kwdagger |
+-----------------+-----------------------------------------------------+
| Github (mirror) | https://github.com/Kitware/kwdagger                 |
+-----------------+-----------------------------------------------------+
| Pypi            | https://pypi.org/project/kwdagger                   |
+-----------------+-----------------------------------------------------+

Overview
--------
KWDagger turns parameterized definitions of existing command-line programs into
static, inspectable graphs of shell commands and hashed result directories. It
builds on
`cmd_queue <https://gitlab.kitware.com/computer-vision/cmd_queue>`_ and
`kwconf <https://gitlab.kitware.com/utils/kwconf>`_ to provide:

* Reusable ``kwdagger.pipeline.Pipeline`` and ``kwdagger.pipeline.ProcessNode``
  abstractions for constructing commands and wiring produced artifacts.
* Parameter-matrix expansion with operational deduplication of equivalent
  requested work.
* Per-process ``invoke.sh`` files and a navigable ``.pred`` / ``.succ`` symlink
  graph, so generated work can be inspected, rerun, or invalidated without
  remaining inside the kwdagger runtime.
* A scheduling CLI (``kwdagger.schedule``) that hands the static command graph
  to serial, tmux, or Slurm cmd_queue backends. The tmux backend is the most
  commonly used interactive runner.
* An optional aggregation CLI (``kwdagger.aggregate``) that loads completed
  results, requested and resolved parameters, and metrics into analytical
  tables and reports.
* A self-contained demo pipeline in ``kwdagger.demo.demodata`` that is used in
  CI and serves as a reference implementation.

Kwdagger wraps ordinary scripts rather than replacing them. A node may use the
default named-argument command convention or subclass ``ProcessNode`` to support
an existing positional or otherwise specialized CLI.

Repository layout
-----------------
* ``kwdagger/pipeline/`` – core pipeline and process node definitions, networkx
  graph construction, and configuration utilities. Import from
  ``kwdagger.pipeline``; the submodules inside it are private.
* ``kwdagger/schedule.py`` – ``ScheduleEvaluationConfig`` CLI for expanding
  parameter grids into runnable jobs and dispatching them through cmd_queue
  backends.
* ``kwdagger/aggregate.py`` – ``AggregateEvluationConfig`` CLI for loading job
  outputs, computing parameter hash IDs, and generating text/plot reports.
* ``kwdagger/demo/demodata.py`` – end-to-end demo pipeline with prediction and
  evaluation stages plus CLI entry points for each node.
* ``docs/`` – Sphinx sources, including an example user module under
  ``docs/source/manual/tutorials/twostage_pipeline``.
* ``tests/`` – unit and functional coverage for pipeline wiring, scheduler
  behavior, aggregation, and import sanity checks.

Quickstart
----------
Run the demo pipeline locally to see the CLI workflow end-to-end:

.. code:: bash

    TMP_DPATH=$(mktemp -d --suffix "-kwdagger-demo")
    cd "$TMP_DPATH"
    echo "demo" > input.txt

    EVAL_DPATH=$PWD/pipeline_output
    python -m kwdagger.schedule \
        --params="
            pipeline: 'kwdagger.demo.demodata.my_demo_pipeline()'
            matrix:
                stage1_predict.src_fpath:
                    - input.txt
                stage1_predict.param1:
                    - 123
                stage1_evaluate.workers: 2
        " \
        --root_dpath="${EVAL_DPATH}" \
        --backend=serial --skip_existing=1 --run=1

    python -m kwdagger.aggregate \
        --pipeline='kwdagger.demo.demodata.my_demo_pipeline()' \
        --target "
            - $EVAL_DPATH
        " \
        --output_dpath="$EVAL_DPATH/full_aggregate" \
        --eval_nodes="
            - stage1_evaluate
        " \
        --stdout_report="
            top_k: 10
            concise: 1
        "

The scheduler generates per-node job directories with ``invoke.sh`` and
``job_config.json`` metadata. Each ``invoke.sh`` is intended to be a usable
recomputation command even when kwdagger is not involved in the rerun. The
aggregator is then one optional way to consolidate results and print a report.

The hashed result tree contains a graph-based symlink structure for navigating
produced-artifact dependencies. The ``.succ`` folder links to results that
depend on the current result, and ``.pred`` links to results consumed by the
current process. This makes downstream inspection and invalidation possible
after the original scheduling command has finished.

For more in-depth information see tutorials:

* `Tutorial #1: twostage_pipeline <docs/source/manual/tutorials/twostage_pipeline/README.rst>`_
* `Tutorial #2: ollama_benchmark <docs/source/manual/tutorials/ollama_benchmark/README.rst>`_

Command line entry points
-------------------------
* ``python -m kwdagger.schedule`` or ``kwdagger schedule`` – build and run a
  pipeline over a parameter matrix (see ``kwdagger.schedule.ScheduleEvaluationConfig``).
* ``python -m kwdagger.aggregate`` or ``kwdagger aggregate`` – load completed
  runs and generate tabular and plotted summaries
  (``kwdagger.aggregate.AggregateEvluationConfig``).
* ``python -m kwdagger`` – modal CLI that exposes the ``schedule`` and
  ``aggregate`` commands via ``kwdagger.__main__.KWDaggerModal``.

.. |Pypi| image:: https://img.shields.io/pypi/v/kwdagger.svg
    :target: https://pypi.python.org/pypi/kwdagger
.. |PypiDownloads| image:: https://img.shields.io/pypi/dm/kwdagger.svg
    :target: https://pypistats.org/packages/kwdagger
.. |ReadTheDocs| image:: https://readthedocs.org/projects/kwdagger/badge/?version=latest
    :target: http://kwdagger.readthedocs.io/en/latest/
.. |GitlabCIPipeline| image:: https://gitlab.kitware.com/computer-vision/kwdagger/badges/main/pipeline.svg
    :target: https://gitlab.kitware.com/computer-vision/kwdagger/-/jobs
.. |GitlabCICoverage| image:: https://gitlab.kitware.com/computer-vision/kwdagger/badges/main/coverage.svg
    :target: https://gitlab.kitware.com/computer-vision/kwdagger/commits/main

