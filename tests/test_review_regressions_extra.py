"""
Further regression tests for the four defects covered by
``test_review_regressions.py``.

That file is the independent statement of the four defects and is deliberately
shape-agnostic: it asserts that a producer is reachable, that no gather
membership is lost, that a structured group value works at all. It was written
without reference to how any of that would be represented, and it is kept
byte-identical to the commit that introduced it.

This file pins the representations actually chosen, which the other file leaves
open on purpose:

* alias-recovered producers appear as ``origins`` on the ``__input__.<port>``
  provenance record, and the lending process is *not* a predecessor;
* gather records carry ``consumer_process_id`` and, when a template has several
  concrete instances, form a list index-aligned with ``__instances__.<node>``;
* a single-instance consumer still writes one record, not a one-element list;
* ``_hashable_group_value`` tags mappings and sets so they cannot collide with
  a sequence of the same contents;
* a recovered producer reaches the consumer's *identity* and not only its
  scheduling, so two consumers reading different files cannot share a result
  directory;
* a gathered port that is aliased makes the job writing the manifest a real
  dependency of whoever borrows the path.

It also covers the complements -- what must *not* change -- which is where the
risk of the alias fix actually lives: a pure configuration alias stays
configuration-only and keeps its operational identity.

Each defect is observable through an established surface -- execution lineage,
``job_config.json`` provenance, or the public :class:`GatherSpec` schema -- so
every test here asserts against those surfaces rather than against internals
that a refactor is allowed to move.
"""

from __future__ import annotations

import pytest
import ubelt as ub

from kwdagger.pipeline import (
    GatherSpec,
    Pipeline,
    ProcessNode,
    _hashable_group_value,
)

# ---------------------------------------------------------------------------
# 1. Produced lineage must survive an input alias
# ---------------------------------------------------------------------------


def _alias_chain_pipeline(*, reverse=False):
    """
    ``producer.output -> middle.input -> consumer.input``.

    The second edge is an input alias, so ``consumer`` reuses a value
    ``middle`` also consumes. But that value is *produced*, so ``producer``
    still has to run and materialize it before ``consumer`` may start.
    """
    producer = ProcessNode(
        name='producer',
        executable='python producer.py',
        out_paths={'produced_fpath': 'produced.json'},
    )
    middle = ProcessNode(
        name='middle',
        executable='python middle.py',
        in_paths={'data_fpath'},
        out_paths={'middle_fpath': 'middle.json'},
    )
    consumer = ProcessNode(
        name='consumer',
        executable='python consumer.py',
        in_paths={'data_fpath'},
        out_paths={'result_fpath': 'result.json'},
    )
    producer.outputs['produced_fpath'].connect(middle.inputs['data_fpath'])
    middle.inputs['data_fpath'].connect(consumer.inputs['data_fpath'])
    nodes = {'producer': producer, 'middle': middle, 'consumer': consumer}
    if reverse:
        nodes = {k: nodes[k] for k in ['consumer', 'middle', 'producer']}
    return Pipeline(nodes), producer, middle, consumer


def test_alias_preserves_the_produced_dependency(tmp_path):
    dag, producer, middle, consumer = _alias_chain_pipeline()
    dag.configure({}, root_dpath=tmp_path, cache=False)

    # The value is the produced path, as it already was.
    assert str(consumer.final_in_paths['data_fpath']) == str(
        producer.outputs['produced_fpath'].final_value
    )

    # ... and the process that produces it must run first.
    preds = consumer.predecessor_process_nodes()
    assert producer in preds, (
        'the producer behind the alias is a real execution dependency'
    )
    assert middle not in preds, (
        'lending an input as an alias does not make the lender a dependency'
    )
    assert producer in consumer.ancestor_process_nodes()

    assert dag.proc_graph.has_edge('producer', 'consumer')
    assert not dag.proc_graph.has_edge('middle', 'consumer')
    # The alias is still a configuration relationship as well.
    assert dag.config_graph.has_edge('middle', 'consumer')


def test_alias_produced_dependency_reaches_identity_and_provenance(tmp_path):
    dag, producer, middle, consumer = _alias_chain_pipeline()
    dag.configure({}, root_dpath=tmp_path, cache=False)

    # Identity: the produced path is the consumer's effective input value,
    # and it carries the producer's process_id, so the producer reaches
    # identity through the value rather than as a lineage record.
    assert str(consumer.depends['__inputs__']['data_fpath']) == str(
        producer.outputs['produced_fpath'].final_value
    )
    assert not any(k.startswith('__input__') for k in consumer.depends)

    # Provenance: a reader of job_config.json can see where the aliased
    # value actually came from.
    record = consumer._depends_config()['__input__.data_fpath']
    assert record['source_kind'] == 'input'
    assert record['source'] == 'middle.data_fpath'
    origins = record['origins']
    assert [item['source_process_id'] for item in origins] == [
        producer.process_id
    ]
    assert [item['source_port'] for item in origins] == ['produced_fpath']


def test_alias_lineage_is_independent_of_node_insertion_order(tmp_path):
    forward, _, _, consumer1 = _alias_chain_pipeline()
    reverse, _, _, consumer2 = _alias_chain_pipeline(reverse=True)
    forward.configure({}, root_dpath=tmp_path, cache=False)
    reverse.configure({}, root_dpath=tmp_path, cache=False)

    assert consumer1.process_id == consumer2.process_id
    assert sorted(n.name for n in consumer1.predecessor_process_nodes()) == (
        sorted(n.name for n in consumer2.predecessor_process_nodes())
    )


def test_a_configured_alias_stays_configuration_only(tmp_path):
    """
    The complement of the fix: an alias that forwards an externally known
    value must not gain execution ordering. This is the property that makes
    a direct configuration and an equivalent alias interchangeable.
    """
    source = ProcessNode(
        name='source',
        executable='python source.py',
        in_paths={'data_fpath': '/data/default.json'},
        out_paths={'source_fpath': 'source.json'},
    )
    consumer = ProcessNode(
        name='consumer',
        executable='python consumer.py',
        in_paths={'data_fpath'},
        out_paths={'result_fpath': 'result.json'},
    )
    source.inputs['data_fpath'].connect(consumer.inputs['data_fpath'])
    dag = Pipeline({'source': source, 'consumer': consumer})
    dag.configure(
        {'source.data_fpath': '/data/current.json'},
        root_dpath=tmp_path,
        cache=False,
    )

    assert consumer.final_in_paths['data_fpath'] == '/data/current.json'
    assert source not in consumer.predecessor_process_nodes()
    assert not dag.proc_graph.has_edge('source', 'consumer')
    assert dag.config_graph.has_edge('source', 'consumer')

    # An alias with no produced origin records none.
    record = consumer._depends_config()['__input__.data_fpath']
    assert 'origins' not in record

    # And it is still identical to writing the value on the consumer.
    direct = ProcessNode(
        name='consumer',
        executable='python consumer.py',
        in_paths={'data_fpath'},
        out_paths={'result_fpath': 'result.json'},
    )
    direct_dag = Pipeline({'consumer': direct})
    direct_dag.configure(
        {'consumer.data_fpath': '/data/current.json'},
        root_dpath=tmp_path,
        cache=False,
    )
    assert direct.process_id == consumer.process_id


def test_alias_chain_recovers_a_transitive_producer(tmp_path):
    """Aliases compose, so origin resolution has to be transitive."""
    producer = ProcessNode(
        name='producer',
        executable='python producer.py',
        out_paths={'produced_fpath': 'produced.json'},
    )
    middle = ProcessNode(
        name='middle',
        executable='python middle.py',
        in_paths={'data_fpath'},
        out_paths={'middle_fpath': 'middle.json'},
    )
    relay = ProcessNode(
        name='relay',
        executable='python relay.py',
        in_paths={'data_fpath'},
        out_paths={'relay_fpath': 'relay.json'},
    )
    consumer = ProcessNode(
        name='consumer',
        executable='python consumer.py',
        in_paths={'data_fpath'},
        out_paths={'result_fpath': 'result.json'},
    )
    producer.outputs['produced_fpath'].connect(middle.inputs['data_fpath'])
    middle.inputs['data_fpath'].connect(relay.inputs['data_fpath'])
    relay.inputs['data_fpath'].connect(consumer.inputs['data_fpath'])
    dag = Pipeline(
        {
            'producer': producer,
            'middle': middle,
            'relay': relay,
            'consumer': consumer,
        }
    )
    dag.configure({}, root_dpath=tmp_path, cache=False)

    preds = consumer.predecessor_process_nodes()
    assert producer in preds
    assert middle not in preds
    assert relay not in preds


# ---------------------------------------------------------------------------
# 2. Nested gather provenance must survive every concrete instance
# ---------------------------------------------------------------------------


def _nested_gather_pipeline():
    """
    The shape the TA1 cards use: shards fan in per cell, cells fan in per
    task. ``analysis`` therefore has several concrete ``merge`` ancestors,
    each with its own gather membership.
    """
    shard = ProcessNode(
        name='shard',
        executable='python shard.py',
        out_paths={'rows_fpath': 'rows.json'},
        algo_params={'task': 'a', 'model': 'x', 'shard': 0},
    )
    merge = ProcessNode(
        name='merge',
        executable='python merge.py',
        in_paths={'shard_rows_fpath'},
        out_paths={'merged_fpath': 'merged.json'},
        algo_params={'task': 'a', 'model': 'x'},
    )
    analysis = ProcessNode(
        name='analysis',
        executable='python analysis.py',
        in_paths={'model_features_fpath'},
        out_paths={'verdict_fpath': 'verdict.json'},
        algo_params={'task': 'a'},
    )
    shard.outputs['rows_fpath'].connect(
        merge.inputs['shard_rows_fpath'],
        gather=GatherSpec(group_by=['task', 'model'], order_by=['shard']),
    )
    merge.outputs['merged_fpath'].connect(
        analysis.inputs['model_features_fpath'],
        gather=GatherSpec(group_by=['task'], order_by=['model']),
    )
    return Pipeline({'shard': shard, 'merge': merge, 'analysis': analysis})


def _nested_gather_rows():
    rows = []
    for model in ['x', 'y']:
        for shard_idx in [0, 1]:
            rows.append(
                {
                    'shard.task': 'a',
                    'shard.model': model,
                    'shard.shard': shard_idx,
                    'merge.task': 'a',
                    'merge.model': model,
                    'analysis.task': 'a',
                }
            )
    return rows


def test_every_gathered_ancestor_instance_is_recorded():
    dag = _nested_gather_pipeline()
    root = (
        ub.Path.appdir('kwdagger/tests/regressions/nested-gather')
        .delete()
        .ensuredir()
    )
    compiled = dag.compile_configurations(
        _nested_gather_rows(), root_dpath=root, cache=False
    )
    by_name = ub.group_items(compiled.nodes.values(), key=lambda n: n.name)
    assert len(by_name['shard']) == 4
    assert len(by_name['merge']) == 2
    assert len(by_name['analysis']) == 1

    analysis = by_name['analysis'][0]
    record = analysis._depends_config()

    # Two distinct merges each gathered their own shards. Neither may be
    # overwritten by the other.
    ancestor_gather = record['__gather__.merge.shard_rows_fpath']
    assert isinstance(ancestor_gather, list)
    assert len(ancestor_gather) == 2

    # Each record names the concrete instance the gather happened on, and
    # the records are ordered to match the instance listing.
    instances = record['__instances__.merge']
    assert sorted(instances) == sorted(n.process_id for n in by_name['merge'])
    assert [
        item['consumer_process_id'] for item in ancestor_gather
    ] == instances

    # No membership is lost: every shard appears under exactly one merge.
    gathered_ids = [
        member['process_id']
        for item in ancestor_gather
        for member in item['members']
    ]
    assert sorted(gathered_ids) == sorted(
        n.process_id for n in by_name['shard']
    )
    for item in ancestor_gather:
        assert len(item['members']) == 2

    # The consumer's own gather keeps its established un-prefixed shape.
    own = record['__gather__.model_features_fpath']
    assert isinstance(own, dict)
    assert own['source'] == 'merge.merged_fpath'
    assert sorted(m['process_id'] for m in own['members']) == sorted(
        n.process_id for n in by_name['merge']
    )


def test_single_gathered_ancestor_keeps_its_scalar_record():
    """A pipeline with one concrete consumer still reads as one record."""
    dag = _nested_gather_pipeline()
    root = (
        ub.Path.appdir('kwdagger/tests/regressions/single-gather')
        .delete()
        .ensuredir()
    )
    rows = [row for row in _nested_gather_rows() if row['shard.model'] == 'x']
    compiled = dag.compile_configurations(rows, root_dpath=root, cache=False)
    by_name = ub.group_items(compiled.nodes.values(), key=lambda n: n.name)
    analysis = by_name['analysis'][0]
    record = analysis._depends_config()

    ancestor_gather = record['__gather__.merge.shard_rows_fpath']
    assert isinstance(ancestor_gather, dict)
    assert ancestor_gather['source'] == 'shard.rows_fpath'
    assert (
        ancestor_gather['consumer_process_id'] == by_name['merge'][0].process_id
    )


# ---------------------------------------------------------------------------
# 3. Structured grouping values must canonicalize
# ---------------------------------------------------------------------------


def test_hashable_group_value_handles_nested_containers():
    value = {'b': [1, {'c': ub.Path('/tmp/x')}], 'a': (2, 3)}
    canonical = _hashable_group_value(value)
    assert hash(canonical) is not None
    assert {canonical} == {
        _hashable_group_value(dict(reversed(list(value.items()))))
    }, 'mapping order must not matter'

    # Structurally different container kinds must not collide.
    assert _hashable_group_value([1, 2]) != _hashable_group_value({1, 2})
    assert _hashable_group_value({'a': 1}) != _hashable_group_value([('a', 1)])

    # Paths still compare equal to their string form, as they always did.
    assert _hashable_group_value(ub.Path('/tmp/x')) == _hashable_group_value(
        '/tmp/x'
    )
    assert _hashable_group_value(frozenset({1, 2})) == _hashable_group_value(
        {2, 1}
    )


def test_gather_can_group_on_a_structured_parameter():
    """
    A qualified key is resolved against every concrete instance of the named
    node and the results are compared for ambiguity. That comparison is
    set-based, so a structured parameter value has to canonicalize before it
    gets there.
    """
    prepare = ProcessNode(
        name='prepare',
        executable='python prepare.py',
        out_paths={'data_fpath': 'data.json'},
        algo_params={'window': {'size': 1, 'stride': [1, 2]}},
    )
    score = ProcessNode(
        name='score',
        executable='python score.py',
        in_paths={'data_fpath'},
        out_paths={'score_fpath': 'score.json'},
        algo_params={'model': 'm1'},
    )
    report = ProcessNode(
        name='report',
        executable='python report.py',
        in_paths={'data_fpath', 'scores_fpath'},
        out_paths={'report_fpath': 'report.json'},
    )
    prepare.outputs['data_fpath'].connect(score.inputs['data_fpath'])
    prepare.outputs['data_fpath'].connect(report.inputs['data_fpath'])
    score.outputs['score_fpath'].connect(
        report.inputs['scores_fpath'],
        gather=GatherSpec(group_by=['prepare.window'], order_by=['model']),
    )
    dag = Pipeline({'prepare': prepare, 'score': score, 'report': report})
    windows = [
        {'size': 1, 'stride': [1, 2]},
        {'size': 2, 'stride': [1, 2]},
    ]
    rows = [
        {
            'prepare.window': window,
            'score.model': model,
        }
        for window in windows
        for model in ['m1', 'm2']
    ]
    root = (
        ub.Path.appdir('kwdagger/tests/regressions/structured-group')
        .delete()
        .ensuredir()
    )
    compiled = dag.compile_configurations(rows, root_dpath=root, cache=False)
    by_name = ub.group_items(compiled.nodes.values(), key=lambda n: n.name)
    assert len(by_name['report']) == 2, 'one report per prepared window'
    for report_node in by_name['report']:
        members = report_node.inputs['scores_fpath']._gather_members
        assert members is not None
        assert len(members) == 2, 'both models, and only this window'


# ---------------------------------------------------------------------------
# 4. Gather provenance must use the public GatherSpec schema
# ---------------------------------------------------------------------------


def test_gather_provenance_uses_the_public_group_by_schema():
    source = ProcessNode(
        name='predict',
        executable='python predict.py',
        out_paths={'pred_fpath': 'pred.json'},
        algo_params={'dataset_fpath': 'train.kwcoco', 'seed': 0},
    )
    sink = ProcessNode(
        name='score',
        executable='python score.py',
        in_paths={'preds_fpath'},
        out_paths={'score_fpath': 'score.json'},
        algo_params={'truth_fpath': 'train.kwcoco'},
    )
    source.outputs['pred_fpath'].connect(
        sink.inputs['preds_fpath'],
        gather=GatherSpec(
            group_by=[{'src': 'dataset_fpath', 'dst': 'truth_fpath'}],
            order_by=['seed'],
        ),
    )
    dag = Pipeline({'predict': source, 'score': sink})
    rows = [
        {
            'predict.dataset_fpath': 'train.kwcoco',
            'predict.seed': seed,
            'score.truth_fpath': 'train.kwcoco',
        }
        for seed in [0, 1]
    ]
    root = (
        ub.Path.appdir('kwdagger/tests/regressions/public-schema')
        .delete()
        .ensuredir()
    )
    compiled = dag.compile_configurations(rows, root_dpath=root, cache=False)
    scorer = [n for n in compiled.nodes.values() if n.name == 'score'][0]
    record = scorer._depends_config()['__gather__.preds_fpath']

    # The internal (src, dst) tuple must not leak into the record.
    assert record['group_by'] == [
        {'src': 'dataset_fpath', 'dst': 'truth_fpath'}
    ]
    # The useful fields stay, including their defaults.
    assert record['order_by'] == ['seed']
    assert record['require'] == 'all_success'

    # And what was written is what the public schema accepts back.
    restored = GatherSpec.coerce(
        {
            'group_by': record['group_by'],
            'order_by': record['order_by'],
            'require': record['require'],
        }
    )
    assert restored == scorer.inputs['preds_fpath']._gather_connection.spec


def test_unmapped_gather_provenance_keeps_plain_names():
    dag = _nested_gather_pipeline()
    root = (
        ub.Path.appdir('kwdagger/tests/regressions/plain-schema')
        .delete()
        .ensuredir()
    )
    compiled = dag.compile_configurations(
        _nested_gather_rows(), root_dpath=root, cache=False
    )
    merges = [n for n in compiled.nodes.values() if n.name == 'merge']
    record = merges[0]._depends_config()['__gather__.shard_rows_fpath']
    assert record['group_by'] == ['task', 'model']
    assert record['order_by'] == ['shard']
    assert record['require'] == 'all_success'
    assert GatherSpec.coerce(
        {'group_by': record['group_by'], 'order_by': record['order_by']}
    ) == GatherSpec(group_by=['task', 'model'], order_by=['shard'])


# ---------------------------------------------------------------------------
# 5. A recovered producer must reach the consumer's identity, not just its
#    scheduling
# ---------------------------------------------------------------------------


def _identity_chain_pipeline():
    """
    ``producer`` runs one algorithm over whichever data it is given, so its
    ``algo_id`` is blind to that choice. Only its ``process_id`` distinguishes
    the runs -- which is exactly what a consumer behind an alias has to record.
    """
    producer = ProcessNode(
        name='producer',
        executable='python producer.py',
        in_paths={'src'},
        out_paths={'produced_fpath': 'produced.json'},
        algo_params={'algo': 'x'},
    )
    middle = ProcessNode(
        name='middle',
        executable='python middle.py',
        in_paths={'data_fpath'},
        out_paths={'middle_fpath': 'middle.json'},
    )
    consumer = ProcessNode(
        name='consumer',
        executable='python consumer.py',
        in_paths={'data_fpath'},
        out_paths={'result_fpath': 'result.json'},
    )
    producer.outputs['produced_fpath'].connect(middle.inputs['data_fpath'])
    middle.inputs['data_fpath'].connect(consumer.inputs['data_fpath'])
    dag = Pipeline(
        {'producer': producer, 'middle': middle, 'consumer': consumer}
    )
    return dag, producer, consumer


def test_alias_consumer_identity_tracks_the_producer_instance(tmp_path):
    """
    Vary only an upstream external input. The producer's algorithm is
    unchanged, so its ``algo_id`` is too -- and the ancestor payload records
    nothing else. If that is all the consumer sees, two consumers reading
    different files share a ``process_id`` and a result directory, and
    whichever compiled first silently wins.
    """
    seen = {}
    for src in ['/data/a', '/data/b']:
        dag, producer, consumer = _identity_chain_pipeline()
        dag.configure(
            {'producer.src': src, 'producer.algo': 'x'},
            root_dpath=tmp_path,
            cache=False,
        )
        seen[src] = {
            'producer_algo_id': producer.algo_id,
            'producer_process_id': producer.process_id,
            'consumer_process_id': consumer.process_id,
            'consumer_input': str(consumer.final_in_paths['data_fpath']),
            'consumer_inputs': dict(consumer.depends.get('__inputs__', {})),
        }
    a, b = seen['/data/a'], seen['/data/b']

    # The setup: same algorithm, different instance, different data reaching
    # the consumer.
    assert a['producer_algo_id'] == b['producer_algo_id']
    assert a['producer_process_id'] != b['producer_process_id']
    assert a['consumer_input'] != b['consumer_input']

    # The consumer records the value it read, and that value contains the
    # producer's process_id -- so identity follows the producer without ever
    # naming it. Nothing lineage-shaped is in the payload.
    assert a['producer_process_id'] in str(a['consumer_inputs']['data_fpath'])
    assert b['producer_process_id'] in str(b['consumer_inputs']['data_fpath'])

    # ... so the two consumers cannot share a result directory.
    assert a['consumer_process_id'] != b['consumer_process_id']


def test_aliasing_two_ports_of_one_producer_stays_distinguishable(tmp_path):
    """The binding names the port, not just the process."""
    ids = {}
    for port in ['first_fpath', 'second_fpath']:
        producer = ProcessNode(
            name='producer',
            executable='python producer.py',
            out_paths={
                'first_fpath': 'first.json',
                'second_fpath': 'second.json',
            },
        )
        middle = ProcessNode(
            name='middle',
            executable='python middle.py',
            in_paths={'data_fpath'},
            out_paths={'middle_fpath': 'middle.json'},
        )
        consumer = ProcessNode(
            name='consumer',
            executable='python consumer.py',
            in_paths={'data_fpath'},
            out_paths={'result_fpath': 'result.json'},
        )
        producer.outputs[port].connect(middle.inputs['data_fpath'])
        middle.inputs['data_fpath'].connect(consumer.inputs['data_fpath'])
        dag = Pipeline(
            {'producer': producer, 'middle': middle, 'consumer': consumer}
        )
        dag.configure({}, root_dpath=tmp_path, cache=False)
        ids[port] = consumer.process_id
    assert ids['first_fpath'] != ids['second_fpath']


# ---------------------------------------------------------------------------
# 6. Aliasing a gathered input depends on the job that writes the manifest
# ---------------------------------------------------------------------------


def test_aliasing_a_gathered_input_depends_on_the_manifest_writer():
    """
    A gathered port resolves to a path manifest, and that manifest is written
    by the consumer's own command -- not by any member of the collection. A
    process that borrows the path therefore has to wait for that job, or it
    reads a file nothing has created yet.
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
    audit = ProcessNode(
        name='audit',
        executable='python audit.py',
        in_paths={'parts_fpath'},
        out_paths={'audit_fpath': 'audit.json'},
    )
    shard.outputs['part_fpath'].connect(
        merge.inputs['parts_fpath'],
        gather=GatherSpec(group_by=['dataset'], order_by=['fold']),
    )
    merge.inputs['parts_fpath'].connect(audit.inputs['parts_fpath'])
    dag = Pipeline({'shard': shard, 'merge': merge, 'audit': audit})
    root = (
        ub.Path.appdir('kwdagger/tests/regressions/gather-alias')
        .delete()
        .ensuredir()
    )
    rows = [
        {'shard.dataset': 'a', 'shard.fold': fold, 'merge.dataset': 'a'}
        for fold in [0, 1]
    ]
    compiled = dag.compile_configurations(rows, root_dpath=root, cache=False)
    by_name = ub.group_items(compiled.nodes.values(), key=lambda n: n.name)
    audit_node = by_name['audit'][0]
    merge_node = by_name['merge'][0]

    # The borrowed value really is merge's manifest.
    assert str(audit_node.final_in_paths['parts_fpath']) == str(
        merge_node.inputs['parts_fpath'].gather_manifest_fpath
    )

    # So merge must run first, and the queue has to know it.
    assert merge_node in audit_node.predecessor_process_nodes()
    assert compiled.proc_graph.has_edge(
        merge_node.process_id, audit_node.process_id
    )

    # The manifest path is the borrower's effective input value, and it sits
    # under merge's result directory, so identity follows the writer through
    # the value. No lineage record is added.
    assert str(audit_node.depends['__inputs__']['parts_fpath']) == str(
        merge_node.inputs['parts_fpath'].gather_manifest_fpath
    )
    assert not any(k.startswith('__input__') for k in audit_node.depends)

    # Provenance still says who supplied it.
    binding = audit_node._depends_config()['__input__.parts_fpath']
    assert binding['source_kind'] == 'input'


# ---------------------------------------------------------------------------
# 7. Identity follows the *effective* input source, not every structural one
# ---------------------------------------------------------------------------


def _connected_input_pipeline():
    producer = ProcessNode(
        name='producer',
        executable='python producer.py',
        out_paths={'produced_fpath': 'produced.json'},
    )
    consumer = ProcessNode(
        name='consumer',
        executable='python consumer.py',
        in_paths={'data_fpath'},
        out_paths={'result_fpath': 'result.json'},
    )
    producer.outputs['produced_fpath'].connect(consumer.inputs['data_fpath'])
    return Pipeline({'producer': producer, 'consumer': consumer}), consumer


def test_overriding_a_connected_input_changes_identity(tmp_path):
    """
    An explicit value outranks a producer -- that precedence is documented and
    deliberate. So the consumer reads the override, not the producer's output,
    and the override is what identifies it. If identity keeps pointing at the
    producer instead, two consumers reading different files hash alike and the
    compiler silently keeps whichever row it saw first.
    """
    seen = {}
    for override in ['/override/a', '/override/b']:
        dag, consumer = _connected_input_pipeline()
        dag.configure(
            {'consumer.data_fpath': override},
            root_dpath=tmp_path,
            cache=False,
        )
        seen[override] = {
            'process_id': consumer.process_id,
            'value': str(consumer.final_in_paths['data_fpath']),
            'input_config': dict(consumer.final_input_config),
            'depends': dict(consumer.depends),
        }
    a, b = seen['/override/a'], seen['/override/b']

    assert a['value'] == '/override/a'
    assert b['value'] == '/override/b'
    assert a['process_id'] != b['process_id']

    # The override is this node's own input now, so it belongs in the input
    # config, and no producer binding stands in for it.
    assert a['input_config']['data_fpath'] == '/override/a'
    assert '__input__.data_fpath' not in a['depends']


def test_an_unoverridden_connected_input_still_names_its_producer(tmp_path):
    """The complement: without an override the producer is what supplies it."""
    dag, consumer = _connected_input_pipeline()
    dag.configure({}, root_dpath=tmp_path, cache=False)
    # The produced path is an ordinary effective input value ...
    assert 'data_fpath' in consumer.final_input_config
    assert str(consumer.depends['__inputs__']['data_fpath']).endswith(
        'produced.json'
    )
    # ... and identity says nothing about where it came from.
    assert not any(k.startswith('__input__') for k in consumer.depends)
    # Provenance does.
    binding = consumer._depends_config()['__input__.data_fpath']
    assert binding['source_port'] == 'produced_fpath'
    assert binding['source_kind'] == 'output'


def test_a_forwarded_known_value_outranks_a_producer(tmp_path):
    """
    Same precedence, reached the other way: the consumer is wired to a
    producer *and* aliased from a port carrying a known value. The alias wins
    resolution, so it must win identity too.
    """
    producer = ProcessNode(
        name='producer',
        executable='python producer.py',
        out_paths={'produced_fpath': 'produced.json'},
    )
    lender = ProcessNode(
        name='lender',
        executable='python lender.py',
        in_paths={'data_fpath': '/data/default.json'},
        out_paths={'lender_fpath': 'lender.json'},
    )
    consumer = ProcessNode(
        name='consumer',
        executable='python consumer.py',
        in_paths={'data_fpath'},
        out_paths={'result_fpath': 'result.json'},
    )
    producer.outputs['produced_fpath'].connect(consumer.inputs['data_fpath'])
    lender.inputs['data_fpath'].connect(consumer.inputs['data_fpath'])
    dag = Pipeline(
        {'producer': producer, 'lender': lender, 'consumer': consumer}
    )
    ids = {}
    for value in ['/data/one.json', '/data/two.json']:
        dag.configure(
            {'lender.data_fpath': value}, root_dpath=tmp_path, cache=False
        )
        assert str(consumer.final_in_paths['data_fpath']) == value
        ids[value] = consumer.process_id
    assert ids['/data/one.json'] != ids['/data/two.json']


# ---------------------------------------------------------------------------
# 8. The template graph tells the truth about gather-manifest aliases
# ---------------------------------------------------------------------------


def _gather_alias_pipeline():
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
    audit = ProcessNode(
        name='audit',
        executable='python audit.py',
        in_paths={'parts_fpath'},
        out_paths={'audit_fpath': 'audit.json'},
    )
    shard.outputs['part_fpath'].connect(
        merge.inputs['parts_fpath'],
        gather=GatherSpec(group_by=['dataset'], order_by=['fold']),
    )
    merge.inputs['parts_fpath'].connect(audit.inputs['parts_fpath'])
    return Pipeline({'shard': shard, 'merge': merge, 'audit': audit}), audit


def test_template_graph_shows_the_manifest_writer():
    """
    The compiled graph gets this right, so there is no race -- but the logical
    graph is what a user reads before compiling, and it described the edge as
    configuration-only. A template port knows it *has* a gather connection long
    before it knows the membership, which is enough to know an edge exists.
    """
    dag, audit = _gather_alias_pipeline()
    assert dag.proc_graph.has_edge('merge', 'audit')
    assert [n.name for n in audit.predecessor_process_nodes()] == ['merge']
    assert [n.name for n in audit.ancestor_process_nodes()] == ['merge']


def test_template_lineage_is_not_stale_after_construction():
    """
    A node memoizes lineage during ``__init__``, before it is connected to
    anything. Building the pipeline is the first moment the whole connection
    state exists, so that is where the stale answers are dropped.
    """
    producer = ProcessNode(
        name='producer',
        executable='python producer.py',
        out_paths={'produced_fpath': 'produced.json'},
    )
    consumer = ProcessNode(
        name='consumer',
        executable='python consumer.py',
        in_paths={'data_fpath'},
        out_paths={'result_fpath': 'result.json'},
    )
    producer.outputs['produced_fpath'].connect(consumer.inputs['data_fpath'])
    # Nothing memoizes lineage during construction any more -- identity stopped
    # asking for it when lineage left the hash -- so this is already right.
    assert consumer.predecessor_process_nodes() == [producer]
    # Building the graph clears the caches regardless, so a future query added
    # to __init__ cannot silently reintroduce the staleness.
    dag = Pipeline({'producer': producer, 'consumer': consumer})
    assert dag.proc_graph.has_edge('producer', 'consumer')
    assert consumer.predecessor_process_nodes() == [producer]
    assert consumer.ancestor_process_nodes() == [producer]


def test_same_node_gather_alias_is_rejected_with_a_clear_error():
    """
    Forwarding a gathered port to another port of the *same* process asks that
    process to depend on itself, and asks its identity to include its own
    ``process_id``. Reject it where it is written rather than let it surface
    as a RecursionError or a self-edge.
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
        in_paths={'parts_fpath', 'parts_copy_fpath'},
        out_paths={'merged_fpath': 'merged.txt'},
        algo_params={'dataset': 'a'},
    )
    shard.outputs['part_fpath'].connect(
        merge.inputs['parts_fpath'],
        gather=GatherSpec(group_by=['dataset'], order_by=['fold']),
    )
    merge.inputs['parts_fpath'].connect(merge.inputs['parts_copy_fpath'])
    with pytest.raises(ValueError) as excinfo:
        Pipeline({'shard': shard, 'merge': merge})
    message = str(excinfo.value)
    assert 'same process' in message
    assert 'merge.parts_fpath' in message


def test_ordinary_same_node_forwarding_still_works(tmp_path):
    """The complement: same-node forwarding is supported and must stay so."""
    node = ProcessNode(
        name='node',
        executable='python node.py',
        in_paths={'src_fpath', 'copy_fpath'},
        out_paths={'dst_fpath': 'dst.json'},
    )
    node.inputs['src_fpath'].connect(node.inputs['copy_fpath'])
    dag = Pipeline({'node': node})
    dag.configure(
        {'node.src_fpath': '/data/in.json'}, root_dpath=tmp_path, cache=False
    )
    assert node.final_in_paths['copy_fpath'] == '/data/in.json'


# ---------------------------------------------------------------------------
# 9. Effective resolution recurses; identity ancestry follows it
# ---------------------------------------------------------------------------


def _override_upstream_pipeline():
    """
    ``producer.output -> lender.input -> consumer.input``, where the override
    lands on ``lender`` -- one alias hop away from the consumer.
    """
    producer = ProcessNode(
        name='producer',
        executable='python producer.py',
        out_paths={'data_fpath': 'data.json'},
        algo_params={'algo': 'x'},
    )
    lender = ProcessNode(
        name='lender',
        executable='python lender.py',
        in_paths={'data_fpath'},
        out_paths={'lender_fpath': 'lender.json'},
    )
    consumer = ProcessNode(
        name='consumer',
        executable='python consumer.py',
        in_paths={'data_fpath'},
        out_paths={'result_fpath': 'result.json'},
    )
    producer.outputs['data_fpath'].connect(lender.inputs['data_fpath'])
    lender.inputs['data_fpath'].connect(consumer.inputs['data_fpath'])
    dag = Pipeline(
        {'producer': producer, 'lender': lender, 'consumer': consumer}
    )
    return dag, consumer


def test_an_override_one_alias_hop_away_still_changes_identity(tmp_path):
    """
    Precedence has to be asked of every source, not just the consumer's own
    port. The lender's explicit value outranks the producer wired behind it,
    so the producer supplies nothing to anybody -- and a structural walk of
    the alias chain would report it anyway.
    """
    seen = {}
    for override in ['/override/a', '/override/b']:
        dag, consumer = _override_upstream_pipeline()
        dag.configure(
            {'lender.data_fpath': override, 'producer.algo': 'x'},
            root_dpath=tmp_path,
            cache=False,
        )
        seen[override] = (
            consumer.process_id,
            str(consumer.final_in_paths['data_fpath']),
        )
    a, b = seen['/override/a'], seen['/override/b']
    assert a[1] == '/override/a'
    assert b[1] == '/override/b'
    assert a[0] != b[0]


def test_an_unoverridden_alias_chain_still_reaches_the_producer(tmp_path):
    """The complement: with nothing overridden the producer is the source."""
    dag, consumer = _override_upstream_pipeline()
    dag.configure({'producer.algo': 'x'}, root_dpath=tmp_path, cache=False)
    binding = consumer._depends_config()['__input__.data_fpath']
    assert binding['source_kind'] == 'input'
    assert [n.name for n in consumer.effective_predecessor_process_nodes()] == [
        'producer'
    ]


def test_an_unread_producer_does_not_reach_consumer_identity(tmp_path):
    """
    Hold the override constant and vary only the producer's algorithm. The
    consumer's command does not change, so neither may its result directory --
    otherwise a producer sweep fans out into identical consumer jobs.
    """
    ids = {}
    commands = {}
    for algo in ['x', 'y']:
        producer = ProcessNode(
            name='producer',
            executable='python producer.py',
            out_paths={'data_fpath': 'data.json'},
            algo_params={'algo': algo},
        )
        consumer = ProcessNode(
            name='consumer',
            executable='python consumer.py',
            in_paths={'data_fpath'},
            out_paths={'result_fpath': 'result.json'},
        )
        producer.outputs['data_fpath'].connect(consumer.inputs['data_fpath'])
        dag = Pipeline({'producer': producer, 'consumer': consumer})
        dag.configure(
            {'consumer.data_fpath': '/precomputed/data', 'producer.algo': algo},
            root_dpath=tmp_path,
            cache=False,
        )
        ids[algo] = consumer.process_id
        commands[algo] = consumer.command
        last = (dag, consumer)

    assert commands['x'] == commands['y']
    assert ids['x'] == ids['y']

    # The conservative scheduling edge is deliberately kept: ordering a job
    # that turns out not to matter costs nothing, missing one is a race.
    dag, consumer = last
    assert dag.proc_graph.has_edge('producer', 'consumer')
    assert [n.name for n in consumer.predecessor_process_nodes()] == [
        'producer'
    ]
    assert consumer.effective_predecessor_process_nodes() == []


def test_provenance_does_not_claim_an_unread_producer_supplied_the_value(
    tmp_path,
):
    """
    ``job_config.json`` recorded both that the producer supplied the input and
    that the command read the override. One of those was false, and a reader
    had no way to tell which.
    """
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
    dag = Pipeline({'producer': producer, 'consumer': consumer})
    dag.configure(
        {'consumer.data_fpath': '/precomputed/data'},
        root_dpath=tmp_path,
        cache=False,
    )
    record = consumer._depends_config()

    # The wiring is still recorded -- it is part of what was requested ...
    binding = record['__input__.data_fpath']
    assert binding['source_port'] == 'data_fpath'
    # ... but it is marked as not having supplied the value.
    assert binding['supplied'] is False
    assert record['consumer.data_fpath'] == '/precomputed/data'
    # No concrete instance is named: several rows can wire different producer
    # instances into one deduplicated consumer, so an instance here would be
    # decided by compile order.
    assert 'source_process_id' not in binding
    assert binding['source'] == 'producer.data_fpath'


def test_a_read_producer_is_not_marked_unsupplied(tmp_path):
    """The complement, so ``supplied`` cannot quietly become always-false."""
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
    dag = Pipeline({'producer': producer, 'consumer': consumer})
    dag.configure({}, root_dpath=tmp_path, cache=False)
    binding = consumer._depends_config()['__input__.data_fpath']
    assert 'supplied' not in binding


# ---------------------------------------------------------------------------
# 10. The runtime gate is effective, on both scheduling paths
# ---------------------------------------------------------------------------
#
# The gate is not merely an ordering hint. A disabled or missing predecessor
# suppresses its successor, so gating a command on a producer it never reads
# can silently skip valid work. There are two execution paths and they used to
# answer this question differently, which is worth testing separately: a
# gather-free pipeline is scheduled a row at a time, while a pipeline
# containing *any* gather is compiled across the whole matrix first. The same
# consumer must not run in one pipeline shape and be skipped in the other.


def test_single_row_gate_ignores_a_producer_the_command_does_not_read(
    tmp_path,
):
    """
    Gather-free, one row: the producer is disabled and the consumer overrides
    the input it was wired to. The consumer reads a path the producer has
    nothing to do with, so it must run.
    """
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
    dag = Pipeline({'producer': producer, 'consumer': consumer})
    dag.configure(
        {
            'consumer.data_fpath': '/precomputed/data',
            'producer.__enabled__': False,
        },
        root_dpath=tmp_path,
        cache=False,
    )

    # The template graph still records the wiring: it answers which
    # dependencies are possible, before anything is configured.
    assert dag.proc_graph.has_edge('producer', 'consumer')

    # The execution graph answers what this command requires, and it requires
    # nothing from the producer.
    execution = dag.effective_execution_graph()
    assert not execution.has_edge('producer', 'consumer')

    status = dag.submit_jobs(
        queue={'backend': 'serial'},
        enable_links=False,
        write_invocations=False,
        write_configs=False,
    )['node_status']
    assert status['producer'] == 'disabled'
    assert status['consumer'] == 'new_submission', (
        'a disabled producer the consumer never reads must not suppress it'
    )


def test_single_row_gate_still_respects_a_producer_that_is_read(tmp_path):
    """The complement: without the override the gate must still bite."""
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
    dag = Pipeline({'producer': producer, 'consumer': consumer})
    dag.configure(
        {'producer.__enabled__': False}, root_dpath=tmp_path, cache=False
    )
    assert dag.effective_execution_graph().has_edge('producer', 'consumer')
    status = dag.submit_jobs(
        queue={'backend': 'serial'},
        enable_links=False,
        write_invocations=False,
        write_configs=False,
    )['node_status']
    assert status['consumer'] == 'skipped'


def _dedup_pipeline():
    """
    A consumer whose input is overridden, plus an unrelated gather.

    The gather earns its place: ``compile_configurations`` refuses a pipeline
    without one, and ``build_schedule`` only takes the full-matrix path when a
    gather exists. So the deduplication this exercises is reachable only in a
    pipeline that gathers *somewhere* -- which is exactly why the two
    scheduling paths could disagree unnoticed.
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
    producer = ProcessNode(
        name='producer',
        executable='python producer.py',
        out_paths={'data_fpath': 'data.json'},
        algo_params={'algo': 'x'},
    )
    consumer = ProcessNode(
        name='consumer',
        executable='python consumer.py',
        in_paths={'data_fpath'},
        out_paths={'result_fpath': 'result.json'},
    )
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
    return [
        dict(base, **{'producer.algo': 'x', 'producer.__enabled__': False}),
        dict(base, **{'producer.algo': 'y', 'producer.__enabled__': True}),
    ]


def _compile_dedup(rows, label):
    root = (
        ub.Path.appdir(f'kwdagger/tests/regressions/dedup/{label}')
        .delete()
        .ensuredir()
    )
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
        'consumers': len(
            [n for n in compiled.nodes.values() if n.name == 'consumer']
        ),
        'producers': len(
            [n for n in compiled.nodes.values() if n.name == 'producer']
        ),
        'process_id': consumer.process_id,
        'preds': sorted(
            compiled.nodes[p].name
            for p in compiled.proc_graph.predecessors(consumer.process_id)
        ),
        'status': status[consumer.process_id],
        'provenance': consumer._depends_config().get('__input__.data_fpath'),
    }


def test_matrix_row_order_does_not_change_a_deduplicated_consumer():
    """
    Two rows wire the same overridden consumer behind different producers, so
    they compile to one consumer. Keeping whichever row arrived first would
    attach it to an arbitrary producer -- and since a disabled predecessor
    suppresses its successor, reversing the matrix would decide whether the
    consumer ran at all.
    """
    forward = _compile_dedup(_dedup_rows(), 'forward')
    reverse = _compile_dedup(list(reversed(_dedup_rows())), 'reverse')

    assert forward['consumers'] == 1
    assert forward['producers'] == 2
    assert forward == reverse, 'compilation must not depend on matrix order'

    # And the outcome is the right one: the consumer reads a path neither
    # producer makes, so neither gates it.
    assert forward['preds'] == []
    assert forward['status'] == 'new_submission'
    assert forward['provenance']['supplied'] is False


# ---------------------------------------------------------------------------
# 11. The rest of the effective/structural surface
# ---------------------------------------------------------------------------
#
# The three findings above each arrived through one consumer of the
# structural/effective distinction. These cover the others, so that a future
# change cannot fix one caller and leave another answering the old question.


def _link_pipeline(config, label):
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
    dag = Pipeline({'producer': producer, 'consumer': consumer})
    root = (
        ub.Path.appdir(f'kwdagger/tests/regressions/links/{label}')
        .delete()
        .ensuredir()
    )
    dag.configure(config, root_dpath=root, cache=False)
    status = dag.submit_jobs(
        queue={'backend': 'serial'},
        enable_links=True,
        write_invocations=False,
        write_configs=False,
    )
    return status['queue'].finalize_text()


def test_pred_succ_links_omit_a_producer_the_result_did_not_come_from():
    """
    The result graph is meant to be navigable: a ``.pred`` entry is a claim
    that this result was computed from that one. Writing one for a producer
    whose output was overridden away would make the lineage on disk say
    something the command never did.
    """
    read = _link_pipeline({}, 'read')
    assert read.count('/.pred/') == 2, 'sanity: links are written at all'
    assert read.count('/.succ/') == 2

    overridden = _link_pipeline(
        {'consumer.data_fpath': '/precomputed/data'}, 'overridden'
    )
    assert overridden.count('/.pred/') == 0
    assert overridden.count('/.succ/') == 0


def test_effective_ancestry_drops_what_only_reached_here_through_an_override(
    tmp_path,
):
    """
    ``a -> b -> c``, with ``b``'s input overridden. ``c`` still reads ``b``,
    but ``b`` no longer reads ``a``, so ``a`` is not part of what produced
    ``c`` -- and changing ``a`` must not move ``c``'s result directory.
    """
    ids = {}
    for algo in ['x', 'y']:
        node_a = ProcessNode(
            name='node_a',
            executable='python a.py',
            out_paths={'out_fpath': 'a.json'},
            algo_params={'algo': algo},
        )
        node_b = ProcessNode(
            name='node_b',
            executable='python b.py',
            in_paths={'in_fpath'},
            out_paths={'out_fpath': 'b.json'},
        )
        node_c = ProcessNode(
            name='node_c',
            executable='python c.py',
            in_paths={'in_fpath'},
            out_paths={'out_fpath': 'c.json'},
        )
        node_a.outputs['out_fpath'].connect(node_b.inputs['in_fpath'])
        node_b.outputs['out_fpath'].connect(node_c.inputs['in_fpath'])
        dag = Pipeline({'node_a': node_a, 'node_b': node_b, 'node_c': node_c})
        dag.configure(
            {'node_b.in_fpath': '/precomputed/b-input', 'node_a.algo': algo},
            root_dpath=tmp_path,
            cache=False,
        )
        ids[algo] = node_c.process_id
        last = node_c

    assert sorted(n.name for n in last.effective_ancestor_process_nodes()) == [
        'node_b'
    ]
    assert sorted(n.name for n in last.ancestor_process_nodes()) == [
        'node_a',
        'node_b',
    ]
    assert ids['x'] == ids['y']


def test_overriding_a_gathered_manifest_alias_is_a_conflict(tmp_path):
    """
    Borrowing a gather manifest *and* configuring the borrowing port is a
    contradiction, and the established conflict check already catches it.
    Pinned here so the gather-alias work cannot quietly turn it into a silent
    precedence win.
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
    audit = ProcessNode(
        name='audit',
        executable='python audit.py',
        in_paths={'parts_fpath'},
        out_paths={'audit_fpath': 'audit.json'},
    )
    shard.outputs['part_fpath'].connect(
        merge.inputs['parts_fpath'],
        gather=GatherSpec(group_by=['dataset'], order_by=['fold']),
    )
    merge.inputs['parts_fpath'].connect(audit.inputs['parts_fpath'])
    dag = Pipeline({'shard': shard, 'merge': merge, 'audit': audit})
    rows = [
        {
            'shard.dataset': 'a',
            'shard.fold': fold,
            'merge.dataset': 'a',
            'audit.parts_fpath': '/precomputed/manifest.txt',
        }
        for fold in [0, 1]
    ]
    with pytest.raises(ValueError, match='Conflicting explicit and shared'):
        dag.compile_configurations(rows, root_dpath=tmp_path, cache=False)


def test_an_alias_forwarding_the_value_already_configured_is_not_unsupplied(
    tmp_path,
):
    """
    ``supplied: false`` means "wired, but something else provided the value".
    An alias that forwards exactly the value the target also declares did
    supply it, so the flag must not appear -- otherwise it degrades into
    noise on every same-valued forward.
    """
    lender = ProcessNode(
        name='lender',
        executable='python lender.py',
        in_paths={'data_fpath'},
        out_paths={'lender_fpath': 'lender.json'},
    )
    consumer = ProcessNode(
        name='consumer',
        executable='python consumer.py',
        in_paths={'data_fpath'},
        out_paths={'result_fpath': 'result.json'},
    )
    lender.inputs['data_fpath'].connect(consumer.inputs['data_fpath'])
    dag = Pipeline({'lender': lender, 'consumer': consumer})
    dag.configure(
        {
            'lender.data_fpath': '/same/value.json',
            'consumer.data_fpath': '/same/value.json',
        },
        root_dpath=tmp_path,
        cache=False,
    )
    binding = consumer._depends_config()['__input__.data_fpath']
    assert binding['source_kind'] == 'input'
    assert 'supplied' not in binding


def test_an_alias_that_supplies_nothing_does_not_hide_one_that_does(tmp_path):
    """
    Two ports forward into the same input and only one resolves a value. The
    silent one must not be mistaken for the effective source, or the producer
    behind the other would drop out of lineage.
    """
    producer = ProcessNode(
        name='producer',
        executable='python producer.py',
        out_paths={'data_fpath': 'data.json'},
    )
    lender = ProcessNode(
        name='lender',
        executable='python lender.py',
        in_paths={'data_fpath'},
        out_paths={'lender_fpath': 'lender.json'},
    )
    silent = ProcessNode(
        name='silent',
        executable='python silent.py',
        in_paths={'data_fpath'},
        out_paths={'silent_fpath': 'silent.json'},
    )
    consumer = ProcessNode(
        name='consumer',
        executable='python consumer.py',
        in_paths={'data_fpath'},
        out_paths={'result_fpath': 'result.json'},
    )
    producer.outputs['data_fpath'].connect(lender.inputs['data_fpath'])
    lender.inputs['data_fpath'].connect(consumer.inputs['data_fpath'])
    silent.inputs['data_fpath'].connect(consumer.inputs['data_fpath'])
    dag = Pipeline(
        {
            'producer': producer,
            'lender': lender,
            'silent': silent,
            'consumer': consumer,
        }
    )
    dag.configure({}, root_dpath=tmp_path, cache=False)

    assert str(consumer.final_in_paths['data_fpath']) == str(
        producer.outputs['data_fpath'].final_value
    )
    assert [n.name for n in consumer.effective_predecessor_process_nodes()] == [
        'producer'
    ]


def test_a_gather_outranks_an_alias_into_the_same_port():
    """
    A port can be gathered into and forwarded into. The manifest wins, as the
    documented precedence says, so the alias is recorded as wired but not
    supplying -- and the collection, not the alias, carries identity.
    """
    shard = ProcessNode(
        name='shard',
        executable='python shard.py',
        out_paths={'part_fpath': 'part.txt'},
        algo_params={'dataset': 'a', 'fold': 0},
    )
    source = ProcessNode(
        name='source',
        executable='python source.py',
        out_paths={'out_fpath': 'source.json'},
    )
    lender = ProcessNode(
        name='lender',
        executable='python lender.py',
        in_paths={'data_fpath'},
        out_paths={'lender_fpath': 'lender.json'},
    )
    merge = ProcessNode(
        name='merge',
        executable='python merge.py',
        in_paths={'parts_fpath'},
        out_paths={'merged_fpath': 'merged.txt'},
        algo_params={'dataset': 'a'},
    )
    source.outputs['out_fpath'].connect(lender.inputs['data_fpath'])
    shard.outputs['part_fpath'].connect(
        merge.inputs['parts_fpath'],
        gather=GatherSpec(group_by=['dataset'], order_by=['fold']),
    )
    lender.inputs['data_fpath'].connect(merge.inputs['parts_fpath'])
    dag = Pipeline(
        {
            'shard': shard,
            'source': source,
            'lender': lender,
            'merge': merge,
        }
    )
    root = (
        ub.Path.appdir('kwdagger/tests/regressions/gather-outranks-alias')
        .delete()
        .ensuredir()
    )
    rows = [
        {'shard.dataset': 'a', 'shard.fold': fold, 'merge.dataset': 'a'}
        for fold in [0, 1]
    ]
    compiled = dag.compile_configurations(rows, root_dpath=root, cache=False)
    merge_node = [n for n in compiled.nodes.values() if n.name == 'merge'][0]

    gathered = merge_node.inputs['parts_fpath']
    assert str(merge_node.final_in_paths['parts_fpath']) == str(
        gathered.gather_manifest_fpath
    )
    binding = merge_node._depends_config()['__input__.parts_fpath']
    assert binding['source_kind'] == 'input'
    assert binding['supplied'] is False
    # Identity comes from the collection, not from the outranked alias.
    assert '__gather__.parts_fpath' in merge_node.depends
    assert '__input__.parts_fpath' not in merge_node.depends
