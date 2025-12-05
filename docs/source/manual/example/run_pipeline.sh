#!/usr/bin/env bash
set -euo pipefail

# Copy/paste friendly: set EXAMPLE_DIR to this folder (edit if you run elsewhere).
EXAMPLE_DIR="${EXAMPLE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]:-$PWD}")" && pwd)}"
cd "$EXAMPLE_DIR"

# Set PYTHONPATH to ensure Python can see the example directory.
export PYTHONPATH=.

EVAL_DPATH=${EVAL_DPATH:-$PWD/results}
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
    --tmux_workers=2 \
    --backend=tmux --skip_existing=1 \
    --run=1

kwdagger aggregate \
    --pipeline='example_user_module.pipelines.my_sentiment_pipeline()' \
    --target "
        - $EVAL_DPATH
    " \
    --output_dpath="$EVAL_DPATH/full_aggregate" \
    --resource_report=1 \
    --io_workers=0 \
    --eval_nodes="
        - sentiment_evaluate
    " \
    --stdout_report="
        top_k: 10
        print_models: True
        concise: 1
    " \
    --plot_params="
        enabled: 1
    " \
    --cache_resolved_results=False
