

This is an example that uses kwdagger to orchestrate runtime benchmarks of
different ollama models. It is a "one-stage" pipeline, which basically means we
are just running a single script with a parameter grid.


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



If you have a local docker instance running ollama:


.. code:: bash

   # Grab the container name:
   OLLAMA_CONTAINER_NAME=$(docker ps --filter "ancestor=ollama/ollama" --format "{{.Names}}")
   OLLAMA_PORT=$(docker ps --filter "ancestor=ollama/ollama" --format "{{.Ports}}" | sed -n 's/.*:\([0-9]\+\)->.*/\1/p')

   # likely you will have ollama and 11434

You should always be able to run your script manually:

.. code:: bash

    python ollama_benchmark.py \
      --prompt_fpath prompts_5.yaml \
      --dst_dpath manual_run \
      --ollama_url http://localhost:11434 \
      --cold_reset_cmd "docker restart ollama" \
      --model gpt-oss:20b \
      --cold_trials 2 \
      --warm_trials 3 \
      --append_jsonl manual_run/all_trials.jsonl

