"""Tests for compile-time gather edges."""

from __future__ import annotations

import json

import pytest
import ubelt as ub

import kwdagger
from kwdagger import schedule
from kwdagger.pipeline import (
    GatherSpec,
    Pipeline,
    ProcessNode,
    bash_heredoc_write_command,
)


def _demo_gather_pipeline():
    train = ProcessNode(
        name='train',
        executable='python train.py',
        in_paths={'data_fpath'},
        out_paths={'checkpoint_fpath': 'checkpoint.txt'},
        algo_params={'algorithm': 'linear', 'seed': 0, 'fold': 0},
    )
    ensemble = ProcessNode(
        name='ensemble',
        executable='python ensemble.py',
        in_paths={'checkpoints_fpath'},
        out_paths={'ensemble_fpath': 'ensemble.txt'},
        algo_params={'algorithm': 'linear', 'seed': 0},
    )
    evaluate = ProcessNode(
        name='evaluate',
        executable='python evaluate.py',
        in_paths={'ensemble_fpath'},
        out_paths={'metrics_fpath': 'metrics.json'},
        algo_params={'algorithm': 'linear', 'seed': 0, 'test_set': 'clean'},
    )
    train.outputs['checkpoint_fpath'].connect(
        ensemble.inputs['checkpoints_fpath'],
        gather=GatherSpec(
            group_by=['algorithm', 'seed'],
            order_by=['fold'],
            require='all_success',
        ),
    )
    ensemble.outputs['ensemble_fpath'].connect(
        evaluate.inputs['ensemble_fpath']
    )
    return Pipeline(
        {'train': train, 'ensemble': ensemble, 'evaluate': evaluate}
    )


def _demo_rows(data_fpath='data.txt'):
    rows = []
    for algorithm in ['linear', 'forest']:
        for seed in [0, 1]:
            for fold in [0, 1, 2]:
                for test_set in ['clean', 'shifted']:
                    rows.append(
                        {
                            'train.data_fpath': data_fpath,
                            'train.algorithm': algorithm,
                            'train.seed': seed,
                            'train.fold': fold,
                            'ensemble.algorithm': algorithm,
                            'ensemble.seed': seed,
                            'evaluate.algorithm': algorithm,
                            'evaluate.seed': seed,
                            'evaluate.test_set': test_set,
                        }
                    )
    return rows


def test_gather_template_graphs_make_collection_edges_visible():
    dag = _demo_gather_pipeline()
    process_graph = dag._process_display_graph()
    process_labels = [
        data.get('label', '') for _, data in process_graph.nodes(data=True)
    ]
    assert any('gather N:1' in label for label in process_labels)
    assert any('group_by=algorithm,seed' in label for label in process_labels)
    assert not any(
        data['node'].name == 'gather'
        for _, data in process_graph.nodes(data=True)
        if 'node' in data
    )

    io_graph = dag._io_display_graph()
    io_labels = [data.get('label', '') for _, data in io_graph.nodes(data=True)]
    assert any('gather N:1 -> path manifest' in label for label in io_labels)
    assert any('(collection)' in label for label in io_labels)


def test_quoted_heredoc_avoids_argv_expansion(tmp_path):
    import subprocess

    # This is intentionally much larger than a comfortable command argument
    # list. It remains script input rather than argv passed to ``cat``.
    lines = [f'/tmp/model path/{idx:06d}/checkpoint.pkl' for idx in range(20000)]
    lines.extend(
        [
            r'/tmp/$HOME/checkpoint.pkl',
            r'/tmp/$(touch should-not-run)/checkpoint.pkl',
            r'/tmp/back\slash/checkpoint.pkl',
            '\\',
        ]
    )
    text = ''.join(line + '\n' for line in lines)
    output_fpath = tmp_path / 'large gather manifest.txt'
    command = bash_heredoc_write_command(
        text, output_fpath, label='KWDAGGER_GATHER_TEST'
    )
    assert "<<'KWDAGGER_GATHER_TEST_" in command
    assert 'printf ' not in command
    assert lines[0] in command
    assert lines[-1] in command

    script_fpath = tmp_path / 'write_manifest.sh'
    script_fpath.write_text('#!/bin/bash\nset -e\n' + command + '\n')
    subprocess.run(['bash', '-n', script_fpath], check=True)
    subprocess.run(['bash', script_fpath], check=True)
    assert output_fpath.read_text() == text


def test_gather_python_compile_static_graph():
    dag = _demo_gather_pipeline()
    root = ub.Path.appdir('kwdagger/tests/gather/compile').delete().ensuredir()
    compiled = dag.compile_configurations(
        _demo_rows(), root_dpath=root, cache=False
    )

    nodes_by_name = ub.group_items(
        compiled.nodes.values(), key=lambda node: node.name
    )
    assert len(nodes_by_name['train']) == 12
    assert len(nodes_by_name['ensemble']) == 4
    assert len(nodes_by_name['evaluate']) == 8
    assert compiled.compile_summary == {
        'concrete_nodes': 24,
        'collection_groups': 4,
        'collection_memberships': 12,
        'largest_collection': 3,
    }

    for ensemble in nodes_by_name['ensemble']:
        gathered = ensemble.inputs['checkpoints_fpath']
        members = gathered._gather_members
        assert members is not None
        assert [m.parent.final_algo_config['fold'] for m in members] == [
            0,
            1,
            2,
        ]
        assert gathered.gather_manifest_fpath.parent.name == '_gather'
        assert gathered.gather_manifest_text().count('\n') == 3
        command = ensemble._raw_command()
        assert '--checkpoints_fpath=' in command
        assert str(gathered.gather_manifest_fpath) in command
        final_command = ensemble.final_command()
        assert final_command.startswith('{\n')
        assert '\n    mkdir -p -- ' in final_command
        assert '\n    cat > ' in final_command
        assert ' &&\n' in final_command
        assert not final_command.startswith('(\n')
        gather_info = ensemble._depends_config()[
            '__gather__.checkpoints_fpath'
        ]
        assert gather_info['group_by'] == ['algorithm', 'seed']
        assert len(gather_info['members']) == 3

    # The graph is static and includes all fan-in / fan-out edges.
    assert len(compiled.proc_graph.edges()) == 20

    records = compiled._edge_cardinality_records()
    gather_record = ub.peek(
        [record for record in records if record['kind'] == 'gather']
    )
    fanout_record = ub.peek(
        [
            record
            for record in records
            if record['source'] == 'ensemble'
            and record['target'] == 'evaluate'
        ]
    )
    assert gather_record['relation'] == 'gather 3:1'
    assert gather_record['source_count'] == 12
    assert gather_record['target_count'] == 4
    assert fanout_record['relation'] == 'fan-out 1:2'

    cardinality_labels = [
        data.get('label', '')
        for _, data in compiled._cardinality_display_graph().nodes(data=True)
    ]
    assert any('gather 3:1' in label for label in cardinality_labels)
    assert any('fan-out 1:2' in label for label in cardinality_labels)


def test_gather_yaml_round_trip():
    spec = {
        'nodes': {
            'train': {
                'executable': 'python train.py',
                'in_paths': ['data_fpath'],
                'out_paths': {'checkpoint_fpath': 'checkpoint.txt'},
                'algo_params': {'algorithm': 'linear', 'seed': 0, 'fold': 0},
            },
            'ensemble': {
                'executable': 'python ensemble.py',
                'in_paths': ['checkpoints_fpath'],
                'out_paths': {'ensemble_fpath': 'ensemble.txt'},
                'algo_params': {'algorithm': 'linear', 'seed': 0},
            },
        },
        'edges': [
            {
                'src': 'train.checkpoint_fpath',
                'dst': 'ensemble.checkpoints_fpath',
                'gather': {
                    'group_by': ['algorithm', 'seed'],
                    'order_by': ['fold'],
                    'require': 'all_success',
                },
            }
        ],
    }
    dag = kwdagger.load_yaml_pipeline(spec)
    connection = dag.gather_connections[0]
    assert connection.spec == GatherSpec(
        group_by=['algorithm', 'seed'], order_by=['fold']
    )
    assert list(dag.proc_graph.edges()) == [('train', 'ensemble')]

    dumped = dag.to_yaml_spec()
    gather_edge = ub.peek(dumped['edges'])
    assert gather_edge['src'] == 'train.checkpoint_fpath'
    assert gather_edge['dst'] == 'ensemble.checkpoints_fpath'
    assert gather_edge['gather']['group_by'] == ['algorithm', 'seed']
    assert gather_edge['gather']['order_by'] == ['fold']

    reloaded = kwdagger.load_yaml_pipeline(dumped)
    assert reloaded.gather_connections[0].spec == connection.spec


def _write_gather_demo_script(dpath):
    script_fpath = dpath / 'gather_demo.py'
    script_fpath.write_text(
        ub.codeblock(
            r'''
            import argparse
            import json
            from pathlib import Path

            parser = argparse.ArgumentParser()
            subparsers = parser.add_subparsers(dest='command', required=True)

            train = subparsers.add_parser('train')
            train.add_argument('--data_fpath')
            train.add_argument('--algorithm')
            train.add_argument('--seed', type=int)
            train.add_argument('--fold', type=int)
            train.add_argument('--checkpoint_fpath')

            ensemble = subparsers.add_parser('ensemble')
            ensemble.add_argument('--algorithm')
            ensemble.add_argument('--seed', type=int)
            ensemble.add_argument('--checkpoints_fpath')
            ensemble.add_argument('--ensemble_fpath')

            evaluate = subparsers.add_parser('evaluate')
            evaluate.add_argument('--algorithm')
            evaluate.add_argument('--seed', type=int)
            evaluate.add_argument('--test_set')
            evaluate.add_argument('--ensemble_fpath')
            evaluate.add_argument('--metrics_fpath')

            args = parser.parse_args()
            if args.command == 'train':
                dst = Path(args.checkpoint_fpath)
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(f'{args.algorithm}:seed={args.seed}:fold={args.fold}\n')
            elif args.command == 'ensemble':
                paths = [Path(p) for p in Path(args.checkpoints_fpath).read_text().splitlines()]
                dst = Path(args.ensemble_fpath)
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(''.join(path.read_text() for path in paths))
            elif args.command == 'evaluate':
                lines = Path(args.ensemble_fpath).read_text().splitlines()
                dst = Path(args.metrics_fpath)
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(json.dumps({
                    'algorithm': args.algorithm,
                    'seed': args.seed,
                    'test_set': args.test_set,
                    'members': lines,
                }))
            '''
        )
    )
    return script_fpath


def test_gather_yaml_schedule_end_to_end():
    dpath = ub.Path.appdir('kwdagger/tests/gather/e2e').delete().ensuredir()
    script_fpath = _write_gather_demo_script(dpath)
    data_fpath = dpath / 'data.txt'
    data_fpath.write_text('demo')
    root_dpath = dpath / 'runs'

    pipeline = {
        'nodes': {
            'train': {
                'executable': f'python {script_fpath} train',
                'in_paths': ['data_fpath'],
                'out_paths': {'checkpoint_fpath': 'checkpoint.txt'},
                'algo_params': {'algorithm': 'linear', 'seed': 0, 'fold': 0},
            },
            'ensemble': {
                'executable': f'python {script_fpath} ensemble',
                'in_paths': ['checkpoints_fpath'],
                'out_paths': {'ensemble_fpath': 'ensemble.txt'},
                'algo_params': {'algorithm': 'linear', 'seed': 0},
            },
            'evaluate': {
                'executable': f'python {script_fpath} evaluate',
                'in_paths': ['ensemble_fpath'],
                'out_paths': {'metrics_fpath': 'metrics.json'},
                'algo_params': {
                    'algorithm': 'linear',
                    'seed': 0,
                    'test_set': 'clean',
                },
            },
        },
        'edges': [
            {
                'src': 'train.checkpoint_fpath',
                'dst': 'ensemble.checkpoints_fpath',
                'gather': {
                    'group_by': ['algorithm', 'seed'],
                    'order_by': ['fold'],
                    'require': 'all_success',
                },
            },
            'ensemble.ensemble_fpath -> evaluate.ensemble_fpath',
        ],
    }
    matrices = []
    for algorithm in ['linear', 'forest']:
        for seed in [0, 1]:
            matrices.append(
                {
                    'matrix': {
                        'train.data_fpath': [str(data_fpath)],
                        'train.algorithm': [algorithm],
                        'train.seed': [seed],
                        'train.fold': [0, 1],
                        'ensemble.algorithm': [algorithm],
                        'ensemble.seed': [seed],
                        'evaluate.algorithm': [algorithm],
                        'evaluate.seed': [seed],
                        'evaluate.test_set': ['clean', 'shifted'],
                    }
                }
            )
    params = {
        'pipeline': pipeline,
        'matrices': matrices,
    }
    config = schedule.ScheduleEvaluationConfig(
        run=1,
        root_dpath=root_dpath,
        backend='serial',
        params=params,
        enable_links=0,
        cache=0,
    )
    compiled, queue = schedule.build_schedule(config)

    assert compiled.compile_summary['collection_groups'] == 4
    assert len(list(root_dpath.glob('**/train/*/checkpoint.txt'))) == 8
    ensemble_fpaths = list(root_dpath.glob('**/ensemble/*/ensemble.txt'))
    assert len(ensemble_fpaths) == 4
    assert all(
        len(path.read_text().splitlines()) == 2 for path in ensemble_fpaths
    )
    manifest_fpaths = list(root_dpath.glob('**/ensemble/*/_gather/*.txt'))
    assert len(manifest_fpaths) == 4
    assert all(
        len(path.read_text().splitlines()) == 2 for path in manifest_fpaths
    )
    metric_fpaths = list(root_dpath.glob('**/evaluate/*/metrics.json'))
    assert len(metric_fpaths) == 8
    assert all(
        len(json.loads(path.read_text())['members']) == 2
        for path in metric_fpaths
    )
    commands = queue.finalize_text()
    assert '_gather/checkpoints_fpath.txt' in commands
    assert '--checkpoints_fpath=' in commands
    assert "cat > " in commands
    assert "<<'KWDAGGER_GATHER_ENSEMBLE_CHECKPOINTS_FPATH_" in commands
    assert '# kwdagger gather:' in commands
    assert '# kwdagger bookkeeping only;' in commands
    # cmd_queue adds a logging subshell around each command. The gather command
    # begins with a brace group so that wrapping yields ``({ ... })`` rather
    # than the arithmetic syntax ``(( ... ))``.
    assert '\n({\n    # kwdagger gather:' in commands
    assert '\n((\nset -e' not in commands
    assert '\n    mkdir -p -- ' in commands

    # Gather materialization is part of the consumer's standalone invocation,
    # not hidden state prepared by kwdagger or an opaque scheduler node.
    ensemble = ub.peek(
        node for node in compiled.nodes.values() if node.name == 'ensemble'
    )
    invoke_fpath = ensemble.final_node_dpath / 'invoke.sh'
    invoke_text = invoke_fpath.read_text()
    assert '# kwdagger gather:' in invoke_text
    assert "cat > " in invoke_text
    manifest_fpath = ensemble.inputs['checkpoints_fpath'].gather_manifest_fpath
    manifest_fpath.delete()
    ensemble.final_out_paths['ensemble_fpath'].delete()
    ub.cmd(['bash', invoke_fpath], check=True)
    assert manifest_fpath.exists()
    assert ensemble.final_out_paths['ensemble_fpath'].exists()


def test_dependent_heredoc_jobs_are_not_indented():
    """cmd_queue dependency guards must preserve column-zero delimiters."""
    import subprocess

    import cmd_queue

    dag = _demo_gather_pipeline()
    root = ub.Path.appdir(
        'kwdagger/tests/gather/heredoc-indent'
    ).delete().ensuredir()
    compiled = dag.compile_configurations(
        _demo_rows(), root_dpath=root, cache=False
    )
    queue = cmd_queue.Queue.create(
        backend='serial', name='gather-heredoc-indent-test'
    )
    compiled.submit_jobs(
        queue=queue,
        enable_links=False,
        write_invocations=True,
        write_configs=True,
    )

    ensemble = ub.peek(
        node for node in compiled.nodes.values() if node.name == 'ensemble'
    )
    consumer_job = queue.named_jobs[ensemble.process_id]
    bookkeeper_job = queue.named_jobs['before_' + ensemble.process_id]
    assert consumer_job.depends
    assert bookkeeper_job.depends
    assert consumer_job.allow_indent is False
    assert bookkeeper_job.allow_indent is False

    script_fpath = queue.write()
    subprocess.run(['bash', '-n', script_fpath], check=True)


def test_gather_slurm_uses_short_file_backed_command():
    """Large gather heredocs must never be passed through sbatch --wrap."""
    import cmd_queue

    dag = _demo_gather_pipeline()
    root = ub.Path.appdir('kwdagger/tests/gather/slurm').delete().ensuredir()
    compiled = dag.compile_configurations(
        _demo_rows(), root_dpath=root, cache=False
    )
    queue = cmd_queue.Queue.create(backend='slurm', name='gather-slurm-test')
    compiled.submit_jobs(
        queue=queue,
        enable_links=False,
        write_invocations=False,
        write_configs=True,
    )

    ensembles = [
        node for node in compiled.nodes.values() if node.name == 'ensemble'
    ]
    assert ensembles
    for ensemble in ensembles:
        invoke_fpath = ensemble.final_node_dpath / 'invoke.sh'
        config_fpath = ensemble.final_node_dpath / 'job_config.json'
        assert invoke_fpath.exists()
        assert config_fpath.exists()
        invoke_text = invoke_fpath.read_text()
        assert '# kwdagger gather:' in invoke_text
        assert "cat > " in invoke_text
        assert "<<'KWDAGGER_GATHER_ENSEMBLE_CHECKPOINTS_FPATH_" in invoke_text

        # The Slurm job receives only the short file-backed command. Even when
        # write_invocations=False, gathered Slurm consumers require this
        # standalone artifact to avoid placing the manifest in --wrap argv.
        job = queue.named_jobs[ensemble.process_id]
        assert 'bash ' in job.command
        assert str(invoke_fpath) in job.command
        assert 'cat > ' not in job.command
        assert len(job.command) < 1024

    slurm_text = queue.finalize_text()
    for ensemble in ensembles:
        assert str(ensemble.final_node_dpath / 'invoke.sh') in slurm_text


def test_gather_rejects_unknown_require_policy():
    with pytest.raises(ValueError, match='all_success'):
        GatherSpec(group_by=['algorithm'], require='successful_only')


def test_gather_process_hash_includes_spec_and_membership():
    rows = _demo_rows()
    dag1 = _demo_gather_pipeline()
    dag2 = _demo_gather_pipeline()
    dag2.gather_connections[0].spec = GatherSpec(
        group_by=['algorithm', 'seed'],
        order_by=[],
    )
    root = ub.Path.appdir('kwdagger/tests/gather/hash').delete().ensuredir()
    compiled1 = dag1.compile_configurations(rows, root_dpath=root, cache=False)
    compiled2 = dag2.compile_configurations(rows, root_dpath=root, cache=False)
    ids1 = sorted(
        node.process_id
        for node in compiled1.nodes.values()
        if node.name == 'ensemble'
    )
    ids2 = sorted(
        node.process_id
        for node in compiled2.nodes.values()
        if node.name == 'ensemble'
    )
    assert ids1 != ids2

    smaller = dag1.compile_configurations(
        [row for row in rows if row['train.fold'] != 2],
        root_dpath=root,
        cache=False,
    )
    smaller_ids = sorted(
        node.process_id
        for node in smaller.nodes.values()
        if node.name == 'ensemble'
    )
    assert ids1 != smaller_ids


def test_gather_allows_global_group():
    source = ProcessNode(
        name='source',
        executable='python source.py',
        out_paths={'item': 'item.txt'},
        algo_params={'trial': 0},
    )
    summarize = ProcessNode(
        name='summarize',
        executable='python summarize.py',
        in_paths={'items'},
        out_paths={'summary': 'summary.txt'},
    )
    source.outputs['item'].connect(
        summarize.inputs['items'],
        gather=GatherSpec(group_by=[], order_by=['trial']),
    )
    dag = Pipeline({'source': source, 'summarize': summarize})
    rows = [{'source.trial': trial} for trial in range(3)]
    compiled = dag.compile_configurations(
        rows, root_dpath='runs', cache=False
    )
    summaries = [
        node
        for node in compiled.nodes.values()
        if node.name == 'summarize'
    ]
    assert len(summaries) == 1
    members = summaries[0].inputs['items']._gather_members
    assert members is not None
    assert [
        member.parent.final_algo_config['trial'] for member in members
    ] == [0, 1, 2]


def test_gather_template_cannot_submit_without_compilation():
    dag = _demo_gather_pipeline()
    with pytest.raises(RuntimeError, match='compile_configurations'):
        dag.submit_jobs()


def test_gather_rejects_string_group_by():
    with pytest.raises(TypeError, match='sequence'):
        GatherSpec(group_by='algorithm')


def test_gather_compiler_preserves_input_forwarding():
    train = ProcessNode(
        name='train',
        executable='python train.py',
        in_paths={'data_fpath'},
        out_paths={'checkpoint_fpath': 'checkpoint.txt'},
        algo_params={'algorithm': 'linear', 'fold': 0},
    )
    ensemble = ProcessNode(
        name='ensemble',
        executable='python ensemble.py',
        in_paths={'data_fpath', 'checkpoints_fpath'},
        out_paths={'ensemble_fpath': 'ensemble.txt'},
        algo_params={'algorithm': 'linear'},
    )
    train.inputs['data_fpath'].connect(ensemble.inputs['data_fpath'])
    train.outputs['checkpoint_fpath'].connect(
        ensemble.inputs['checkpoints_fpath'],
        gather=GatherSpec(
            group_by=['algorithm'],
            order_by=['fold'],
        ),
    )
    dag = Pipeline({'train': train, 'ensemble': ensemble})
    rows = [
        {
            'train.data_fpath': 'data.txt',
            'train.algorithm': 'linear',
            'train.fold': fold,
            'ensemble.algorithm': 'linear',
        }
        for fold in range(3)
    ]
    compiled = dag.compile_configurations(rows, root_dpath='runs', cache=False)
    ensembles = [
        node for node in compiled.nodes.values() if node.name == 'ensemble'
    ]
    assert len(ensembles) == 1
    assert ensembles[0].inputs['data_fpath'].final_value == 'data.txt'


def test_multiple_gathered_inputs_are_aligned():
    trial = ProcessNode(
        name='trial',
        executable='python trial.py',
        out_paths={
            'checkpoint_fpath': 'checkpoint.txt',
            'metric_fpath': 'metric.json',
        },
        algo_params={'algorithm': 'linear', 'trial': 0},
    )
    select = ProcessNode(
        name='select',
        executable='python select.py',
        in_paths={'checkpoints_fpath', 'metrics_fpath'},
        out_paths={'selected_fpath': 'selected.txt'},
        algo_params={'algorithm': 'linear'},
    )
    gather = GatherSpec(group_by=['algorithm'], order_by=['trial'])
    trial.outputs['checkpoint_fpath'].connect(
        select.inputs['checkpoints_fpath'], gather=gather
    )
    trial.outputs['metric_fpath'].connect(
        select.inputs['metrics_fpath'], gather=gather
    )
    dag = Pipeline({'trial': trial, 'select': select})
    rows = [
        {
            'trial.algorithm': 'linear',
            'trial.trial': trial_idx,
            'select.algorithm': 'linear',
        }
        for trial_idx in range(4)
    ]
    compiled = dag.compile_configurations(rows, root_dpath='runs', cache=False)
    selectors = [
        node for node in compiled.nodes.values() if node.name == 'select'
    ]
    assert len(selectors) == 1
    selector = selectors[0]
    checkpoint_members = selector.inputs['checkpoints_fpath']._gather_members
    metric_members = selector.inputs['metrics_fpath']._gather_members
    assert checkpoint_members is not None
    assert metric_members is not None
    assert [m.parent.process_id for m in checkpoint_members] == [
        m.parent.process_id for m in metric_members
    ]


def test_gather_compiler_preserves_dependency_only_edges():
    prepare = ProcessNode(
        name='prepare',
        executable='python prepare.py',
        out_paths={'ready_fpath': 'ready.txt'},
    )
    trial = ProcessNode(
        name='trial',
        executable='python trial.py',
        out_paths={'item_fpath': 'item.txt'},
        algo_params={'trial': 0},
    )
    summarize = ProcessNode(
        name='summarize',
        executable='python summarize.py',
        in_paths={'items_fpath'},
        out_paths={'summary_fpath': 'summary.txt'},
    )
    trial.outputs['item_fpath'].connect(
        summarize.inputs['items_fpath'],
        gather=GatherSpec(group_by=[], order_by=['trial']),
    )
    summarize._pred_nodes_without_io_connection.append(prepare)
    dag = Pipeline(
        {'prepare': prepare, 'trial': trial, 'summarize': summarize}
    )
    rows = [{'trial.trial': trial_idx} for trial_idx in range(3)]
    compiled = dag.compile_configurations(rows, root_dpath='runs', cache=False)
    summaries = [
        node for node in compiled.nodes.values() if node.name == 'summarize'
    ]
    predecessors = summaries[0].predecessor_process_nodes()
    assert sum(node.name == 'prepare' for node in predecessors) == 1
    assert sum(node.name == 'trial' for node in predecessors) == 3


def test_gather_can_refan_out_and_gather_again():
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
    score = ProcessNode(
        name='score',
        executable='python score.py',
        in_paths={'merged_fpath'},
        out_paths={'score_fpath': 'score.json'},
        algo_params={'dataset': 'a', 'metric': 'accuracy'},
    )
    summarize = ProcessNode(
        name='summarize',
        executable='python summarize.py',
        in_paths={'scores_fpath'},
        out_paths={'summary_fpath': 'summary.json'},
    )
    shard.outputs['part_fpath'].connect(
        merge.inputs['parts_fpath'],
        gather=GatherSpec(group_by=['dataset'], order_by=['fold']),
    )
    merge.outputs['merged_fpath'].connect(score.inputs['merged_fpath'])
    score.outputs['score_fpath'].connect(
        summarize.inputs['scores_fpath'],
        gather=GatherSpec(group_by=[], order_by=['dataset', 'metric']),
    )
    dag = Pipeline(
        {
            'shard': shard,
            'merge': merge,
            'score': score,
            'summarize': summarize,
        }
    )
    rows = []
    for dataset in ['a', 'b']:
        for fold in [0, 1]:
            for metric in ['accuracy', 'f1']:
                rows.append(
                    {
                        'shard.dataset': dataset,
                        'shard.fold': fold,
                        'merge.dataset': dataset,
                        'score.dataset': dataset,
                        'score.metric': metric,
                    }
                )
    compiled = dag.compile_configurations(
        rows, root_dpath='runs', cache=False
    )
    nodes_by_name = ub.group_items(
        compiled.nodes.values(), key=lambda node: node.name
    )
    assert len(nodes_by_name['shard']) == 4
    assert len(nodes_by_name['merge']) == 2
    assert len(nodes_by_name['score']) == 4
    assert len(nodes_by_name['summarize']) == 1
    summary_members = nodes_by_name['summarize'][0].inputs[
        'scores_fpath'
    ]._gather_members
    assert summary_members is not None
    assert [
        (
            member.parent.final_algo_config['dataset'],
            member.parent.final_algo_config['metric'],
        )
        for member in summary_members
    ] == [
        ('a', 'accuracy'),
        ('a', 'f1'),
        ('b', 'accuracy'),
        ('b', 'f1'),
    ]
