Notes
=====

These are developer notes that were helpful when writing this tutorial.


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

