#!/usr/bin/env bash

# This script is assumed to be run inside the example directory
cd ~/code/kwdagger/docs/source/manual/example

# Set PYTHONPATH to ensure Python can see the example directory.
export PYTHONPATH=.

EVAL_DPATH=$PWD/pipeline_output
kwdagger schedule \
    --params="
        pipeline: 'example_user_module.pipelines.my_demo_pipeline()'
        matrix:
            stage1_predict.src_fpath:
                - README.rst
                - run_pipeline.sh
            stage1_predict.param1:
                - 123
                - 456
                - 32
                - 33
            stage1_evaluate.workers: 4
    " \
    --root_dpath="${EVAL_DPATH}" \
    --tmux_workers=2 \
    --backend=tmux --skip_existing=1 \
    --run=1


EVAL_DPATH=$PWD/pipeline_output
kwdagger aggregate \
    --pipeline='example_user_module.pipelines.my_demo_pipeline()' \
    --target "
        - $EVAL_DPATH
    " \
    --output_dpath="$EVAL_DPATH/full_aggregate" \
    --resource_report=1 \
    --io_workers=0 \
    --eval_nodes="
        - stage1_evaluate
    " \
    --stdout_report="
        top_k: 100
        per_group: null
        macro_analysis: 0
        analyze: 0
        print_models: True
        reference_region: null
        concise: 1
        show_csv: 0
    " \
    --plot_params="
        enabled: 1
    " \
    --cache_resolved_results=False


extra_commands(){

    # Test with a query
    EVAL_DPATH=$PWD/pipeline_output
    kwdagger aggregate \
        --pipeline='example_user_module.pipelines.my_demo_pipeline()' \
        --target "
            - $EVAL_DPATH
        " \
        --output_dpath="$EVAL_DPATH/full_aggregate" \
        --resource_report=1 \
        --io_workers=0 \
        --eval_nodes="
            - stage1_evaluate
        " \
        --stdout_report="
            top_k: 100
            per_group: null
            macro_analysis: 0
            analyze: 0
            print_models: True
            reference_region: null
            concise: 1
            show_csv: 0
        " \
        --query "
        df['params.stage1_predict.param1'] > 100
        "

    # cleanup
    rm -rf ~/code/kwdagger/docs/source/manual/example/pipeline_output
}
