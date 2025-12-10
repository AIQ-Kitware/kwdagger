#!/usr/bin/env bash

# Copy/paste friendly: set SCRIPT_DIR to this folder (edit if you run elsewhere).
if [[ -n "${BASH_SOURCE[0]}" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
else
    SCRIPT_DIR="$HOME/code/kwdagger/docs/source/manual/tutorials/ollama_benchmark"
fi
cd "$SCRIPT_DIR"


# Ensure Python can import this example as a module
export PYTHONPATH=.

# Where kwdagger will write all run directories / results
EVAL_DPATH=$SCRIPT_DIR/results_ollama
echo "EVAL_DPATH = $EVAL_DPATH"

# Default Ollama URL and a conservative cold reset command
# (Adjust the reset command to match your environment)
OLLAMA_URL_DEFAULT="${OLLAMA_URL_DEFAULT:-http://localhost:11434}"
COLD_RESET_CMD_DEFAULT="${COLD_RESET_CMD_DEFAULT:-docker restart ollama}"

# Note, it's actually important to use serial (or a queue where there is only one job
# running at a time, so we don't interfere with timeings, but the kwdagger
# schedule is still useful to enumerate the grid of commands that needs to be
# run and orangize results)

kwdagger schedule \
    --params="
        pipeline: 'pipelines.py::ollama_benchmark_pipeline()'
        matrix:
            # --- Inputs ---
            ollama_benchmark.prompt_fpath:
                # YAML file describing prompts
                - $SCRIPT_DIR/prompts_5.yaml

            # --- Model sweep ---
            ollama_benchmark.model:
                - gemma3:12b
                - gemma3:1b
                - gemma3:4b
                - gemma3n:e4b
                - llama3.2:3b
                - phi3:3.8b
                - qwen3:0.6b
                - qwen3:1.7b
                - qwen3:4b
                - qwen3:8b
                - qwen3:14b

                #- qwen3-vl:2b
                #- qwen3-vl:4b
                #- qwen3-vl:8b

                #- llama3.2:1b
                #- gemma3:27b

                #- deepseek-r1:1.5b
                #- deepseek-r1:7b
                #- deepseek-r1:8b

                #- ministral-3:3b
                #- ministral-3:8b

                #- gemma3:270m
                #- gemma3n:e4b
                #- gpt-oss:20b
                #- ministral-3:14b
                #- olmo2:13b
                #- olmo2:7b

            # --- Cold / warm behavior ---
            ollama_benchmark.cold_trials:
                - 3          # cold runs (first prompt only, no concurrency)
            ollama_benchmark.warm_trials:
                - 3          # warm trials per prompt

            # --- Concurrency for warm runs ---
            ollama_benchmark.concurrency:
                - 0          # no concurrency
                #- 4          # 4 concurrent warm requests

            # --- Server config ---
            ollama_benchmark.ollama_url:
                - '${OLLAMA_URL_DEFAULT}'

            # Use a cold reset command that restarts the Ollama container.
            # You can override this with COLD_RESET_CMD_DEFAULT in the environment.
            ollama_benchmark.cold_reset_cmd:
                - '${COLD_RESET_CMD_DEFAULT}'

            # We specify the hostname so the parameter based hashes can be
            # aggregated across machines
            ollama_benchmark.notes:
                - 'Run on $HOSTNAME'
    " \
    --root_dpath="${EVAL_DPATH}" \
    --backend=serial --skip_existing=1 \
    --run=0


## Technically aggregate does work here, but because parmeters like cold_trials
# and warm_trials impact the stats, and not the underlying method, it isn't the
# most appropriate tool. The aggregate tool assume categorical diffrences
# between different parameters, which is often useful, but less so here.  Still
# this does give you a quick and dirty view of your main results if there
# aren't too many.

kwdagger aggregate \
    --pipeline='pipelines.py::ollama_benchmark_pipeline()' \
    --target "
        - $EVAL_DPATH
    " \
    --output_dpath="$EVAL_DPATH/full_aggregate" \
    --resource_report=0 \
    --io_workers=0 \
    --eval_nodes="
        - ollama_benchmark
    " \
    --stdout_report="
        top_k: 10
        print_models: True
        concise: 1
    " \
    --plot_params="
        enabled: 0
    "

# But it might be more useful to write a custom aggregator for this instance
# and leverage the kwdagger scheduling to manage
python custom_aggregate.py \
    --pattern="results_ollama/ollama_benchmark/*/ollama_benchmark.json"
