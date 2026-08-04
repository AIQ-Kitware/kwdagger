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
