Tutorial 2: Benchmarking Ollama Models with kwdagger
====================================================

This example shows how to use **kwdagger** to benchmark Ollama models—even with a
pipeline containing only **one node**. It demonstrates:

* defining a minimal pipeline
* sweeping parameters with ``kwdagger schedule``
* collecting results
* aggregating them with a simple custom script

This tutorial is intentionally concise: follow the steps, run the sweep, and
inspect the results.


Overview
--------

You will:

1. Define a single-node pipeline: ``ollama_benchmark``.
2. Run a model + parameter sweep with ``kwdagger schedule``.
3. Produce structured per-run folders.
4. Aggregate results with ``custom_aggregate.py``.
5. Plot metrics such as TTFT and throughput.

Even a one-node DAG benefits from reproducible configuration, clean run
directories, and consistent metadata.

Preqeq: Setting Up an Ollama Server
===================================

This pipeline depends on a little bit of setup.  If you do not already have an
Ollama server running, the simplest option is to start one in Docker.

Run the server
--------------

.. code-block:: bash

    docker run --rm -it \
        -p 11434:11434 \
        --name ollama \
        ollama/ollama:latest

This exposes the standard Ollama API on ``http://localhost:11434``.
Keep this container running in a separate terminal while you execute the
benchmarking pipeline.

(If you use a remote machine, simply replace ``localhost`` in your configuration
with the machine’s hostname or IP address.)


Pulling Models
--------------

Assuming that ollama is running on localhost using port 11434, ensure we have relevant models

.. code-block:: bash

    curl -X POST http://localhost:11434/api/pull -d '{"model": "gemma3:12b"}'
    curl -X POST http://localhost:11434/api/pull -d '{"model": "gemma3:1b"}'

You only need to pull the models listed in your parameter sweep.
The benchmarking node will reuse these local model files—no network downloads
occur during the tests.

The Task
========

The program `ollama_benchmark.py`  runs a series of cold and warm trials
against an Ollama server and records detailed timing data (TTFT, total latency,
throughput, token counts, and Ollama’s internal duration fields). It loads
prompts from a YAML file, executes the requested number of trials, and writes
the results to a structured JSON file.

We could run this CLI directly:

.. code-block:: bash

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
            "cold_reset_cmd": None,
        }


The purpose of this class is to tell kwdagger how to construct the command to
run your program.

Once you have a node, you also need to define  the function that builds the
pipeline. In this case we do not need to connect any inputs to any outputs.


.. code-block:: python

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


This pipeline has no dependencies—just a single callable node. kwdagger handles
parameter expansion, run IDs, and output organization automatically.


Running the Sweep
-----------------

Use ``kwdagger schedule`` to enumerate all combinations of model, prompt file,
cold/warm trials, and concurrency.

Excerpt from ``run_pipelines.sh``:

.. code-block:: bash

    kwdagger schedule \
        --params="
            pipeline: 'pipelines.py::ollama_benchmark_pipeline()'
            matrix:
                ollama_benchmark.prompt_fpath:
                    - prompts_5.yaml
                ollama_benchmark.model:
                    - gemma3:12b
                    - gemma3:1b
                ollama_benchmark.cold_trials: [3]
                ollama_benchmark.warm_trials: [3]
                ollama_benchmark.concurrency: [0]
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

::

    results_ollama/
        ollama_benchmark/
            ollama_benchmark_id_<hash>/
                ollama_benchmark.json
                ...

Quick Aggregate (Optional)
--------------------------

``kwdagger aggregate`` gives a quick summary of all runs:

.. code-block:: bash

    kwdagger aggregate \
        --pipeline="pipelines.py::ollama_benchmark_pipeline()" \
        --target "$EVAL_DPATH" \
        --output_dpath="$EVAL_DPATH/full_aggregate" \
        --eval_nodes="ollama_benchmark"


Custom Aggregation
------------------

For more detailed analysis, use ``custom_aggregate.py``:

.. code-block:: bash

    python custom_aggregate.py \
        --pattern="results_ollama/ollama_benchmark/*/ollama_benchmark.json"

This script loads each JSON file, extracts both trial-level stats and machine
metadata, and builds a Pandas DataFrame. Export formats such as CSV, Parquet,
or Feather are supported.


Misc
====

Ollama Notes
------------

Helpful ollama commands

Test that you have an ollama server working:

.. code:: bash

    OLLAMA_URL="http://localhost:11434"
    curl -sSf "${OLLAMA_URL}/api/tags" | jq


Test that your ollama server generates results

.. code:: bash

    OLLAMA_URL="http://localhost:11434"
    curl -sS "${OLLAMA_URL}/api/generate" \
        -d '{
              "model": "gpt-oss:20b",
              "prompt": "Summarize the concept of reinforcement learning in one sentence."
            }'


Automatic ways to grab your container and port

.. code:: bash

   # Grab the container name:
   OLLAMA_CONTAINER_NAME=$(docker ps --filter "ancestor=ollama/ollama" --format "{{.Names}}")
   OLLAMA_PORT=$(docker ps --filter "ancestor=ollama/ollama" --format "{{.Ports}}" | sed -n 's/.*:\([0-9]\+\)->.*/\1/p')

