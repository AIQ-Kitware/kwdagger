#!/usr/bin/env python3
r"""
A self-contained declarative-pipeline introspection example.

This file contains five kwconf subcommands:

* ``preprocess`` -- a tiny text preprocessing stage.
* ``train`` -- a tiny stage that writes a JSON "model".
* ``evaluate`` -- a tiny stage that reads the model and writes a JSON metric.
* ``introspect`` -- loads the YAML pipeline below and inspects both its logical
  graph and its compiled concrete process graph without running any jobs.
* ``run`` -- compiles the same YAML pipeline and executes it with kwdagger's
  serial backend using a small input file created by the example itself.

The YAML is the pipeline definition.  Its three ``executable`` entries point
back to the first three subcommands in this same file, so the example does not
depend on separate ``preprocess.py``, ``train.py``, or ``evaluate.py`` scripts.
``THIS_FILE`` is replaced with this file's absolute path before the YAML is
loaded so the generated commands remain runnable outside the repository root.

CommandLine:
    python examples/yaml_introspection_example.py --help
    python examples/yaml_introspection_example.py introspect
    python examples/yaml_introspection_example.py run
"""

from __future__ import annotations

from collections.abc import Iterable
import json
from pathlib import Path
from pprint import pprint
import shlex
import sys
from typing import Any

import kwconf as kw
import kwdagger
import networkx as nx
from kwdagger.pipeline import CompiledPipeline, IONode, Pipeline


PIPELINE_YAML = r"""
__doc__: |
  A small preprocess -> train -> evaluate pipeline whose executables are
  subcommands registered by the same Python file that loads this YAML.

nodes:
  preprocess:
    executable: python THIS_FILE preprocess
    in_paths: [src_fpath]
    out_paths: {dst_fpath: prepared.txt}
    primary_out_key: dst_fpath
    algo_params:
      normalize: true

  train:
    executable: python THIS_FILE train
    in_paths: [train_fpath]
    out_paths: {model_fpath: model.json}
    primary_out_key: model_fpath
    algo_params:
      learning_rate: 0.001
    perf_params:
      workers: 4

  evaluate:
    executable: python THIS_FILE evaluate
    in_paths: [data_fpath, model_fpath]
    out_paths: {metrics_fpath: metrics.json}
    primary_out_key: metrics_fpath
    algo_params:
      metric: accuracy

edges:
  - preprocess.dst_fpath -> train.train_fpath
  - preprocess.dst_fpath -> evaluate.data_fpath
  - train.model_fpath -> evaluate.model_fpath
"""


MATRIX_ROWS: list[dict[str, object]] = [
    {
        'preprocess.src_fpath': '/data/reviews.txt',
        'train.learning_rate': 0.001,
        'train.workers': 2,
    },
    {
        'preprocess.src_fpath': '/data/reviews.txt',
        'train.learning_rate': 0.001,
        'train.workers': 8,
    },
    {
        'preprocess.src_fpath': '/data/reviews.txt',
        'train.learning_rate': 0.01,
        'train.workers': 8,
    },
]


def _ensure_parent(fpath: Path) -> None:
    fpath.parent.mkdir(parents=True, exist_ok=True)


class PreprocessCLI(kw.Config):
    """Tiny pipeline stage used by the YAML example."""

    src_fpath = kw.Value(None, type=str, help='input text file')
    dst_fpath = kw.Value(None, type=str, help='preprocessed output text file')
    normalize = kw.Value(
        True,
        type=bool,
        help='lowercase and normalize whitespace',
    )

    @classmethod
    def main(cls, argv: int | bool | list[str] = 1, **kwargs: Any) -> None:
        config = cls.cli(argv=argv, data=kwargs, strict=True)

        text = Path(config.src_fpath).read_text()
        if config.normalize:
            text = ' '.join(text.lower().split()) + '\n'

        dst_fpath = Path(config.dst_fpath)
        _ensure_parent(dst_fpath)
        dst_fpath.write_text(text)
        print(f'wrote {dst_fpath}')


class TrainCLI(kw.Config):
    """Tiny pipeline stage used by the YAML example."""

    train_fpath = kw.Value(None, type=str, help='preprocessed training text')
    model_fpath = kw.Value(None, type=str, help='output JSON model')
    learning_rate = kw.Value(
        0.001,
        type=float,
        help='dummy algorithm parameter',
    )
    workers = kw.Value(4, type=int, help='dummy performance parameter')

    @classmethod
    def main(cls, argv: int | bool | list[str] = 1, **kwargs: Any) -> None:
        config = cls.cli(argv=argv, data=kwargs, strict=True)

        text = Path(config.train_fpath).read_text()
        model = {
            'learning_rate': config.learning_rate,
            'num_characters': len(text),
        }

        model_fpath = Path(config.model_fpath)
        _ensure_parent(model_fpath)
        model_fpath.write_text(json.dumps(model, indent=2) + '\n')
        print(f'wrote {model_fpath}')


class EvaluateCLI(kw.Config):
    """Tiny pipeline stage used by the YAML example."""

    data_fpath = kw.Value(None, type=str, help='preprocessed evaluation text')
    model_fpath = kw.Value(None, type=str, help='input JSON model')
    metrics_fpath = kw.Value(None, type=str, help='output JSON metrics')
    metric = kw.Value('accuracy', type=str, help='dummy metric name')

    @classmethod
    def main(cls, argv: int | bool | list[str] = 1, **kwargs: Any) -> None:
        config = cls.cli(argv=argv, data=kwargs, strict=True)

        text = Path(config.data_fpath).read_text()
        model = json.loads(Path(config.model_fpath).read_text())
        expected = model['num_characters']
        score = 1.0 if len(text) == expected else 0.0
        metrics = {
            'metric': config.metric,
            'score': score,
        }

        metrics_fpath = Path(config.metrics_fpath)
        _ensure_parent(metrics_fpath)
        metrics_fpath.write_text(json.dumps(metrics, indent=2) + '\n')
        print(f'wrote {metrics_fpath}')


def _resolved_pipeline_yaml() -> str:
    """Make the YAML's three executables point back to this Python file."""
    script_fpath = Path(__file__).resolve()
    interpreter = shlex.quote(sys.executable)
    script = shlex.quote(str(script_fpath))
    return PIPELINE_YAML.replace('python THIS_FILE', f'{interpreter} {script}')


def _qualified_port_names(ports: Iterable[IONode]) -> list[str]:
    return [f'{port.parent.name}.{port.name}' for port in ports]


def inspect_networkx_graphs(dag: Pipeline) -> None:
    """Show that the YAML pipeline is directly queryable as NetworkX graphs."""
    proc_graph = dag.proc_graph
    io_graph = dag.io_graph

    print('\n=== NetworkX graph views ===')
    print('available graph layers:')
    for graph_name in ['proc_graph', 'io_graph', 'config_graph', 'value_graph']:
        graph = getattr(dag, graph_name)
        print(
            f'  {graph_name}: {type(graph).__name__}, '
            f'{graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges'
        )
    print('process graph is a DAG:', nx.is_directed_acyclic_graph(proc_graph))
    print(
        'graph node carries ProcessNode object:',
        proc_graph.nodes['train']['node'] is dag.node_dict['train'],
    )

    # KWDagger's graph printers use networkx.write_network_text internally.
    # The process view shows coarse job dependencies; the IO view expands the
    # same pipeline into process and port nodes so the actual data wiring is
    # visible.
    dag.print_process_graph(shrink_labels=1, show_types=1)
    dag.print_io_graph(shrink_labels=1, show_types=1)

    roots = sorted(node for node, degree in proc_graph.in_degree() if degree == 0)
    leaves = sorted(node for node, degree in proc_graph.out_degree() if degree == 0)
    generations = [
        sorted(generation) for generation in nx.topological_generations(proc_graph)
    ]
    print('\nNetworkX structural queries:')
    print('roots:', roots)
    print('leaves:', leaves)
    print('topological order:', list(nx.topological_sort(proc_graph)))
    print('topological generations:')
    pprint(generations)
    print('descendants of preprocess:')
    pprint(sorted(nx.descendants(proc_graph, 'preprocess')))
    print('ancestors of evaluate:')
    pprint(sorted(nx.ancestors(proc_graph, 'evaluate')))
    print('all process paths from preprocess to evaluate:')
    pprint(list(nx.all_simple_paths(proc_graph, 'preprocess', 'evaluate')))

    print('\nNetworkX IO-path queries:')
    pprint(
        nx.shortest_path(
            io_graph,
            'preprocess.dst_fpath',
            'evaluate.model_fpath',
        )
    )
    print('IO node types:')
    pprint(
        {
            key: data['node_clsname']
            for key, data in sorted(io_graph.nodes(data=True))
        }
    )


def inspect_logical_pipeline(dag: Pipeline) -> None:
    """Inspect the declarative graph reconstructed from YAML."""
    print('\n=== Logical pipeline loaded from YAML ===')
    print('node names:')
    pprint(list(dag.node_dict))

    print('process edges:')
    pprint(sorted(dag.proc_graph.edges()))

    evaluate = dag.node_dict['evaluate']
    print('direct predecessors of evaluate:')
    pprint(sorted(node.name for node in evaluate.predecessor_process_nodes()))

    print('all ancestors of evaluate:')
    pprint(sorted(node.name for node in evaluate.ancestor_process_nodes()))

    model_input = evaluate.inputs['model_fpath']
    print('ports wired into evaluate.model_fpath:')
    pprint(_qualified_port_names(model_input.pred))

    print('node interfaces and declared defaults:')
    for name, node in dag.node_dict.items():
        info = {
            'executable': node.executable,
            'inputs': sorted(node.inputs),
            'outputs': sorted(node.outputs),
            'algo_params': dict(node.algo_params),
            'perf_params': dict(node.perf_params),
        }
        print(f'  {name}:')
        pprint(info, indent=4)

    assert sorted(dag.proc_graph.edges()) == [
        ('preprocess', 'evaluate'),
        ('preprocess', 'train'),
        ('train', 'evaluate'),
    ]
    assert _qualified_port_names(model_input.pred) == ['train.model_fpath']


def inspect_round_trip(dag: Pipeline) -> None:
    """Recover the declarative form and load it again."""
    print('\n=== YAML round-trip ===')
    recovered_spec = dag.to_yaml_spec()
    pprint(recovered_spec)

    restored = kwdagger.load_yaml_pipeline(recovered_spec)
    assert sorted(restored.node_dict) == sorted(dag.node_dict)
    assert sorted(restored.proc_graph.edges()) == sorted(dag.proc_graph.edges())
    print('round-trip graph equivalence: OK')


def inspect_compiled_pipeline(dag: Pipeline, root_dpath: str) -> CompiledPipeline:
    """Inspect concrete process instances generated from three matrix rows."""
    print('\n=== Compiled matrix ===')
    compiled = dag.compile_configurations(
        MATRIX_ROWS,
        root_dpath=root_dpath,
        cache=False,
    )

    counts = {
        name: len(nodes)
        for name, nodes in sorted(compiled.nodes_by_name.items())
    }
    print('requested matrix rows:', len(MATRIX_ROWS))
    print('concrete process instances by logical node:')
    pprint(counts)
    print('compile summary:')
    pprint(compiled.compile_summary)

    # This is a compact logical rendering of the concrete expansion. It makes
    # fan-out / fan-in and the number of concrete instances visible without
    # replacing the logical node names with long process ids.
    compiled.print_cardinality_graph()

    # Rows 0 and 1 differ only in train.workers.  workers is a performance
    # parameter, so both requests have the same process identity and the first
    # request is the representative.  Row 2 changes an algorithm parameter and
    # therefore creates a second train process and downstream evaluate process.
    assert counts == {'evaluate': 2, 'preprocess': 1, 'train': 2}
    assert compiled.compile_summary['concrete_nodes'] == 5

    print('\nconcrete train instances:')
    for node in compiled.nodes_by_name['train']:
        info = {
            'process_id': node.process_id,
            'algo_config': dict(node.final_algo_config),
            'perf_config': dict(node.final_perf_config),
            'input_config': {
                key: str(value) for key, value in node.final_input_config.items()
            },
            'output_paths': {
                key: str(value) for key, value in node.final_out_paths.items()
            },
            'command': node.final_command(),
        }
        pprint(info)

    print('\ncompiled process graph edges (process ids):')
    pprint(sorted(compiled.proc_graph.edges()))

    print('\ncompiled NetworkX topological generations:')
    for generation in nx.topological_generations(compiled.proc_graph):
        labels = [
            f'{compiled.nodes[process_id].name}: {process_id}'
            for process_id in generation
        ]
        pprint(labels)

    sink_ids = sorted(
        process_id
        for process_id, degree in compiled.proc_graph.out_degree()
        if degree == 0
    )
    print('\ncompiled sink processes and their concrete ancestors:')
    for sink_id in sink_ids:
        sink = compiled.nodes[sink_id]
        ancestor_ids = sorted(nx.ancestors(compiled.proc_graph, sink_id))
        ancestor_names = [compiled.nodes[pid].name for pid in ancestor_ids]
        print(f'{sink.name}: {sink_id}')
        pprint(ancestor_names)

    return compiled


class IntrospectCLI(kw.Config):
    """Inspect the YAML pipeline without executing it."""

    root_dpath = kw.Value(
        '/tmp/kwdagger-yaml-introspection',
        type=str,
        help='root used when resolving concrete output paths and commands',
    )

    @classmethod
    def main(cls, argv: int | bool | list[str] = 1, **kwargs: Any) -> None:
        config = cls.cli(argv=argv, data=kwargs, strict=True)

        pipeline_yaml = _resolved_pipeline_yaml()
        dag = kwdagger.load_yaml_pipeline(pipeline_yaml)
        inspect_networkx_graphs(dag)
        inspect_logical_pipeline(dag)
        inspect_round_trip(dag)
        inspect_compiled_pipeline(dag, root_dpath=config.root_dpath)


class RunCLI(kw.Config):
    """Compile and execute the YAML pipeline with the serial backend."""

    root_dpath = kw.Value(
        '/tmp/kwdagger-yaml-introspection',
        type=str,
        help='root used when resolving concrete output paths and commands',
    )

    @classmethod
    def main(cls, argv: int | bool | list[str] = 1, **kwargs: Any) -> None:
        config = cls.cli(argv=argv, data=kwargs, strict=True)

        root_dpath = Path(config.root_dpath).expanduser().resolve()
        root_dpath.mkdir(parents=True, exist_ok=True)

        # Keep the runnable example self-contained.  The introspection matrix
        # uses a conspicuous placeholder input path because it never executes
        # jobs; the run command replaces that path with a real tiny input.
        src_fpath = root_dpath / 'demo_input.txt'
        src_fpath.write_text('The QUICK brown fox.\nThe quick brown fox.\n')

        run_rows = []
        for row in MATRIX_ROWS:
            run_row = dict(row)
            run_row['preprocess.src_fpath'] = str(src_fpath)
            run_rows.append(run_row)

        pipeline_yaml = _resolved_pipeline_yaml()
        dag = kwdagger.load_yaml_pipeline(pipeline_yaml)
        compiled = dag.compile_configurations(
            run_rows,
            root_dpath=str(root_dpath),
            cache=False,
        )

        print('\n=== Running compiled pipeline ===')
        print('root_dpath:', root_dpath)
        print('requested matrix rows:', len(run_rows))
        print('concrete processes:', len(compiled.nodes))

        status = compiled.submit_jobs(
            queue={'backend': 'serial', 'name': 'yaml-introspection-example'},
            skip_existing=False,
            log=False,
        )
        queue = status['queue']
        queue.run()

        print('\n=== Evaluation outputs ===')
        for node in compiled.nodes_by_name['evaluate']:
            metrics_fpath = Path(node.final_out_paths['metrics_fpath'])
            metrics = json.loads(metrics_fpath.read_text())
            print(f'{node.process_id}: {metrics_fpath}')
            pprint(metrics)


class YamlIntrospectionExampleCLI(kw.ModalCLI):
    """Worker, introspection, and execution commands for this example."""

    preprocess = PreprocessCLI
    train = TrainCLI
    evaluate = EvaluateCLI
    introspect = IntrospectCLI
    run = RunCLI


__cli__ = YamlIntrospectionExampleCLI


if __name__ == '__main__':
    __cli__.main()
