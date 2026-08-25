"""
The identity invariant: ``process_id`` describes computation, not lineage.

    A process is identified by the computation it will perform -- its effective
    algorithm configuration, its effective resolved input values, and anything
    else that changes its command or outputs. It is *not* identified by how
    those input values were obtained.

So a produced path and the same path supplied by hand hash identically. They
still differ in scheduling and in provenance, and this file pins all three
answers together, because the temptation when reading any one of them alone is
to "repair" the other two into it.

This is value identity, not content identity: kwdagger does not read the bytes
at a path. See ``docs/source/manual/technical/hashing_scheme.rst``.
"""

from __future__ import annotations

import json

import pytest
import ubelt as ub

from kwdagger.pipeline import GatherSpec, Pipeline, ProcessNode


def _consumer(name='consumer'):
    return ProcessNode(
        name=name,
        executable='python consumer.py',
        in_paths={'data_fpath'},
        out_paths={'result_fpath': 'result.json'},
        algo_params={'thresh': 0.5},
    )


def _producer(algo='x', out_name='data.json', node_dpath=None):
    kwargs = {} if node_dpath is None else {'node_dpath': node_dpath}
    return ProcessNode(
        name='producer',
        executable='python producer.py',
        out_paths={'data_fpath': out_name},
        algo_params={'algo': algo},
        **kwargs,
    )


def _configured(dag, config, tmp_path):
    dag.configure(config, root_dpath=tmp_path, cache=False)
    return dag


# ---------------------------------------------------------------------------
# Produced versus manual
# ---------------------------------------------------------------------------


def test_produced_and_manual_inputs_with_one_value_share_an_identity(tmp_path):
    """
    The core of the invariant. The same path, delivered two ways, is one
    computation -- so one result directory and one job.
    """
    producer = _producer()
    wired = _consumer()
    producer.outputs['data_fpath'].connect(wired.inputs['data_fpath'])
    _configured(Pipeline([producer, wired]), {}, tmp_path)
    produced_path = str(wired.final_in_paths['data_fpath'])

    manual = _consumer()
    _configured(
        Pipeline([manual]),
        {'consumer.data_fpath': produced_path},
        tmp_path,
    )

    assert {k: str(v) for k, v in manual.final_input_config.items()} == {
        k: str(v) for k, v in wired.final_input_config.items()
    }
    assert manual.command == wired.command
    assert manual.process_id == wired.process_id
    assert manual.final_node_dpath == wired.final_node_dpath

    # ... and they are still told apart everywhere it matters.
    assert [n.name for n in wired.effective_predecessor_process_nodes()] == [
        'producer'
    ]
    assert manual.effective_predecessor_process_nodes() == []
    wired_source = wired._depends_config()['__input__.data_fpath']
    assert wired_source['source_kind'] == 'output'
    assert '__input__.data_fpath' not in manual._depends_config()


def test_two_producers_exposing_one_path_give_one_consumer_identity(tmp_path):
    """
    Producer identities may differ; what reaches the consumer is the value.
    Here the producers write to a fixed location, so both consumers read the
    same path and are the same computation.
    """
    ids = {}
    for algo in ['x', 'y']:
        # node_dpath='.' puts both producers' outputs at one fixed path.
        producer = _producer(algo=algo, node_dpath='.')
        consumer = _consumer()
        producer.outputs['data_fpath'].connect(consumer.inputs['data_fpath'])
        dag = Pipeline([producer, consumer])
        _configured(dag, {'producer.algo': algo}, tmp_path)
        ids[algo] = {
            'producer': producer.process_id,
            'consumer': consumer.process_id,
            'command': consumer.command,
            'input': str(consumer.final_in_paths['data_fpath']),
        }
    assert ids['x']['input'] == ids['y']['input'], 'setup: one shared path'
    assert ids['x']['producer'] != ids['y']['producer']
    assert ids['x']['consumer'] == ids['y']['consumer']
    assert ids['x']['command'] == ids['y']['command']


def test_different_effective_paths_give_different_identities(tmp_path):
    ids = []
    for value in ['/data/one.json', '/data/two.json']:
        consumer = _consumer()
        _configured(
            Pipeline([consumer]),
            {'consumer.data_fpath': value},
            tmp_path,
        )
        ids.append(consumer.process_id)
    assert ids[0] != ids[1]


def test_a_producer_change_that_moves_its_output_moves_the_consumer(tmp_path):
    """
    The invariant is not "producers never matter". A producer whose output
    path carries its own identity changes the consumer's effective input when
    it is reconfigured, and the consumer follows -- through the value, not
    through a lineage record.
    """
    seen = {}
    for algo in ['x', 'y']:
        producer = _producer(algo=algo)
        consumer = _consumer()
        producer.outputs['data_fpath'].connect(consumer.inputs['data_fpath'])
        dag = Pipeline([producer, consumer])
        _configured(dag, {'producer.algo': algo}, tmp_path)
        seen[algo] = (
            producer.process_id,
            consumer.process_id,
            str(consumer.final_in_paths['data_fpath']),
        )
    assert seen['x'][2] != seen['y'][2], 'setup: the path moved'
    assert seen['x'][1] != seen['y'][1]
    # The reason is the value, not a separately injected producer id.
    assert seen['x'][0] in seen['x'][2]
    # The hashed form is root-relative, so the cache location is not identity.
    hashed = _consumer().depends.get('__inputs__', {})
    assert all(
        not str(v).startswith('/') or '{root}' not in str(v)
        for v in hashed.values()
    )
    assert not any(key.startswith('__input__') for key in _consumer().depends)


# ---------------------------------------------------------------------------
# Delivery mechanism is irrelevant to identity
# ---------------------------------------------------------------------------


def _by_delivery(tmp_path, mechanism, value):
    """Build a consumer that receives ``value`` by one of four routes."""
    nodes = {}
    consumer = _consumer()
    nodes['consumer'] = consumer
    config = {}
    if mechanism in {'manual', 'manual_alias'}:
        lender = ProcessNode(
            name='lender',
            executable='python lender.py',
            in_paths={'data_fpath'},
            out_paths={'lender_fpath': 'lender.json'},
        )
        if mechanism == 'manual':
            config['consumer.data_fpath'] = value
        else:
            nodes['lender'] = lender
            lender.inputs['data_fpath'].connect(consumer.inputs['data_fpath'])
            config['lender.data_fpath'] = value
    else:
        producer = _producer(out_name=value, node_dpath='.')
        nodes['producer'] = producer
        if mechanism == 'produced':
            producer.outputs['data_fpath'].connect(
                consumer.inputs['data_fpath']
            )
        else:
            lender = ProcessNode(
                name='lender',
                executable='python lender.py',
                in_paths={'data_fpath'},
                out_paths={'lender_fpath': 'lender.json'},
            )
            nodes['lender'] = lender
            producer.outputs['data_fpath'].connect(lender.inputs['data_fpath'])
            lender.inputs['data_fpath'].connect(consumer.inputs['data_fpath'])
    _configured(Pipeline(list(nodes.values())), config, tmp_path)
    return consumer


@pytest.mark.parametrize(
    'mechanism',
    ['manual', 'manual_alias', 'produced', 'produced_alias'],
)
def test_identity_is_the_same_however_the_value_arrives(mechanism, tmp_path):
    """
    Four delivery routes, one value, one identity. Scheduling and provenance
    are allowed to differ -- and do.
    """
    value = str(tmp_path / 'fixed' / 'data.json')
    baseline = _by_delivery(tmp_path, 'manual', value)
    other = _by_delivery(tmp_path, mechanism, value)
    assert str(other.final_in_paths['data_fpath']) == value
    assert other.process_id == baseline.process_id
    assert other.command == baseline.command


# ---------------------------------------------------------------------------
# Overridden connections
# ---------------------------------------------------------------------------


def test_an_override_removes_the_producer_from_identity_and_scheduling(
    tmp_path,
):
    ids = {}
    for algo in ['x', 'y']:
        producer = _producer(algo=algo)
        consumer = _consumer()
        producer.outputs['data_fpath'].connect(consumer.inputs['data_fpath'])
        dag = Pipeline([producer, consumer])
        _configured(
            dag,
            {'consumer.data_fpath': '/precomputed/data', 'producer.algo': algo},
            tmp_path,
        )
        ids[algo] = consumer.process_id
        last = (dag, consumer)

    # Changing only the unused producer does not move the consumer.
    assert ids['x'] == ids['y']

    dag, consumer = last
    assert consumer.effective_predecessor_process_nodes() == []
    # The structural wiring is still visible in the template graph ...
    assert dag.proc_graph.has_edge('producer', 'consumer')
    # ... and provenance says it did not supply the value.
    binding = consumer._depends_config()['__input__.data_fpath']
    assert binding['supplied'] is False
    assert consumer._depends_config()['consumer.data_fpath'] == (
        '/precomputed/data'
    )


# ---------------------------------------------------------------------------
# Canonicalization
# ---------------------------------------------------------------------------


def _dedup_pipeline():
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
    producer = _producer()
    consumer = _consumer()
    producer.outputs['data_fpath'].connect(consumer.inputs['data_fpath'])
    return Pipeline([shard, merge, producer, consumer])


def _dedup_rows():
    base = {
        'shard.dataset': 'a',
        'shard.fold': 0,
        'merge.dataset': 'a',
        'consumer.data_fpath': '/precomputed/data',
    }
    return [dict(base, **{'producer.algo': algo}) for algo in ['x', 'y']]


def _canonical_state(rows, label):
    root = ub.Path.appdir('kwdagger/tests/identity/dedup').ensuredir()
    compiled = _dedup_pipeline().compile_configurations(
        rows, root_dpath=root, cache=False
    )
    consumer = [n for n in compiled.nodes.values() if n.name == 'consumer'][0]
    status = compiled.submit_jobs(
        queue={'backend': 'serial'},
        enable_links=False,
        write_invocations=False,
        write_configs=False,
    )['node_status']
    return {
        'process_id': consumer.process_id,
        'command': consumer.command,
        'node_dpath': str(consumer.final_node_dpath),
        'out_paths': {k: str(v) for k, v in consumer.final_out_paths.items()},
        'edges': sorted(
            compiled.nodes[p].name
            for p in compiled.proc_graph.predecessors(consumer.process_id)
        ),
        'status': status[consumer.process_id],
        'depends_config': consumer._depends_config(),
    }


def test_reversing_matrix_rows_changes_nothing_about_the_canonical_node():
    """
    Rows that collapse to one consumer must produce the same everything --
    including the *complete* requested record, not just one binding of it.
    """
    forward = _canonical_state(_dedup_rows(), 'forward')
    reverse = _canonical_state(list(reversed(_dedup_rows())), 'reverse')
    assert forward == reverse


def test_a_node_that_changes_its_own_identity_is_an_internal_error(
    monkeypatch, tmp_path
):
    """
    An internal invariant, and the shape of one: a contradiction *within* one
    request rather than a difference between two. A concrete node is filed
    under the identity it reported; if it then reports a different one, the
    graph key no longer names what it holds and that is a kwdagger defect.

    Two legitimate requests differing is the user's business and belongs to
    ``duplicate_policy``. Do not conflate the two -- dressing a policy
    rejection as an internal error is what made the old rules look
    unremovable.
    """
    import itertools

    from kwdagger.pipeline import _compile

    counter = itertools.count()
    original = _compile.ProcessNode.process_id

    def drifting(self):
        value = original.__get__(self, type(self))
        # Only after the node has been filed, and only for one template, so
        # the rest of compilation behaves normally.
        if self.name == 'consumer':
            return f'{value}_{next(counter)}'
        return value

    monkeypatch.setattr(_compile.ProcessNode, 'process_id', property(drifting))
    with pytest.raises(AssertionError, match='Internal consistency error'):
        _dedup_pipeline().compile_configurations(
            _dedup_rows(), root_dpath=tmp_path, cache=False
        )


def test_a_path_template_may_not_name_another_nodes_id(tmp_path):
    """
    Ancestor-id placeholders are gone: they let a node this one does not read
    decide where its results land, which contradicts the identity invariant.
    The error has to explain that rather than surface as a bare KeyError.
    """
    with pytest.raises(KeyError) as excinfo:
        ProcessNode(
            name='consumer',
            executable='python consumer.py',
            in_paths={'data_fpath'},
            out_paths={'result_fpath': 'result.json'},
            node_dpath='{producer_id}/{consumer_id}',
        )
    message = str(excinfo.value)
    assert 'producer_id' in message
    assert 'own ids' in message


# ---------------------------------------------------------------------------
# Identity must not depend on where the cache lives
# ---------------------------------------------------------------------------


def _root_probe_pipeline():
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
    producer = _producer()
    consumer = _consumer()
    producer.outputs['data_fpath'].connect(consumer.inputs['data_fpath'])
    return Pipeline([shard, merge, producer, consumer])


ROOT_PROBE_ROWS = [
    {'shard.dataset': 'a', 'shard.fold': 0, 'merge.dataset': 'a'}
]


def test_moving_the_cache_root_does_not_change_any_identity():
    """
    A produced path contains the root the pipeline runs under. Hashing that
    verbatim would make every downstream id change when the cache moves --
    which is not a different computation, and which the gather contract in
    ``AGENTS.md`` forbids outright. Values inside the root hash relative to it.

    Covers ordinary produced inputs *and* gather membership, which are hashed
    through separate code paths.
    """
    ids = {}
    edges = {}
    for location in ['root-a', 'root-b']:
        root = (
            ub.Path.appdir(f'kwdagger/tests/identity/{location}')
            .delete()
            .ensuredir()
        )
        compiled = _root_probe_pipeline().compile_configurations(
            ROOT_PROBE_ROWS, root_dpath=root, cache=False
        )
        ids[location] = {n.name: n.process_id for n in compiled.nodes.values()}
        edges[location] = sorted(
            (compiled.nodes[a].name, compiled.nodes[b].name)
            for a, b in compiled.proc_graph.edges()
        )
    assert ids['root-a'] == ids['root-b']
    assert edges['root-a'] == edges['root-b']
    # An external input is *not* rewritten, so it still identifies the data.
    consumer = _consumer()
    Pipeline([consumer]).configure(
        {'consumer.data_fpath': '/outside/the/root.json'},
        root_dpath=ub.Path.appdir('kwdagger/tests/identity/root-a'),
        cache=False,
    )
    assert consumer.depends['__inputs__']['data_fpath'] == (
        '/outside/the/root.json'
    )


# ---------------------------------------------------------------------------
# Canonicalization of rows that share an identity
# ---------------------------------------------------------------------------


def _mixed_delivery_pipeline():
    """A gather elsewhere, as this was written before every matrix compiled."""
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
    producer = _producer(node_dpath='.')
    consumer = _consumer()
    producer.outputs['data_fpath'].connect(consumer.inputs['data_fpath'])
    return Pipeline([shard, merge, producer, consumer])


def test_one_produced_row_and_one_manual_row_are_one_computation():
    """
    The point of taking delivery out of identity. The same configured path is
    the same effective data -- that is the governing convention -- so a row
    that takes the value from a producer and a row that types it in are one
    process. The first row wins, including whether the producer is a
    prerequisite.

    Each order picks its own first row. That is first-request-wins, not a
    defect: kwdagger runs the grid it was given, in the order it was given.
    """
    base = {'shard.dataset': 'a', 'shard.fold': 0, 'merge.dataset': 'a'}
    root = ub.Path.appdir('kwdagger/tests/identity/mixed').delete().ensuredir()
    probe = _mixed_delivery_pipeline().compile_configurations(
        [dict(base)], root_dpath=root, cache=False
    )
    consumer = [n for n in probe.nodes.values() if n.name == 'consumer'][0]
    produced_path = str(consumer.final_in_paths['data_fpath'])

    produced_row = dict(base)
    manual_row = dict(base, **{'consumer.data_fpath': produced_path})
    seen = {}
    for label, rows in [
        ('produced_first', [produced_row, manual_row]),
        ('manual_first', [manual_row, produced_row]),
    ]:
        compiled = _mixed_delivery_pipeline().compile_configurations(
            rows, root_dpath=root, cache=False
        )
        (node,) = compiled.nodes_by_name['consumer']
        seen[label] = sorted(
            n.name for n in node.effective_predecessor_process_nodes()
        )
    # One consumer either way, and the same identity -- only the delivery the
    # winning row described differs.
    assert seen['produced_first'] == ['producer']
    assert seen['manual_first'] == []


def test_perf_params_may_differ_between_rows(tmp_path):
    """
    ``perf_params`` reach the command but not identity -- deliberately. So two
    rows sweeping only a perf value are one process, and the first supplies
    the command. Sweeping a perf param is a way of saying "I do not care which
    of these runs"; if you do care, it belongs in ``algo_params``.
    """
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
    predict = ProcessNode(
        name='predict',
        executable='python predict.py',
        out_paths={'out_fpath': 'out.json'},
        algo_params={'model': 'm'},
        perf_params={'workers': 4},
    )
    dag = Pipeline([shard, merge, predict])
    base = {'shard.dataset': 'a', 'shard.fold': 0, 'merge.dataset': 'a'}
    rows = [
        dict(base, **{'predict.workers': workers, 'predict.model': 'm'})
        for workers in [4, 16]
    ]
    compiled = dag.compile_configurations(
        rows, root_dpath=tmp_path, cache=False
    )
    (node,) = compiled.nodes_by_name['predict']
    assert '--workers=4' in node.command, 'the first row supplies the command'

    # Reversed, the other row wins. Order selects the representative.
    compiled = dag.compile_configurations(
        list(reversed(rows)), root_dpath=tmp_path, cache=False
    )
    (node,) = compiled.nodes_by_name['predict']
    assert '--workers=16' in node.command

    # Opt in, and the same rows are reported instead.
    with pytest.raises(ValueError, match='perf_params'):
        dag.compile_configurations(
            rows, root_dpath=tmp_path, cache=False, duplicate_policy='error'
        )


# ---------------------------------------------------------------------------
# First-request-wins reaches a pipeline with no gather in it
# ---------------------------------------------------------------------------
#
# The duplicate rules used to live only in the compiler, which only ran when a
# pipeline gathered. Every pipeline compiles now, so the policy reaches all of
# them -- and the policy is that the first row wins. ``test_duplicate_policy``
# covers each difference class under each policy; these keep a gather-free
# pipeline in the picture here, where the identity model is being described.


def _perf_pipeline():
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


@pytest.mark.parametrize('order', [[4, 16], [16, 4]])
def test_gather_free_perf_difference_takes_the_first_row(order, tmp_path):
    """Both rows are one process with two commands. The first one supplies it."""
    rows = [{'predict.workers': w, 'predict.model': 'm'} for w in order]
    compiled = _perf_pipeline().compile_configurations(
        rows, root_dpath=tmp_path, cache=False
    )
    (node,) = compiled.nodes_by_name['predict']
    assert f'--workers={order[0]}' in node.command


def test_gather_free_agreeing_rows_still_deduplicate(tmp_path):
    """The complement: identical requests collapse to one job."""
    rows = [{'predict.workers': 8, 'predict.model': 'm'}] * 2
    compiled = _perf_pipeline().compile_configurations(
        rows, root_dpath=tmp_path, cache=False
    )
    assert len(compiled.nodes_by_name['predict']) == 1
    summary = compiled.submit_jobs(
        queue={'backend': 'serial', 'name': 'dedup'},
        enable_links=False,
        write_invocations=False,
        write_configs=False,
    )
    assert '--workers=8' in summary['queue'].finalize_text()


def _delivery_pipeline():
    producer = _producer(node_dpath='.')
    consumer = _consumer()
    producer.outputs['data_fpath'].connect(consumer.inputs['data_fpath'])
    return Pipeline([producer, consumer])


# ---------------------------------------------------------------------------
# Root canonicalization covers structured values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    'shape',
    ['scalar', 'list', 'mapping', 'nested'],
)
def test_structured_input_values_hash_relative_to_the_cache_root(shape):
    """
    A scalar-only rewrite would leave the cache root in the hash for exactly
    the structured configs that are hardest to notice.
    """
    ids = {}
    for location in ['struct-a', 'struct-b']:
        root = (
            ub.Path.appdir(f'kwdagger/tests/identity/{location}')
            .delete()
            .ensuredir()
        )
        producer = _producer()
        consumer = _consumer()
        dag = Pipeline([producer, consumer])
        dag.configure({}, root_dpath=root, cache=False)
        produced = str(producer.outputs['data_fpath'].final_value)
        value = {
            'scalar': produced,
            'list': [produced, produced],
            'mapping': {'files': produced},
            'nested': {'groups': [{'files': [produced]}, 'plain']},
        }[shape]
        dag.configure(
            {'consumer.data_fpath': value}, root_dpath=root, cache=False
        )
        ids[location] = consumer.process_id
        hashed = consumer.depends['__inputs__']['data_fpath']
    assert ids['struct-a'] == ids['struct-b']
    assert str(root) not in str(hashed)


def test_a_path_merely_sharing_the_roots_prefix_is_left_alone(tmp_path):
    """Containment is by path component, not by string prefix."""
    root = tmp_path / 'cache'
    root.mkdir()
    outsider = str(tmp_path / 'cache-backup' / 'data.json')
    consumer = _consumer()
    Pipeline([consumer]).configure(
        {'consumer.data_fpath': outsider}, root_dpath=root, cache=False
    )
    assert consumer.depends['__inputs__']['data_fpath'] == outsider


# ---------------------------------------------------------------------------
# Delivery conflicts the prerequisite union cannot see
# ---------------------------------------------------------------------------


def _two_output_pipeline():
    """
    One producer feeding two of a consumer's inputs. Overriding just one of
    them leaves the producer a prerequisite either way, so the union of
    predecessors is blind to the difference.
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


def _delivery_rows(root):
    dag = _two_output_pipeline()
    dag.configure(config={}, root_dpath=root, cache=False)
    produced_a = str(dag.node_dict['consumer'].final_in_paths['in_a_fpath'])
    return {}, {'consumer.in_a_fpath': produced_a}


def test_a_delivery_difference_takes_the_first_row(tmp_path):
    """
    Both rows need the producer -- ``in_b_fpath`` still comes from it -- and
    they disagree only about where ``in_a_fpath`` came from. One consumer,
    described by the first row.
    """
    produced_row, manual_row = _delivery_rows(tmp_path)

    def _record(rows):
        compiled = _two_output_pipeline().compile_configurations(
            rows, root_dpath=tmp_path, cache=False
        )
        (consumer,) = compiled.nodes_by_name['consumer']
        return json.dumps(consumer._depends_config(), sort_keys=True)

    alone = {
        'produced': _record([produced_row]),
        'manual': _record([manual_row]),
    }
    assert alone['produced'] != alone['manual'], 'setup: the records differ'
    # Whichever row is first supplies the record, exactly as if it were the
    # only row.
    assert _record([produced_row, manual_row]) == alone['produced']
    assert _record([manual_row, produced_row]) == alone['manual']


def test_a_delivery_difference_can_be_reported_on_request(tmp_path):
    """The same rows under the opt-in ``error`` policy."""
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

    def build():
        dag = _two_output_pipeline()
        nodes = dict(dag.node_dict)
        nodes.update({'shard': shard, 'merge': merge})
        return Pipeline(list(nodes.values()))

    base = {'shard.dataset': 'a', 'shard.fold': 0, 'merge.dataset': 'a'}
    probe = build().compile_configurations(
        [dict(base)], root_dpath=tmp_path, cache=False
    )
    consumer = [n for n in probe.nodes.values() if n.name == 'consumer'][0]
    produced_a = str(consumer.final_in_paths['in_a_fpath'])
    rows = [
        dict(base),
        dict(base, **{'consumer.in_a_fpath': produced_a}),
    ]
    for ordering in (rows, list(reversed(rows))):
        build().compile_configurations(
            ordering, root_dpath=tmp_path, cache=False
        )
        with pytest.raises(ValueError, match='requested experiment'):
            build().compile_configurations(
                ordering,
                root_dpath=tmp_path,
                cache=False,
                duplicate_policy='error',
            )


def test_differing_delivery_still_hashes_the_same(tmp_path):
    """
    These are the *same computation*, which is why they deduplicate at all.
    Delivery affects the record and the scheduling edge, never the hash -- if
    this ever starts failing, lineage has crept back into identity.
    """
    produced_row, manual_row = _delivery_rows(tmp_path)
    ids = []
    for row in (produced_row, manual_row):
        dag = _two_output_pipeline()
        dag.configure(config=row, root_dpath=tmp_path, cache=False)
        ids.append(dag.node_dict['consumer'].process_id)
    assert ids[0] == ids[1]


def test_invoke_sh_lineage_comments_follow_effective_ancestry(tmp_path):
    """
    ``invoke.sh`` is meant to be independently inspectable, so its ``See Also``
    lineage must not point at a producer the command never read -- and must not
    depend on which row supplied the canonical node.
    """
    producer = _producer()
    consumer = _consumer()
    producer.outputs['data_fpath'].connect(consumer.inputs['data_fpath'])
    dag = Pipeline([producer, consumer])
    dag.configure(
        {'consumer.data_fpath': '/precomputed/data'},
        root_dpath=tmp_path,
        cache=False,
    )
    script = consumer._invocation_script_text()
    assert str(producer.final_node_dpath) not in script

    # The complement: a producer that *is* read stays listed.
    dag.configure({}, root_dpath=tmp_path, cache=False)
    assert str(producer.final_node_dpath) in consumer._invocation_script_text()


def test_mapping_keys_are_root_relative_too():
    """A produced path can be a dict key, not only a value."""
    ids = {}
    for location in ['key-a', 'key-b']:
        root = (
            ub.Path.appdir(f'kwdagger/tests/identity/{location}')
            .delete()
            .ensuredir()
        )
        producer = _producer()
        consumer = _consumer()
        dag = Pipeline([producer, consumer])
        dag.configure({}, root_dpath=root, cache=False)
        produced = str(producer.outputs['data_fpath'].final_value)
        dag.configure(
            {'consumer.data_fpath': {produced: {'weight': 1}}},
            root_dpath=root,
            cache=False,
        )
        ids[location] = consumer.process_id
        hashed = consumer.depends['__inputs__']['data_fpath']
    assert ids['key-a'] == ids['key-b']
    assert all('{root}' in key for key in hashed)


# ---------------------------------------------------------------------------
# Provenance conflicts no summary of delivery can see
# ---------------------------------------------------------------------------
#
# Arbitration exists because one result directory holds one requested-experiment
# record. Comparing a summary of where values came from -- producer origins,
# prerequisite sets -- can only catch the conflicts that summary happens to
# represent. These are the ones it does not: the requests agree on identity, on
# prerequisites, and on producer origins, and still ask for different things.


def _two_alias_pipeline():
    """
    Two peers forwarding into one consumer port. Neither is a producer, so
    there are no origins and no prerequisites to disagree about -- but which
    peer supplied the value is part of what was requested.
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
    consumer = _consumer()
    left.inputs['data_fpath'].connect(consumer.inputs['data_fpath'])
    right.inputs['data_fpath'].connect(consumer.inputs['data_fpath'])
    return Pipeline([left, right, consumer])


_ALIAS_ROWS = [
    {'left.data_fpath': '/same/path', 'consumer.thresh': 0.5},
    {'right.data_fpath': '/same/path', 'consumer.thresh': 0.5},
]


def test_which_alias_supplied_the_value_is_a_requested_difference(tmp_path):
    """
    The premise, stated separately so a failure says which half broke: the two
    rows are one computation, and they ask for different things.
    """
    ids = []
    records = []
    for row in _ALIAS_ROWS:
        dag = _two_alias_pipeline()
        dag.configure(config=row, root_dpath=tmp_path, cache=False)
        consumer = dag.node_dict['consumer']
        assert str(consumer.final_in_paths['data_fpath']) == '/same/path'
        ids.append(consumer.process_id)
        records.append(json.dumps(consumer._depends_config(), sort_keys=True))
    assert ids[0] == ids[1], 'delivery must stay out of the hash'
    assert records[0] != records[1], 'the persisted records genuinely differ'


@pytest.mark.parametrize('order', [[0, 1], [1, 0]])
def test_the_first_alias_row_becomes_the_canonical_request(order, tmp_path):
    """
    One job, and the record describes the row that won. Which peer supplied
    an equal value is provenance; it does not make two computations.
    """
    rows = [_ALIAS_ROWS[idx] for idx in order]

    def _record(subset):
        compiled = _two_alias_pipeline().compile_configurations(
            subset, root_dpath=tmp_path, cache=False
        )
        (consumer,) = compiled.nodes_by_name['consumer']
        return json.dumps(consumer._depends_config(), sort_keys=True)

    assert _record([rows[0]]) != _record([rows[1]]), 'setup: records differ'
    assert _record(rows) == _record([rows[0]])


@pytest.mark.parametrize('order', [[0, 1], [1, 0]])
def test_an_alias_provenance_difference_can_be_reported_on_request(
    order, tmp_path
):
    """The same rows under the opt-in ``error`` policy."""
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

    def build():
        nodes = dict(_two_alias_pipeline().node_dict)
        nodes.update({'shard': shard, 'merge': merge})
        return Pipeline(list(nodes.values()))

    base = {'shard.dataset': 'a', 'shard.fold': 0, 'merge.dataset': 'a'}
    rows = [dict(base, **_ALIAS_ROWS[idx]) for idx in order]
    build().compile_configurations(rows, root_dpath=tmp_path, cache=False)
    with pytest.raises(ValueError, match='requested experiment'):
        build().compile_configurations(
            rows,
            root_dpath=tmp_path,
            cache=False,
            duplicate_policy='error',
        )


def test_agreeing_alias_rows_still_deduplicate(tmp_path):
    """
    The complement, and the reason this cannot simply reject a duplicate: two
    identical requests share a record and must collapse to one job.
    """
    rows = [dict(_ALIAS_ROWS[0]), dict(_ALIAS_ROWS[0])]
    compiled = _two_alias_pipeline().compile_configurations(
        rows, root_dpath=tmp_path, cache=False
    )
    assert len(compiled.nodes_by_name['consumer']) == 1


def _param_alias_pipeline():
    """The same shape one layer over, on a shared algorithm parameter."""
    left = ProcessNode(
        name='left',
        executable='python left.py',
        out_paths={'left_fpath': 'left.json'},
        algo_params={'thresh'},
    )
    right = ProcessNode(
        name='right',
        executable='python right.py',
        out_paths={'right_fpath': 'right.json'},
        algo_params={'thresh'},
    )
    consumer = _consumer()
    left.param_ports['thresh'].connect(consumer.param_ports['thresh'])
    right.param_ports['thresh'].connect(consumer.param_ports['thresh'])
    return Pipeline([left, right, consumer])


#: The same value, supplied by one peer or the other.
_PARAM_ALIAS_ROWS = [
    {'left.thresh': 0.5, 'consumer.data_fpath': '/data/a'},
    {'right.thresh': 0.5, 'consumer.data_fpath': '/data/a'},
]


@pytest.mark.parametrize('order', [[0, 1], [1, 0]])
def test_which_parameter_port_supplied_the_value_is_provenance(order, tmp_path):
    """
    A wired algorithm parameter behaves like an aliased input: which peer
    supplied an equal value is recorded, and does not make two computations.
    The first row supplies the record.
    """
    rows = [_PARAM_ALIAS_ROWS[idx] for idx in order]

    def _record(subset):
        compiled = _param_alias_pipeline().compile_configurations(
            subset, root_dpath=tmp_path, cache=False
        )
        (consumer,) = compiled.nodes_by_name['consumer']
        return json.dumps(consumer._depends_config(), sort_keys=True)

    assert _record([rows[0]]) != _record([rows[1]]), 'setup: records differ'
    assert _record(rows) == _record([rows[0]])


# ---------------------------------------------------------------------------
# Canonicalization must not merge distinct mapping entries
# ---------------------------------------------------------------------------


def test_colliding_canonical_mapping_keys_are_refused(tmp_path):
    """
    Rewriting a key is many-to-one. Rebuilding the dictionary would drop an
    entry, leaving a two-entry mapping whose hashed payload is identical to a
    genuinely one-entry mapping while the commands still differ. That is one
    configuration meaning two things -- a contradiction within a single
    request, so an internal invariant rather than a duplicate-request policy.
    """
    consumer = _consumer()
    dag = Pipeline([consumer])
    inside = str(tmp_path / 'x.json')
    aliased = str(tmp_path / 'sub' / '..' / 'x.json')
    with pytest.raises(ValueError, match='canonicalize'):
        dag.configure(
            {'consumer.data_fpath': {inside: 1, aliased: 2}},
            root_dpath=tmp_path,
            cache=False,
        )


def test_distinct_mapping_keys_are_still_hashed_together(tmp_path):
    """The complement: only genuine collisions are refused."""
    consumer = _consumer()
    dag = Pipeline([consumer])
    mapping = {
        str(tmp_path / 'x.json'): 1,
        str(tmp_path / 'y.json'): 2,
        'plain': 3,
    }
    dag.configure(
        {'consumer.data_fpath': mapping}, root_dpath=tmp_path, cache=False
    )
    hashed = consumer.depends['__inputs__']['data_fpath']
    assert sorted(hashed) == ['plain', '{root}/x.json', '{root}/y.json']


def test_a_pathlike_mapping_key_canonicalizes_like_a_string_one(tmp_path):
    """
    A produced value arrives as a ``Path`` and a hand-written one as a
    ``str``. Keys were exempt from that equivalence, so the same mapping kept
    the absolute cache root in the hash depending on how it was spelled.
    """
    ids = []
    for key in [str(tmp_path / 'x.json'), ub.Path(tmp_path / 'x.json')]:
        consumer = _consumer()
        dag = Pipeline([consumer])
        dag.configure(
            {'consumer.data_fpath': {key: 1}},
            root_dpath=tmp_path,
            cache=False,
        )
        hashed = consumer.depends['__inputs__']['data_fpath']
        assert list(hashed) == ['{root}/x.json']
        ids.append(consumer.process_id)
    assert ids[0] == ids[1]


# ---------------------------------------------------------------------------
# Pipeline-level Slurm layers reach the resolved request
# ---------------------------------------------------------------------------
#
# This section used to be about *arbitrating* Slurm options and bookkeeping
# flags across submissions. It is not kwdagger's business which of two calls
# to a shared queue asked for what, so what remains is the part that is: the
# pipeline base and row-global layers must actually reach the request each
# node is submitted with, and a row that omits them must reset to the base
# rather than inherit the previous row's.


@pytest.mark.parametrize('order', [['gpu:1', 'gpu:4'], ['gpu:4', 'gpu:1']])
def test_a_row_global_slurm_difference_takes_the_first_row(order, tmp_path):
    """One process, and the first row supplies its resources."""
    rows = [
        {'predict.model': 'm', '__slurm_options__': {'gres': gres}}
        for gres in order
    ]
    compiled = _perf_pipeline().compile_configurations(
        rows, root_dpath=tmp_path, cache=False
    )
    (node,) = compiled.nodes_by_name['predict']
    assert node.effective_slurm_options['gres'] == order[0]


def test_a_row_global_and_a_node_level_request_resolve_together(tmp_path):
    """
    Asking for the same thing at either level is one request, so the rows
    deduplicate on identity as they always would.
    """
    rows = [
        {'predict.model': 'm', '__slurm_options__': {'gres': 'gpu:2'}},
        {'predict.model': 'm', 'predict.__slurm_options__': {'gres': 'gpu:2'}},
    ]
    compiled = _perf_pipeline().compile_configurations(
        rows, root_dpath=tmp_path, cache=False
    )
    (node,) = compiled.nodes_by_name['predict']
    assert node.effective_slurm_options['gres'] == 'gpu:2'


@pytest.mark.parametrize('order', [['gpu:1', None], [None, 'gpu:1']])
def test_omitting_row_global_slurm_options_does_not_inherit_them(
    order, tmp_path
):
    """
    The stale-row-state property, which is about *resolution* rather than
    arbitration and still holds. A row that omits ``__slurm_options__`` asks
    for the pipeline default, not for whatever the previous row asked.
    """
    dag = _perf_pipeline()
    dag._base_slurm_options = {'partition': 'base'}
    rows = []
    for idx, gres in enumerate(order):
        row = {'predict.model': f'm{idx}'}
        if gres is not None:
            row['__slurm_options__'] = {'gres': gres}
        rows.append(row)
    compiled = dag.compile_configurations(
        rows, root_dpath=tmp_path, cache=False
    )
    by_model = {
        node.final_algo_config['model']: node
        for node in compiled.nodes_by_name['predict']
    }
    for idx, gres in enumerate(order):
        resolved = by_model[f'm{idx}'].effective_slurm_options
        assert resolved['partition'] == 'base'
        assert resolved.get('gres') == gres


def test_a_pipeline_wide_default_applies_to_every_row(tmp_path):
    """A persistent default is how the CLI's --slurm_options reach each row."""
    dag = _perf_pipeline()
    dag._base_slurm_options = {'gres': 'gpu:2'}
    compiled = dag.compile_configurations(
        [{'predict.model': 'a'}, {'predict.model': 'b'}],
        root_dpath=tmp_path,
        cache=False,
    )
    for node in compiled.nodes_by_name['predict']:
        assert node.effective_slurm_options['gres'] == 'gpu:2'


# ---------------------------------------------------------------------------
# One mapping-key policy, shared by identity and by what is persisted
# ---------------------------------------------------------------------------
#
# JSON object names are strings. Leaving that conversion to ``json.dumps``
# makes the identity payload and the file on disk disagree about what the keys
# are: a ``Path`` key is refused outright, an ``int`` key is renamed silently,
# and a mixture cannot be sorted. Keys are normalized where the value is
# stored instead, so all four readers see the same mapping.


def _mapping_key_dag():
    consumer = _consumer()
    return Pipeline([consumer]), consumer


def test_a_pathlike_mapping_key_survives_submission(tmp_path):
    """
    Identity alone was not enough to catch this: configuring and hashing
    worked, and writing the requested record raised ``TypeError`` on the key.
    """
    dag, consumer = _mapping_key_dag()
    dag.configure(
        {'consumer.data_fpath': {ub.Path(tmp_path / 'x.json'): 1}},
        root_dpath=tmp_path,
        cache=False,
    )
    # Normalized once, where the value is stored, so every later reader agrees.
    assert list(consumer.config['data_fpath']) == [str(tmp_path / 'x.json')]
    assert list(consumer.depends['__inputs__']['data_fpath']) == [
        '{root}/x.json'
    ]
    record = consumer.requested_provenance_record()['consumer.data_fpath']
    assert json.loads(record) == {str(tmp_path / 'x.json'): 1}

    summary = dag.submit_jobs(
        queue={'backend': 'serial'},
        enable_links=False,
        write_invocations=False,
        write_configs=True,
    )
    assert 'job_config.json' in summary['queue'].finalize_text()


def test_a_path_and_its_string_are_one_key(tmp_path):
    """
    Normalization is many-to-one, so the two spellings collide. Rebuilding the
    mapping would drop an entry and give two configurations one identity.
    """
    dag, _consumer_node = _mapping_key_dag()
    with pytest.raises(ValueError, match='collision after normalization'):
        dag.configure(
            {'consumer.data_fpath': {ub.Path('/a'): 1, '/a': 2}},
            root_dpath=tmp_path,
            cache=False,
        )


@pytest.mark.parametrize('key', [1, None, ('a', 'b'), 2.5])
def test_a_key_that_is_not_a_string_or_path_is_refused(key, tmp_path):
    """
    The configuration-domain invariant: after coercion every mapping key is a
    string. Anything else is a Python-only shape that ``json.dumps`` would
    rename on the way to disk -- or refuse -- so the configuration read back
    would not be the one that was written.
    """
    dag, _consumer_node = _mapping_key_dag()
    with pytest.raises(TypeError, match='must be a string or a path'):
        dag.configure(
            {'consumer.data_fpath': {key: 1}},
            root_dpath=tmp_path,
            cache=False,
        )


def test_normalizing_a_path_does_not_resolve_it(tmp_path):
    """
    ``os.fspath`` converts spelling, not meaning. A relative path stays
    relative until the path-resolution stage deliberately interprets it.
    """
    dag, consumer = _mapping_key_dag()
    dag.configure(
        {'consumer.data_fpath': {ub.Path('rel/x.json'): ub.Path('rel/y')}},
        root_dpath=tmp_path,
        cache=False,
    )
    assert consumer.config['data_fpath'] == {'rel/x.json': 'rel/y'}


class _BytesPath:
    """A ``PathLike`` whose ``__fspath__`` returns bytes, which is legal."""

    def __fspath__(self):
        return b'/tmp/x.json'


@pytest.mark.parametrize('shape', ['value', 'key', 'nested'])
def test_a_bytes_path_is_refused_at_the_boundary(shape, tmp_path):
    """
    ``os.fspath`` may return ``bytes``. Accepting that would leave a
    Python-only shape past the boundary that claims to have removed them: it
    is not a JSON object name, it is not JSON-serializable as a value, and
    kwdagger has no business guessing an encoding for what ends up in the hash
    and on the command line.
    """
    values = {
        'value': _BytesPath(),
        'key': {_BytesPath(): 1},
        'nested': {'files': [_BytesPath()]},
    }
    dag, _consumer_node = _mapping_key_dag()
    with pytest.raises(TypeError, match='bytes path'):
        dag.configure(
            {'consumer.data_fpath': values[shape]},
            root_dpath=tmp_path,
            cache=False,
        )


def test_a_declared_default_crosses_the_boundary_too(tmp_path):
    """
    A declared default reaches identity and job_config.json by the same route
    a row override does, so it is normalized at the same place.
    """
    node = ProcessNode(
        name='consumer',
        executable='python consumer.py',
        in_paths={'data_fpath': ub.Path('rel/default.json')},
        out_paths={'result_fpath': 'result.json'},
        algo_params={'weights': {ub.Path('rel/w.json'): 1}},
    )
    dag = Pipeline([node])
    dag.configure({}, root_dpath=tmp_path, cache=False)
    assert node.in_paths['data_fpath'] == 'rel/default.json'
    assert node.algo_params['weights'] == {'rel/w.json': 1}
    # ... so it reaches identity as a string, not as a Path. A default is
    # deliberately absent from the requested record -- it was not requested --
    # but it is very much part of what this node computes.
    assert node.final_algo_config['weights'] == {'rel/w.json': 1}
    assert node.depends['__inputs__']['data_fpath'] == 'rel/default.json'
    json.dumps(node._depends_config())


def test_the_requested_record_and_the_written_file_agree(tmp_path):
    """
    Arbitration used to serialize with ``default=str`` while the writer had no
    fallback, so a leaked value could pass arbitration and fail only when
    ``job_config.json`` was written. Both now refuse the same things.
    """
    dag, consumer = _mapping_key_dag()
    dag.configure(
        {'consumer.data_fpath': {'a': [ub.Path(tmp_path / 'x')]}},
        root_dpath=tmp_path,
        cache=False,
    )
    record = consumer.requested_provenance_record()
    written = json.dumps(consumer._depends_config())
    for key, value in record.items():
        assert json.loads(value) == json.loads(written)[key]
