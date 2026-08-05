"""
Duplicate requests: first wins, and that is normal.

Two matrix rows that compile to one ``process_id`` are one job. Whatever they
disagree about is, by construction, something the user excluded from identity
-- ``perf_params``, Slurm options, ``__enabled__``, output overrides, how a
value was delivered. Kwdagger runs the grid and records what it ran; it does
not protect a user from a collision they chose by deciding which fields reach
the hash.

So the default is **first-request-wins**, and it is not a compatibility mode:
matrix order selects the representative, the representative is what runs, and
``job_config.json`` describes it. ``warn`` and ``error`` are opt-in diagnostics
for someone who wants to be told, and they change nothing about execution
except that ``error`` stops it.

These tests are deliberately written per row order, asserting that *each order
picks its own first row*. Order-dependent representative selection is the
design, not a defect to be smoothed over.
"""

from __future__ import annotations

import pytest

from kwdagger.pipeline import Pipeline, ProcessNode

# ---------------------------------------------------------------------------
# Pipelines, and pairs of rows that share an identity while differing
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


def _alias_pipeline():
    """Two peers forwarding the same value into one consumer port."""
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


def _perf_rows():
    return [
        {'predict.model': 'm', 'predict.workers': 4},
        {'predict.model': 'm', 'predict.workers': 16},
    ]


def _slurm_rows():
    return [
        {'predict.model': 'm', '__slurm_options__': {'gres': 'gpu:1'}},
        {'predict.model': 'm', '__slurm_options__': {'gres': 'gpu:2'}},
    ]


def _enabled_rows():
    return [
        {'predict.model': 'm', 'predict.__enabled__': True},
        {'predict.model': 'm', 'predict.__enabled__': False},
    ]


def _out_path_rows():
    return [
        {'predict.model': 'm'},
        {'predict.model': 'm', 'predict.out_fpath': '/elsewhere/out.json'},
    ]


def _alias_rows():
    return [
        {'left.data_fpath': '/same/path'},
        {'right.data_fpath': '/same/path'},
    ]


#: ``(id, pipeline factory, rows, the field a diagnostic should name)``. Every
#: pair shares one ``process_id`` and differs in something identity excludes.
DIFFERENCE_CASES = [
    ('perf_params', _solo_pipeline, _perf_rows, 'perf'),
    ('slurm_options', _solo_pipeline, _slurm_rows, 'slurm'),
    ('enabled', _solo_pipeline, _enabled_rows, 'enabled'),
    ('out_paths', _solo_pipeline, _out_path_rows, 'out_paths'),
    ('provenance', _alias_pipeline, _alias_rows, 'requested'),
]


def _cases():
    return [
        pytest.param(factory, rows, field, id=f'{name}-{order}')
        for name, factory, rows, field in DIFFERENCE_CASES
        for order in ('forward', 'reversed')
    ]


def _ordered(rows, order):
    return rows if order == 'forward' else list(reversed(rows))


def _compile(factory, rows, tmp_path, **kwargs):
    return factory().compile_configurations(
        rows, root_dpath=tmp_path, cache=False, **kwargs
    )


# ---------------------------------------------------------------------------
# The default: first wins, in whichever order the rows arrived
# ---------------------------------------------------------------------------


@pytest.mark.parametrize('order', ['forward', 'reversed'])
@pytest.mark.parametrize(
    'name,factory,rows_fn,field',
    DIFFERENCE_CASES,
    ids=lambda v: getattr(v, '__name__', v),
)
def test_differences_outside_identity_compile(
    name, factory, rows_fn, field, order, tmp_path
):
    """No rejection. One process, and the grid runs."""
    rows = _ordered(rows_fn(), order)
    compiled = _compile(factory, rows, tmp_path)
    assert len(compiled.nodes) >= 1


@pytest.mark.parametrize('order', ['forward', 'reversed'])
def test_the_first_row_supplies_the_perf_parameters(order, tmp_path):
    rows = _ordered(_perf_rows(), order)
    expected = rows[0]['predict.workers']
    compiled = _compile(_solo_pipeline, rows, tmp_path)
    (node,) = compiled.nodes_by_name['predict']
    assert node.final_perf_config['workers'] == expected
    assert f'--workers={expected}' in node.command


@pytest.mark.parametrize('order', ['forward', 'reversed'])
def test_the_first_row_supplies_the_slurm_request(order, tmp_path):
    rows = _ordered(_slurm_rows(), order)
    expected = rows[0]['__slurm_options__']['gres']
    compiled = _compile(_solo_pipeline, rows, tmp_path)
    (node,) = compiled.nodes_by_name['predict']
    assert node.effective_slurm_options['gres'] == expected


@pytest.mark.parametrize('order', ['forward', 'reversed'])
def test_the_first_row_supplies_the_enabled_state(order, tmp_path):
    rows = _ordered(_enabled_rows(), order)
    expected = rows[0]['predict.__enabled__']
    compiled = _compile(_solo_pipeline, rows, tmp_path)
    (node,) = compiled.nodes_by_name['predict']
    assert node.enabled is expected


@pytest.mark.parametrize('order', ['forward', 'reversed'])
def test_the_first_row_supplies_the_recorded_request(order, tmp_path):
    """``job_config.json`` describes the representative that actually ran."""
    rows = _ordered(_perf_rows(), order)
    expected = rows[0]['predict.workers']
    compiled = _compile(_solo_pipeline, rows, tmp_path)
    (node,) = compiled.nodes_by_name['predict']
    assert node._depends_config()['predict.workers'] == expected


def test_a_produced_and_a_manual_equal_path_are_one_computation(tmp_path):
    """
    The governing convention: the same configured path is the same effective
    data. How the value arrived is provenance, not identity, so these two rows
    are one job and neither is an error.
    """
    probe = _compile(_chain_pipeline, [{}], tmp_path)
    (consumer,) = probe.nodes_by_name['consumer']
    produced = str(consumer.final_in_paths['data_fpath'])

    rows = [{}, {'consumer.data_fpath': produced}]
    for ordering in (rows, list(reversed(rows))):
        compiled = _compile(_chain_pipeline, ordering, tmp_path)
        assert len(compiled.nodes_by_name['consumer']) == 1


@pytest.mark.parametrize('order', ['forward', 'reversed'])
def test_the_first_row_supplies_the_scheduling_edge(order, tmp_path):
    """
    First-wins reaches the execution graph, not just the recorded request.

    Produced and manual equal paths are one identity, but they are not one
    *schedule*: an explicitly configured value outranks the producer, so the
    manual row's consumer has no effective predecessor. Whichever row is
    first decides whether the producer must finish before the consumer runs.

    This is the design -- the representative is kept whole -- and it is
    asserted per order rather than smoothed into order independence.
    """
    probe = _compile(_chain_pipeline, [{}], tmp_path)
    (consumer,) = probe.nodes_by_name['consumer']
    produced = str(consumer.final_in_paths['data_fpath'])

    produced_row: dict = {}
    manual_row = {'consumer.data_fpath': produced}
    rows = _ordered([produced_row, manual_row], order)

    compiled = _compile(_chain_pipeline, rows, tmp_path)
    (consumer,) = compiled.nodes_by_name['consumer']
    (producer,) = compiled.nodes_by_name['producer']
    preds = list(compiled.proc_graph.pred[consumer.process_id])

    if rows[0] is produced_row:
        assert preds == [producer.process_id]
    else:
        assert preds == []
        # ... and the retained request says so too: the manual row put the
        # path in its own config rather than reading it from the producer.
        assert consumer._depends_config()['consumer.data_fpath'] == produced


def test_identical_rows_still_collapse(tmp_path):
    row = {'predict.model': 'm', 'predict.workers': 8}
    compiled = _compile(_solo_pipeline, [dict(row), dict(row)], tmp_path)
    assert len(compiled.nodes_by_name['predict']) == 1


def test_rows_that_differ_in_identity_are_still_two_processes(tmp_path):
    rows = [{'predict.model': 'a'}, {'predict.model': 'b'}]
    compiled = _compile(_solo_pipeline, rows, tmp_path)
    assert len(compiled.nodes_by_name['predict']) == 2


# ---------------------------------------------------------------------------
# warn: same execution, plus a diagnostic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize('order', ['forward', 'reversed'])
@pytest.mark.parametrize(
    'name,factory,rows_fn,field',
    DIFFERENCE_CASES,
    ids=lambda v: getattr(v, '__name__', v),
)
def test_warn_reports_the_difference_and_runs_anyway(
    name, factory, rows_fn, field, order, tmp_path
):
    rows = _ordered(rows_fn(), order)
    with pytest.warns(UserWarning) as caught:
        compiled = _compile(factory, rows, tmp_path, duplicate_policy='warn')
    text = '\n'.join(str(w.message) for w in caught)
    assert field in text, text
    assert compiled.nodes


@pytest.mark.parametrize('order', ['forward', 'reversed'])
def test_warn_changes_nothing_about_what_runs(order, tmp_path):
    rows = _ordered(_perf_rows(), order)
    default = _compile(_solo_pipeline, rows, tmp_path)
    with pytest.warns(UserWarning):
        warned = _compile(
            _solo_pipeline, rows, tmp_path, duplicate_policy='warn'
        )
    assert sorted(default.nodes) == sorted(warned.nodes)
    (lhs,) = default.nodes_by_name['predict']
    (rhs,) = warned.nodes_by_name['predict']
    assert lhs.command == rhs.command
    assert lhs.final_perf_config == rhs.final_perf_config


def test_warn_is_silent_when_duplicates_agree(tmp_path, recwarn):
    row = {'predict.model': 'm', 'predict.workers': 8}
    _compile(
        _solo_pipeline,
        [dict(row), dict(row)],
        tmp_path,
        duplicate_policy='warn',
    )
    assert [w for w in recwarn if issubclass(w.category, UserWarning)] == []


# ---------------------------------------------------------------------------
# error: opt-in rejection, before anything is submitted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize('order', ['forward', 'reversed'])
@pytest.mark.parametrize(
    'name,factory,rows_fn,field',
    DIFFERENCE_CASES,
    ids=lambda v: getattr(v, '__name__', v),
)
def test_error_rejects_the_difference(
    name, factory, rows_fn, field, order, tmp_path
):
    rows = _ordered(rows_fn(), order)
    with pytest.raises(ValueError) as excinfo:
        _compile(factory, rows, tmp_path, duplicate_policy='error')
    message = str(excinfo.value)
    assert field in message, message
    # The identity the two requests share has to be nameable.
    assert 'process' in message.lower()


def test_error_accepts_duplicates_that_agree(tmp_path):
    row = {'predict.model': 'm', 'predict.workers': 8}
    compiled = _compile(
        _solo_pipeline,
        [dict(row), dict(row)],
        tmp_path,
        duplicate_policy='error',
    )
    assert len(compiled.nodes_by_name['predict']) == 1


def test_error_raises_before_anything_is_queued(tmp_path):
    """It is a compile-time constraint; no queue and no filesystem work."""
    root = tmp_path / 'runs'
    with pytest.raises(ValueError):
        _solo_pipeline().compile_configurations(
            _perf_rows(), root_dpath=root, cache=False, duplicate_policy='error'
        )
    assert not root.exists() or not any(root.iterdir())


# ---------------------------------------------------------------------------
# The policy argument itself
# ---------------------------------------------------------------------------


def test_the_default_is_first(tmp_path):
    """Stated as a test so it cannot drift into something stricter."""
    compiled = _compile(_solo_pipeline, _perf_rows(), tmp_path)
    (node,) = compiled.nodes_by_name['predict']
    assert node.final_perf_config['workers'] == 4


def test_an_unknown_policy_is_refused(tmp_path):
    with pytest.raises(ValueError, match='duplicate_policy'):
        _compile(
            _solo_pipeline, _perf_rows(), tmp_path, duplicate_policy='strict'
        )


# ---------------------------------------------------------------------------
# Internal invariants are not policy
# ---------------------------------------------------------------------------
#
# A difference between two legitimate equal-identity requests is policy. A
# contradiction within one request, or in the compiled graph, is a kwdagger
# defect. These stay unconditional under every policy.


def test_a_node_changing_its_own_identity_is_refused_under_every_policy(
    monkeypatch, tmp_path
):
    import itertools

    from kwdagger.pipeline import _compile

    counter = itertools.count()
    original = _compile.ProcessNode.process_id

    def drifting(self):
        return f'{original.__get__(self, type(self))}_{next(counter)}'

    monkeypatch.setattr(_compile.ProcessNode, 'process_id', property(drifting))
    for policy in ('first', 'warn', 'error'):
        with pytest.raises(AssertionError, match='Internal consistency error'):
            _solo_pipeline().compile_configurations(
                [{'predict.model': 'm'}],
                root_dpath=tmp_path,
                cache=False,
                duplicate_policy=policy,
            )


def test_configuration_that_cannot_be_normalized_is_still_refused(tmp_path):
    """Not a difference between requests -- a request that cannot be read."""
    with pytest.raises(TypeError):
        _compile(_solo_pipeline, [{b'predict.model': 'm'}], tmp_path)


# ---------------------------------------------------------------------------
# The default costs nothing
# ---------------------------------------------------------------------------


def test_first_builds_no_comparison(monkeypatch, tmp_path):
    """
    Not a timing assertion: the comparison is simply never called. Under the
    default, canonicalization is a dict lookup, and the provenance and command
    reads a comparison would do never happen.
    """
    from kwdagger.pipeline import _compile

    calls = []
    original = _compile.compare_duplicate_requests

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(_compile, 'compare_duplicate_requests', counted)

    rows = _perf_rows() * 4
    _compile_rows = _solo_pipeline().compile_configurations
    _compile_rows(rows, root_dpath=tmp_path, cache=False)
    assert calls == [], 'the default policy must not compare anything'

    with pytest.warns(UserWarning):
        _solo_pipeline().compile_configurations(
            rows, root_dpath=tmp_path, cache=False, duplicate_policy='warn'
        )
    assert calls, 'warn does compare'
