"""
Case: a non-path grouping key, declared once and wired.

`model_family` is a label, not a file. It is declared as an *input port*
rather than an algo param, which is what makes it shareable: ports can be
connected, algo params cannot.

    detect.model_family          <- the only place it is set
        |-> score.model_family        (input -> input)
        `-> calib.model_family        (input -> input)

`include` then carries only the irreducible correlation -- that `resnet`
is a `cnn` -- and says nothing about the consumers. Adding a third
consumer requires no change to `include` at all.

This case exists because it refutes an argument that was made at length
during the audit: that gathering by a non-path key required a new
"partition" compilation mode, on the grounds that a label could not be
wired. It can. Nothing about `in_paths` requires a filesystem path, and
once a value is a port it participates in aliasing, identity, and
grouping like any other.
"""

from __future__ import annotations

from kwdagger.pipeline import GatherSpec, Pipeline, ProcessNode

#: Six models across three families. The correlation is irreducible: it is
#: the actual information, and no mechanism can remove it.
MODELS = {
    'resnet': 'cnn',
    'convnext': 'cnn',
    'vit': 'transformer',
    'swin': 'transformer',
    'mamba': 'ssm',
    'hyena': 'ssm',
}


def build(consumers=('score', 'calib')):
    detect = ProcessNode(
        name='detect', executable='python detect.py',
        # model_family is an in_path purely so it can be wired.
        in_paths={'dataset_fpath', 'model_family'},
        out_paths={'dets_fpath': 'dets.json'},
        algo_params={'model': 'resnet'})

    nodes = {'detect': detect}
    for name in consumers:
        node = ProcessNode(
            name=name, executable=f'python {name}.py',
            in_paths={'dets_fpath', 'model_family'},
            out_paths={f'{name}_fpath': f'{name}.json'})
        detect.inputs['model_family'].connect(node.inputs['model_family'])
        detect.outputs['dets_fpath'].connect(
            node.inputs['dets_fpath'],
            gather=GatherSpec(group_by=['model_family'], order_by=['model']))
        nodes[name] = node

    dag = Pipeline(nodes)
    dag.build_nx_graphs()
    return dag


MATRIX = {
    'detect.dataset_fpath': ['/data/train.kwcoco.json'],
    'detect.model': list(MODELS),
}

#: Only the correlation. No consumer appears here, however many there are.
INCLUDE = [
    {'detect.model': model, 'detect.model_family': family}
    for model, family in MODELS.items()
]

#: What the same pipeline costs if model_family is an algo param instead:
#: every consumer must be restated on every row.
def include_without_wiring(consumers=('score', 'calib')):
    return [
        {'detect.model': model, 'detect.model_family': family,
         **{f'{c}.model_family': family for c in consumers}}
        for model, family in MODELS.items()
    ]
