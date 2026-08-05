"""
Characterization: the two batch scheduling paths must agree.

kwdagger currently schedules a batch two ways. A pipeline with a gather
compiles the whole matrix up front and submits a ``CompiledPipeline``; a
pipeline without one configures and submits a row at a time. That is two
implementations of the same job, and every review round has found them
disagreeing somewhere -- stale row state, divergent Slurm layering, divergent
normalization boundaries.

These tests pin what the two paths must produce identically, so the single-path
refactor can be shown to preserve behavior rather than merely to compile. Each
case builds the *same* pipeline twice, once with an unrelated gather bolted on
to force full-matrix compilation, and compares everything observable about the
nodes that have nothing to do with that gather.

The gather is deliberately unrelated. Its only job is to select the engine.
Nothing about an unrelated node's request should depend on it.
"""

from __future__ import annotations

import json

import pytest
import ubelt as ub

from kwdagger.pipeline import GatherSpec, Pipeline, ProcessNode

# ---------------------------------------------------------------------------
# The pipeline under test, and the unrelated gather that switches engines
# ---------------------------------------------------------------------------


def _subject_nodes():
    """A producer and a consumer, wired the ordinary way."""
    producer = ProcessNode(
        name='producer',
        executable='python producer.py',
        in_paths={'src_fpath'},
        out_paths={'data_fpath': 'data.json'},
        algo_params={'algo': 'x'},
        perf_params={'workers': 1},
    )
    consumer = ProcessNode(
        name='consumer',
        executable='python consumer.py',
        in_paths={'data_fpath'},
        out_paths={'result_fpath': 'result.json'},
        algo_params={'thresh': 0.5},
    )
    producer.outputs['data_fpath'].connect(consumer.inputs['data_fpath'])
    return [producer, consumer]


def _unrelated_gather_nodes():
    """Wired only to each other; touches nothing in ``_subject_nodes``."""
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
    return [shard, merge]


#: Rows for the gather nodes, merged into every row so the gather resolves.
_GATHER_ROW = {'shard.dataset': 'a', 'shard.fold': 0, 'merge.dataset': 'a'}

#: The names this file compares. The gather's own nodes are excluded: they
#: exist on one side only, which is the whole point of them.
_SUBJECT_NAMES = ('producer', 'consumer')


def _build(with_gather):
    nodes = _subject_nodes()
    if with_gather:
        nodes = nodes + _unrelated_gather_nodes()
    return Pipeline(nodes)


def _rows_for(rows, with_gather):
    if not with_gather:
        return [dict(row) for row in rows]
    return [dict(row, **_GATHER_ROW) for row in rows]


# ---------------------------------------------------------------------------
# Observing a submitted batch, identically on either engine
# ---------------------------------------------------------------------------


def _scrub(value, root):
    """
    Replace the cache root so the two runs are comparable.

    They deliberately run under different roots -- identity is root-relative,
    so if scrubbing were ever unnecessary the relocation test below would be
    the one making that point, not this helper hiding it.
    """
    if isinstance(value, str):
        return value.replace(str(root), '{root}')
    if isinstance(value, dict):
        return {k: _scrub(v, root) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(v, root) for v in value]
    return value


def _job_slurm_options(job):
    """The Slurm options a submitted job carries, as cmd_queue renders them."""
    if job is None:
        return None
    return {
        key: value
        for key, value in sorted(getattr(job, 'unused_kwargs', {}).items())
        if value is not None
    }


def _node_record(node, job, root):
    """Everything about one submitted node that the two paths must agree on."""
    return _scrub(
        {
            'process_id': node.process_id,
            'command': node.final_command(),
            'node_dpath': str(node.final_node_dpath.relative_to(root)),
            'out_paths': {
                k: str(v) for k, v in sorted(node.final_out_paths.items())
            },
            'input_config': {
                k: str(v) for k, v in sorted(node.final_input_config.items())
            },
            'algo_config': dict(sorted(node.final_algo_config.items())),
            'perf_config': dict(sorted(node.final_perf_config.items())),
            'enabled': bool(node.enabled),
            # What the scheduler is actually asked for. Deliberately not
            # ``node.slurm_options``: that is an intermediate the two paths fill
            # differently (see the test at the bottom of this file), and comparing
            # it would pin an implementation detail rather than the request.
            'effective_slurm': _job_slurm_options(job),
            'predecessors': sorted(
                n.name for n in node.effective_predecessor_process_nodes()
            ),
            'provenance': node.requested_provenance_record(),
            'job_config': json.dumps(node._depends_config(), sort_keys=True),
            'invoke_sh': node._invocation_script_text(),
            'queue_depends': sorted(
                getattr(d, 'name', d) for d in (job.depends or [])
            )
            if job is not None
            else None,
            'queue_command': job.command if job is not None else None,
        },
        root,
    )


def _submit(rows, with_gather, root, backend='serial', **submit_kw):
    """
    Schedule ``rows`` and report each subject node's request.

    With a gather this compiles the full matrix; without one it configures and
    submits a row at a time. Those are the two paths under comparison.
    """
    dag = _build(with_gather)
    rows = _rows_for(rows, with_gather)
    kwargs = {
        'enable_links': False,
        'write_invocations': False,
        'write_configs': False,
    }
    kwargs.update(submit_kw)
    records = {}

    def _collect(nodes, queue):
        for node in nodes:
            if node.name not in _SUBJECT_NAMES:
                continue
            job = queue.named_jobs.get(node.process_id)
            records[(node.name, node.process_id)] = _node_record(
                node, job, root
            )

    if with_gather:
        compiled = dag.compile_configurations(
            rows, root_dpath=root, cache=False
        )
        summary = compiled.submit_jobs(
            queue={'backend': backend, 'name': 'parity'}, **kwargs
        )
        statuses = [summary['node_status']]
        queue = summary['queue']
        _collect(compiled.nodes.values(), queue)
    else:
        queue = None
        statuses = []
        for row in rows:
            dag.configure(config=row, root_dpath=root, cache=False)
            summary = dag.submit_jobs(
                queue=queue or {'backend': backend, 'name': 'parity'},
                **kwargs,
            )
            queue = summary['queue']
            statuses.append(summary['node_status'])
            # Collected per row: this path reuses one mutable node object, so
            # after the loop every name reports only the final row's state.
            # The compiler clones instead, which is the difference the
            # refactor removes.
            _collect(dag.node_dict.values(), queue)

    return {
        'records': records,
        'statuses': [
            {k: v for k, v in s.items() if k in _SUBJECT_NAMES}
            for s in statuses
        ],
    }


def _assert_parity(rows, tmp_path, **submit_kw):
    plain = _submit(rows, False, tmp_path / 'plain', **submit_kw)
    gathered = _submit(rows, True, tmp_path / 'gather', **submit_kw)
    assert set(plain['records']) == set(gathered['records']), (
        'the same requests must exist on both paths'
    )
    for key in sorted(plain['records']):
        lhs, rhs = plain['records'][key], gathered['records'][key]
        for field in sorted(lhs):
            assert lhs[field] == rhs[field], (
                f'{key[0]}.{field} differs between the two scheduling paths'
            )
    return plain, gathered


# ---------------------------------------------------------------------------
# Parity across the matrix shapes
# ---------------------------------------------------------------------------


def test_one_row(tmp_path):
    _assert_parity([{'producer.src_fpath': '/data/a'}], tmp_path)


def test_multiple_independent_rows(tmp_path):
    _assert_parity(
        [
            {'producer.src_fpath': '/data/a'},
            {'producer.src_fpath': '/data/b'},
            {'producer.src_fpath': '/data/c'},
        ],
        tmp_path,
    )


def test_duplicate_equal_identities(tmp_path):
    """Two identical requests must collapse the same way on both paths."""
    row = {'producer.src_fpath': '/data/a'}
    _assert_parity([dict(row), dict(row)], tmp_path)


def test_manual_and_produced_equal_paths(tmp_path):
    """
    The identity invariant across engines: a hand-written path equal to the
    produced one is the same computation.

    The override has to be computed per root, since a produced path contains
    the root it was produced under -- which is exactly why the records are
    compared with the root scrubbed.
    """
    out = {}
    for with_gather in (False, True):
        root = tmp_path / ('gather' if with_gather else 'plain')
        probe = _build(False)
        probe.configure(
            {'producer.src_fpath': '/data/a'}, root_dpath=root, cache=False
        )
        produced = str(probe.node_dict['consumer'].final_in_paths['data_fpath'])
        out[with_gather] = _submit(
            [
                {
                    'producer.src_fpath': '/data/a',
                    'consumer.data_fpath': produced,
                }
            ],
            with_gather,
            root,
        )
    plain, gathered = out[False], out[True]
    assert set(plain['records']) == set(gathered['records'])
    for key in sorted(plain['records']):
        for field in sorted(plain['records'][key]):
            assert (
                plain['records'][key][field] == gathered['records'][key][field]
            ), f'{key[0]}.{field} differs between the two scheduling paths'


def test_a_node_default_and_a_row_override(tmp_path):
    _assert_parity(
        [
            {'producer.src_fpath': '/data/a'},
            {'producer.src_fpath': '/data/a', 'consumer.thresh': 0.9},
        ],
        tmp_path,
    )


def test_disabled_producer(tmp_path):
    _assert_parity(
        [{'producer.src_fpath': '/data/a', 'producer.__enabled__': False}],
        tmp_path,
    )


def test_pathlike_normalization(tmp_path):
    _assert_parity([{'producer.src_fpath': ub.Path('/data/a')}], tmp_path)


def test_output_path_override(tmp_path):
    _assert_parity(
        [
            {
                'producer.src_fpath': '/data/a',
                'producer.data_fpath': '/out/d.json',
            }
        ],
        tmp_path,
    )


def test_perf_params_do_not_reach_identity_on_either_path(tmp_path):
    _assert_parity(
        [{'producer.src_fpath': '/data/a', 'producer.workers': 4}], tmp_path
    )


@pytest.mark.parametrize('flag', ['write_configs', 'write_invocations'])
def test_bookkeeping_flags(flag, tmp_path):
    _assert_parity(
        [{'producer.src_fpath': '/data/a'}], tmp_path, **{flag: True}
    )


def test_slurm_options_layer_identically(tmp_path):
    _assert_parity(
        [
            {
                'producer.src_fpath': '/data/a',
                '__slurm_options__': {'partition': 'row'},
                'consumer.__slurm_options__': {'gres': 'gpu:1'},
            }
        ],
        tmp_path,
        backend='slurm',
    )


def test_cache_root_relocation_changes_no_identity(tmp_path):
    """Identity is root-relative, and must be so on both paths."""
    ids = {}
    for location in ['one', 'two']:
        for with_gather in [False, True]:
            root = tmp_path / location / ('g' if with_gather else 'p')
            out = _submit(
                [{'producer.src_fpath': '/data/a'}], with_gather, root
            )
            ids[(location, with_gather)] = sorted(
                pid for _name, pid in out['records']
            )
    assert ids[('one', False)] == ids[('two', False)]
    assert ids[('one', True)] == ids[('two', True)]
    assert ids[('one', False)] == ids[('one', True)]


# ---------------------------------------------------------------------------
# The divergence this refactor removed
# ---------------------------------------------------------------------------


def test_node_slurm_options_means_the_same_thing_on_both_paths(tmp_path):
    """
    This test used to record a *difference*, deliberately.

    ``node.slurm_options`` meant "node-level options" on one path and
    "node-level plus row-global" on the other: the compiler copied a row-global
    mapping into node-local configuration, while the row-at-a-time path left it
    on the ``Pipeline`` and layered it at submission. The two agreed on the
    effective request -- every other test in this file asserts that -- but the
    intermediate they agreed *through* did not exist. That was the duplicated
    Slurm authority, visible in one attribute.

    Phase 4 replaced it with one resolver, so the assertion flips: the
    attribute means node-level on both paths, and the complete request is
    ``effective_slurm_options``, resolved once from all four layers.
    """
    row = {
        'producer.src_fpath': '/data/a',
        '__slurm_options__': {'partition': 'row'},
        'producer.__slurm_options__': {'gres': 'gpu:1'},
    }
    node_level = {}
    effective = {}
    for with_gather in (False, True):
        root = tmp_path / ('gather' if with_gather else 'plain')
        dag = _build(with_gather)
        rows = _rows_for([row], with_gather)
        if with_gather:
            compiled = dag.compile_configurations(
                rows, root_dpath=root, cache=False
            )
        else:
            dag.configure(config=rows[0], root_dpath=root, cache=False)
            compiled = dag.compile_current_configuration()
        (node,) = compiled.nodes_by_name['producer']
        node_level[with_gather] = dict(node.slurm_options or {})
        effective[with_gather] = dict(node.effective_slurm_options or {})

    assert node_level[False] == node_level[True] == {'gres': 'gpu:1'}, (
        'the attribute means the node-level layers, on either path'
    )
    assert (
        effective[False]
        == effective[True]
        == {
            'partition': 'row',
            'gres': 'gpu:1',
        }
    ), 'and the complete request is resolved once, the same way'
