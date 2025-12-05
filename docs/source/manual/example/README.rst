KWDagger Example
================

NOTE: These docs are sparse and need to be expanded.

This folder contains a minimal example of how to use the GeoWATCH MLOps
pipeline system. Look into the ``example_user_module/pipelines.py`` file to
see examples of pipeline definitions, and ``run_pipeline.sh`` for how to
execute pipelines and aggregate results.

Other Demo Locations:


There is also a demo integrated into the code at
:func:`kwdagger.pipeline:demodata_pipeline` that is run in CI
testing.

See Also:

* ~/code/kwdagger/kwdagger/pipeline_nodes.py
* ~/code/kwdagger/tests/test_mlops_scheduler.py
* ~/code/kwdagger/tests/test_mlops_pipeline.py


Slides: https://docs.google.com/presentation/d/1mZJCGXZT6ekfj3KZ7gTFiBa3Sj8Y8hsP2YlvHaYbrAM/edit#slide=id.g30a42d1df1d_0_141


At a high level, running demo pipeline involves scheduling a set of jobs to
evaluate a pipeline over a grid of parameters. The matrix of parameters works
similarly to how GithubActions CI works.

.. code:: bash

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


This produces a set of output directories:

.. code::

    ├── stage1_evaluate
    │   ├── stage1_evaluate_id_351a406d
    │   ├── stage1_evaluate_id_4e8b440f
    │   ├── stage1_evaluate_id_57507328
    │   ├── stage1_evaluate_id_6f5bd28e
    │   ├── stage1_evaluate_id_7668fa99
    │   ├── stage1_evaluate_id_791c09ad
    │   ├── stage1_evaluate_id_9d708340
    │   └── stage1_evaluate_id_c709c5d0
    └── stage1_predict
        ├── stage1_predict_id_0416dd8c
        ├── stage1_predict_id_1c725100
        ├── stage1_predict_id_8a282106
        ├── stage1_predict_id_a3256a1d
        ├── stage1_predict_id_ba7128ac
        ├── stage1_predict_id_bb85c542
        ├── stage1_predict_id_c38c1967
        └── stage1_predict_id_e9117090


Where predict node outputs look like:

.. code:: text

    └── stage1_predict
        ├── stage1_predict_id_0416dd8c
        │   ├──  invoke.sh
        │   ├──  job_config.json
        │   └──  stage1_prediction.json

And evaluate outputs look like:

.. code:: text

    └── stage1_evaluate
        ├── stage1_evaluate_id_351a406d
        │   ├──  invoke.sh
        │   ├──  job_config.json
        │   ├──  resolved_result_row_v012.json
        │   └──  stage1_evaluation.json


Calling aggregate, loops over that directory structure, loads all of the inputs
/ output / results into a table for each node, and then displays / visualizes
those results. E.g.

.. code:: bash

    EVAL_DPATH=$PWD/pipeline_output
    python -m geowatch.mlops.aggregate \
        --pipeline='example_user_module.pipelines.my_demo_pipeline()' \
        --target "
            - $EVAL_DPATH
        " \
        --output_dpath="$EVAL_DPATH/full_aggregate" \
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
        --cache_resolved_results=False


Will produce a summary table of the results:

.. code:: text

    Varied Basis: = {
        'params.stage1_predict.src_fpath': {
            'run_pipeline.sh': 4,
            'README.rst': 4,
        },
        'params.stage1_predict.param1': {
            123: 2,
            456: 2,
            32: 2,
            33: 2,
        },
    }
    Constant Params: {
        'params.stage1_evaluate.workers': 4,
    }
    Varied Parameter LUT: {
        'glbocdwvkxtv': {
            'params.stage1_predict.src_fpath': 'run_pipeline.sh',
            'params.stage1_predict.param1': 123,
        },
        'nwwgnpyasplu': {
            'params.stage1_predict.src_fpath': 'run_pipeline.sh',
            'params.stage1_predict.param1': 456,
        },
        'jtzwyszovjbn': {
            'params.stage1_predict.src_fpath': 'run_pipeline.sh',
            'params.stage1_predict.param1': 32,
        },
        'rezvuaccxrwa': {
            'params.stage1_predict.src_fpath': 'README.rst',
            'params.stage1_predict.param1': 123,
        },
        'hgiumhuqyzgi': {
            'params.stage1_predict.src_fpath': 'README.rst',
            'params.stage1_predict.param1': 456,
        },
        'pidrwcexvksv': {
            'params.stage1_predict.src_fpath': 'README.rst',
            'params.stage1_predict.param1': 33,
        },
        'isorfkkiscnn': {
            'params.stage1_predict.src_fpath': 'run_pipeline.sh',
            'params.stage1_predict.param1': 33,
        },
        'rssahtzdgbts': {
            'params.stage1_predict.src_fpath': 'README.rst',
            'params.stage1_predict.param1': 32,
        },
    }
    ---
    Top 8 / 8 for stage1_evaluate, unknown
    region_id param_hashid  accuracy  hamming_distance
      unknown glbocdwvkxtv  0.478516               267
      unknown nwwgnpyasplu  0.484375               264
      unknown jtzwyszovjbn  0.490234               261
      unknown rezvuaccxrwa  0.492188               260
      unknown hgiumhuqyzgi  0.503906               254
      unknown pidrwcexvksv  0.509766               251
      unknown isorfkkiscnn  0.529297               241
      unknown rssahtzdgbts  0.533203               239

The table corresponds to the highest scoring set of results. Becuase each
process may have many parameters, a hash of the parameters is shown instead,
and above the table we print out a mapping from the parameter hash to the
specific paremeters that were used.
