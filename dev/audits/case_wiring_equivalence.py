"""
Case: the same value, supplied three different ways.

`predict` runs the identical algorithm on the identical dataset in all
three variants. Only the *wiring* of how the path reaches it differs:

    unconnected  the matrix gives predict.data_fpath directly
    aliased      another node's input is aliased into it
    produced     an upstream node emits it as an out_path

If ``algo_id`` means "the identity of this computation", all three should
arguably agree -- the algorithm and its data are the same. They do not.
This case exists to make that visible and to ask whether it is right.
"""

from __future__ import annotations

from kwdagger.pipeline import Pipeline, ProcessNode

DATA = '/data/train.kwcoco.json'


def _predict():
    return ProcessNode(
        name='predict', executable='python predict.py',
        in_paths={'data_fpath'},
        out_paths={'pred_fpath': 'pred.json'},
        algo_params={'model': 'resnet'})


def unconnected():
    dag = Pipeline({'predict': _predict()}); dag.build_nx_graphs(); return dag


def aliased():
    peer = ProcessNode(
        name='peer', executable='python peer.py',
        in_paths={'data_fpath'}, out_paths={'peer_fpath': 'peer.json'})
    predict = _predict()
    peer.inputs['data_fpath'].connect(predict.inputs['data_fpath'])
    dag = Pipeline({'peer': peer, 'predict': predict})
    dag.build_nx_graphs(); return dag


def produced():
    prep = ProcessNode(
        name='prep', executable='python prep.py',
        out_paths={'data_fpath': 'data.json'},
        algo_params={'dataset': 'train'})
    predict = _predict()
    prep.outputs['data_fpath'].connect(predict.inputs['data_fpath'])
    dag = Pipeline({'prep': prep, 'predict': predict})
    dag.build_nx_graphs(); return dag


VARIANTS = {
    'unconnected': (unconnected,
                    {'predict.data_fpath': [DATA], 'predict.model': ['resnet']}),
    'aliased':     (aliased,
                    {'peer.data_fpath': [DATA], 'predict.model': ['resnet']}),
    'produced':    (produced,
                    {'prep.dataset': ['train'], 'predict.model': ['resnet']}),
}
