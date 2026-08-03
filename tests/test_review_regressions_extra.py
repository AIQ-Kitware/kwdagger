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
  a sequence of the same contents.

It also covers the complements -- what must *not* change -- which is where the
risk of the alias fix actually lives: a pure configuration alias stays
configuration-only and keeps its operational identity.

Each defect is observable through an established surface -- execution lineage,
``job_config.json`` provenance, or the public :class:`GatherSpec` schema -- so
every test here asserts against those surfaces rather than against internals
that a refactor is allowed to move.
"""

from __future__ import annotations

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

    # Identity: the producer speaks for itself in the consumer's depends.
    assert 'producer' in consumer.depends

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
    root = ub.Path.appdir(
        'kwdagger/tests/regressions/nested-gather'
    ).delete().ensuredir()
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
    root = ub.Path.appdir(
        'kwdagger/tests/regressions/single-gather'
    ).delete().ensuredir()
    rows = [
        row for row in _nested_gather_rows() if row['shard.model'] == 'x'
    ]
    compiled = dag.compile_configurations(rows, root_dpath=root, cache=False)
    by_name = ub.group_items(compiled.nodes.values(), key=lambda n: n.name)
    analysis = by_name['analysis'][0]
    record = analysis._depends_config()

    ancestor_gather = record['__gather__.merge.shard_rows_fpath']
    assert isinstance(ancestor_gather, dict)
    assert ancestor_gather['source'] == 'shard.rows_fpath'
    assert (
        ancestor_gather['consumer_process_id']
        == by_name['merge'][0].process_id
    )


# ---------------------------------------------------------------------------
# 3. Structured grouping values must canonicalize
# ---------------------------------------------------------------------------


def test_hashable_group_value_handles_nested_containers():
    value = {'b': [1, {'c': ub.Path('/tmp/x')}], 'a': (2, 3)}
    canonical = _hashable_group_value(value)
    assert hash(canonical) is not None
    assert {canonical} == {_hashable_group_value(dict(reversed(list(
        value.items()))))}, 'mapping order must not matter'

    # Structurally different container kinds must not collide.
    assert _hashable_group_value([1, 2]) != _hashable_group_value({1, 2})
    assert _hashable_group_value({'a': 1}) != _hashable_group_value(
        [('a', 1)]
    )

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
    root = ub.Path.appdir(
        'kwdagger/tests/regressions/structured-group'
    ).delete().ensuredir()
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
    root = ub.Path.appdir(
        'kwdagger/tests/regressions/public-schema'
    ).delete().ensuredir()
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
    root = ub.Path.appdir(
        'kwdagger/tests/regressions/plain-schema'
    ).delete().ensuredir()
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
