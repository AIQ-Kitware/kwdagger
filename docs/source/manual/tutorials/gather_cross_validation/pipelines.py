"""Python definition of the gather tutorial pipeline."""

from __future__ import annotations

import kwdagger


def build_pipeline():
    train = kwdagger.ProcessNode(
        name='train',
        executable='python gather_demo.py train',
        in_paths={'data_fpath'},
        out_paths={'checkpoint_fpath': 'checkpoint.txt'},
        algo_params={'algorithm': 'linear', 'seed': 0, 'fold': 0},
    )
    build_ensemble = kwdagger.ProcessNode(
        name='build_ensemble',
        executable='python gather_demo.py ensemble',
        in_paths={'checkpoints_fpath'},
        out_paths={'ensemble_fpath': 'ensemble.txt'},
        algo_params={'algorithm': 'linear', 'seed': 0},
    )
    evaluate = kwdagger.ProcessNode(
        name='evaluate',
        executable='python gather_demo.py evaluate',
        in_paths={'ensemble_fpath'},
        out_paths={'metrics_fpath': 'metrics.json'},
        algo_params={'algorithm': 'linear', 'seed': 0, 'test_set': 'clean'},
    )

    train.outputs['checkpoint_fpath'].connect(
        build_ensemble.inputs['checkpoints_fpath'],
        gather=kwdagger.GatherSpec(
            group_by=['algorithm', 'seed'],
            order_by=['fold'],
            require='all_success',
        ),
    )
    build_ensemble.outputs['ensemble_fpath'].connect(
        evaluate.inputs['ensemble_fpath']
    )
    return kwdagger.Pipeline(
        {
            'train': train,
            'build_ensemble': build_ensemble,
            'evaluate': evaluate,
        }
    )
