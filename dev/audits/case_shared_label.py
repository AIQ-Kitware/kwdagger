"""
Case: a non-path grouping key, declared once and wired.

`model_family` is a label, not a file, and not private to one node. It is
an algo param with a *wired port*: algo params are connectable, so the
value is declared once and flows.

    detect.model_family          <- the only place it is set
        |-> score.model_family        (input -> input)
        `-> calib.model_family        (input -> input)

`include` then carries only the irreducible correlation -- that `resnet`
is a `cnn` -- and says nothing about the consumers. Adding a third
consumer requires no change to `include` at all.

This case drove the addition of parameter ports. Before them, an algo
param could not be connected, so a value every consumer needed had to be
restated for each of them in `include` -- growing as consumers x values.
Declaring it an `in_path` instead would make it connectable but would be
an abuse: `in_paths` are paths, and a label is not one.
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
        name='detect',
        executable='python detect.py',
        in_paths={'dataset_fpath'},
        out_paths={'dets_fpath': 'dets.json'},
        algo_params={'model': 'resnet', 'model_family': 'cnn'},
    )

    nodes = {'detect': detect}
    for name in consumers:
        node = ProcessNode(
            name=name,
            executable=f'python {name}.py',
            in_paths={'dets_fpath'},
            out_paths={f'{name}_fpath': f'{name}.json'},
            algo_params={'model_family': 'cnn'},
        )
        detect.param_ports['model_family'].connect(
            node.param_ports['model_family']
        )
        detect.outputs['dets_fpath'].connect(
            node.inputs['dets_fpath'],
            gather=GatherSpec(group_by=['model_family'], order_by=['model']),
        )
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


#: What the same pipeline costs without wiring: every consumer must be
#: restated on every row.
def include_without_wiring(consumers=('score', 'calib')):
    return [
        {
            'detect.model': model,
            'detect.model_family': family,
            **{f'{c}.model_family': family for c in consumers},
        }
        for model, family in MODELS.items()
    ]
