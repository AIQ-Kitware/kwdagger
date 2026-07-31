"""Regression tests for non-materialized shared configuration values."""

import json

import pytest

from kwdagger.pipeline import (
    GatherSpec,
    Pipeline,
    ProcessNode,
    _node_param_value,
)


def _input_alias_pipeline(*, reverse=False):
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
    nodes = (
        {'consumer': consumer, 'source': source}
        if reverse
        else {'source': source, 'consumer': consumer}
    )
    return Pipeline(nodes), source, consumer


def _parameter_pipeline(*, reverse=False):
    source = ProcessNode(
        name='source',
        executable='python source.py',
        out_paths={'source_fpath': 'source.json'},
        algo_params={'family': 'cnn'},
    )
    consumer = ProcessNode(
        name='consumer',
        executable='python consumer.py',
        out_paths={'result_fpath': 'result.json'},
        algo_params={'family': 'cnn'},
    )
    source.param_ports['family'].connect(consumer.param_ports['family'])
    nodes = (
        {'consumer': consumer, 'source': source}
        if reverse
        else {'source': source, 'consumer': consumer}
    )
    return Pipeline(nodes), source, consumer


def test_input_alias_is_ordered_for_configuration_not_execution(tmp_path):
    dag, source, consumer = _input_alias_pipeline(reverse=True)
    dag.configure(
        {'source.data_fpath': '/data/current.json'},
        root_dpath=tmp_path,
        cache=False,
    )

    assert consumer.final_in_paths['data_fpath'] == '/data/current.json'
    assert source not in consumer.predecessor_process_nodes()
    assert not dag.proc_graph.has_edge('source', 'consumer')
    assert dag.config_graph.has_edge('source', 'consumer')


def test_parameter_connection_is_ordered_for_configuration_not_execution(
    tmp_path,
):
    dag, source, consumer = _parameter_pipeline(reverse=True)
    dag.configure(
        {'source.family': 'transformer'},
        root_dpath=tmp_path,
        cache=False,
    )

    assert consumer.final_algo_config['family'] == 'transformer'
    assert '--family=transformer' in consumer.command
    assert source not in consumer.predecessor_process_nodes()
    assert not dag.proc_graph.has_edge('source', 'consumer')
    assert dag.config_graph.has_edge('source', 'consumer')


def test_shared_parameter_forwards_declared_default(tmp_path):
    dag, _source, consumer = _parameter_pipeline(reverse=True)
    dag.configure({}, root_dpath=tmp_path, cache=False)
    assert consumer.final_algo_config['family'] == 'cnn'
    assert '--family=cnn' in consumer.command


def test_shared_values_do_not_leak_between_rows(tmp_path):
    dag, _source, consumer = _parameter_pipeline(reverse=True)
    dag.configure(
        {'source.family': 'transformer'},
        root_dpath=tmp_path,
        cache=False,
    )
    assert consumer.final_algo_config['family'] == 'transformer'

    dag.configure({}, root_dpath=tmp_path, cache=False)
    assert consumer.final_algo_config['family'] == 'cnn'

    alias_dag, _source, alias_consumer = _input_alias_pipeline(reverse=True)
    alias_dag.configure(
        {'source.data_fpath': '/data/first.json'},
        root_dpath=tmp_path,
        cache=False,
    )
    assert alias_consumer.final_in_paths['data_fpath'] == '/data/first.json'

    alias_dag.configure({}, root_dpath=tmp_path, cache=False)
    assert (
        alias_consumer.final_in_paths['data_fpath']
        == '/data/default.json'
    )


@pytest.mark.parametrize('kind', ['input', 'parameter'])
def test_shared_values_reject_conflicting_explicit_targets(tmp_path, kind):
    if kind == 'input':
        dag, _source, _consumer = _input_alias_pipeline(reverse=True)
        config = {
            'source.data_fpath': '/data/source.json',
            'consumer.data_fpath': '/data/other.json',
        }
    else:
        dag, _source, _consumer = _parameter_pipeline(reverse=True)
        config = {
            'source.family': 'cnn',
            'consumer.family': 'transformer',
        }

    with pytest.raises(ValueError, match='Conflicting explicit and shared'):
        dag.configure(config, root_dpath=tmp_path, cache=False)


def test_matching_explicit_shared_values_are_allowed(tmp_path):
    dag, _source, consumer = _parameter_pipeline(reverse=True)
    dag.configure(
        {'source.family': 'cnn', 'consumer.family': 'cnn'},
        root_dpath=tmp_path,
        cache=False,
    )
    assert consumer.final_algo_config['family'] == 'cnn'


def test_same_node_shared_values_are_allowed(tmp_path):
    node = ProcessNode(
        name='node',
        executable='python node.py',
        out_paths={'result_fpath': 'result.json'},
        algo_params={'canonical': 'cnn', 'alias': 'cnn'},
    )
    node.param_ports['canonical'].connect(node.param_ports['alias'])
    dag = Pipeline({'node': node})
    dag.configure(
        {'node.canonical': 'transformer'},
        root_dpath=tmp_path,
        cache=False,
    )
    assert node.final_algo_config['alias'] == 'transformer'


def test_shared_value_cycles_are_rejected():
    left = ProcessNode(
        name='left',
        executable='left',
        out_paths={'out': 'left.txt'},
        algo_params={'value': 1},
    )
    right = ProcessNode(
        name='right',
        executable='right',
        out_paths={'out': 'right.txt'},
        algo_params={'value': 1},
    )
    left.param_ports['value'].connect(right.param_ports['value'])
    right.param_ports['value'].connect(left.param_ports['value'])
    with pytest.raises(ValueError, match='shared-value relationships.*cycle'):
        Pipeline({'left': left, 'right': right})


def test_direct_and_aliased_inputs_reuse_the_same_process(tmp_path):
    direct = ProcessNode(
        name='consumer',
        executable='python consumer.py',
        in_paths={'data_fpath'},
        out_paths={'result_fpath': 'result.json'},
        algo_params={'mode': 'fast'},
    )
    direct_dag = Pipeline({'consumer': direct})
    direct_dag.configure(
        {
            'consumer.data_fpath': '/data/items.json',
            'consumer.mode': 'fast',
        },
        root_dpath=tmp_path,
        cache=False,
    )

    source = ProcessNode(
        name='source',
        executable='python source.py',
        in_paths={'data_fpath'},
        out_paths={'marker_fpath': 'marker.json'},
    )
    aliased = ProcessNode(
        name='consumer',
        executable='python consumer.py',
        in_paths={'data_fpath'},
        out_paths={'result_fpath': 'result.json'},
        algo_params={'mode': 'fast'},
    )
    source.inputs['data_fpath'].connect(aliased.inputs['data_fpath'])
    alias_dag = Pipeline({'consumer': aliased, 'source': source})
    alias_dag.configure(
        {'source.data_fpath': '/data/items.json', 'consumer.mode': 'fast'},
        root_dpath=tmp_path,
        cache=False,
    )

    assert aliased.command == direct.command
    assert aliased.algo_id == direct.algo_id
    assert aliased.process_id == direct.process_id
    assert aliased.final_node_dpath == direct.final_node_dpath


def test_shared_values_are_recorded_without_process_lineage(tmp_path):
    alias_dag, source, consumer = _input_alias_pipeline(reverse=True)
    input_fpath = tmp_path / 'items.json'
    alias_dag.configure(
        {'source.data_fpath': input_fpath},
        root_dpath=tmp_path,
        cache=False,
    )
    config = consumer._depends_config()
    binding = config['__input__.data_fpath']
    assert config['consumer.data_fpath'] == str(input_fpath)
    assert binding['source'] == 'source.data_fpath'
    assert binding['target'] == 'consumer.data_fpath'
    assert binding['value'] == str(input_fpath)
    assert 'source_process_id' not in binding
    assert source not in consumer.ancestor_process_nodes()

    param_dag, source, consumer = _parameter_pipeline(reverse=True)
    param_dag.configure(
        {'source.family': 'transformer'},
        root_dpath=tmp_path,
        cache=False,
    )
    config = consumer._depends_config()
    binding = config['__parameter__.family']
    assert config['consumer.family'] == 'transformer'
    assert binding['source'] == 'source.family'
    assert binding['target'] == 'consumer.family'
    assert binding['value'] == 'transformer'
    assert source not in consumer.ancestor_process_nodes()

    # The bookkeeping artifact must remain directly JSON serializable even
    # when shared values are path-like objects.
    json.dumps(alias_dag.node_dict['consumer']._depends_config())
    json.dumps(param_dag.node_dict['consumer']._depends_config())


def test_dependency_only_edges_include_concrete_predecessor_identity(tmp_path):
    prepare = ProcessNode(
        name='prepare',
        executable='python prepare.py',
        in_paths={'data_fpath'},
        out_paths={'prepared_fpath': 'prepared.json'},
    )
    summarize = ProcessNode(
        name='summarize',
        executable='python summarize.py',
        out_paths={'summary_fpath': 'summary.json'},
    )
    summarize._pred_nodes_without_io_connection.append(prepare)
    dag = Pipeline({'summarize': summarize, 'prepare': prepare})

    child_ids = []
    predecessor_ids = []
    for data_fpath in ['/data/a.json', '/data/b.json']:
        dag.configure(
            {'prepare.data_fpath': data_fpath},
            root_dpath=tmp_path,
            cache=False,
        )
        child_ids.append(summarize.process_id)
        predecessor_ids.append(prepare.process_id)
        assert (
            summarize.depends['__dependency__.prepare']
            == prepare.process_id
        )

    assert len(set(predecessor_ids)) == 2
    assert len(set(child_ids)) == 2


def test_parameter_ports_are_visible_in_the_io_graph():
    dag, _source, _consumer = _parameter_pipeline(reverse=True)
    assert 'source.family' in dag.io_graph
    assert 'consumer.family' in dag.io_graph
    edge = dag.io_graph.edges['source.family', 'consumer.family']
    assert edge['shared_kind'] == 'parameter'
    display = dag._io_display_graph(shrink_labels=0, show_types=1)
    labels = [data.get('label', '') for _, data in display.nodes(data=True)]
    assert any('shared parameter' in label for label in labels)


def _gather_with_shared_parameter(reverse=False):
    label = ProcessNode(
        name='label',
        executable='python label.py',
        out_paths={'label_fpath': 'label.json'},
        algo_params={'family': 'cnn'},
    )
    train = ProcessNode(
        name='train',
        executable='python train.py',
        out_paths={'checkpoint_fpath': 'checkpoint.pt'},
        algo_params={'family': 'cnn', 'fold': 0},
    )
    collect = ProcessNode(
        name='collect',
        executable='python collect.py',
        in_paths={'checkpoints_fpath'},
        out_paths={'result_fpath': 'result.json'},
        algo_params={'family': 'cnn'},
    )
    label.param_ports['family'].connect(train.param_ports['family'])
    label.param_ports['family'].connect(collect.param_ports['family'])
    train.outputs['checkpoint_fpath'].connect(
        collect.inputs['checkpoints_fpath'],
        gather=GatherSpec(group_by=['family'], order_by=['fold']),
    )
    nodes = {'collect': collect, 'train': train, 'label': label}
    if not reverse:
        nodes = {'label': label, 'train': train, 'collect': collect}
    return Pipeline(nodes)


def test_gather_compiler_uses_shared_value_configuration_order(tmp_path):
    dag = _gather_with_shared_parameter(reverse=True)
    rows = [
        {'label.family': 'cnn', 'train.fold': 0},
        {'label.family': 'cnn', 'train.fold': 1},
    ]
    compiled = dag.compile_configurations(
        rows, root_dpath=tmp_path, cache=False
    )
    collectors = [
        node for node in compiled.nodes.values() if node.name == 'collect'
    ]
    assert len(collectors) == 1
    collector = collectors[0]
    assert collector.final_algo_config['family'] == 'cnn'
    assert len(collector.inputs['checkpoints_fpath']._gather_members) == 2


def test_qualified_group_key_checks_all_same_named_ancestors(tmp_path):
    train = ProcessNode(
        name='train',
        executable='python train.py',
        out_paths={'checkpoint_fpath': 'checkpoint.pt'},
        algo_params={'dataset': 'demo', 'fold': 0},
    )
    ensemble = ProcessNode(
        name='ensemble',
        executable='python ensemble.py',
        in_paths={'checkpoints_fpath'},
        out_paths={'model_fpath': 'model.pt'},
        algo_params={'dataset': 'demo'},
    )
    train.outputs['checkpoint_fpath'].connect(
        ensemble.inputs['checkpoints_fpath'],
        gather=GatherSpec(group_by=['dataset'], order_by=['fold']),
    )
    dag = Pipeline({'ensemble': ensemble, 'train': train})
    compiled = dag.compile_configurations(
        [
            {'train.dataset': 'demo', 'train.fold': 0},
            {'train.dataset': 'demo', 'train.fold': 1},
        ],
        root_dpath=tmp_path,
        cache=False,
    )
    ensemble = next(
        node for node in compiled.nodes.values() if node.name == 'ensemble'
    )

    assert _node_param_value(ensemble, 'train.dataset') == 'demo'
    with pytest.raises(ValueError, match='concrete.*ancestors disagree'):
        _node_param_value(ensemble, 'train.fold')
