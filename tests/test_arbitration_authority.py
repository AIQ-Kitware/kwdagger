"""
Compilation arbitrates; submission is a backstop.

Several things deliberately stay out of ``process_id``, so two matrix rows can
be one process and still disagree about how it should run. Nothing in the
payload can settle that, and whichever request arrived first would otherwise
decide silently.

This file pins where that is settled and that the answer does not depend on
matrix order. Every conflict class is exercised through
``compile_configurations``, in both row orders, and shown to be reported before
any queue exists. The one class compilation cannot see -- the bookkeeping flags
passed to ``submit_jobs``, and requests that were never compiled together --
is shown to be caught by the defensive check at submission instead.

``test_identity_model.py`` covers what each class *means*; this covers who
decides and when.
"""

from __future__ import annotations

import pytest

from kwdagger.pipeline import Pipeline, ProcessNode

# ---------------------------------------------------------------------------
# Pipelines, and pairs of rows that share an identity while disagreeing
# ---------------------------------------------------------------------------


def _solo_pipeline():
    return Pipeline(
        [
            ProcessNode(
                name='predict',
                executable='python predict.py',
                out_paths={'out_fpath': 'out.json'},
                algo_params={'model': 'm'},
                perf_params={'workers': 4},
            )
        ]
    )


def _chain_pipeline():
    producer = ProcessNode(
        name='producer',
        executable='python producer.py',
        out_paths={'data_fpath': 'data.json'},
        node_dpath='.',
    )
    consumer = ProcessNode(
        name='consumer',
        executable='python consumer.py',
        in_paths={'data_fpath'},
        out_paths={'result_fpath': 'result.json'},
    )
    producer.outputs['data_fpath'].connect(consumer.inputs['data_fpath'])
    return Pipeline([producer, consumer])


def _two_output_pipeline():
    """
    One producer feeding two consumer inputs. Overriding one of them leaves
    the producer a prerequisite either way, so the prerequisite *union* is
    blind to the difference and only the delivery comparison sees it.
    """
    producer = ProcessNode(
        name='producer',
        executable='python producer.py',
        out_paths={'out_a_fpath': 'a.json', 'out_b_fpath': 'b.json'},
        node_dpath='.',
    )
    consumer = ProcessNode(
        name='consumer',
        executable='python consumer.py',
        in_paths={'in_a_fpath', 'in_b_fpath'},
        out_paths={'result_fpath': 'result.json'},
    )
    producer.outputs['out_a_fpath'].connect(consumer.inputs['in_a_fpath'])
    producer.outputs['out_b_fpath'].connect(consumer.inputs['in_b_fpath'])
    return Pipeline([producer, consumer])


def _alias_pipeline():
    """
    Two peers forwarding into one consumer port. Neither is a producer, so
    there are no origins and no prerequisites to disagree about -- but which
    peer supplied the value is part of what was requested, and a row supplies
    exactly one of them.
    """
    left = ProcessNode(
        name='left',
        executable='python left.py',
        in_paths={'data_fpath'},
        out_paths={'left_fpath': 'left.json'},
    )
    right = ProcessNode(
        name='right',
        executable='python right.py',
        in_paths={'data_fpath'},
        out_paths={'right_fpath': 'right.json'},
    )
    consumer = ProcessNode(
        name='consumer',
        executable='python consumer.py',
        in_paths={'data_fpath'},
        out_paths={'result_fpath': 'result.json'},
    )
    left.inputs['data_fpath'].connect(consumer.inputs['data_fpath'])
    right.inputs['data_fpath'].connect(consumer.inputs['data_fpath'])
    return Pipeline([left, right, consumer])


def _produced_path(build, base, root, node_name, port):
    """Compile one row just to learn the path a producer will write."""
    compiled = build().compile_configurations(
        [dict(base)], root_dpath=root, cache=False
    )
    (node,) = compiled.nodes_by_name[node_name]
    return str(node.final_in_paths[port])


def _enabled_case(tmp_path):
    rows = [
        {'predict.model': 'm', 'predict.__enabled__': True},
        {'predict.model': 'm', 'predict.__enabled__': False},
    ]
    return _solo_pipeline, rows, '__enabled__'


def _slurm_case(tmp_path):
    rows = [
        {'predict.model': 'm', '__slurm_options__': {'gres': 'gpu:1'}},
        {'predict.model': 'm', '__slurm_options__': {'gres': 'gpu:2'}},
    ]
    return _solo_pipeline, rows, '__slurm_options__'


def _perf_case(tmp_path):
    rows = [
        {'predict.model': 'm', 'predict.workers': 4},
        {'predict.model': 'm', 'predict.workers': 16},
    ]
    return _solo_pipeline, rows, 'perf_params'


def _out_paths_case(tmp_path):
    rows = [
        {'predict.model': 'm'},
        {'predict.model': 'm', 'predict.out_fpath': '/elsewhere/out.json'},
    ]
    return _solo_pipeline, rows, 'out_paths'


def _prerequisite_case(tmp_path):
    produced = _produced_path(
        _chain_pipeline, {}, tmp_path, 'consumer', 'data_fpath'
    )
    rows = [{}, {'consumer.data_fpath': produced}]
    return _chain_pipeline, rows, 'execution prerequisites'


def _delivery_case(tmp_path):
    produced = _produced_path(
        _two_output_pipeline, {}, tmp_path, 'consumer', 'in_a_fpath'
    )
    rows = [{}, {'consumer.in_a_fpath': produced}]
    return _two_output_pipeline, rows, 'input delivery'


def _requested_case(tmp_path):
    # The same effective value, supplied by a different peer. Identity,
    # prerequisites and delivery all agree; only the record differs.
    rows = [
        {'left.data_fpath': '/same/path'},
        {'right.data_fpath': '/same/path'},
    ]
    return _alias_pipeline, rows, 'requested experiment'


#: Every class of disagreement two requests sharing an identity can have,
#: paired with the phrase the report must name it by.
CONFLICT_CASES = [
    _enabled_case,
    _slurm_case,
    _perf_case,
    _out_paths_case,
    _prerequisite_case,
    _delivery_case,
    _requested_case,
]


def _case_id(case):
    return case.__name__.strip('_').removesuffix('_case')


# ---------------------------------------------------------------------------
# The compiler is the authority, and it does not care about row order
# ---------------------------------------------------------------------------


@pytest.mark.parametrize('case', CONFLICT_CASES, ids=_case_id)
def test_every_conflict_class_is_reported_in_either_row_order(case, tmp_path):
    build, rows, phrase = case(tmp_path)
    messages = []
    for ordering in (rows, list(reversed(rows))):
        with pytest.raises(ValueError) as excinfo:
            build().compile_configurations(
                ordering, root_dpath=tmp_path, cache=False
            )
        messages.append(str(excinfo.value))
    assert phrase in messages[0], messages[0]
    assert phrase in messages[1], messages[1]
    # The same conflict either way, not two different outcomes. The words are
    # the same set; only which row is named first differs.
    assert set(messages[0].split()) == set(messages[1].split())


@pytest.mark.parametrize('case', CONFLICT_CASES, ids=_case_id)
def test_a_conflict_is_reported_before_any_queue_exists(case, tmp_path):
    """
    The point of arbitrating at compile time. Nothing is submitted, no result
    directory is touched, and the user is told before any of the matrix runs.
    """
    build, rows, _phrase = case(tmp_path)
    with pytest.raises(ValueError):
        build().compile_configurations(rows, root_dpath=tmp_path, cache=False)


@pytest.mark.parametrize('case', CONFLICT_CASES, ids=_case_id)
def test_the_conflicting_row_is_named(case, tmp_path):
    """A user has to be able to find which rows to fix."""
    build, rows, _phrase = case(tmp_path)
    with pytest.raises(ValueError) as excinfo:
        build().compile_configurations(rows, root_dpath=tmp_path, cache=False)
    message = str(excinfo.value)
    assert 'row 0' in message.lower()
    assert 'row 1' in message.lower()


def test_agreeing_rows_deduplicate_in_either_order(tmp_path):
    """
    The complement. Rows that agree about everything identity cannot
    arbitrate are one process, and which one compiled first is not observable.
    """
    row = {'predict.model': 'm', 'predict.workers': 8}
    seen = []
    for _ in range(2):
        compiled = _solo_pipeline().compile_configurations(
            [dict(row), dict(row)], root_dpath=tmp_path, cache=False
        )
        (node,) = compiled.nodes_by_name['predict']
        seen.append((node.process_id, node.final_command()))
    assert seen[0] == seen[1]


def test_which_row_becomes_canonical_is_not_observable(tmp_path):
    """
    Reversing the matrix must not change the compiled result. It cannot,
    because everything the surviving instance carries has been compared -- but
    that is the property worth asserting rather than reasoning about.
    """

    def _fingerprint(rows):
        compiled = _chain_pipeline().compile_configurations(
            rows, root_dpath=tmp_path, cache=False
        )
        return sorted(
            (
                node.name,
                node.process_id,
                node.final_command(),
                sorted(
                    p.process_id
                    for p in node.effective_predecessor_process_nodes()
                ),
            )
            for node in compiled.nodes.values()
        )

    rows = [{}, {}, {}]
    assert _fingerprint(rows) == _fingerprint(list(reversed(rows)))


# ---------------------------------------------------------------------------
# What compilation cannot see, submission still catches
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'flag', ['log', 'enable_links', 'write_invocations', 'write_configs']
)
def test_bookkeeping_flags_are_arbitrated_at_submission(flag, tmp_path):
    """
    These are arguments to ``submit_jobs``, so no compilation can know them.
    A duplicate request returns before the flag is applied, which would make
    the second call's choice silently vanish.
    """
    row = {'predict.model': 'm'}
    # Every flag stated, so flipping one is unambiguously a disagreement
    # rather than a difference from an unstated default.
    kwargs = {
        'log': True,
        'enable_links': False,
        'write_invocations': False,
        'write_configs': False,
    }
    compiled = _solo_pipeline().compile_configurations(
        [row], root_dpath=tmp_path, cache=False
    )
    summary = compiled.submit_jobs(
        queue={'backend': 'serial', 'name': 'flags'}, **kwargs
    )
    queue = summary['queue']
    again = _solo_pipeline().compile_configurations(
        [row], root_dpath=tmp_path, cache=False
    )
    with pytest.raises(ValueError, match=flag):
        again.submit_jobs(queue=queue, **{**kwargs, flag: not kwargs[flag]})


def test_separately_compiled_graphs_sharing_a_queue_are_arbitrated(tmp_path):
    """
    The gap the defensive check exists for. Two compilations each see one
    request, so neither can compare them; the queue is what they have in
    common.
    """
    kwargs = {
        'enable_links': False,
        'write_invocations': False,
        'write_configs': False,
    }
    first = _solo_pipeline().compile_configurations(
        [{'predict.model': 'm', 'predict.workers': 4}],
        root_dpath=tmp_path,
        cache=False,
    )
    queue = first.submit_jobs(
        queue={'backend': 'serial', 'name': 'shared'}, **kwargs
    )['queue']

    second = _solo_pipeline().compile_configurations(
        [{'predict.model': 'm', 'predict.workers': 16}],
        root_dpath=tmp_path,
        cache=False,
    )
    with pytest.raises(ValueError, match='perf_params'):
        second.submit_jobs(queue=queue, **kwargs)


def test_the_backstop_never_fires_on_one_compiled_graph(tmp_path):
    """
    Within a compiled graph a ``process_id`` is a key, so it appears once.
    Submitting a large matrix therefore exercises the compiler's arbitration
    and none of the runtime's -- which is what "defensive" means here.
    """
    rows = [{'predict.model': f'm{idx}'} for idx in range(8)]
    compiled = _solo_pipeline().compile_configurations(
        rows, root_dpath=tmp_path, cache=False
    )
    assert len(compiled.nodes) == 8
    summary = compiled.submit_jobs(
        queue={'backend': 'serial', 'name': 'matrix'},
        enable_links=False,
        write_invocations=False,
        write_configs=False,
    )
    registry = summary['queue'].__kwdagger_requests__
    assert registry['submissions'] == 1
    assert len(registry['by_process_id']) == 8
    assert set(summary['node_status'].values()) == {'new_submission'}
