"""Tests for compile-time gather edges."""

from __future__ import annotations

import json
from typing import Any

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
    return Pipeline([train, ensemble, evaluate])


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
    lines = [
        f'/tmp/model path/{idx:06d}/checkpoint.pkl' for idx in range(20000)
    ]
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
        gather_info = ensemble._depends_config()['__gather__.checkpoints_fpath']
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
            if record['source'] == 'ensemble' and record['target'] == 'evaluate'
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
            r"""
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
            """
        )
    )
    return script_fpath


def test_downstream_job_config_keeps_the_whole_gathered_lineage():
    """
    A node downstream of a gather has several concrete ancestors sharing one
    template name. Flattening them into one dotted namespace lets the
    last-visited member overwrite its siblings, so the written record would
    describe a single fold that never produced the evaluated result.

    Runs the real queue and reads the artifact off disk, because
    ``job_config.json`` is written by a generated bash job, not in process.
    """
    import sys

    dpath = ub.Path.appdir('kwdagger/tests/gather/lineage').delete().ensuredir()
    script_fpath = _write_gather_demo_script(dpath)
    root_dpath = dpath / 'runs'

    # Each fold trains on its own data, so the folds disagree on two dotted
    # keys, not just on the one the sweep is indexed by.
    data_fpaths = []
    for fold in [0, 1, 2]:
        data_fpath = dpath / f'data_{fold}.txt'
        data_fpath.write_text(f'fold-{fold}')
        data_fpaths.append(data_fpath)

    pipeline = {
        'nodes': {
            'train': {
                'executable': f'{sys.executable} {script_fpath} train',
                'in_paths': ['data_fpath'],
                'out_paths': {'checkpoint_fpath': 'checkpoint.txt'},
                'algo_params': {'algorithm': 'linear', 'seed': 0, 'fold': 0},
            },
            'ensemble': {
                'executable': f'{sys.executable} {script_fpath} ensemble',
                'in_paths': ['checkpoints_fpath'],
                'out_paths': {'ensemble_fpath': 'ensemble.txt'},
                'algo_params': {'algorithm': 'linear', 'seed': 0},
            },
            'evaluate': {
                'executable': f'{sys.executable} {script_fpath} evaluate',
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
                },
            },
            'ensemble.ensemble_fpath -> evaluate.ensemble_fpath',
        ],
    }
    params = {
        'pipeline': pipeline,
        'matrix': {
            'train.fold': [0, 1, 2],
            'train.algorithm': ['linear'],
            'train.seed': [0],
            'ensemble.algorithm': ['linear'],
            'ensemble.seed': [0],
            'evaluate.algorithm': ['linear'],
            'evaluate.seed': [0],
            'evaluate.test_set': ['clean'],
        },
        # Correlate each fold with its own data, so the gathered members
        # disagree on two dotted keys rather than only on the sweep axis.
        'include': [
            {'train.fold': fold, 'train.data_fpath': str(data_fpath)}
            for fold, data_fpath in enumerate(data_fpaths)
        ],
    }
    config = schedule.ScheduleEvaluationConfig(
        run=1,
        root_dpath=root_dpath,
        backend='serial',
        params=params,
        enable_links=1,
        cache=0,
    )
    compiled, _queue = schedule.build_schedule(config)

    evaluators = [n for n in compiled.nodes.values() if n.name == 'evaluate']
    assert len(evaluators) == 1
    trainers = [n for n in compiled.nodes.values() if n.name == 'train']
    assert len(trainers) == 3

    config_fpath = evaluators[0].final_node_dpath / 'job_config.json'
    assert config_fpath.exists()
    record = json.loads(config_fpath.read_text())

    # Every gathered member survives, and the collections are aligned to a
    # named instance ordering so a value can be traced to the process that
    # used it.
    instances = record['__instances__.train']
    assert sorted(instances) == sorted(n.process_id for n in trainers)
    assert len(instances) == 3

    folds = record['train.fold']
    data = record['train.data_fpath']
    assert isinstance(folds, list) and isinstance(data, list)
    assert sorted(folds) == [0, 1, 2]
    assert sorted(data) == sorted(str(p) for p in data_fpaths)
    by_id = {n.process_id: n for n in trainers}
    for process_id, fold, data_fpath in zip(instances, folds, data):
        assert by_id[process_id].config['fold'] == fold
        assert by_id[process_id].config['data_fpath'] == data_fpath

    # A key every member agrees on stays scalar, as it always has.
    assert record['train.algorithm'] == 'linear'
    assert record['evaluate.test_set'] == 'clean'

    # Gather membership reaches the descendant, not just the consumer.
    gather_record = record['__gather__.ensemble.checkpoints_fpath']
    assert gather_record['source'] == 'train.checkpoint_fpath'
    member_ids = [m['process_id'] for m in gather_record['members']]
    assert sorted(member_ids) == sorted(n.process_id for n in trainers)


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
    assert 'cat > ' in commands
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
    assert 'cat > ' in invoke_text
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
    root = (
        ub.Path.appdir('kwdagger/tests/gather/heredoc-indent')
        .delete()
        .ensuredir()
    )
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
    # ``Any`` because ``allow_indent`` is declared on cmd_queue's serial job
    # subclass rather than on the base job type ``named_jobs`` advertises.
    consumer_job: Any = queue.named_jobs[ensemble.process_id]
    bookkeeper_job: Any = queue.named_jobs['before_' + ensemble.process_id]
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
        assert 'cat > ' in invoke_text
        assert "<<'KWDAGGER_GATHER_ENSEMBLE_CHECKPOINTS_FPATH_" in invoke_text

        # The Slurm job receives only the short file-backed command. Even when
        # write_invocations=False, gathered Slurm consumers require this
        # standalone artifact to avoid placing the manifest in --wrap argv.
        job = queue.named_jobs[ensemble.process_id]
        # cmd_queue allows a job without a command; a gathered consumer is not
        # one, and saying so keeps the rest of this block about the command.
        command = job.command
        assert command is not None
        assert 'bash ' in command
        assert str(invoke_fpath) in command
        assert 'cat > ' not in command
        assert len(command) < 1024

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
    dag = Pipeline([source, summarize])
    rows = [{'source.trial': trial} for trial in range(3)]
    compiled = dag.compile_configurations(rows, root_dpath='runs', cache=False)
    summaries = [
        node for node in compiled.nodes.values() if node.name == 'summarize'
    ]
    assert len(summaries) == 1
    members = summaries[0].inputs['items']._gather_members
    assert members is not None
    assert [member.parent.final_algo_config['trial'] for member in members] == [
        0,
        1,
        2,
    ]


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
    dag = Pipeline([train, ensemble])
    rows = [
        {
            'train.data_fpath': f'fold{fold}.txt',
            'train.algorithm': 'linear',
            'train.fold': fold,
            'ensemble.algorithm': 'linear',
        }
        for fold in range(3)
    ]
    compiled = dag.compile_configurations(rows, root_dpath='runs', cache=False)
    ensembles = sorted(
        (node for node in compiled.nodes.values() if node.name == 'ensemble'),
        key=lambda node: str(node.inputs['data_fpath'].final_value),
    )
    assert len(ensembles) == 3
    assert [
        str(node.inputs['data_fpath'].final_value) for node in ensembles
    ] == ['fold0.txt', 'fold1.txt', 'fold2.txt']
    assert len({node.process_id for node in ensembles}) == 3
    for node in ensembles:
        # A forwarded input is provenance, not process lineage. The
        # resolved value is already represented by final_input_config, so
        # changing from direct configuration to forwarding must not change
        # the process hash.
        provenance = node._depends_config()['__input__.data_fpath']
        assert provenance == {
            'source': 'train.data_fpath',
            'target': 'ensemble.data_fpath',
            'source_port': 'data_fpath',
            'source_kind': 'input',
            'value': node.inputs['data_fpath'].final_value,
        }
        assert '__input__.data_fpath' not in node.depends
        # Nothing upstream produced it, so it is identity-bearing here.
        assert 'data_fpath' in node.final_input_config
        assert 'data_fpath' not in node.final_algo_config
        assert node.depends['__inputs__']['data_fpath'] == (
            node.inputs['data_fpath'].final_value
        )
        members = node.inputs['checkpoints_fpath']._gather_members
        assert members is not None
        assert len(members) == 3

    records = compiled._edge_cardinality_records()
    parallel_records = [
        record
        for record in records
        if record['source'] == 'train' and record['target'] == 'ensemble'
    ]
    assert {
        (
            record['kind'],
            record['source_port'],
            record['target_port'],
            record['edge_count'],
        )
        for record in parallel_records
    } == {
        ('shared_input', 'data_fpath', 'data_fpath', 3),
        ('gather', 'checkpoint_fpath', 'checkpoints_fpath', 9),
    }


def test_compile_configurations_defaults_none_root_to_cwd():
    source = ProcessNode(
        name='source',
        executable='python source.py',
        out_paths={'result_fpath': 'result.txt'},
    )
    collect = ProcessNode(
        name='collect',
        executable='python collect.py',
        in_paths={'results_fpath'},
        out_paths={'summary_fpath': 'summary.txt'},
    )
    source.outputs['result_fpath'].connect(
        collect.inputs['results_fpath'],
        gather=GatherSpec(group_by=[]),
    )
    dag = Pipeline([source, collect])
    compiled = dag.compile_configurations([{}], cache=False)
    assert compiled.root_dpath == ub.Path('.')
    assert all(
        node.root_dpath == ub.Path('.') for node in compiled.nodes.values()
    )


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
    dag = Pipeline([trial, select])
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

    gather_records = [
        record
        for record in compiled._edge_cardinality_records()
        if record['kind'] == 'gather'
    ]
    assert len(gather_records) == 2
    assert {
        (record['source_port'], record['target_port'])
        for record in gather_records
    } == {
        ('checkpoint_fpath', 'checkpoints_fpath'),
        ('metric_fpath', 'metrics_fpath'),
    }
    assert all(record['relation'] == 'gather 4:1' for record in gather_records)


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
    dag = Pipeline([prepare, trial, summarize])
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
    dag = Pipeline([shard, merge, score, summarize])
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
    compiled = dag.compile_configurations(rows, root_dpath='runs', cache=False)
    nodes_by_name = ub.group_items(
        compiled.nodes.values(), key=lambda node: node.name
    )
    assert len(nodes_by_name['shard']) == 4
    assert len(nodes_by_name['merge']) == 2
    assert len(nodes_by_name['score']) == 4
    assert len(nodes_by_name['summarize']) == 1
    summary_members = (
        nodes_by_name['summarize'][0].inputs['scores_fpath']._gather_members
    )
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


def _slurm_conflict_rows(*, gpu1_first, key='train.__slurm_options__'):
    """Two rows with one identity and two Slurm resource requests.

    ``__slurm_options__`` is stripped from the hashed config just like
    ``__enabled__``, so the surviving row silently decides the partition,
    GPU count, memory, time limit, and account for every duplicate.
    """

    def row(fold, gres=None):
        config = {
            'train.data_fpath': 'data.txt',
            'train.algorithm': 'linear',
            'train.seed': 0,
            'train.fold': fold,
            'ensemble.algorithm': 'linear',
            'ensemble.seed': 0,
        }
        if gres is not None:
            config[key] = {'gres': gres}
        return config

    states = ['gpu:1', 'gpu:4'] if gpu1_first else ['gpu:4', 'gpu:1']
    return [row(0, states[0]), row(0, states[1]), row(1)]


@pytest.mark.parametrize('gpu1_first', [True, False])
@pytest.mark.parametrize(
    'key', ['train.__slurm_options__', '__slurm_options__']
)
def test_gather_rejects_conflicting_slurm_options(gpu1_first, key):
    """Node-specific and row-global Slurm options both must agree."""
    rows = _slurm_conflict_rows(gpu1_first=gpu1_first, key=key)
    dag = _demo_gather_pipeline()
    with pytest.raises(ValueError) as excinfo:
        dag.compile_configurations(rows, root_dpath='runs', cache=False)
    message = str(excinfo.value)
    assert '__slurm_options__' in message
    assert "'train'" in message


@pytest.mark.parametrize('gpu1_first', [True, False])
def test_gather_allows_agreeing_slurm_options(gpu1_first):
    """Duplicates requesting the same resources still collapse."""
    rows = _slurm_conflict_rows(gpu1_first=gpu1_first)
    rows[1]['train.__slurm_options__'] = rows[0]['train.__slurm_options__']
    dag = _demo_gather_pipeline()
    compiled = dag.compile_configurations(rows, root_dpath='runs', cache=False)
    nodes_by_name = ub.group_items(
        compiled.nodes.values(), key=lambda node: node.name
    )
    assert len(nodes_by_name['train']) == 2


def _enabled_conflict_rows(*, enabled_first, target='train'):
    """Two rows with one identity and two ``__enabled__`` values, plus a peer.

    The peer keeps the gather group non-degenerate so the conflict is the only
    thing under test.
    """

    def row(fold, enabled=None):
        config = {
            'train.data_fpath': 'data.txt',
            'train.algorithm': 'linear',
            'train.seed': 0,
            'train.fold': fold,
            'ensemble.algorithm': 'linear',
            'ensemble.seed': 0,
        }
        if enabled is not None:
            config[f'{target}.__enabled__'] = enabled
        return config

    states = [True, False] if enabled_first else [False, True]
    return [row(0, states[0]), row(0, states[1]), row(1)]


@pytest.mark.parametrize('enabled_first', [True, False])
def test_gather_rejects_conflicting_enabled_on_source(enabled_first):
    """A gather source cannot be both enabled and disabled.

    ``__enabled__`` is popped before process identity is computed, so without
    an explicit check the winner is whichever row compiled first. A disabled
    source would stay in the consumer's manifest membership while its output
    is never produced.
    """
    rows = _enabled_conflict_rows(enabled_first=enabled_first, target='train')
    dag = _demo_gather_pipeline()
    with pytest.raises(ValueError) as excinfo:
        dag.compile_configurations(rows, root_dpath='runs', cache=False)
    message = str(excinfo.value)
    assert '__enabled__' in message
    assert "'train'" in message


@pytest.mark.parametrize('enabled_first', [True, False])
def test_gather_rejects_conflicting_enabled_on_consumer(enabled_first):
    """The same guard applies to the gather consumer, not just the source."""
    rows = _enabled_conflict_rows(
        enabled_first=enabled_first, target='ensemble'
    )
    dag = _demo_gather_pipeline()
    with pytest.raises(ValueError) as excinfo:
        dag.compile_configurations(rows, root_dpath='runs', cache=False)
    message = str(excinfo.value)
    assert '__enabled__' in message
    assert "'ensemble'" in message


@pytest.mark.parametrize('enabled_first', [True, False])
def test_gather_compilation_is_row_order_independent_when_enabled_agrees(
    enabled_first,
):
    """Agreeing duplicates still collapse, and ``1`` matches ``True``.

    Only truthiness and equality against ``'redo'`` are meaningful to
    ``submit_jobs``, so equivalent spellings must not be reported as a
    conflict.
    """
    states = [True, 1] if enabled_first else [1, True]
    rows = [
        {
            'train.data_fpath': 'data.txt',
            'train.algorithm': 'linear',
            'train.seed': 0,
            'train.fold': fold,
            'train.__enabled__': enabled,
            'ensemble.algorithm': 'linear',
            'ensemble.seed': 0,
        }
        for fold, enabled in [(0, states[0]), (0, states[1]), (1, True)]
    ]
    dag = _demo_gather_pipeline()
    compiled = dag.compile_configurations(rows, root_dpath='runs', cache=False)
    nodes_by_name = ub.group_items(
        compiled.nodes.values(), key=lambda node: node.name
    )
    assert len(nodes_by_name['train']) == 2
    assert all(node.enabled for node in nodes_by_name['train'])
    members = (
        nodes_by_name['ensemble'][0].inputs['checkpoints_fpath']._gather_members
    )
    assert members is not None
    assert [member.parent.final_algo_config['fold'] for member in members] == [
        0,
        1,
    ]


def _tutorial_dpath():
    import kwdagger

    dpath = (
        ub.Path(kwdagger.__file__).parent.parent
        / 'docs/source/manual/tutorials/gather_cross_validation'
    )
    if not (dpath / 'params.yaml').exists():
        pytest.skip('tutorial sources are not part of the installed package')
    return dpath


def _expand_tutorial_params(fpath):
    import kwutil

    from kwdagger.utils.util_param_grid import expand_param_grid

    data = kwutil.Yaml.coerce(fpath.read_text())
    data.pop('pipeline', None)
    return list(expand_param_grid(data))


def test_gather_tutorial_include_form_matches_matrices_form():
    """The tutorial's two parameter spellings must stay interchangeable.

    ``params.yaml`` writes one matrix per algorithm/seed pair;
    ``params-include.yaml`` writes a single matrix and uses ``include`` to
    propagate the shared axes downstream. The README presents them as
    equivalent, so drift in either file -- or in ``include`` semantics --
    should fail here rather than silently change what the tutorial runs.
    """
    dpath = _tutorial_dpath()
    matrices_rows = _expand_tutorial_params(dpath / 'params.yaml')
    include_rows = _expand_tutorial_params(dpath / 'params-include.yaml')
    assert len(matrices_rows) == 24
    assert include_rows == matrices_rows


def test_gather_tutorial_include_form_compiles_identically():
    """Equal parameter rows must also produce an identical compiled DAG."""
    from kwdagger.pipeline import coerce_pipeline

    dpath = _tutorial_dpath()

    def compile_fingerprint(name):
        rows = _expand_tutorial_params(dpath / name)
        dag = coerce_pipeline(str(dpath / 'pipeline.yaml'))
        compiled = dag.compile_configurations(
            rows, root_dpath='results', cache=False
        )
        return {
            process_id: {
                input_name: [
                    member.parent.process_id
                    for member in input_node._gather_members
                ]
                for input_name, input_node in node.inputs.items()
                if input_node._gather_members is not None
            }
            for process_id, node in compiled.nodes.items()
        }

    assert compile_fingerprint('params.yaml') == compile_fingerprint(
        'params-include.yaml'
    )


def _fanout_pipeline(group_by, report_port='data_fpath'):
    """prepare fans out over ``dataset``; score and report both consume it.

    This is the shape a sweep normally has: exactly one node carries the
    parameter that varies, and every consumer is grouped by it through an
    edge instead of redeclaring it.
    """
    prepare = ProcessNode(
        name='prepare',
        executable='python prepare.py',
        out_paths={'data_fpath': 'data.json'},
        algo_params={'dataset': 'cats'},
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
        in_paths={report_port, 'scores_fpath'},
        out_paths={'report_fpath': 'report.json'},
    )
    prepare.outputs['data_fpath'].connect(score.inputs['data_fpath'])
    prepare.outputs['data_fpath'].connect(report.inputs[report_port])
    score.outputs['score_fpath'].connect(
        report.inputs['scores_fpath'],
        gather=GatherSpec(group_by=list(group_by), order_by=['model']),
    )
    dag = Pipeline([prepare, score, report])
    dag.build_nx_graphs()
    return dag


def _compile(dag, root_dpath, matrix=None):
    config = schedule.ScheduleEvaluationConfig(
        params={
            'pipeline': dag,
            'matrix': matrix
            or {
                'prepare.dataset': ['cats', 'dogs'],
                'score.model': ['m1', 'm2'],
            },
        },
        root_dpath=root_dpath,
        run=False,
    )
    compiled, _queue = schedule.build_schedule(config)
    return compiled


def _instances(dag, name):
    return [n for n in dag.node_dict.values() if n.name == name]


def _assert_partitioned_by_dataset(dag):
    reports = _instances(dag, 'report')
    assert len(reports) == 2, 'one report per prepared dataset'
    for report in reports:
        members = report.inputs['scores_fpath']._gather_members
        assert len(members) == 2, 'both models, and only this dataset'


def test_gather_groups_on_a_qualified_upstream_parameter(tmp_path):
    # The preferred form: name the node the value lives on, exactly as a
    # matrix key does. Only `prepare` declares `dataset`; the consumers
    # are grouped by it through their edges.
    _assert_partitioned_by_dataset(
        _compile(_fanout_pipeline(['prepare.dataset']), tmp_path / 'a')
    )


def test_gather_groups_on_a_qualified_output_path(tmp_path):
    # The produced path is itself a fine identity to group on.
    _assert_partitioned_by_dataset(
        _compile(_fanout_pipeline(['prepare.data_fpath']), tmp_path / 'b')
    )


def test_gather_qualified_key_must_name_a_node_both_ends_can_see(tmp_path):
    # A grouping key is resolved on the source instances *and* on the target,
    # so it has to name a node reachable from both -- in practice a common
    # ancestor. Naming the *target* does not work: the sources cannot see it.
    #
    # This once failed on the target side too, because a produced input was
    # not part of its own node's identity and so resolved nowhere. It is now,
    # so the remaining reason is the only real one: `report` is downstream of
    # `score`, and a source cannot group by something it cannot reach.
    dag = _fanout_pipeline(['report.data_fpath'])
    with pytest.raises(KeyError) as excinfo:
        _compile(dag, tmp_path / 'c')
    message = str(excinfo.value)
    assert 'report' in message
    assert 'not this node nor one of its ancestors' in message


def test_gather_groups_on_a_connected_input_path(tmp_path):
    # A connected in_path is excluded from final_algo_config because paths are
    # not algorithm parameters -- it still reaches identity as an effective
    # input value. That exclusion is right for algo_id and wrong for grouping,
    # and the common ancestor's port names the same path from both ends.
    _assert_partitioned_by_dataset(
        _compile(_fanout_pipeline(['prepare.data_fpath']), tmp_path / 'c2')
    )


def test_gather_groups_on_an_unqualified_ancestor_parameter(tmp_path):
    # Backwards-compatible bare form. Here the consumer holds the producer's
    # output under a *different* port name, so the path is not resolvable by
    # name, but the ancestor still carries the parameter that varies.
    _assert_partitioned_by_dataset(
        _compile(
            _fanout_pipeline(['dataset'], report_port='ctx_fpath'),
            tmp_path / 'd',
        )
    )


def test_gather_qualified_key_rejects_an_unreachable_node(tmp_path):
    dag = _fanout_pipeline(['nonexistent.dataset'])
    with pytest.raises(
        KeyError, match='not this node nor one of its ancestors'
    ):
        _compile(dag, tmp_path / 'e')


def test_gather_key_error_names_every_place_it_looked(tmp_path):
    # The consumer has no ordinary edge to prepare, so its only route to the
    # fanned-out node is the gather being resolved. Nothing to group on.
    prepare = ProcessNode(
        name='prepare',
        executable='python prepare.py',
        out_paths={'data_fpath': 'data.json'},
        algo_params={'dataset': 'cats'},
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
        in_paths={'scores_fpath'},
        out_paths={'report_fpath': 'report.json'},
    )
    prepare.outputs['data_fpath'].connect(score.inputs['data_fpath'])
    score.outputs['score_fpath'].connect(
        report.inputs['scores_fpath'],
        gather=GatherSpec(group_by=['dataset'], order_by=['model']),
    )
    dag = Pipeline([prepare, score, report])
    dag.build_nx_graphs()

    with pytest.raises(KeyError) as excinfo:
        _compile(dag, tmp_path / 'f')

    message = str(excinfo.value)
    for expected in ('algo=', 'inputs=', 'ancestors=', '<node>.<param>'):
        assert expected in message, message


def test_gather_unqualified_key_refuses_to_guess_between_ancestors(tmp_path):
    # Two ancestors declaring the same parameter with different values would
    # otherwise silently group on whichever was visited first.
    left = ProcessNode(
        name='left',
        executable='python left.py',
        out_paths={'left_fpath': 'left.json'},
        algo_params={'split': 'train'},
    )
    right = ProcessNode(
        name='right',
        executable='python right.py',
        out_paths={'right_fpath': 'right.json'},
        algo_params={'split': 'val'},
    )
    score = ProcessNode(
        name='score',
        executable='python score.py',
        in_paths={'left_fpath', 'right_fpath'},
        out_paths={'score_fpath': 'score.json'},
        algo_params={'model': 'm1'},
    )
    report = ProcessNode(
        name='report',
        executable='python report.py',
        in_paths={'left_fpath', 'right_fpath', 'scores_fpath'},
        out_paths={'report_fpath': 'report.json'},
    )
    for producer, port in ((left, 'left_fpath'), (right, 'right_fpath')):
        producer.outputs[port].connect(score.inputs[port])
        producer.outputs[port].connect(report.inputs[port])
    score.outputs['score_fpath'].connect(
        report.inputs['scores_fpath'],
        gather=GatherSpec(group_by=['split'], order_by=['model']),
    )
    dag = Pipeline([left, right, score, report])
    dag.build_nx_graphs()

    matrix = {
        'left.split': ['train'],
        'right.split': ['val'],
        'score.model': ['m1', 'm2'],
    }
    with pytest.raises(ValueError, match='Qualify it as'):
        _compile(dag, tmp_path / 'g', matrix=matrix)


# --------------------------------------------------------------------------
# identity model: algo config vs input config
# --------------------------------------------------------------------------


def _collector_for(source, port):
    """A trivial terminal gather, so compile_configurations is applicable."""
    collect = ProcessNode(
        name='collect',
        executable='python collect.py',
        in_paths={'items_fpath'},
        out_paths={'all_fpath': 'all.json'},
    )
    source.outputs[port].connect(
        collect.inputs['items_fpath'], gather=GatherSpec(group_by=[])
    )
    return collect


def _predict_only(model='resnet'):
    node = ProcessNode(
        name='predict',
        executable='python predict.py',
        in_paths={'data_fpath'},
        out_paths={'pred_fpath': 'pred.json'},
        algo_params={'model': model},
    )
    dag = Pipeline([node, _collector_for(node, 'pred_fpath')])
    dag.build_nx_graphs()
    return dag


def test_algo_config_excludes_paths():
    dag = _predict_only()
    compiled = dag.compile_configurations(
        [{'predict.data_fpath': '/d/a.json', 'predict.model': 'resnet'}],
        root_dpath='runs',
        cache=False,
    )
    node = [n for n in compiled.nodes.values() if n.name == 'predict'][0]
    assert 'data_fpath' not in node.final_algo_config
    assert node.final_algo_config == {'model': 'resnet'}
    # ...but it is not lost: it moves to the input config and to depends.
    assert node.final_input_config == {'data_fpath': '/d/a.json'}
    assert node.depends['__inputs__'] == {'data_fpath': '/d/a.json'}


def test_algo_id_is_independent_of_how_a_path_is_wired():
    # The same algorithm on the same data must have one algorithm identity,
    # whether the path comes from the matrix, from a peer's input, or from
    # an upstream node's output.
    data = '/d/a.json'

    unconnected = _predict_only()

    peer = ProcessNode(
        name='peer',
        executable='python peer.py',
        in_paths={'data_fpath'},
        out_paths={'peer_fpath': 'peer.json'},
    )
    aliased_predict = ProcessNode(
        name='predict',
        executable='python predict.py',
        in_paths={'data_fpath'},
        out_paths={'pred_fpath': 'pred.json'},
        algo_params={'model': 'resnet'},
    )
    peer.inputs['data_fpath'].connect(aliased_predict.inputs['data_fpath'])
    aliased = Pipeline(
        [peer, aliased_predict, _collector_for(aliased_predict, 'pred_fpath')]
    )
    aliased.build_nx_graphs()

    prep = ProcessNode(
        name='prep',
        executable='python prep.py',
        out_paths={'data_fpath': 'data.json'},
        algo_params={'dataset': 'train'},
    )
    produced_predict = ProcessNode(
        name='predict',
        executable='python predict.py',
        in_paths={'data_fpath'},
        out_paths={'pred_fpath': 'pred.json'},
        algo_params={'model': 'resnet'},
    )
    prep.outputs['data_fpath'].connect(produced_predict.inputs['data_fpath'])
    produced = Pipeline(
        [prep, produced_predict, _collector_for(produced_predict, 'pred_fpath')]
    )
    produced.build_nx_graphs()

    cases = [
        (unconnected, {'predict.data_fpath': data, 'predict.model': 'resnet'}),
        (aliased, {'peer.data_fpath': data, 'predict.model': 'resnet'}),
        (produced, {'prep.dataset': 'train', 'predict.model': 'resnet'}),
    ]
    algo_ids = set()
    for dag, row in cases:
        compiled = dag.compile_configurations(
            [row], root_dpath='runs', cache=False
        )
        node = [n for n in compiled.nodes.values() if n.name == 'predict'][0]
        algo_ids.add(node.algo_id)
    assert len(algo_ids) == 1, algo_ids


def test_differing_unconnected_paths_do_not_collide():
    # algo_id no longer carries the path, so process_id must -- otherwise two
    # runs over different data would share an output directory.
    dag = _predict_only()
    compiled = dag.compile_configurations(
        [
            {'predict.data_fpath': p, 'predict.model': 'resnet'}
            for p in ['/d/a.json', '/d/b.json']
        ],
        root_dpath='runs',
        cache=False,
    )
    nodes = [n for n in compiled.nodes.values() if n.name == 'predict']
    assert len({n.algo_id for n in nodes}) == 1, 'same algorithm'
    assert len({n.process_id for n in nodes}) == 2, 'different data'
    assert len({str(n.final_node_dpath) for n in nodes}) == 2


def test_perf_params_are_not_identity_bearing():
    node = ProcessNode(
        name='predict',
        executable='python predict.py',
        in_paths={'data_fpath'},
        out_paths={'pred_fpath': 'pred.json'},
        algo_params={'model': 'resnet'},
        perf_params={'workers': 4},
    )
    dag = Pipeline([node, _collector_for(node, 'pred_fpath')])
    dag.build_nx_graphs()
    ids = set()
    for workers in [4, 16]:
        compiled = dag.compile_configurations(
            [
                {
                    'predict.data_fpath': '/d/a.json',
                    'predict.model': 'resnet',
                    'predict.workers': workers,
                }
            ],
            root_dpath='runs',
            cache=False,
        )
        got = [n for n in compiled.nodes.values() if n.name == 'predict'][0]
        ids.add((got.algo_id, got.process_id))
    assert len(ids) == 1, 'changing workers must not invalidate a result'


def test_algo_id_distinguishes_nodes_with_empty_algo_configs():
    # The node name is part of the hashed payload, not merely a prefix, so
    # the hash portion is usable on its own.
    def build(name):
        src = ProcessNode(
            name='src',
            executable='python src.py',
            out_paths={'o_fpath': 'o.json'},
            algo_params={'a': 1},
        )
        tgt = ProcessNode(
            name=name,
            executable='python t.py',
            in_paths={'i_fpath'},
            out_paths={'r_fpath': 'r.json'},
        )
        src.outputs['o_fpath'].connect(
            tgt.inputs['i_fpath'], gather=GatherSpec(group_by=[])
        )
        dag = Pipeline([src, tgt])
        dag.build_nx_graphs()
        return dag

    hashes = set()
    for name in ['summarize', 'report']:
        compiled = build(name).compile_configurations(
            [{'src.a': 1}], root_dpath='runs', cache=False
        )
        node = [n for n in compiled.nodes.values() if n.name == name][0]
        assert node.final_algo_config == {}
        hashes.add(node.algo_id.split('_id_')[-1])
    assert len(hashes) == 2, hashes


# --------------------------------------------------------------------------
# group keys that differ between the two ends
# --------------------------------------------------------------------------


def test_group_by_may_name_the_key_differently_on_each_side():
    # A scorer whose truth port is `truth_fpath` groups predictions keyed on
    # `dataset_fpath`, without either node renaming a port for the other.
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
    predict.outputs['pred_fpath'].connect(
        score.inputs['preds_fpath'],
        gather=GatherSpec(
            group_by=[{'src': 'dataset_fpath', 'dst': 'truth_fpath'}],
            order_by=['model'],
        ),
    )
    dag = Pipeline([predict, score])
    dag.build_nx_graphs()

    rows = [
        {
            'predict.dataset_fpath': data,
            'predict.model': model,
            'score.truth_fpath': data,
        }
        for data in ['/d/train.json', '/d/val.json']
        for model in ['resnet', 'vit']
    ]
    compiled = dag.compile_configurations(rows, root_dpath='runs', cache=False)
    scores = [n for n in compiled.nodes.values() if n.name == 'score']
    assert len(scores) == 2, 'one score per dataset'
    for node in scores:
        members = node.inputs['preds_fpath']._gather_members
        assert len(members) == 2, 'both models for that dataset'
        truth = node.final_input_config['truth_fpath']
        for member in members:
            assert member.parent.final_input_config['dataset_fpath'] == truth


def test_group_by_pair_round_trips_and_is_hashable():
    spec = GatherSpec(
        group_by=[{'src': 'dataset_fpath', 'dst': 'truth_fpath'}, 'model']
    )
    assert spec.source_keys() == ('dataset_fpath', 'model')
    assert spec.target_keys() == ('truth_fpath', 'model')
    assert spec.display_keys() == ('dataset_fpath->truth_fpath', 'model')
    assert hash(spec) is not None, 'used as a dict key when reporting'
    assert GatherSpec.coerce(spec.to_dict()).group_by == spec.group_by


def test_group_by_rejects_a_malformed_pair():
    with pytest.raises(ValueError, match='"src" and "dst"'):
        GatherSpec(group_by=[{'src': 'a'}])


# --------------------------------------------------------------------------
# wired algorithm parameters
# --------------------------------------------------------------------------

MODEL_FAMILY = {
    'resnet': 'cnn',
    'convnext': 'cnn',
    'vit': 'transformer',
    'swin': 'transformer',
    'mamba': 'ssm',
    'hyena': 'ssm',
}


def _family_pipeline(consumers=('score',)):
    detect = ProcessNode(
        name='detect',
        executable='python detect.py',
        in_paths={'dataset_fpath'},
        out_paths={'dets_fpath': 'dets.json'},
        algo_params={'model': 'resnet', 'model_family': 'cnn'},
    )
    nodes = {'detect': detect}
    for name in consumers:
        node = ProcessNode(
            name=name,
            executable=f'python {name}.py',
            in_paths={'dets_fpath'},
            out_paths={f'{name}_fpath': f'{name}.json'},
            algo_params={'model_family': 'cnn'},
        )
        detect.param_ports['model_family'].connect(
            node.param_ports['model_family']
        )
        detect.outputs['dets_fpath'].connect(
            node.inputs['dets_fpath'],
            gather=GatherSpec(group_by=['model_family'], order_by=['model']),
        )
        nodes[name] = node
    dag = Pipeline(list(nodes.values()))
    dag.build_nx_graphs()
    return dag


def _family_rows():
    # `include` carries only the correlation. No consumer appears in it,
    # however many consumers there are.
    return [
        {
            'detect.dataset_fpath': '/d/t.json',
            'detect.model': model,
            'detect.model_family': family,
        }
        for model, family in MODEL_FAMILY.items()
    ]


def test_a_wired_algo_param_carries_its_value():
    dag = _family_pipeline()
    compiled = dag.compile_configurations(
        _family_rows(), root_dpath='runs', cache=False
    )
    scores = [n for n in compiled.nodes.values() if n.name == 'score']
    families = sorted(n.final_algo_config['model_family'] for n in scores)
    assert families == ['cnn', 'ssm', 'transformer']
    # ...and reaches the command line, not just the config.
    for node in scores:
        assert (
            f'--model_family={node.final_algo_config["model_family"]}'
            in node.command
        )


def test_a_wired_algo_param_is_identity_bearing():
    # It is a parameter, so it belongs to the algorithm's identity -- unlike
    # an input path, which belongs to the data's.
    dag = _family_pipeline()
    compiled = dag.compile_configurations(
        _family_rows(), root_dpath='runs', cache=False
    )
    scores = [n for n in compiled.nodes.values() if n.name == 'score']
    assert len({n.algo_id for n in scores}) == 3
    for node in scores:
        assert 'model_family' not in node.final_input_config
        assert 'model_family' not in node.final_in_paths


def test_a_wired_algo_param_is_not_a_scheduling_dependency():
    # Like an aliased input it carries a value, not a dependency. If it were
    # a dependency the consumer would inherit the producer's fan-out over
    # `model` and there would be six scores rather than three.
    dag = _family_pipeline()
    compiled = dag.compile_configurations(
        _family_rows(), root_dpath='runs', cache=False
    )
    scores = [n for n in compiled.nodes.values() if n.name == 'score']
    assert len(scores) == 3
    for node in scores:
        # `detect` is an ancestor via the gather, not via the parameter.
        via_param = [pred for pred in node.param_ports['model_family'].pred]
        assert via_param, 'the parameter really is wired'
        assert all(
            p.parent not in node.predecessor_process_nodes()
            or node.inputs['dets_fpath']._gather_members
            for p in via_param
        )


def test_wiring_a_param_does_not_grow_with_consumer_count():
    # The point of the feature: `include` states the correlation once, and
    # adding consumers costs nothing.
    rows = _family_rows()
    for consumers in [
        ('score',),
        ('score', 'calib'),
        ('score', 'calib', 'recall_curve'),
    ]:
        dag = _family_pipeline(consumers)
        compiled = dag.compile_configurations(
            rows, root_dpath='runs', cache=False
        )
        for name in consumers:
            nodes = [n for n in compiled.nodes.values() if n.name == name]
            assert len(nodes) == 3, (name, consumers)
            sizes = sorted(
                len(n.inputs['dets_fpath']._gather_members) for n in nodes
            )
            assert sizes == [2, 2, 2], (name, sizes)


def test_param_ports_exist_for_every_declared_algo_param():
    node = ProcessNode(
        name='n',
        executable='python n.py',
        out_paths={'o_fpath': 'o.json'},
        algo_params={'alpha': 1, 'beta': 2},
    )
    assert sorted(node.param_ports) == ['alpha', 'beta']
    # and they are not inputs -- they are not data the node reads
    assert 'alpha' not in node.inputs


def test_wired_params_survive_a_yaml_round_trip():
    # A dropped edge here would silently change what the consumer runs.
    from kwdagger import dump_yaml_pipeline, load_yaml_pipeline

    dag = _family_pipeline()
    spec = dump_yaml_pipeline(dag)
    assert 'detect.model_family -> score.model_family' in spec['edges']

    restored = load_yaml_pipeline(spec)
    assert restored.node_dict['score'].param_ports['model_family'].pred
    assert dump_yaml_pipeline(restored) == spec
