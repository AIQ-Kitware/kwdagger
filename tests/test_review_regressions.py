"""Regression tests for issues found while reviewing ``v0.2.6..HEAD``."""

import os

import networkx as nx

from kwdagger.pipeline import GatherSpec, Pipeline, ProcessNode


def test_produced_value_forwarded_through_alias_keeps_dependency(tmp_path):
    """An input alias must not erase the producer behind the shared value."""
    producer = ProcessNode(
        name='producer',
        executable='python producer.py',
        out_paths={'data_fpath': 'data.json'},
        algo_params={'revision': 1},
    )
    middle = ProcessNode(
        name='middle',
        executable='python middle.py',
        in_paths={'data_fpath'},
        out_paths={'marker_fpath': 'marker.json'},
    )
    consumer = ProcessNode(
        name='consumer',
        executable='python consumer.py',
        in_paths={'data_fpath'},
        out_paths={'result_fpath': 'result.json'},
    )
    producer.outputs['data_fpath'].connect(middle.inputs['data_fpath'])
    middle.inputs['data_fpath'].connect(consumer.inputs['data_fpath'])
    dag = Pipeline([producer, middle, consumer])
    dag.configure({'producer.revision': 1}, root_dpath=tmp_path, cache=False)

    assert os.fspath(consumer.final_in_paths['data_fpath']) == os.fspath(
        producer.outputs['data_fpath'].final_value
    )
    assert producer in consumer.ancestor_process_nodes()
    assert nx.has_path(dag.proc_graph, 'producer', 'consumer')


def _find_gather_records(value, source):
    """Recursively find serialized gather records from a particular port."""
    found = []
    if isinstance(value, dict):
        if value.get('source') == source and isinstance(
            value.get('members'), list
        ):
            found.append(value)
        for item in value.values():
            found.extend(_find_gather_records(item, source))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.extend(_find_gather_records(item, source))
    return found


def test_nested_gather_provenance_keeps_each_consumer_instance(tmp_path):
    """Same-named gather consumers must not overwrite one another."""
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
    report = ProcessNode(
        name='report',
        executable='python report.py',
        in_paths={'merged_items_fpath'},
        out_paths={'report_fpath': 'report.json'},
    )
    shard.outputs['part_fpath'].connect(
        merge.inputs['parts_fpath'],
        gather=GatherSpec(group_by=['dataset'], order_by=['fold']),
    )
    merge.outputs['merged_fpath'].connect(
        report.inputs['merged_items_fpath'],
        gather=GatherSpec(group_by=[], order_by=['dataset']),
    )
    dag = Pipeline([shard, merge, report])
    rows = [
        {
            'shard.dataset': dataset,
            'shard.fold': fold,
            'merge.dataset': dataset,
        }
        for dataset in ['a', 'b']
        for fold in [0, 1]
    ]
    compiled = dag.compile_configurations(
        rows, root_dpath=tmp_path, cache=False
    )
    merges = [node for node in compiled.nodes.values() if node.name == 'merge']
    reports = [
        node for node in compiled.nodes.values() if node.name == 'report'
    ]
    assert len(merges) == 2
    assert len(reports) == 1

    expected_member_sets = {
        frozenset(
            member.parent.process_id
            for member in node.inputs['parts_fpath']._gather_members
        )
        for node in merges
    }
    record = reports[0]._depends_config()
    nested_records = _find_gather_records(record, 'shard.part_fpath')
    observed_member_sets = {
        frozenset(member['process_id'] for member in item['members'])
        for item in nested_records
    }
    assert observed_member_sets == expected_member_sets


def test_qualified_group_by_accepts_mapping_values(tmp_path):
    """Structured parameter values should be canonicalized before grouping."""
    train = ProcessNode(
        name='train',
        executable='python train.py',
        out_paths={'checkpoint_fpath': 'checkpoint.pt'},
        algo_params={'settings': {}, 'fold': 0},
    )
    collect = ProcessNode(
        name='collect',
        executable='python collect.py',
        in_paths={'checkpoints_fpath'},
        out_paths={'result_fpath': 'result.json'},
        algo_params={'settings': {}},
    )
    train.outputs['checkpoint_fpath'].connect(
        collect.inputs['checkpoints_fpath'],
        gather=GatherSpec(
            group_by=[{'src': 'train.settings', 'dst': 'collect.settings'}],
            order_by=['fold'],
        ),
    )
    dag = Pipeline([train, collect])
    settings = {
        'family': 'cnn',
        'thresholds': {'low': 0.2, 'high': 0.8},
        'labels': ['cat', 'dog'],
    }
    rows = [
        {
            'train.settings': settings,
            'train.fold': fold,
            'collect.settings': settings,
        }
        for fold in [0, 1]
    ]

    compiled = dag.compile_configurations(
        rows, root_dpath=tmp_path, cache=False
    )
    collectors = [
        node for node in compiled.nodes.values() if node.name == 'collect'
    ]
    assert len(collectors) == 1
    assert len(collectors[0].inputs['checkpoints_fpath']._gather_members) == 2


def test_gather_provenance_uses_public_group_by_shape(tmp_path):
    """A provenance record should round-trip through ``GatherSpec.coerce``."""
    predict = ProcessNode(
        name='predict',
        executable='python predict.py',
        in_paths={'dataset_fpath'},
        out_paths={'pred_fpath': 'pred.json'},
        algo_params={'model': 'resnet'},
    )
    score = ProcessNode(
        name='score',
        executable='python score.py',
        in_paths={'truth_fpath', 'preds_fpath'},
        out_paths={'score_fpath': 'score.json'},
    )
    spec = GatherSpec(
        group_by=[{'src': 'dataset_fpath', 'dst': 'truth_fpath'}],
        order_by=['model'],
    )
    predict.outputs['pred_fpath'].connect(
        score.inputs['preds_fpath'], gather=spec
    )
    dag = Pipeline([predict, score])
    rows = [
        {
            'predict.dataset_fpath': '/data/items.json',
            'predict.model': model,
            'score.truth_fpath': '/data/items.json',
        }
        for model in ['resnet', 'vit']
    ]
    compiled = dag.compile_configurations(
        rows, root_dpath=tmp_path, cache=False
    )
    scorer = next(
        node for node in compiled.nodes.values() if node.name == 'score'
    )
    record = scorer._depends_config()['__gather__.preds_fpath']

    serialized_spec = {
        'group_by': record['group_by'],
        'order_by': record['order_by'],
        'require': record['require'],
    }
    assert record['group_by'] == spec.to_dict()['group_by']
    assert GatherSpec.coerce(serialized_spec) == spec
