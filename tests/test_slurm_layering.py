"""
Slurm options layer the same way whether or not the pipeline has a gather.

There are four layers, least specific first:

1. the pipeline base -- the CLI's ``--slurm_options`` or a parameter file's
   top-level ``slurm_options``;
2. a matrix row's global ``__slurm_options__``;
3. a node's declared default;
4. that row's per-node ``<node>.__slurm_options__`` override.

The two scheduling paths used to combine them differently. An ordinary
pipeline merged key-wise, while the compiler *substituted* a row-global
mapping for the node's own: a node with any local option dropped every
row-global key, and a node with none took the row-global mapping at node
precedence. Adding an unrelated gather to a pipeline could therefore change
what resources one of its nodes asked for, which is what this file pins.
"""

from __future__ import annotations

import pytest
import ubelt as ub

from kwdagger.pipeline import GatherSpec, Pipeline, ProcessNode


def _train(**kwargs):
    return ProcessNode(
        name='train',
        executable='python train.py',
        out_paths={'out_fpath': 'out.json'},
        algo_params={'model': 'm'},
        **kwargs,
    )


def _gather_nodes():
    """An unrelated gather, present only to force full-matrix compilation."""
    shard = ProcessNode(
        name='shard',
        executable='python shard.py',
        out_paths={'part_fpath': 'part.txt'},
        algo_params={'dataset': 'a', 'fold': 0},
    )
    merge = ProcessNode(
        name='merge',
        executable='python merge.py',
        in_paths={'parts_fpath'},
        out_paths={'merged_fpath': 'merged.txt'},
        algo_params={'dataset': 'a'},
    )
    shard.outputs['part_fpath'].connect(
        merge.inputs['parts_fpath'],
        gather=GatherSpec(group_by=['dataset'], order_by=['fold']),
    )
    return {'shard': shard, 'merge': merge}


_GATHER_ROW = {'shard.dataset': 'a', 'shard.fold': 0, 'merge.dataset': 'a'}


def _effective_options(row, *, base, node_default, gather, root):
    """
    The Slurm options ``train`` is actually submitted with.

    Read back off the queued job so the assertion covers the whole path, not
    just the merge helper.
    """
    train = (
        _train() if node_default is None else _train(slurm_options=node_default)
    )
    nodes = {'train': train}
    row = dict(row)
    if gather:
        nodes.update(_gather_nodes())
        row.update(_GATHER_ROW)
    dag = Pipeline(list(nodes.values()))
    dag._base_slurm_options = dict(base or {})
    dag.__slurm_options__ = dict(dag._base_slurm_options)

    if gather:
        compiled = dag.compile_configurations(
            [row], root_dpath=root, cache=False
        )
        summary = compiled.submit_jobs(
            queue={'backend': 'slurm', 'name': 'layering'},
            enable_links=False,
            write_invocations=False,
            write_configs=False,
        )
        node = [n for n in compiled.nodes.values() if n.name == 'train'][0]
    else:
        dag.configure(config=row, root_dpath=root, cache=False)
        summary = dag.submit_jobs(
            queue={'backend': 'slurm', 'name': 'layering'},
            enable_links=False,
            write_invocations=False,
            write_configs=False,
        )
        node = train
    # cmd_queue keeps options it has no attribute for in ``unused_kwargs``
    # and renders them as ``--<key>=<value>`` in the sbatch line, so this is
    # what the scheduler is actually asked for.
    job = summary['queue'].named_jobs[node.process_id]
    return {
        key: value
        for key, value in job.unused_kwargs.items()
        # The queue's own defaults arrive as None, which is not a request.
        if key in {'partition', 'gres', 'account'} and value is not None
    }


CASES = [
    pytest.param(
        {},
        {'partition': 'base'},
        None,
        {'partition': 'base'},
        id='base-only',
    ),
    pytest.param(
        {'__slurm_options__': {'gres': 'gpu:1'}},
        {'partition': 'base'},
        None,
        {'partition': 'base', 'gres': 'gpu:1'},
        id='disjoint-keys-at-each-layer',
    ),
    pytest.param(
        {'__slurm_options__': {'partition': 'row'}},
        {'partition': 'base'},
        None,
        {'partition': 'row'},
        id='row-global-overrides-the-base',
    ),
    pytest.param(
        {'__slurm_options__': {'partition': 'row', 'gres': 'gpu:1'}},
        None,
        {'partition': 'declared'},
        {'partition': 'declared', 'gres': 'gpu:1'},
        id='node-default-outranks-row-global-but-keeps-its-other-keys',
    ),
    pytest.param(
        {
            '__slurm_options__': {'partition': 'row', 'account': 'acct'},
            'train.__slurm_options__': {'partition': 'node'},
        },
        {'gres': 'gpu:1'},
        None,
        {'partition': 'node', 'account': 'acct', 'gres': 'gpu:1'},
        id='row-global-plus-per-node',
    ),
    pytest.param(
        {'train.__slurm_options__': {'partition': 'node'}},
        {'partition': 'base'},
        {'partition': 'declared'},
        {'partition': 'node'},
        id='same-key-overridden-at-every-layer',
    ),
]


@pytest.mark.parametrize('row, base, node_default, expected', CASES)
@pytest.mark.parametrize('gather', [False, True], ids=['plain', 'gather'])
def test_the_layers_combine_the_same_way_on_both_paths(
    row, base, node_default, expected, gather, tmp_path
):
    got = _effective_options(
        row,
        base=base,
        node_default=node_default,
        gather=gather,
        root=tmp_path / ('gather' if gather else 'plain'),
    )
    assert got == expected


@pytest.mark.parametrize('row, base, node_default, expected', CASES)
def test_a_gather_does_not_change_what_a_node_asks_for(
    row, base, node_default, expected, tmp_path
):
    """
    The parity statement itself: an unrelated gather is a compilation detail
    and must not reach an unrelated node's resource request.
    """
    plain = _effective_options(
        row,
        base=base,
        node_default=node_default,
        gather=False,
        root=tmp_path / 'plain',
    )
    gathered = _effective_options(
        row,
        base=base,
        node_default=node_default,
        gather=True,
        root=tmp_path / 'gather',
    )
    assert plain == gathered == expected


# ---------------------------------------------------------------------------
# Both paths cross the normalization boundary at the same point
# ---------------------------------------------------------------------------
#
# ``Pipeline.configure`` used to pop ``__slurm_options__`` *before* normalizing
# the row, while full-matrix compilation normalized its rows first. So whether
# a reserved key could even be seen, and what a value inside it looked like,
# depended on which path the row took.


@pytest.mark.parametrize(
    'row, expected',
    [
        pytest.param(
            {'__slurm_options__': {'partition': ub.Path('/p')}},
            {'partition': '/p'},
            id='pathlike-value-inside-row-global-options',
        ),
        pytest.param(
            {ub.Path('__slurm_options__'): {'partition': 'row'}},
            {'partition': 'row'},
            id='pathlike-outer-key-naming-the-reserved-option',
        ),
        pytest.param(
            {'train.__slurm_options__': {'partition': ub.Path('/n')}},
            {'partition': '/n'},
            id='pathlike-value-inside-a-per-node-override',
        ),
    ],
)
def test_normalization_reaches_slurm_options_on_both_paths(
    row, expected, tmp_path
):
    plain = _effective_options(
        row,
        base=None,
        node_default=None,
        gather=False,
        root=tmp_path / 'plain',
    )
    gathered = _effective_options(
        row,
        base=None,
        node_default=None,
        gather=True,
        root=tmp_path / 'gather',
    )
    assert plain == gathered == expected
    # The point of normalizing: what reaches the sbatch line is text, whichever
    # entrance it came in by.
    assert all(isinstance(value, str) for value in plain.values())


def test_a_pathlike_pipeline_base_normalizes_too(tmp_path):
    """The base is the fourth entrance, and it bypasses row normalization."""
    got = _effective_options(
        {},
        base={'partition': ub.Path('/base')},
        node_default=None,
        gather=False,
        root=tmp_path,
    )
    assert got == {'partition': '/base'}
    assert isinstance(got['partition'], str)
