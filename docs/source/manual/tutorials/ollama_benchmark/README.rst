Tutorial 2: Benchmarking Ollama Models with kwdagger
====================================================

This example shows how to use ``kwdagger`` to benchmark Ollama models-even with a
pipeline containing only *one node*. It demonstrates how to:

At a high level it follows the following steps:

1. Define a single-node pipeline: ``ollama_benchmark``.
2. Run a parameter sweep with ``kwdagger schedule``.
3. Produce structured per-run folders.
4. Inspect these results with a custom aggregation script: ``custom_aggregate.py``.

Even a one-node DAG benefits from reproducible configuration, clean run
directories, and consistent metadata.

Preqeq: Setting Up an Ollama Server
===================================

This pipeline requires an existing ollama server that you can query. This
examples assumes that it is running on your localhost machine.  If you do not
already have an Ollama server running, the simplest option is to start one in
Docker.

Run the server
--------------

.. code:: bash

    docker run --rm -it \
        -p 11434:11434 \
        --name ollama \
        ollama/ollama:latest

This exposes the standard Ollama API on ``http://localhost:11434``.
Keep this container running in in the background while you execute the
benchmarking pipeline.

If you use a remote machine, you can replace ``localhost`` in your
configuration with the machine's hostname or IP address. You will also be
unable to run the cold-start trials, so set that number to zero to skip them.


Pulling Models
--------------

Assuming that ollama is running on localhost using port 11434, ensure we have relevant models

.. code:: bash

    curl -X POST http://localhost:11434/api/pull -d '{"model": "gemma3:12b"}'
    curl -X POST http://localhost:11434/api/pull -d '{"model": "gemma3:1b"}'

You only need to pull the models listed in your parameter sweep.
The benchmarking node will reuse these local model files-no network downloads
occur during the tests.

The Task
========

The program ``ollama_benchmark.py`` runs a series of cold and warm trials
against an Ollama server and records detailed timing data: TTFT (time to first
token), total latency, throughput, token counts, and Ollama's internal duration
fields. It loads prompts from a YAML file, executes the requested number of
trials, and writes the results to a structured JSON file.

We could run this CLI directly:

.. code:: bash

    python ollama_benchmark.py \
      --prompt_fpath prompts_5.yaml \
      --dst_dpath manual_run \
      --ollama_url http://localhost:11434 \
      --cold_reset_cmd "docker restart ollama" \
      --model gemma3:1b \
      --cold_trials 2 \
      --warm_trials 3 \
      --append_jsonl manual_run/all_trials.jsonl

We can repeat this manually for every combination of parameters, but that
requires managing output directories, avoiding duplicate runs, and doing
a significant amount of bookkeeping by hand.

Or we could manage it with kwdagger. Even though this benchmark consists of a
single node, kwdagger schedule makes it simple to run large parameter sweeps
reproducibly. A small parameter grid can expand into many runs, each placed in
an organized directory with full configuration and machine metadata. kwdagger
ensures completed runs are not recomputed and provides a clean foundation for
custom aggregation and plotting.


The Pipeline
============
I think that makes sense. I will need to get to peeling apart the module for any of this anyway. After I finish pulling together slides for our middle managing customer for our monthly

Before defining the pipeline itself, we need a ProcessNode that wraps the benchmark CLI. A ProcessNode simply tells kwdagger:

* which executable to run

* which parameters it accepts

* which files it expects to read or write

* how to expose results for downstream aggregation

Here is a shortened version of the node definition used in this tutorial:

.. code:: python

    import kwdagger
    import ubelt as ub

    EXAMPLE_DPATH = ub.Path(__file__).parent

    class OllamaBenchmark(kwdagger.ProcessNode):
        """
        Wrap the benchmark CLI so kwdagger can schedule runs and
        collect outputs.
        """
        name = "ollama_benchmark"
        executable = f"python {EXAMPLE_DPATH}/ollama_benchmark.py"

        # Input and output paths map directly to CLI arguments.
        in_paths = {
            "prompt_fpath",
        }
        out_paths = {
            "dst_fpath": "ollama_benchmark.json",
            "dst_dpath": ".",
        }
        primary_out_key = "dst_fpath"

        # Parameters that can be swept by kwdagger schedule.
        algo_params = {
            "model": "gemma3:1b",
            "cold_trials": 1,
            "warm_trials": 3,
            "concurrency": 0,
            "ollama_url": "http://localhost:11434",
            "cold_reset_cmd": "docker restart ollama",
        }


The purpose of this class is to tell kwdagger how to construct the command to
run your program.

Once you have a node, you also need to define  the function that builds the
pipeline. In this case we do not need to connect any inputs to any outputs.


.. code:: python

    def ollama_benchmark_pipeline():
        """
        Create the one-node benchmark pipeline.

        This is what you'll point kwdagger's scheduler at.
        """
        nodes = {
            "ollama_benchmark": OllamaBenchmark(),
        }
        dag = kwdagger.Pipeline(nodes)
        dag.build_nx_graphs()
        return dag


This pipeline has no dependencies-just a single callable node. kwdagger handles
parameter expansion, run IDs, and output organization automatically.


Running the Sweep
-----------------

Use ``kwdagger schedule`` to enumerate all combinations of model, prompt file,
cold/warm trials, and concurrency.

Excerpt from ``run_pipelines.sh``:

.. code:: bash

    OLLAMA_URL_DEFAULT=http://localhost:11434
    COLD_RESET_CMD_DEFAULT="docker restart ollama"

    kwdagger schedule \
        --params="
            pipeline: 'pipelines.py::ollama_benchmark_pipeline()'
            matrix:
                ollama_benchmark.prompt_fpath:
                    - prompts_5.yaml
                ollama_benchmark.model:
                    - gemma3:12b
                    - gemma3:1b
                ollama_benchmark.cold_trials:
                    - 3
                ollama_benchmark.warm_trials:
                    - 3
                ollama_benchmark.concurrency:
                   - 0
                ollama_benchmark.ollama_url:
                    - '${OLLAMA_URL_DEFAULT}'
                ollama_benchmark.cold_reset_cmd:
                    - '${COLD_RESET_CMD_DEFAULT}'
                ollama_benchmark.notes:
                    - 'Run on $HOSTNAME'
        " \
        --root_dpath="${EVAL_DPATH}" \
        --backend=serial \
        --skip_existing=1 \
        --run=0

Each parameter combination becomes a clean, deterministic run directory:

.. code::

    results_ollama/
        ollama_benchmark/
            ollama_benchmark_id_<hash>/
                ollama_benchmark.json
                ...

Quick Aggregate (Optional)
--------------------------

``kwdagger aggregate`` gives a quick summary of all runs:

.. code:: bash

    kwdagger aggregate \
        --pipeline="pipelines.py::ollama_benchmark_pipeline()" \
        --target "$EVAL_DPATH" \
        --output_dpath="$EVAL_DPATH/full_aggregate" \
        --eval_nodes="ollama_benchmark"


This can be useful to quickly browse the results, but the ``kwdagger``
aggregation script is built for the case where different parameter choices are
categorically different, so it is less valuable if you aren't optimizing over
varied parameters or there are custom non-trivial groupings of results that
should be considered jointly.

However, the output format of ``kwdagger`` runs is ammenable to custom
aggregation, which we sketch out next.

Custom Aggregation
------------------

For a customized analysis tailored to your problem, you can parse the results
from multiple runs. We provide sample code in ``custom_aggregate.py`` that does
this. It can be run via:

.. code:: bash

    python custom_aggregate.py \
        --pattern="results_ollama/ollama_benchmark/*/ollama_benchmark.json"

The main idea is that we glob over relevant JSON output files, extract both
trial-level stats and machine metadata, and build a Pandas DataFrame.  We can
then do custom groupings on this data frame, report stats, and build custom
plots.
