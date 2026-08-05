"""
A compiled pipeline keeps describing what was requested.

It is handed back to callers, inspected, and submitted more than once, so
submission must not edit it. Three things used to, or could:

* ``skip_existing`` wrote its per-call decision back as ``node.enabled``;
* the lookup views were cached, so the mapping a caller mutated survived while
  ``proc_graph`` -- the thing submission walks -- knew nothing about it;
* cloning deep-copied a node's neighbours along with it.

The last is a cost rather than a correctness problem, but it has the same
cause: reaching outside the one node the operation is about.
"""

from __future__ import annotations

import time

import pytest
import ubelt as ub

from kwdagger.pipeline import Pipeline, ProcessNode

SUBMIT_KW = {
    'enable_links': False,
    'write_invocations': False,
    'write_configs': False,
}


def _chain_pipeline():
    producer = ProcessNode(
        name='producer',
        executable='python producer.py',
        out_paths={'data_fpath': 'data.json'},
    )
    consumer = ProcessNode(
        name='consumer',
        executable='python consumer.py',
        in_paths={'data_fpath'},
        out_paths={'result_fpath': 'result.json'},
    )
    producer.outputs['data_fpath'].connect(consumer.inputs['data_fpath'])
    return Pipeline([producer, consumer])


def _materialize(node):
    """Write a node's outputs so ``does_exist`` is true."""
    for fpath in node.final_out_paths.values():
        fpath = ub.Path(fpath)
        fpath.parent.ensuredir()
        fpath.write_text('{}')


# ---------------------------------------------------------------------------
# skip_existing is a per-submission decision, not an edit
# ---------------------------------------------------------------------------


def test_skip_existing_does_not_disable_the_configured_node(tmp_path):
    compiled = _chain_pipeline().compile_configurations(
        [{}], root_dpath=tmp_path, cache=False
    )
    (producer,) = compiled.nodes_by_name['producer']
    (consumer,) = compiled.nodes_by_name['consumer']
    _materialize(producer)
    _materialize(consumer)

    summary = compiled.submit_jobs(
        queue={'backend': 'serial', 'name': 'skip'},
        skip_existing=True,
        **SUBMIT_KW,
    )
    assert summary['node_status'][producer.process_id] == 'skipped'
    # The request is unchanged: both nodes are still enabled, because the user
    # asked for them. Only this submission declined to queue them.
    assert producer.enabled is True
    assert consumer.enabled is True


def test_a_later_submission_is_not_shaped_by_an_earlier_skip(tmp_path):
    """
    The consequence that bites. Submitting with ``skip_existing=True`` and
    then again without it must give a fresh request, not a node the first call
    quietly turned off.
    """
    compiled = _chain_pipeline().compile_configurations(
        [{}], root_dpath=tmp_path, cache=False
    )
    (producer,) = compiled.nodes_by_name['producer']
    (consumer,) = compiled.nodes_by_name['consumer']
    _materialize(producer)
    _materialize(consumer)

    compiled.submit_jobs(
        queue={'backend': 'serial', 'name': 'first'},
        skip_existing=True,
        **SUBMIT_KW,
    )
    second = compiled.submit_jobs(
        queue={'backend': 'serial', 'name': 'second'},
        skip_existing=False,
        **SUBMIT_KW,
    )
    assert second['node_status'][producer.process_id] == 'new_submission'
    assert second['node_status'][consumer.process_id] == 'new_submission'
    assert producer.enabled is True


def test_resubmitting_to_one_queue_is_not_a_false_conflict(tmp_path):
    """
    Arbitration compares the request. When the first submission edited the
    node, the second compared the mutated node against the first snapshot and
    reported an ``__enabled__`` conflict the user never created.
    """
    compiled = _chain_pipeline().compile_configurations(
        [{}], root_dpath=tmp_path, cache=False
    )
    (producer,) = compiled.nodes_by_name['producer']
    _materialize(producer)
    _materialize(compiled.nodes_by_name['consumer'][0])

    first = compiled.submit_jobs(
        queue={'backend': 'serial', 'name': 'shared'},
        skip_existing=True,
        **SUBMIT_KW,
    )
    # Same queue, same request. A duplicate, not a disagreement.
    again = compiled.submit_jobs(
        queue=first['queue'], skip_existing=True, **SUBMIT_KW
    )
    assert set(again['node_status'].values()) == {'skipped'}


def test_a_genuinely_disabled_node_is_still_reported_as_disabled(tmp_path):
    """The complement: ``__enabled__: False`` is a request, and still holds."""
    compiled = _chain_pipeline().compile_configurations(
        [{'producer.__enabled__': False}], root_dpath=tmp_path, cache=False
    )
    (producer,) = compiled.nodes_by_name['producer']
    (consumer,) = compiled.nodes_by_name['consumer']
    status = compiled.submit_jobs(
        queue={'backend': 'serial', 'name': 'disabled'}, **SUBMIT_KW
    )['node_status']
    assert status[producer.process_id] == 'disabled'
    assert status[consumer.process_id] == 'skipped'
    assert producer.enabled is False


def test_a_skipped_producer_is_not_a_queue_dependency(tmp_path):
    """
    A producer whose output already exists is not in the queue, so nothing may
    wait on it -- even though it is still an enabled part of the request.
    """
    compiled = _chain_pipeline().compile_configurations(
        [{}], root_dpath=tmp_path, cache=False
    )
    (producer,) = compiled.nodes_by_name['producer']
    (consumer,) = compiled.nodes_by_name['consumer']
    _materialize(producer)

    summary = compiled.submit_jobs(
        queue={'backend': 'serial', 'name': 'dep'},
        skip_existing=True,
        **SUBMIT_KW,
    )
    assert summary['node_status'][producer.process_id] == 'skipped'
    assert summary['node_status'][consumer.process_id] == 'new_submission'
    job = summary['queue'].named_jobs[consumer.process_id]
    assert [getattr(d, 'name', d) for d in (job.depends or [])] == []
    assert producer.enabled is True


# ---------------------------------------------------------------------------
# The lookup views are derived, not cached
# ---------------------------------------------------------------------------


def test_mutating_a_returned_lookup_does_not_stick(tmp_path):
    compiled = _chain_pipeline().compile_configurations(
        [{}], root_dpath=tmp_path, cache=False
    )
    assert len(compiled.nodes) == 2
    compiled.nodes.pop(next(iter(compiled.nodes)))
    compiled.nodes_by_name['producer'].clear()
    assert len(compiled.nodes) == 2
    assert len(compiled.nodes_by_name['producer']) == 1


def test_the_lookups_follow_the_graph(tmp_path):
    """Not merely unaffected by an edit -- they track the graph afterwards."""
    compiled = _chain_pipeline().compile_configurations(
        [{}], root_dpath=tmp_path, cache=False
    )
    (producer,) = compiled.nodes_by_name['producer']
    assert producer.process_id in compiled.nodes
    compiled.proc_graph.remove_node(producer.process_id)
    assert producer.process_id not in compiled.nodes
    assert 'producer' not in compiled.nodes_by_name


# ---------------------------------------------------------------------------
# Cloning stays inside the node being cloned
# ---------------------------------------------------------------------------


def _linear_chain(n_nodes):
    nodes = []
    previous = None
    for idx in range(n_nodes):
        node = ProcessNode(
            name=f'step{idx}',
            executable=f'python step{idx}.py',
            in_paths={'src'},
            out_paths={'dst': 'out.json'},
            algo_params={'p': 0},
        )
        if previous is not None:
            previous.outputs['dst'].connect(node.inputs['src'])
        nodes.append(node)
        previous = node
    return Pipeline(nodes)


def test_cloning_does_not_copy_the_rest_of_the_pipeline(monkeypatch):
    """
    The direct statement. Copying one node used to materialize every node
    reachable from it, so a wired node cost the whole connected component.
    """
    import copy

    from kwdagger.pipeline._compile import _clone_unconnected_process_node

    dag = _linear_chain(4)
    target = dag.node_dict['step2']
    # Warm the memoization cache the way ordinary use does. It holds computed
    # results, and the predecessor query among them is a list of *other*
    # nodes -- the last route out after the port links are detached.
    dag.configure({'step0.src': '/data/a'}, cache=False)
    assert target.effective_predecessor_process_nodes()

    def _holds_a_node(value, depth=0):
        if isinstance(value, ProcessNode):
            return True
        if depth > 3:
            return False
        if isinstance(value, dict):
            value = value.values()
        if isinstance(value, (list, tuple, set)) or hasattr(value, '__iter__'):
            try:
                return any(_holds_a_node(item, depth + 1) for item in value)
            except TypeError:
                return False
        return False

    assert _holds_a_node(target._configured_cache), (
        'setup: the cache must actually hold other nodes'
    )

    original = copy.deepcopy
    materialized = []

    def _tracking_deepcopy(obj, *args, **kwargs):
        result = original(obj, *args, **kwargs)
        if isinstance(obj, ProcessNode):
            materialized.append(obj.name)
        return result

    monkeypatch.setattr(copy, 'deepcopy', _tracking_deepcopy)
    clone = _clone_unconnected_process_node(target)
    assert materialized == ['step2'], (
        f'cloning step2 reached {sorted(set(materialized))}'
    )
    assert clone.name == 'step2'


def test_cloning_leaves_the_template_connected():
    """Detaching is temporary; the template must survive it untouched."""
    from kwdagger.pipeline._compile import _clone_unconnected_process_node

    dag = _linear_chain(3)
    target = dag.node_dict['step1']
    before = [port.parent.name for port in target.inputs['src'].pred]
    _clone_unconnected_process_node(target)
    after = [port.parent.name for port in target.inputs['src'].pred]
    assert before == after == ['step0']
    assert [p.name for p in target.effective_predecessor_process_nodes()] == [
        'step0'
    ]


def test_the_clone_is_disconnected():
    from kwdagger.pipeline._compile import _clone_unconnected_process_node

    dag = _linear_chain(3)
    clone = _clone_unconnected_process_node(dag.node_dict['step1'])
    assert clone.inputs['src'].pred == []
    assert clone.outputs['dst'].succ == []
    assert clone._pred_nodes_without_io_connection == []
    assert clone._configured_cache == {}
    assert clone.inputs['src'].parent is clone
    assert clone.outputs['dst'].parent is clone


def test_a_node_holding_something_uncopyable_does_not_break_its_neighbours():
    """
    The failure mode the traversal created. A helper that cannot be deep-copied
    -- a lock, an open file, a client -- attached to one node used to break
    compilation of every node connected to it.
    """
    import threading

    from kwdagger.pipeline._compile import _clone_unconnected_process_node

    dag = _linear_chain(3)
    dag.node_dict['step0'].client = threading.Lock()

    clone = _clone_unconnected_process_node(dag.node_dict['step2'])
    assert clone.name == 'step2'
    with pytest.raises(TypeError):
        # Its own uncopyable attribute is still its own problem, which is the
        # correct scope for the failure.
        _clone_unconnected_process_node(dag.node_dict['step0'])


@pytest.mark.parametrize('n_nodes', [4, 32])
def test_compilation_cost_does_not_grow_with_the_pipeline(n_nodes, tmp_path):
    """
    A guard rather than a benchmark. Per-clone cost was proportional to the
    pipeline, so compiling a matrix was quadratic in it; this asserts the
    shape, with a threshold loose enough not to be a flake on a busy machine.
    """
    rows = [{'step0.src': '/data/a', 'step0.p': idx} for idx in range(4)]
    dag = _linear_chain(n_nodes)
    start = time.perf_counter()
    compiled = dag.compile_configurations(
        rows, root_dpath=tmp_path, cache=False
    )
    elapsed = time.perf_counter() - start
    clones = n_nodes * len(rows)
    assert len(compiled.nodes) == clones
    per_clone_ms = elapsed / clones * 1000
    assert per_clone_ms < 5.0, (
        f'{per_clone_ms:.2f} ms per clone at {n_nodes} nodes; cloning is '
        'reaching outside the node again'
    )


# ---------------------------------------------------------------------------
# Sharing a queue between calls that disagree about skip_existing
# ---------------------------------------------------------------------------


def _partially_existing(tmp_path):
    """A compiled chain where only the producer's output exists."""
    compiled = _chain_pipeline().compile_configurations(
        [{}], root_dpath=tmp_path, cache=False
    )
    (producer,) = compiled.nodes_by_name['producer']
    (consumer,) = compiled.nodes_by_name['consumer']
    _materialize(producer)
    return compiled, producer, consumer


@pytest.mark.parametrize('flags', [(True, False), (False, True)], ids=str)
def test_one_queue_two_skip_existing_answers_is_refused(flags, tmp_path):
    """
    The job is created once, by whichever call comes first, and keeps that
    call's dependencies. So a queue shared between a skipping call and a
    rerunning one used to end up with a different shape depending on their
    order -- and in one order the consumer had no dependency on a producer the
    queue was about to rerun, so it could read the stale output.

    Rejected in both orders now, which is the same answer either way.
    """
    compiled, _producer, _consumer = _partially_existing(tmp_path)
    first, second = flags
    queue = compiled.submit_jobs(
        queue={'backend': 'serial', 'name': 'mixed'},
        skip_existing=first,
        **SUBMIT_KW,
    )['queue']
    with pytest.raises(ValueError, match='queued prerequisites'):
        compiled.submit_jobs(queue=queue, skip_existing=second, **SUBMIT_KW)


@pytest.mark.parametrize('skip', [True, False])
def test_one_queue_and_one_answer_is_an_ordinary_duplicate(skip, tmp_path):
    """
    The complement, and the reason this cannot simply reject a second
    submission: two calls that agree queue the same thing, and the second is a
    duplicate rather than a conflict.
    """
    compiled, _producer, consumer = _partially_existing(tmp_path)
    first = compiled.submit_jobs(
        queue={'backend': 'serial', 'name': 'agreeing'},
        skip_existing=skip,
        **SUBMIT_KW,
    )
    again = compiled.submit_jobs(
        queue=first['queue'], skip_existing=skip, **SUBMIT_KW
    )
    assert again['node_status'][consumer.process_id] == 'duplicate_submission'


def test_the_queued_dependencies_are_the_ones_arbitration_compared(tmp_path):
    """
    One evaluation, used twice. A job's dependencies and the set arbitration
    checks have to be the same list, or the check is guarding something other
    than what was queued.
    """
    compiled, producer, consumer = _partially_existing(tmp_path)
    summary = compiled.submit_jobs(
        queue={'backend': 'serial', 'name': 'queued'},
        skip_existing=True,
        **SUBMIT_KW,
    )
    job = summary['queue'].named_jobs[consumer.process_id]
    assert [getattr(d, 'name', d) for d in (job.depends or [])] == []
    registry = summary['queue'].__kwdagger_requests__['by_process_id']
    assert (
        registry[consumer.process_id]['snapshot']['queued_prerequisites'] == []
    )
    # The computation still requires it; only the queue does not contain it.
    assert registry[consumer.process_id]['snapshot']['prerequisites'] == [
        producer.process_id
    ]
