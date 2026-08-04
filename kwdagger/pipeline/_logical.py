"""
The logical pipeline: :class:`Pipeline`, graph construction, and diagnostics.

This is the layer users build directly -- a set of process nodes and the edges
between them -- plus the graph views that make those edges inspectable, and the
coercion that turns a concise expression or a YAML path into a pipeline.

It sits on top of the connection, process, and compilation layers, and reaches
the runtime submitter through a deferred import so that submission depends on
the pipeline rather than the other way around.
"""

from __future__ import annotations

import os
from collections import defaultdict

# From collections.abc, not typing: `isinstance(x, typing.Mapping)` gives a
# type checker no class to narrow on, so a `str | Mapping` union stays a union
# inside the isinstance branch and every `key['src']` looks like an error. The
# typing aliases have been deprecated since 3.9 in any case.
from collections.abc import Mapping, Sequence
from typing import Any

import networkx as nx
import ubelt as ub

from kwdagger.pipeline._compile import (
    CompiledPipeline,
    _compile_pipeline_configurations,
)
from kwdagger.pipeline._config_values import normalize_config
from kwdagger.pipeline._connections import (
    GatherConnection,
    _alias_preds,
    _produced_origins,
)
from kwdagger.pipeline._process import ProcessNode
from kwdagger.pipeline._runtime import QueueSpec
from kwdagger.pipeline._slurm import layer_slurm_options
from kwdagger.utils import util_dotdict


class Pipeline:
    """
    A container for a group of nodes that have been connected.

    Allows these connected nodes to be jointly configured and submitted to a
    cmd-queue for execution. Adds extra bookkeeping jobs that write invoke.sh
    job_config.sh metadata as well as symlinks between node output directories.

    Example:
        >>> from kwdagger.pipeline import Pipeline, ProcessNode
        >>> node_A1 = ProcessNode(name='node_A1', in_paths={'src'}, out_paths={'dst': 'dst.txt'}, executable='node_A1')
        >>> node_A2 = ProcessNode(name='node_A2', in_paths={'src'}, out_paths={'dst': 'dst.txt'}, executable='node_A2')
        >>> node_A3 = ProcessNode(name='node_A3', in_paths={'src'}, out_paths={'dst': 'dst.txt'}, executable='node_A3')
        >>> node_B1 = ProcessNode(name='node_B1', in_paths={'path1'}, out_paths={'path2': 'dst.txt'}, executable='node_B1')
        >>> node_B2 = ProcessNode(name='node_B2', in_paths={'path2'}, out_paths={'path3': 'dst.txt'}, executable='node_B2')
        >>> node_B3 = ProcessNode(name='node_B3', in_paths={'path3'}, out_paths={'path4': 'dst.txt'}, executable='node_B3')
        >>> node_C1 = ProcessNode(name='node_C1', in_paths={'src1', 'src2'}, out_paths={'dst1': 'dst.txt', 'dst2': 'dst.txt'}, executable='node_C1')
        >>> node_C2 = ProcessNode(name='node_C2', in_paths={'src1', 'src2'}, out_paths={'dst1': 'dst.txt', 'dst2': 'dst.txt'}, executable='node_C2')
        >>> # You can connect outputs -> inputs directly (RECOMMENDED)
        >>> node_A1.outputs['dst'].connect(node_A2.inputs['src'])
        >>> node_A2.outputs['dst'].connect(node_A3.inputs['src'])
        >>> # You can connect nodes to nodes that share input/output names (NOT RECOMMENDED)
        >>> node_B1.connect(node_B2)
        >>> node_B2.connect(node_B3)
        >>> #
        >>> # Ports that are named differently must be named explicitly
        >>> node_A3.outputs['dst'].connect(node_B1.inputs['path1'])
        >>> #
        >>> # You can connect inputs to other inputs, which effectively
        >>> # forwards the input path to the destination
        >>> node_A1.inputs['src'].connect(node_C1.inputs['src1'])
        >>> # The pipeline is just a container for the nodes
        >>> nodes = [node_A1, node_A2, node_A3, node_B1, node_B2, node_B3, node_C1, node_C2]
        >>> self = Pipeline(nodes=nodes)
        >>> self.print_graphs()
    """

    def __init__(
        self, nodes: Any = None, config: Any = None, root_dpath: Any = None
    ) -> None:
        self.proc_graph: nx.DiGraph = nx.DiGraph()
        self.config_graph: nx.DiGraph = nx.DiGraph()
        self.value_graph: nx.DiGraph = nx.DiGraph()
        self.io_graph: nx.DiGraph = nx.DiGraph()
        if nodes is None:
            nodes = []
        self.nodes = nodes
        self.config: Any = None
        #: Persistent pipeline-wide defaults: what every row gets unless it
        #: asks for something else. Set by the CLI or a Python caller, never
        #: by a matrix row.
        self._base_slurm_options: dict[str, Any] = {}
        #: The options the *current* row requested, base included. Reset from
        #: the base on every ``configure`` -- a row that omits them is asking
        #: for the default, not for whatever the previous row happened to ask
        #: for, and arbitration compares this to decide whether two rows want
        #: the same resources.
        self.__slurm_options__: dict[str, Any] = {}

        self._dirty = True
        self._unique_hanes: set[str] = set()

        if self.nodes:
            self.build_nx_graphs()

        if config:
            self.configure(config, root_dpath=root_dpath)

    @classmethod
    def demo(cls) -> 'Pipeline':
        from kwdagger.demo.demodata import demodata_pipeline

        return demodata_pipeline()

    def to_yaml_spec(self) -> dict[str, Any]:
        """
        Serialize this pipeline to the declarative dict form.

        This is the inverse of :func:`kwdagger.load_yaml_pipeline`: the returned
        mapping can be fed back through :func:`coerce_pipeline` to reconstruct an
        equivalent pipeline. Custom :class:`ProcessNode` subclasses are emitted
        as ``class`` references (their behavior lives in code, not data).

        Returns:
            dict: a ``{'nodes': ..., 'edges': ...}`` mapping.
        """
        from kwdagger.yaml_pipeline import dump_yaml_pipeline

        return dump_yaml_pipeline(self)

    def _ensure_clean(self) -> None:
        if self._dirty:
            self.build_nx_graphs()

    def submit(self, executable: str, **kwargs: Any) -> Any:
        """
        Dynamically create a new unique process node and add it to the dag

        Is this ever used? May be able to simplify and remove this.
        """
        name = kwargs.get('name', None)
        if name is not None:
            if name in self._unique_hanes:
                raise Exception(name)
            self._unique_hanes.add(name)
        task = ProcessNode(executable=executable, **kwargs)
        self.nodes.append(task)
        self._dirty = True
        return task

    @property
    def node_dict(self) -> dict[str, Any]:
        if isinstance(self.nodes, dict):
            node_dict = self.nodes
        else:
            node_names = [node.name for node in self.nodes]
            if len(node_names) != len(set(node_names)):
                print('node_names = {}'.format(ub.urepr(node_names, nl=1)))
                raise AssertionError(
                    f'Non unique nodes detected: {len(node_names)}, {len(set(node_names))}'
                )
            node_dict = dict(zip(node_names, self.nodes))
        return node_dict

    @property
    def gather_connections(self) -> list[GatherConnection]:
        """Return all template-level gather edges in this pipeline."""
        connections = []
        for node in self.node_dict.values():
            for input_node in node.inputs.values():
                connection = input_node._gather_connection
                if connection is not None:
                    connections.append(connection)
        return connections

    @property
    def has_gather_connections(self) -> bool:
        return bool(self.gather_connections)

    def build_nx_graphs(self) -> None:
        node_dict = self.node_dict

        # A ProcessNode memoizes lineage queries while it is being
        # constructed -- before it has been connected to anything -- and only
        # ``configure`` clears that cache. Every one of those answers is stale
        # by the time a pipeline exists. This is the first moment the complete
        # connection state is known, so drop them here and let the queries
        # below recompute. Without this, a template pipeline reports the
        # lineage of a graph that was never built.
        for node in node_dict.values():
            node._configured_cache.clear()

        self.proc_graph = nx.DiGraph()
        for name, node in node_dict.items():
            self.proc_graph.add_node(node.name, node=node)

            for s in node.successor_process_nodes():
                self.proc_graph.add_edge(node.name, s.name)

            for p in node.predecessor_process_nodes():
                self.proc_graph.add_edge(p.name, node.name)

            # This list is mutated directly by the legacy API, so a cached
            # predecessor query may predate the append. Record these explicit
            # ordering dependencies from their source of truth as well.
            for p in node._pred_nodes_without_io_connection:
                self.proc_graph.add_edge(p.name, node.name)

            # Producers standing behind an input are read from the ports
            # rather than inferred from the successor pass: an alias has no
            # successor edge into this node, and a gather manifest is
            # produced by a port rather than by an output at all.
            for input_node in node.inputs.values():
                for origin in _produced_origins(input_node):
                    self.proc_graph.add_edge(origin.parent.name, node.name)

        for connection in self.gather_connections:
            self.proc_graph.add_edge(
                connection.source.parent.name,
                connection.target.parent.name,
                gather=connection.spec,
            )

        # Configuration-value relationships are not process dependencies, but
        # their sources must be configured before their targets can resolve a
        # shared value. Keep that ordering separate from ``proc_graph`` so an
        # alias does not create execution or lineage dependency.
        self.config_graph = self.proc_graph.copy()
        for _src, _dst, edge_data in self.config_graph.edges(data=True):
            edge_data['config_kinds'] = ('process_dependency',)

        self.value_graph = nx.DiGraph()

        def _add_config_edge(source: Any, target: Any, kind: str) -> None:
            self.value_graph.add_edge(
                source.key, target.key, kind=kind, source=source, target=target
            )
            src_name = source.parent.name
            dst_name = target.parent.name
            if src_name == dst_name:
                # All local values are assigned before a ProcessNode finalizes
                # its command, so same-node forwarding needs no node ordering.
                return
            if self.config_graph.has_edge(src_name, dst_name):
                edge_data = self.config_graph.edges[src_name, dst_name]
                kinds = set(edge_data.get('config_kinds', ()))
                kinds.add(kind)
                edge_data['config_kinds'] = tuple(sorted(kinds))
            else:
                self.config_graph.add_edge(
                    src_name, dst_name, config_kinds=(kind,)
                )

        for node in node_dict.values():
            for input_node in node.inputs.values():
                for source in _alias_preds(input_node):
                    _add_config_edge(source, input_node, 'shared_input')
            for param_port in node.param_ports.values():
                for source in param_port.pred:
                    _add_config_edge(source, param_port, 'shared_parameter')

        if not nx.is_directed_acyclic_graph(self.value_graph):
            cycle = nx.find_cycle(self.value_graph)
            details = []
            for src, dst in cycle:
                kind = self.value_graph.edges[src, dst]['kind']
                details.append(f'{src} -{kind}-> {dst}')
            raise ValueError(
                'Pipeline shared-value relationships contain a cycle: '
                + ' ; '.join(details)
            )

        if not nx.is_directed_acyclic_graph(self.config_graph):
            cycle = nx.find_cycle(self.config_graph)
            details = []
            for src, dst in cycle:
                kinds = self.config_graph.edges[src, dst].get(
                    'config_kinds', ('process_dependency',)
                )
                details.append(f'{src} -{",".join(kinds)}-> {dst}')
            raise ValueError(
                'Pipeline configuration relationships contain a cycle: '
                + ' ; '.join(details)
            )

        self.io_graph = nx.DiGraph()

        # Add nodes first
        for name, node in node_dict.items():
            self.io_graph.add_node(
                node.key, node=node, node_clsname=node.__class__.__name__
            )
            for iname, inode in node.inputs.items():
                self.io_graph.add_node(
                    inode.key, node=inode, node_clsname=inode.__class__.__name__
                )
            for oname, onode in node.outputs.items():
                self.io_graph.add_node(
                    onode.key, node=onode, node_clsname=onode.__class__.__name__
                )
            # Only *wired* parameters belong in the IO graph. An unwired
            # parameter has no relationship to display, and a node commonly
            # declares dozens of them, which would bury the data flow the
            # graph exists to show.
            for pname, pnode in node.param_ports.items():
                if pnode.pred or pnode.succ:
                    self.io_graph.add_node(
                        pnode.key,
                        node=pnode,
                        node_clsname=pnode.__class__.__name__,
                    )

        # Next add edges
        for name, node in node_dict.items():
            for iname, inode in node.inputs.items():
                self.io_graph.add_edge(inode.key, node.key)
                # Account for input/input connections
                for succ in inode.succ:
                    self.io_graph.add_edge(
                        inode.key, succ.key, shared_kind='input'
                    )
            for oname, onode in node.outputs.items():
                self.io_graph.add_edge(node.key, onode.key)
                for oi_node in onode.succ:
                    self.io_graph.add_edge(onode.key, oi_node.key)
                for connection in onode._gather_connections:
                    self.io_graph.add_edge(
                        onode.key,
                        connection.target.key,
                        gather=connection.spec,
                    )
            for pname, pnode in node.param_ports.items():
                if not (pnode.pred or pnode.succ):
                    continue
                self.io_graph.add_edge(pnode.key, node.key, parameter=True)
                for succ in pnode.succ:
                    self.io_graph.add_edge(
                        pnode.key, succ.key, shared_kind='parameter'
                    )
            # hack for nodes that dont have an io dependency
            # but still must run after one another
            for pred in node._pred_nodes_without_io_connection:
                self.io_graph.add_edge(pred.key, node.key)

        self._dirty = False

    def inspect_configurables(self) -> None:
        """
        Show the user what config options should be specified.

        TODO:
            The idea is that we want to give the user a list of options that
            they could configure for this pipeline, as well as mark the one
            that are required / suggested / unnecessary. For now it gives a
            little bit of that information, but more work could be done to make
            it nicer.

        Example:
            >>> from kwdagger.pipeline import Pipeline
            >>> self = Pipeline.demo()
            >>> self.inspect_configurables()
        """
        import pandas as pd
        import rich
        from kwutil import util_yaml
        # Nodes don't always have full knowledge of their entire parameter
        # space, but they should at least have some knowledge of it.
        # Find required inputs
        # required_inputs = {
        #     n for n in self.io_graph if self.io_graph.in_degree[n] == 0
        # }

        rows: list[dict[str, Any]] = []
        assert isinstance(self.io_graph, nx.DiGraph)
        for node in self.node_dict.values():
            # Build up information about each node option

            # TODO: determine if a source input node is required or not
            # by setting a required=False flags at the node leve.

            # Determine which inputs are connected vs unconnected
            for key, io_node in node.inputs.items():
                is_connected = self.io_graph.in_degree[io_node.key] > 0
                rows.append(
                    {
                        'node': node.name,
                        'key': key,
                        'connected': is_connected,
                        'type': 'in_path',
                        'maybe_required': not is_connected,
                    }
                )

            for key, io_node in node.outputs.items():
                is_connected = self.io_graph.out_degree[io_node.key] > 0
                rows.append(
                    {
                        'node': node.name,
                        'key': key,
                        'connected': is_connected,
                        'type': 'out_path',
                        'maybe_required': False,
                    }
                )

            for param in node.algo_params:
                rows.append(
                    {
                        'node': node.name,
                        'key': param,
                        'type': 'algo_param',
                        'maybe_required': True,
                    }
                )

            for param in node.perf_params:
                rows.append(
                    {
                        'node': node.name,
                        'key': param,
                        'type': 'perf_param',
                        'maybe_required': True,
                    }
                )

        df = pd.DataFrame(rows)
        df = df.sort_values(
            ['maybe_required', 'type', 'node', 'key'],
            ascending=[False, True, True, True],
        )
        rich.print(df.to_string())

        default: dict[str, Any] = {}
        for _, row in df[df['maybe_required']].iterrows():
            default[str(row['node']) + '.' + str(row['key'])] = None
        rich.print(util_yaml.Yaml.dumps(default))

    def configure(
        self,
        config: Mapping[str, Any] | None = None,
        root_dpath: str | os.PathLike[str] | None = None,
        cache: bool = True,
    ) -> None:
        """
        Update the DAG configuration

        Note:
            Currently, this will completely reset the config, and not update
            it. This behavior will change in the future

        Example:
            >>> from kwdagger.pipeline import Pipeline
            >>> self = Pipeline.demo()
            >>> self.configure()
        """
        self._ensure_clean()

        if root_dpath is not None:
            root_dpath = ub.Path(root_dpath)
            self.root_dpath = root_dpath
            for node in self.node_dict.values():
                node.root_dpath = root_dpath
                node._configured_cache.clear()  # hack, make more elegant
        else:
            for node in self.node_dict.values():
                node._configured_cache.clear()  # hack, make more elegant

        assert isinstance(self.proc_graph, nx.DiGraph)
        if config is not None:
            config = dict(config)
            self.__slurm_options__ = layer_slurm_options(
                self._base_slurm_options,
                config.pop('__slurm_options__', None),
            )
            # The requested row crosses the boundary once, here, rather than
            # each node re-deriving it: what a reader of ``Pipeline.config``
            # sees is then the same shape the nodes were configured with.
            config = normalize_config(config)
            self.config = config
            # print('CONFIGURE config = {}'.format(ub.urepr(config, nl=1)))

            # Set the configuration for each node in this pipeline.
            dotconfig = util_dotdict.DotDict(config)
            for node_name in nx.topological_sort(self.config_graph):
                node = self.config_graph.nodes[node_name]['node']
                node_config = dict(dotconfig.prefix_get(node.name, {}))
                node.configure(node_config, cache=cache)
        else:
            # Hack: if config is not given, update the cache state only.
            for node_name in nx.topological_sort(self.config_graph):
                node = self.config_graph.nodes[node_name]['node']
                node.configure(config=node.config, cache=cache)

    def compile_configurations(
        self,
        configs: Sequence[Mapping[str, Any]],
        root_dpath: Any = None,
        cache: bool = True,
    ) -> 'CompiledPipeline':
        """
        Compile matrix rows into one concrete, static process graph.

        This is required for gather edges because a target instance needs to
        see source instances configured by multiple matrix rows before its
        input manifest and process identity can be finalized.
        """
        self._ensure_clean()
        if not self.has_gather_connections:
            raise ValueError(
                'compile_configurations is currently intended for pipelines '
                'with gather connections'
            )
        return _compile_pipeline_configurations(
            self,
            configs=configs,
            root_dpath=root_dpath,
            cache=cache,
        )

    def _process_display_graph(
        self, shrink_labels: int = 1, show_types: int = 0
    ) -> nx.DiGraph:
        """Build a display-only process graph with gather edges made explicit."""
        self._ensure_clean()
        graph = self.proc_graph.copy()
        _labelize_graph(graph, shrink_labels, show_types)

        gather_by_pair: dict[tuple[str, str], list[GatherConnection]] = (
            defaultdict(list)
        )
        for connection in self.gather_connections:
            pair = (
                connection.source.parent.name,
                connection.target.parent.name,
            )
            gather_by_pair[pair].append(connection)

        for marker_idx, ((src, dst), connections) in enumerate(
            gather_by_pair.items()
        ):
            if graph.has_edge(src, dst):
                graph.remove_edge(src, dst)
            labels = []
            for connection in connections:
                spec = connection.spec
                label = 'gather N:1'
                if spec.group_by:
                    label += ' | group_by=' + ','.join(spec.display_keys())
                else:
                    label += ' | group_by=<all>'
                if spec.order_by:
                    label += ' | order_by=' + ','.join(spec.order_by)
                labels.append(label)
            marker = f'__gather_process_edge_{marker_idx}'
            graph.add_node(
                marker,
                label='[bright_magenta]'
                + ' ; '.join(labels)
                + '[/bright_magenta]',
            )
            graph.add_edge(src, marker)
            graph.add_edge(marker, dst)
        return graph

    def _io_display_graph(
        self, shrink_labels: int = 1, show_types: int = 0
    ) -> nx.DiGraph:
        """Build a display-only IO graph with collection artifacts visible."""
        self._ensure_clean()
        graph = self.io_graph.copy()
        _labelize_graph(graph, shrink_labels, show_types, color_procs=True)

        marker_idx = 0
        for src, dst, edge_data in list(graph.edges(data=True)):
            spec = edge_data.get('gather')
            shared_kind = edge_data.get('shared_kind')
            if spec is None and shared_kind is None:
                continue
            graph.remove_edge(src, dst)
            if spec is not None:
                label = 'gather N:1 -> path manifest'
                if spec.group_by:
                    label += ' | group_by=' + ','.join(spec.display_keys())
                else:
                    label += ' | group_by=<all>'
                if spec.order_by:
                    label += ' | order_by=' + ','.join(spec.order_by)
                marker = f'__gather_io_edge_{marker_idx}'
                styled_label = f'[bright_magenta]{label}[/bright_magenta]'
                target_label = graph.nodes[dst].get('label', dst)
                if '(collection)' not in target_label:
                    graph.nodes[dst]['label'] = target_label + ' (collection)'
            else:
                label = f'shared {shared_kind} | configuration only'
                marker = f'__shared_io_edge_{marker_idx}'
                styled_label = f'[bright_blue]{label}[/bright_blue]'
            marker_idx += 1
            graph.add_node(marker, label=styled_label)
            graph.add_edge(src, marker)
            graph.add_edge(marker, dst)
        return graph

    def print_process_graph(
        self, shrink_labels: int = 1, show_types: int = 0
    ) -> None:
        """
        Draw the logical process graph.

        Gather edges are represented by display-only ``gather N:1`` marker
        nodes so many-to-one semantics are not mistaken for ordinary direct
        dependencies. The marker is not an executable process node.
        """
        import networkx as nx
        import rich

        graph = self._process_display_graph(shrink_labels, show_types)
        print('')
        print('Process Graph')
        nx.write_network_text(
            graph, path=rich.print, end='', vertical_chains=True
        )

    def print_io_graph(
        self, shrink_labels: int = 1, show_types: int = 0
    ) -> None:
        """
        Draw the logical IO graph.

        Gather edges show the compile-time path-manifest conversion and mark
        the receiving input as collection-valued.
        """
        import networkx as nx
        import rich

        graph = self._io_display_graph(shrink_labels, show_types)
        print('')
        print('IO Graph')
        nx.write_network_text(
            graph, path=rich.print, end='', vertical_chains=True
        )

    def print_commands(self, **kwargs: Any) -> None:
        """
        Helper (mostly for debugging) to show the commands for the current
        pipeline configuration. This involves making a cmdqueue instance, which
        is the real object that knows how to structure the commmand sequence.

        Args:
            **kwargs:
                See :func:`cmd_queue.base_queue.Queue.print_commands`
                with_status=False, with_gaurds=False, with_locks=1,
                exclude_tags=None, style='colors', **kwargs
        """
        queue = self.make_queue()['queue']
        queue.print_commands(**kwargs)

    def print_graphs(self, shrink_labels: int = 1, show_types: int = 0) -> None:
        """
        Prints the Process and IO graph for the DAG.
        """
        self.print_process_graph(
            shrink_labels=shrink_labels, show_types=show_types
        )
        self.print_io_graph(shrink_labels=shrink_labels, show_types=show_types)

    def effective_execution_graph(self) -> nx.DiGraph:
        """
        What each configured command actually requires, by node name.

        :attr:`proc_graph` is the template: it answers which dependencies are
        *possible*, before anything is configured, and it has to stay
        structural because at that point no value has been resolved. This is
        the configured counterpart, and it is what execution runs on.

        The two differ only where something outranks a producer -- an explicit
        path, or a value forwarded from a peer port. There the wired producer
        supplies nothing, and the difference matters because the runtime does
        not treat a predecessor as a mere ordering hint: a disabled or missing
        predecessor suppresses its successor. Gating a command on a producer
        it never reads can silently skip valid work.

        Returns:
            nx.DiGraph: nodes keyed by name, carrying ``node``, as
                :attr:`proc_graph` does.
        """
        graph = nx.DiGraph()
        for name, node in self.node_dict.items():
            graph.add_node(node.name, node=node)
        for name, node in self.node_dict.items():
            for pred in node.effective_predecessor_process_nodes():
                graph.add_edge(pred.name, node.name)
        return graph

    def submit_jobs(
        self,
        queue: QueueSpec = None,
        skip_existing: bool = False,
        enable_links: bool = True,
        write_invocations: bool = True,
        write_configs: bool = True,
        log: bool = True,
    ) -> dict[str, Any]:
        """
        Submits the jobs to an existing command queue or creates a new one.

        See :func:`kwdagger.pipeline._runtime.submit_jobs` for the arguments
        and for what gets written to the result directories.
        """
        from kwdagger.pipeline import _runtime

        if self.has_gather_connections:
            # A gather's membership is only known once the whole matrix has
            # been compiled, so a logical pipeline cannot answer what a
            # consumer's collection contains. This precondition belongs to
            # the logical layer, not to submission.
            raise RuntimeError(
                'Gather pipelines must be compiled across all matrix rows '
                'before submission. Use Pipeline.compile_configurations(...) '
                'or kwdagger schedule.'
            )
        return _runtime.submit_jobs(
            # The execution graph, not the template one: submission has to
            # ask what each configured command actually requires.
            self.effective_execution_graph(),
            slurm_options=self.__slurm_options__,
            queue=queue,
            skip_existing=skip_existing,
            enable_links=enable_links,
            write_invocations=write_invocations,
            write_configs=write_configs,
            log=log,
        )

    make_queue = submit_jobs


def _labelize_graph(
    graph: Any, shrink_labels: Any, show_types: Any, color_procs: int = 0
) -> None:
    """
    Add a label to a networkx graph with rich colors specific to this use-case.
    """
    colors = ['bright_magenta', 'yellow', 'cyan']
    unused_colors = colors.copy()
    clsname_to_color: dict[str, str | None] = {
        'ProcessNode': 'yellow',
        'InputNode': 'bright_cyan',
        'OutputNode': 'bright_yellow',
        'ParamNode': 'bright_green',
    }
    clsname_to_typename = {
        'ProcessNode': 'proc',
        'InputNode': 'in',
        'OutputNode': 'out',
        'ParamNode': 'param',
    }

    all_names = []
    for _, data in graph.nodes(data=True):
        all_names.append(data['node'].name)

    ambiguous_names = []
    if shrink_labels:
        ambiguous_names = list(ub.find_duplicates(all_names))

    for _, data in graph.nodes(data=True):
        if shrink_labels:
            if data['node'].name in ambiguous_names:
                data['label'] = data['node'].key
            else:
                data['label'] = data['node'].name
        else:
            data['label'] = data['node'].key

        if show_types:
            clsname = data.get('node_clsname')
            if shrink_labels:
                typename = clsname
            else:
                typename = clsname_to_typename.get(clsname, clsname)
            data['label'] = f'{typename}: ' + data['label']

        if color_procs:
            clsname = data.get('node_clsname')
            if clsname not in clsname_to_color:
                if unused_colors:
                    color = unused_colors.pop()
                else:
                    color = None
                clsname_to_color[clsname] = color
            color = clsname_to_color[clsname]
            if color is not None:
                label = data['label']
                data['label'] = f'[{color}]{label}[/{color}]'


def coerce_pipeline(pipeline: Any) -> Pipeline:
    """
    Attempts to resolve a concise expression (typically from the command line) into a pre-defined pipeline.

    Args:
        pipeline (str | dict | PathLike | Pipeline):
            One of:

            * a :class:`Pipeline` instance (returned as-is),
            * a declarative mapping with ``nodes`` / ``edges`` keys, or a path to
              a ``.yaml`` / ``.yml`` / ``.json`` file containing one (the
              hardened, code-free form -- see
              :func:`kwdagger.yaml_pipeline.load_yaml_pipeline`),
            * a pre-registered name, or evaluatable code, e.g.
              ``"module.func()"`` or ``"file.py::func()"``.

    Returns:
        Pipeline
    """
    import os

    if isinstance(pipeline, Pipeline):
        return pipeline

    if isinstance(pipeline, dict):
        # Declarative (pure-data) pipeline, no code execution.
        from kwdagger.yaml_pipeline import load_yaml_pipeline

        return load_yaml_pipeline(pipeline)

    if isinstance(pipeline, (str, os.PathLike)):
        pipeline_str = os.fspath(pipeline)
        # A path to a YAML/JSON pipeline file is the hardened variant. The
        # ``::`` form is reserved for executable Python files, so skip those.
        if '::' not in pipeline_str:
            suffix = pipeline_str.rsplit('.', 1)[-1].lower()
            if suffix in {'yaml', 'yml', 'json'}:
                if not os.path.exists(pipeline_str):
                    raise FileNotFoundError(
                        f'YAML pipeline file does not exist: {pipeline_str}'
                    )
                from kwdagger.yaml_pipeline import load_yaml_pipeline

                return load_yaml_pipeline(pipeline_str)
        # Fall back to the (code-executing) module/expression resolver.
        return _resolve_pipeline(pipeline_str)

    raise TypeError(
        f'Unknown coerce technique for {type(pipeline)} with value {pipeline}'
    )


def _resolve_pipeline(pipeline: Any) -> Any:
    """
    Users need to be able to build and specify their own pipelines here
    (similar to how kwiver pipelines work). This is initial support.

    Ignore:
        from kwdagger.pipeline import *  # NOQA
        from kwdagger.pipeline import _resolve_pipeline
        pipeline = 'user_module.pipelines.custom_pipeline_func()'
        pipeline = 'shitspotter.pipelines.heatmap_evaluation_pipeline()'
        pipeline = 'shitspotter.pipelines.heatmap_evaluation_pipeline()'
        pipeline = 'example_user_module.pipelines.my_demo_pipeline()'
        _resolve_pipeline(pipeline)
    """
    # Case: given in the format `{module_name}.{attribute_expression}`
    # Note the attribute_expression allows arbitrary code execution
    print('Resolving user-specified pipeline')
    if '::' in pipeline:
        fpath, code = pipeline.split('::', 1)
        module = ub.import_module_from_path(fpath)
        limited_namespace = {'pipeline_module': module}
        statement = f'pipeline_module.{code}'
        dag = eval(statement, limited_namespace)
        return dag
    elif '.' in pipeline:
        # Find which part is the module and which is the member
        parts = pipeline.split('.')
        found = None
        for idx in reversed(range(1, len(parts) + 1)):
            candidate = '.'.join(parts[:idx])
            print(f'candidate={candidate}')
            try:
                modpath = _coerce_modpath(candidate)
                print(f'modpath={modpath}')
            except ValueError:
                ...
            else:
                lhs = candidate
                rhs = '.'.join(parts[idx:])
                module = ub.import_module_from_path(modpath)
                found = (module, modpath, lhs, rhs)
                print(f'found = {ub.urepr(found, nl=1)}')
                break
        if found is None:
            raise ValueError(f'unable to resolve pipeline: {pipeline}')
        else:
            # This initial specification allows arbitrary code execution.
            # We should define a hardened variant which does not require this.
            (module, modpath, lhs, rhs) = found
            limited_namespace = {'pipeline_module': module}
            statement = f'pipeline_module.{rhs}'
            try:
                dag = eval(statement, limited_namespace)
            except Exception:
                print(f'Input: pipeline={pipeline}')
                print(f'modpath={modpath}')
                print(f'module = {ub.urepr(module, nl=1)}')
                print(f'Error with: statement={statement}')
                raise
            return dag
    else:
        raise ValueError(pipeline)


def _coerce_modpath(modpath_or_name: str | os.PathLike[str]) -> str:
    import os
    import types

    if isinstance(modpath_or_name, types.ModuleType):
        raise TypeError('Expected a static module but got a dynamic one')
    # A path may arrive as a Path; everything below, and every caller, wants
    # the string form.
    name = os.fspath(modpath_or_name)
    modpath = ub.modname_to_modpath(name)
    if modpath is None:
        if os.path.exists(name):
            modpath = name
        else:
            raise ValueError('Cannot find module={}'.format(name))
    return modpath
