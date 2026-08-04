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
    _configured(
        Pipeline({'producer': producer, 'consumer': wired}), {}, tmp_path
    )
    produced_path = str(wired.final_in_paths['data_fpath'])

    manual = _consumer()
    _configured(
        Pipeline({'consumer': manual}),
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
        dag = Pipeline({'producer': producer, 'consumer': consumer})
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
            Pipeline({'consumer': consumer}),
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
        dag = Pipeline({'producer': producer, 'consumer': consumer})
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
    _configured(Pipeline(nodes), config, tmp_path)
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
        dag = Pipeline({'producer': producer, 'consumer': consumer})
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
    return Pipeline(
        {
            'shard': shard,
            'merge': merge,
            'producer': producer,
            'consumer': consumer,
        }
    )


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


def test_equal_identity_implies_equal_finalized_state(monkeypatch, tmp_path):
    """
    The guard behind the invariant. If anything ever reaches the command or
    the output paths without reaching identity, compilation must say so rather
    than let whichever row compiled first decide what runs.
    """
    from kwdagger.pipeline import _compile

    calls = {'n': 0}
    real = _compile.ProcessNode.final_command

    def unstable(self):
        if self.name == 'consumer':
            calls['n'] += 1
            return f'{real(self)} # {calls["n"]}'
        return real(self)

    monkeypatch.setattr(_compile.ProcessNode, 'final_command', unstable)
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
    return Pipeline(
        {
            'shard': shard,
            'merge': merge,
            'producer': producer,
            'consumer': consumer,
        }
    )


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
    Pipeline({'consumer': consumer}).configure(
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
    """A gather elsewhere, so the full-matrix path is taken at all."""
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
    return Pipeline(
        {
            'shard': shard,
            'merge': merge,
            'producer': producer,
            'consumer': consumer,
        }
    )


def test_one_produced_row_and_one_manual_row_are_a_reported_conflict():
    """
    The cost of taking delivery out of identity: two rows can be the same
    computation and still need different jobs to run first. Nothing in the
    payload can distinguish them, so keeping whichever compiled first would
    put row order back in charge of whether the consumer waits for the
    producer -- and, if that producer were disabled, of whether it runs at all.

    Rejected in both orders, with the same message, rather than silently
    resolved.
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
    messages = []
    for rows in ([produced_row, manual_row], [manual_row, produced_row]):
        with pytest.raises(ValueError) as excinfo:
            _mixed_delivery_pipeline().compile_configurations(
                rows, root_dpath=root, cache=False
            )
        messages.append(str(excinfo.value))
    assert 'execution prerequisites' in messages[0]
    assert 'consumer' in messages[0]
    # Both orders report the same conflict, not two different outcomes.
    assert set(messages[0].split()) == set(messages[1].split())


def test_perf_params_may_differ_between_rows_only_by_agreeing(tmp_path):
    """
    ``perf_params`` reach the command but not identity -- deliberately. So two
    rows sweeping only a perf value are one process with two commands, which
    identity cannot arbitrate. That is a user-facing conflict in the same
    family as ``__enabled__`` and Slurm options, and must be reported as one
    rather than as an internal consistency failure.
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
    dag = Pipeline({'shard': shard, 'merge': merge, 'predict': predict})
    base = {'shard.dataset': 'a', 'shard.fold': 0, 'merge.dataset': 'a'}
    rows = [
        dict(base, **{'predict.workers': workers, 'predict.model': 'm'})
        for workers in [4, 16]
    ]
    with pytest.raises(ValueError, match='perf_params'):
        dag.compile_configurations(rows, root_dpath=tmp_path, cache=False)

    # Agreeing rows compile, and the perf value still reaches the command.
    agreeing = [dict(base, **{'predict.workers': 8, 'predict.model': 'm'})]
    compiled = dag.compile_configurations(
        agreeing, root_dpath=tmp_path, cache=False
    )
    node = [n for n in compiled.nodes.values() if n.name == 'predict'][0]
    assert '--workers=8' in node.command


# ---------------------------------------------------------------------------
# Arbitration must reach both scheduling paths
# ---------------------------------------------------------------------------
#
# A pipeline with any gather compiles the whole matrix up front; an ordinary
# one configures and submits a row at a time. The safeguards used to live only
# in the compiler, so a gather-free matrix was still first-row-wins. These
# tests deliberately use *no* gather.


def _submit_rows(dag, rows, root, backend='serial', per_row=None, **kwargs):
    """
    Configure and submit each row against one queue, as the scheduler does.

    ``per_row`` gives each submission its own ``submit_jobs`` keyword
    arguments, which is how a caller's bookkeeping choices differ between
    otherwise identical requests.
    """
    queue = None
    statuses = []
    for idx, row in enumerate(rows):
        dag.configure(config=row, root_dpath=root, cache=False)
        submit_kwargs = {
            'enable_links': False,
            'write_invocations': False,
            'write_configs': False,
            **kwargs,
            **(per_row[idx] if per_row else {}),
        }
        summary = dag.submit_jobs(
            queue=queue or {'backend': backend, 'name': 'identity-test'},
            **submit_kwargs,
        )
        queue = summary['queue']
        statuses.append(summary['node_status'])
    return queue, statuses


def _perf_pipeline():
    return Pipeline(
        {
            'predict': ProcessNode(
                name='predict',
                executable='python predict.py',
                out_paths={'out_fpath': 'out.json'},
                algo_params={'model': 'm'},
                perf_params={'workers': 4},
            )
        }
    )


@pytest.mark.parametrize('order', [[4, 16], [16, 4]])
def test_gather_free_perf_conflict_is_reported_in_either_order(order, tmp_path):
    """
    Both rows are one process with two commands. Whichever was submitted first
    used to supply the queued command and the other was silently recorded as a
    duplicate.
    """
    rows = [{'predict.workers': w, 'predict.model': 'm'} for w in order]
    with pytest.raises(ValueError, match='perf_params'):
        _submit_rows(_perf_pipeline(), rows, tmp_path)


def test_gather_free_agreeing_rows_still_deduplicate(tmp_path):
    """The complement: identical requests must still collapse to one job."""
    rows = [{'predict.workers': 8, 'predict.model': 'm'}] * 2
    queue, statuses = _submit_rows(_perf_pipeline(), rows, tmp_path)
    assert statuses[0]['predict'] == 'new_submission'
    assert statuses[1]['predict'] == 'duplicate_submission'
    assert '--workers=8' in queue.finalize_text()


def _delivery_pipeline():
    producer = _producer(node_dpath='.')
    consumer = _consumer()
    producer.outputs['data_fpath'].connect(consumer.inputs['data_fpath'])
    return Pipeline({'producer': producer, 'consumer': consumer})


def test_gather_free_mixed_delivery_is_reported_in_either_order(tmp_path):
    """
    One row takes the input from the producer, the other supplies the same
    path directly. Same identity, different prerequisites -- so the queue
    graph used to depend on which row came first.
    """
    dag = _delivery_pipeline()
    dag.configure(config={}, root_dpath=tmp_path, cache=False)
    consumer = dag.node_dict['consumer']
    produced = str(consumer.final_in_paths['data_fpath'])

    produced_row: dict = {}
    manual_row = {'consumer.data_fpath': produced}
    for rows in ([produced_row, manual_row], [manual_row, produced_row]):
        with pytest.raises(ValueError, match='execution prerequisites'):
            _submit_rows(_delivery_pipeline(), rows, tmp_path)


def test_gather_free_output_override_conflict_is_reported(tmp_path):
    """Output paths are excluded from identity but move the command."""
    rows = [
        {'predict.model': 'm', 'predict.out_fpath': name}
        for name in ['first.json', 'second.json']
    ]
    with pytest.raises(ValueError, match='out_paths'):
        _submit_rows(_perf_pipeline(), rows, tmp_path)


def test_gather_free_enabled_conflict_is_reported(tmp_path):
    rows = [
        {'predict.model': 'm', 'predict.__enabled__': flag}
        for flag in [True, False]
    ]
    with pytest.raises(ValueError, match='__enabled__'):
        _submit_rows(_perf_pipeline(), rows, tmp_path)


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
        dag = Pipeline({'producer': producer, 'consumer': consumer})
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
    Pipeline({'consumer': consumer}).configure(
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
    return Pipeline({'producer': producer, 'consumer': consumer})


def _delivery_rows(root):
    dag = _two_output_pipeline()
    dag.configure(config={}, root_dpath=root, cache=False)
    produced_a = str(dag.node_dict['consumer'].final_in_paths['in_a_fpath'])
    return {}, {'consumer.in_a_fpath': produced_a}


def test_delivery_conflict_is_caught_when_prerequisites_agree(tmp_path):
    """
    Both rows need the producer -- ``in_b_fpath`` still comes from it -- so
    the prerequisite sets match and the coarse check passes. They still
    disagree about where ``in_a_fpath`` came from, and only one requested
    record can be written for the directory they share.
    """
    produced_row, manual_row = _delivery_rows(tmp_path)
    for rows in ([produced_row, manual_row], [manual_row, produced_row]):
        with pytest.raises(ValueError, match='input delivery') as excinfo:
            _submit_rows(_two_output_pipeline(), rows, tmp_path)
        assert 'in_a_fpath' in str(excinfo.value)
        assert 'in_b_fpath' not in str(excinfo.value)


def test_delivery_conflict_is_caught_by_the_compiler_too(tmp_path):
    """The same conflict, on the full-matrix path."""
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
        return Pipeline(nodes)

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
        with pytest.raises(ValueError, match='input delivery'):
            build().compile_configurations(
                ordering, root_dpath=tmp_path, cache=False
            )


def test_differing_delivery_still_hashes_the_same(tmp_path):
    """
    The point of rejecting: these are the *same computation*. Rejection is
    about which requested record gets written, not about identity -- if this
    ever starts failing, lineage has crept back into the hash.
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
    dag = Pipeline({'producer': producer, 'consumer': consumer})
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
        dag = Pipeline({'producer': producer, 'consumer': consumer})
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
    return Pipeline({'left': left, 'right': right, 'consumer': consumer})


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
def test_alias_provenance_conflict_is_reported_in_either_order(order, tmp_path):
    """Neither row may be silently adopted as the canonical request."""
    rows = [_ALIAS_ROWS[idx] for idx in order]
    with pytest.raises(ValueError, match='requested experiment') as excinfo:
        _submit_rows(_two_alias_pipeline(), rows, tmp_path)
    assert '__input__.data_fpath' in str(excinfo.value)


@pytest.mark.parametrize('order', [[0, 1], [1, 0]])
def test_alias_provenance_conflict_reaches_the_compiler_too(order, tmp_path):
    """The same conflict on the full-matrix path, which a gather forces."""
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
        return Pipeline(nodes)

    base = {'shard.dataset': 'a', 'shard.fold': 0, 'merge.dataset': 'a'}
    rows = [dict(base, **_ALIAS_ROWS[idx]) for idx in order]
    with pytest.raises(ValueError, match='requested experiment'):
        build().compile_configurations(rows, root_dpath=tmp_path, cache=False)


def test_agreeing_alias_rows_still_deduplicate(tmp_path):
    """
    The complement, and the reason this cannot simply reject every duplicate:
    two identical requests share a record and must still collapse to one job.
    """
    rows = [dict(_ALIAS_ROWS[0]), dict(_ALIAS_ROWS[0])]
    _queue, statuses = _submit_rows(_two_alias_pipeline(), rows, tmp_path)
    assert statuses[0]['consumer'] == 'new_submission'
    assert statuses[1]['consumer'] == 'duplicate_submission'


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
    return Pipeline({'left': left, 'right': right, 'consumer': consumer})


@pytest.mark.parametrize('order', [[0, 1], [1, 0]])
def test_which_parameter_port_supplied_the_value_is_arbitrated(order, tmp_path):
    """
    Forwarded parameters are not delivery in the input sense at all -- they
    have no port in the delivery signature -- so nothing derived from input
    origins could ever see this.
    """
    rows = [
        {'left.thresh': 0.25, 'consumer.data_fpath': '/data'},
        {'right.thresh': 0.25, 'consumer.data_fpath': '/data'},
    ]
    ordered = [rows[idx] for idx in order]
    ids = []
    for row in ordered:
        dag = _param_alias_pipeline()
        dag.configure(config=row, root_dpath=tmp_path, cache=False)
        ids.append(dag.node_dict['consumer'].process_id)
    assert ids[0] == ids[1], 'the same parameter value is the same computation'

    with pytest.raises(ValueError, match='requested experiment') as excinfo:
        _submit_rows(_param_alias_pipeline(), ordered, tmp_path)
    assert '__parameter__.thresh' in str(excinfo.value)


# ---------------------------------------------------------------------------
# Canonicalization must not merge distinct mapping entries
# ---------------------------------------------------------------------------


def test_colliding_canonical_mapping_keys_are_refused(tmp_path):
    """
    Rewriting a key is many-to-one. Rebuilding the dictionary would drop an
    entry, leaving a two-entry mapping whose hashed payload is identical to a
    genuinely one-entry mapping while the commands still differ -- and two
    schedules that never see each other cannot be arbitrated after the fact.
    """
    consumer = _consumer()
    dag = Pipeline({'consumer': consumer})
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
    dag = Pipeline({'consumer': consumer})
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
        dag = Pipeline({'consumer': consumer})
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
# Request state that lives on the submission, not on the node
# ---------------------------------------------------------------------------
#
# A duplicate request returns before its Slurm options and bookkeeping flags
# are applied, so anything the node does not carry was first-call-wins. An
# ordinary pipeline keeps top-level ``__slurm_options__`` on the Pipeline and
# never copies it onto a node, which is precisely why a node-only snapshot
# could not see it. The compiler happens to copy a row-global value into each
# node config, so only the row-at-a-time path had the hole.


@pytest.mark.parametrize('order', [['gpu:1', 'gpu:4'], ['gpu:4', 'gpu:1']])
def test_gather_free_pipeline_slurm_options_conflict(order, tmp_path):
    """Reversing the rows used to change the resources the one job gets."""
    rows = [
        {'predict.model': 'm', '__slurm_options__': {'gres': gres}}
        for gres in order
    ]
    with pytest.raises(ValueError, match='__slurm_options__'):
        _submit_rows(_perf_pipeline(), rows, tmp_path, backend='slurm')


def test_agreeing_pipeline_slurm_options_still_deduplicate(tmp_path):
    """The complement: one job, submitted once, with the requested resources."""
    rows = [{'predict.model': 'm', '__slurm_options__': {'gres': 'gpu:2'}}] * 2
    queue, statuses = _submit_rows(
        _perf_pipeline(), rows, tmp_path, backend='slurm'
    )
    assert statuses[0]['predict'] == 'new_submission'
    assert statuses[1]['predict'] == 'duplicate_submission'
    assert 'gpu:2' in queue.finalize_text()


def test_pipeline_wide_and_node_level_options_compare_effectively(tmp_path):
    """
    The two halves are merged before they are compared, so requesting the same
    thing at either level is agreement rather than a conflict.
    """
    rows = [
        {'predict.model': 'm', '__slurm_options__': {'gres': 'gpu:2'}},
        {'predict.model': 'm', 'predict.__slurm_options__': {'gres': 'gpu:2'}},
    ]
    _queue, statuses = _submit_rows(
        _perf_pipeline(), rows, tmp_path, backend='slurm'
    )
    assert statuses[1]['predict'] == 'duplicate_submission'


@pytest.mark.parametrize(
    'flag', ['write_configs', 'write_invocations', 'enable_links', 'log']
)
def test_submission_bookkeeping_flags_must_agree(flag, tmp_path):
    """
    The reviewer's case: submit with ``write_configs=False`` and then with
    ``write_configs=True`` and no ``job_config.json`` is ever written, because
    the second request exits as a duplicate before the bookkeeping runs.
    """
    rows = [{'predict.model': 'm'}] * 2
    per_row = [{flag: False}, {flag: True}]
    with pytest.raises(ValueError, match=flag):
        _submit_rows(_perf_pipeline(), rows, tmp_path, per_row=per_row)


def test_agreeing_submission_flags_still_deduplicate(tmp_path):
    rows = [{'predict.model': 'm'}] * 2
    _queue, statuses = _submit_rows(
        _perf_pipeline(), rows, tmp_path, write_configs=True
    )
    assert statuses[0]['predict'] == 'new_submission'
    assert statuses[1]['predict'] == 'duplicate_submission'


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
    return Pipeline({'consumer': consumer}), consumer


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


@pytest.mark.parametrize('order', [[0, 1], [1, 0]])
def test_omitting_pipeline_slurm_options_does_not_inherit_them(order, tmp_path):
    """
    A row that says nothing about Slurm options is asking for the default, not
    for whatever the previous row asked for. Reusing the previous value made
    the distinction between "explicit options" and "no options" depend on row
    order -- the same stale-row-state class already fixed for input and
    parameter ports, and now load-bearing because arbitration reads this off
    the pipeline.
    """
    rows = [
        {'predict.model': 'm', '__slurm_options__': {'gres': 'gpu:1'}},
        {'predict.model': 'm'},
    ]
    ordered = [rows[idx] for idx in order]
    with pytest.raises(ValueError, match='__slurm_options__'):
        _submit_rows(_perf_pipeline(), ordered, tmp_path, backend='slurm')


def test_a_pipeline_wide_default_still_applies_to_every_row(tmp_path):
    """
    The complement, and why the reset is to a base rather than to nothing: a
    persistent default is how the CLI's ``--slurm_options`` reach every row.
    """
    dag = _perf_pipeline()
    dag._base_slurm_options = {'gres': 'gpu:2'}
    rows = [{'predict.model': 'm'}] * 2
    queue, statuses = _submit_rows(dag, rows, tmp_path, backend='slurm')
    assert statuses[1]['predict'] == 'duplicate_submission'
    assert 'gpu:2' in queue.finalize_text()


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
    dag = Pipeline({'consumer': node})
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
