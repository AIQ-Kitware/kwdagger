"""
Full-matrix compilation: the logical template becomes a concrete graph.

A gather cannot be resolved one matrix row at a time -- its membership is only
known once every row exists -- so this layer expands the whole matrix, clones a
process node per configuration, resolves grouping keys, selects and orders
gather members, canonicalizes duplicate processes, and checks that rows which
compile to one process agree about how it should execute.

Imports the connection and process layers. It refers to
:class:`~kwdagger.pipeline._logical.Pipeline` only as a type annotation, which
``from __future__ import annotations`` keeps out of the runtime import graph.
"""

from __future__ import annotations

import copy
import os
from collections import defaultdict

# From collections.abc, not typing: `isinstance(x, typing.Mapping)` gives a
# type checker no class to narrow on, so a `str | Mapping` union stays a union
# inside the isinstance branch and every `key['src']` looks like an error. The
# typing aliases have been deprecated since 3.9 in any case.
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

import networkx as nx
import ubelt as ub

from kwdagger.pipeline._agreement import (
    check_execution_agreement,
    execution_snapshot,
)
from kwdagger.pipeline._config_values import normalize_config
from kwdagger.pipeline._connections import (
    _UNSET,
    GatherConnection,
    GatherSpec,
    InputNode,
    OutputNode,
)
from kwdagger.pipeline._process import ProcessNode
from kwdagger.pipeline._runtime import QueueSpec
from kwdagger.pipeline._slurm import layer_slurm_options

if TYPE_CHECKING:
    from kwdagger.pipeline._logical import Pipeline


class CompiledPipeline:
    """A static concrete graph produced from all matrix configurations."""

    def __init__(
        self,
        *,
        proc_graph: nx.DiGraph,
        root_dpath: str | os.PathLike[str],
        slurm_options: Mapping[str, Any] | None = None,
        compile_summary: Mapping[str, Any] | None = None,
    ) -> None:
        self.proc_graph = proc_graph
        self.root_dpath = ub.Path(root_dpath)
        self.nodes = {
            key: data['node'] for key, data in proc_graph.nodes(data=True)
        }
        self.node_dict = self.nodes
        self.compile_summary = dict(compile_summary or {})
        # Pipeline-wide options, handed to the shared runtime submitter
        # alongside the process graph.
        self.__slurm_options__ = dict(slurm_options or {})

    def _edge_cardinality_records(self) -> list[dict[str, Any]]:
        """Summarize concrete edge multiplicity by logical port binding."""
        grouped: dict[tuple[Any, ...], dict[str, Any]] = {}

        def _record_for(
            *,
            source: ProcessNode,
            target: ProcessNode,
            kind: str,
            source_port: str | None,
            target_port: str | None,
            spec: GatherSpec | None = None,
        ) -> dict[str, Any]:
            assert isinstance(source.name, str)
            assert isinstance(target.name, str)
            key = (
                source.name,
                source_port,
                target.name,
                target_port,
                kind,
                spec,
            )
            record = grouped.setdefault(
                key,
                {
                    'source': source.name,
                    'target': target.name,
                    'kind': kind,
                    'source_port': source_port,
                    'target_port': target_port,
                    'source_ids': set(),
                    'target_ids': set(),
                    'edge_pairs': set(),
                    'member_counts': [],
                    'spec': spec,
                },
            )
            return record

        for target in self.nodes.values():
            for input_name, input_node in target.inputs.items():
                members = input_node._gather_members
                if members is not None:
                    connection = input_node._gather_connection
                    assert connection is not None
                    if not members:
                        continue
                    source = members[0].parent
                    record = _record_for(
                        source=source,
                        target=target,
                        kind='gather',
                        source_port=connection.source.name,
                        target_port=input_name,
                        spec=connection.spec,
                    )
                    record['target_ids'].add(target.process_id)
                    record['member_counts'].append(len(members))
                    for member in members:
                        record['source_ids'].add(member.parent.process_id)
                        record['edge_pairs'].add(
                            (member.parent.process_id, target.process_id)
                        )

                for source_port_node in input_node.pred:
                    source = source_port_node.parent
                    kind = (
                        'shared_input'
                        if isinstance(source_port_node, InputNode)
                        else 'ordinary'
                    )
                    record = _record_for(
                        source=source,
                        target=target,
                        kind=kind,
                        source_port=source_port_node.name,
                        target_port=input_name,
                    )
                    record['source_ids'].add(source.process_id)
                    record['target_ids'].add(target.process_id)
                    record['edge_pairs'].add(
                        (source.process_id, target.process_id)
                    )

            for param_name, param_port in target.param_ports.items():
                for source_port in param_port.pred:
                    source = source_port.parent
                    record = _record_for(
                        source=source,
                        target=target,
                        kind='shared_parameter',
                        source_port=source_port.name,
                        target_port=param_name,
                    )
                    record['source_ids'].add(source.process_id)
                    record['target_ids'].add(target.process_id)
                    record['edge_pairs'].add(
                        (source.process_id, target.process_id)
                    )

            for source in target._pred_nodes_without_io_connection:
                record = _record_for(
                    source=source,
                    target=target,
                    kind='ordinary',
                    source_port=None,
                    target_port=None,
                )
                record['source_ids'].add(source.process_id)
                record['target_ids'].add(target.process_id)
                record['edge_pairs'].add((source.process_id, target.process_id))

        def _sort_key(item: tuple[tuple[Any, ...], dict[str, Any]]) -> Any:
            _, record = item
            spec = record['spec']
            spec_key = repr(spec.to_dict()) if spec is not None else ''
            return (
                record['source'],
                record['source_port'] or '',
                record['target'],
                record['target_port'] or '',
                record['kind'],
                spec_key,
            )

        records = []
        for _, record in sorted(grouped.items(), key=_sort_key):
            source_count = len(record.pop('source_ids'))
            target_count = len(record.pop('target_ids'))
            edge_count = len(record.pop('edge_pairs'))
            member_counts = record.pop('member_counts')
            record['source_count'] = source_count
            record['target_count'] = target_count
            record['edge_count'] = edge_count
            if record['kind'] == 'gather':
                if len(set(member_counts)) == 1:
                    ratio = f'{member_counts[0]}:1'
                else:
                    ratio = f'{min(member_counts)}-{max(member_counts)}:1'
                record['relation'] = f'gather {ratio}'
            else:
                record.pop('spec')
                targets_per_source = (
                    edge_count / source_count if source_count else 0
                )
                sources_per_target = (
                    edge_count / target_count if target_count else 0
                )
                if targets_per_source > 1 and sources_per_target == 1:
                    relation = f'fan-out 1:{targets_per_source:g}'
                elif sources_per_target > 1 and targets_per_source == 1:
                    relation = f'fan-in {sources_per_target:g}:1'
                elif targets_per_source == 1 and sources_per_target == 1:
                    relation = 'direct 1:1'
                else:
                    relation = 'many-to-many'
                record['relation'] = relation
            records.append(record)
        return records

    def _cardinality_display_graph(self) -> nx.DiGraph:
        """Build a logical graph annotated with concrete instance counts."""
        graph = nx.DiGraph()
        grouped_nodes = ub.group_items(
            self.nodes.values(), key=lambda node: node.name
        )
        for name, nodes in sorted(grouped_nodes.items()):
            graph.add_node(name, label=f'{name} [{len(nodes)} instances]')
        for idx, record in enumerate(self._edge_cardinality_records()):
            marker = f'__cardinality_edge_{idx}'
            label = (
                f'{record["relation"]} | '
                f'{record["source_count"]} -> {record["target_count"]} instances'
            )
            source_port = record['source_port']
            target_port = record['target_port']
            if source_port is not None or target_port is not None:
                label += f' | {source_port} -> {target_port}'
            if record['kind'] == 'gather':
                spec = record['spec']
                if spec.group_by:
                    label += ' | group_by=' + ','.join(spec.display_keys())
                if spec.order_by:
                    label += ' | order_by=' + ','.join(spec.order_by)
            elif record['kind'].startswith('shared_'):
                label += ' | configuration only'
            graph.add_node(
                marker,
                label=f'[bright_magenta]{label}[/bright_magenta]'
                if record['kind'] == 'gather'
                else label,
            )
            graph.add_edge(record['source'], marker)
            graph.add_edge(marker, record['target'])
        return graph

    def print_cardinality_graph(self) -> None:
        """Print fan-in, fan-out, and direct cardinalities after compilation."""
        import rich

        print('')
        print('Compiled Process Cardinality Graph')
        nx.write_network_text(
            self._cardinality_display_graph(),
            path=rich.print,
            end='',
            vertical_chains=True,
        )

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

        A compiled pipeline is already concrete, so it has no precondition to
        check: it goes straight to the shared runtime submitter.
        """
        from kwdagger.pipeline import _runtime

        return _runtime.submit_jobs(
            self.proc_graph,
            slurm_options=self.__slurm_options__,
            queue=queue,
            skip_existing=skip_existing,
            enable_links=enable_links,
            write_invocations=write_invocations,
            write_configs=write_configs,
            log=log,
        )

    make_queue = submit_jobs


def _clone_unconnected_process_node(template: 'ProcessNode') -> 'ProcessNode':
    """Deep-copy a node while removing all template graph connections."""
    node = copy.deepcopy(template)
    node._pred_nodes_without_io_connection = []
    for input_node in node.inputs.values():
        input_node.parent = node
        input_node.pred = []
        input_node.succ = []
        input_node._gather_connection = None
        input_node._gather_members = None
        input_node._final_value = _UNSET
    for output_node in node.outputs.values():
        output_node.parent = node
        output_node.pred = []
        output_node.succ = []
        output_node._gather_connections = []
    for param_port in node.param_ports.values():
        param_port.parent = node
        param_port.pred = []
        param_port.succ = []
        param_port._final_value = _UNSET
    node._configured_cache.clear()
    return node


def _node_param_value(node: 'ProcessNode', key: str) -> Any:
    """
    Resolve a gather grouping key against a concrete node.

    A grouping key names whatever identifies which slice of the sweep an
    instance belongs to. The preferred form is **qualified**, naming the
    node the value lives on, exactly as matrix keys do::

        group_by: ['prepare.dataset']

    A qualified key is resolved on the named node -- ``node`` itself if the
    names match, otherwise the ancestor with that name. This is what lets a
    fan-out be expressed the natural way: give the one node that varies the
    parameter, and let every consumer be partitioned by it through an edge.

    Resolving only against a node's *own* algorithm parameters forces every
    consumer to redeclare the parameter purely to satisfy the gather, which
    creates a second, independent sweep axis. kwdagger then takes the
    product of the two, producing instances whose declared parameter value
    and actual upstream input disagree.

    On the resolved node the value is looked up in its identity-bearing
    surface -- ``final_algo_config`` then ``final_input_config`` -- and
    finally its output ports. Output paths are eligible deliberately:
    naming a producer's port is the direct way to say "group by which
    upstream made this".

    An unqualified key stays supported for backwards compatibility. It
    resolves against the node itself, then unambiguously against ancestors.

    Args:
        node (ProcessNode): the concrete instance to resolve against.
        key (str): ``'<node>.<param>'``, or a bare parameter name.

    Returns:
        Any: the resolved value. Paths are returned as ``str`` so that a
            ``Path`` on one node compares equal to a ``str`` on another.

    Raises:
        KeyError: if the key resolves nowhere, or names an unreachable node.
        ValueError: if an unqualified key resolves to conflicting values on
            different ancestors. Qualify the key to disambiguate.
    """
    if '.' in key:
        node_name, param = key.split('.', 1)
        resolved_nodes = _resolve_named_nodes(node, node_name)
        resolved_values = []
        for resolved in resolved_nodes:
            value = _lookup_on_node(resolved, param)
            if value is _MISSING:
                raise KeyError(
                    f'Node {node_name!r} has no parameter, input, or output '
                    f'{param!r} (resolving group key {key!r} for '
                    f'{node.name!r}); algo={sorted(resolved.final_algo_config)} '
                    f'inputs={sorted(resolved.inputs)} '
                    f'outputs={sorted(resolved.outputs)}'
                )
            resolved_values.append((resolved, value))
        distinct = {value for _resolved, value in resolved_values}
        if len(distinct) > 1:
            details = {
                resolved.process_id: value
                for resolved, value in resolved_values
            }
            raise ValueError(
                f'Ambiguous qualified group key {key!r} for '
                f'{node.name!r}: concrete {node_name!r} ancestors disagree '
                f'({details!r})'
            )
        if not resolved_values:
            raise KeyError(
                f'No concrete node named {node_name!r} is reachable from '
                f'{node.name!r}'
            )
        return resolved_values[0][1]

    value = _lookup_on_node(node, key)
    if value is not _MISSING:
        return value

    # Unqualified fallback: look through ancestors, but refuse to guess if
    # they disagree.
    ancestor_values = []
    for ancestor in node.ancestor_process_nodes():
        found = _lookup_on_node(ancestor, key)
        if found is not _MISSING:
            ancestor_values.append((ancestor, found))

    if ancestor_values:
        distinct = {value for _ancestor, value in ancestor_values}
        if len(distinct) > 1:
            details = {
                f'{ancestor.name}:{ancestor.process_id}': value
                for ancestor, value in ancestor_values
            }
            raise ValueError(
                f'Ambiguous group key {key!r} for node {node.name!r}: '
                f'ancestors disagree ({details!r}). Qualify it as '
                f'"<node>.{key}" to say which one you mean.'
            )
        return next(iter(distinct))

    raise KeyError(
        f'Node {node.name!r} has no parameter, input, or ancestor providing '
        f'{key!r}; algo={sorted(node.final_algo_config)} '
        f'inputs={sorted(node.inputs)} '
        f'ancestors={sorted(str(n.name) for n in node.ancestor_process_nodes())}. '
        f'Qualify the key as "<node>.<param>" to group on another node.'
    )


class _Missing:
    """Sentinel distinguishing "absent" from a legitimately ``None`` value."""

    def __repr__(self) -> str:
        return '<missing>'


_MISSING = _Missing()


def _resolve_named_nodes(
    node: 'ProcessNode', node_name: str
) -> list['ProcessNode']:
    """Find all concrete ``node_name`` instances visible from ``node``."""
    if node.name == node_name:
        return [node]
    ancestors = [
        a for a in node.ancestor_process_nodes() if a.name == node_name
    ]
    if not ancestors:
        raise KeyError(
            f'Cannot group {node.name!r} by a parameter of {node_name!r}: '
            f'it is not this node nor one of its ancestors '
            f'({sorted(str(n.name) for n in node.ancestor_process_nodes())}). '
            f"Note that a gather's own source is not yet an ancestor while "
            f'that gather is being resolved.'
        )
    return ancestors


def _lookup_on_node(node: 'ProcessNode', param: str) -> Any:
    """
    Look ``param`` up in a node's identity-bearing surface.

    That surface is ``final_algo_config`` (what algorithm it runs) plus
    ``final_input_config`` (every effective non-gather input, at the value it
    will read, however that value was delivered), then its output ports. Those
    are exactly the things this node owns and that reach its ``process_id``, so
    they are exactly the things it is meaningful to partition it by.
    """
    config = node.final_algo_config
    if param in config:
        return _hashable_group_value(config[param])

    inputs = node.final_input_config
    if param in inputs:
        return _hashable_group_value(inputs[param])

    port = node.outputs.get(param)
    if port is not None:
        return _hashable_group_value(port.final_value)

    return _MISSING


def _hashable_group_value(value: Any) -> Any:
    """
    Normalize a resolved grouping value so equal identities compare equal.

    Group values are put into sets and used as sort keys, so every accepted
    configuration value has to reduce to something hashable and totally
    ordered. Paths are stringified (a ``Path`` on one node must match a
    ``str`` on another), and containers are canonicalized recursively.

    Mappings and sets are tagged, because their canonical forms would
    otherwise be indistinguishable from an ordinary sequence of the same
    contents. Lists and tuples are deliberately *not* distinguished: a value
    that round-trips through YAML or JSON loses that difference anyway, so
    treating them as equal is the same accommodation ``Path``/``str`` gets.

    This is internal comparison material. It is never written to a
    configuration file or to provenance.

    Example:
        >>> _hashable_group_value({'b': 1, 'a': [2, 3]})
        ('map', (('a', (2, 3)), ('b', 1)))
        >>> _hashable_group_value([1, 2]) == _hashable_group_value((1, 2))
        True
        >>> _hashable_group_value([1, 2]) == _hashable_group_value({1, 2})
        False
    """
    if isinstance(value, os.PathLike):
        return str(value)
    if isinstance(value, Mapping):
        # Sorted by canonicalized key, so declaration order cannot change the
        # comparison. ``repr`` gives a total order over otherwise
        # incomparable key types.
        items = [
            (_hashable_group_value(k), _hashable_group_value(v))
            for k, v in value.items()
        ]
        items.sort(key=lambda item: repr(item[0]))
        return ('map', tuple(items))
    if isinstance(value, (set, frozenset)):
        elements = [_hashable_group_value(v) for v in value]
        elements.sort(key=repr)
        return ('set', tuple(elements))
    if isinstance(value, (list, tuple)):
        return tuple(_hashable_group_value(v) for v in value)
    return value


def _sort_gather_members(
    members: list['ProcessNode'], spec: GatherSpec
) -> list['ProcessNode']:
    if not spec.order_by:
        return sorted(members, key=lambda node: node.process_id)

    def keyfunc(node: 'ProcessNode') -> tuple[Any, ...]:
        return tuple(_node_param_value(node, key) for key in spec.order_by)

    try:
        return sorted(
            members, key=lambda node: (keyfunc(node), node.process_id)
        )
    except TypeError:
        # Heterogeneous values are unusual but still need deterministic output.
        return sorted(
            members,
            key=lambda node: (
                tuple(repr(v) for v in keyfunc(node)),
                node.process_id,
            ),
        )


def _compile_pipeline_configurations(
    template: Pipeline,
    *,
    configs: Sequence[Mapping[str, Any]],
    root_dpath: str | os.PathLike[str] | None,
    cache: bool,
) -> CompiledPipeline:
    """Compile a matrix-expanded template into a concrete static DAG."""
    from kwdagger.utils import util_dotdict

    template._ensure_clean()
    # The same boundary an ordinary ``Pipeline.configure`` applies, and
    # before anything reads a reserved key or routes a value to a node:
    # whether a mapping key is accepted must not depend on whether the
    # pipeline happens to contain a gather.
    rows = [normalize_config(config) for config in configs]
    if root_dpath is None:
        template_nodes = list(template.node_dict.values())
        root_dpath = (
            template_nodes[0].root_dpath if template_nodes else None
        ) or '.'
    root_dpath = ub.Path(root_dpath)
    template_order = list(nx.topological_sort(template.config_graph))
    row_nodes: list[dict[str, ProcessNode]] = [dict() for _ in rows]
    instances_by_template: dict[str, dict[str, ProcessNode]] = defaultdict(dict)
    concrete_by_process_id: dict[str, ProcessNode] = {}
    canonical_row_idx: dict[str, int] = {}
    canonical_snapshots: dict[str, dict[str, Any]] = {}

    for template_name in template_order:
        template_node = template.node_dict[template_name]

        ordinary_inputs: list[tuple[str, str, str, str]] = []
        gather_inputs: list[tuple[str, GatherConnection]] = []
        for input_name, input_node in template_node.inputs.items():
            for pred in input_node.pred:
                port_kind = (
                    'output' if isinstance(pred, OutputNode) else 'input'
                )
                ordinary_inputs.append(
                    (input_name, pred.parent.name, pred.name, port_kind)
                )
            if input_node._gather_connection is not None:
                gather_inputs.append(
                    (input_name, input_node._gather_connection)
                )
        # Wired algorithm parameters. Like an aliased input these carry a
        # value rather than a dependency, so they are replicated onto each
        # concrete instance but never become predecessors.
        param_edges: list[tuple[str, str, str]] = []
        for param_name, param_port in template_node.param_ports.items():
            for pred in param_port.pred:
                param_edges.append((param_name, pred.parent.name, pred.name))

        dependency_only_predecessors = [
            pred.name
            for pred in template_node._pred_nodes_without_io_connection
        ]

        for row_idx, row_config in enumerate(rows):
            dotconfig = util_dotdict.DotDict(row_config)
            node_config = dict(dotconfig.prefix_get(template_name, {}))
            # A row-global mapping is a *layer* under the node's own, not a
            # stand-in for it. Substituting one for the other meant a node
            # with any local option silently dropped every row-global key,
            # and a node with none took the row-global mapping at node
            # precedence -- so an otherwise identical node asked for
            # different resources depending on whether the pipeline had a
            # gather. The node's declared default is restated between them
            # because it outranks a row-global one, exactly as it does on the
            # row-at-a-time path.
            row_slurm_options = row_config.get('__slurm_options__')
            node_slurm_options = node_config.get('__slurm_options__')
            if row_slurm_options is not None or node_slurm_options is not None:
                node_config['__slurm_options__'] = layer_slurm_options(
                    row_slurm_options,
                    template_node._base_slurm_options,
                    node_slurm_options,
                )
            node = _clone_unconnected_process_node(template_node)
            node.root_dpath = root_dpath

            # A node may forward a value to one of its own ports. That source
            # is the instance being built right now, which is not registered
            # in ``row_nodes`` until the end of this iteration.
            row_instances = dict(row_nodes[row_idx])
            row_instances[template_name] = node

            for (
                input_name,
                source_name,
                source_port_name,
                source_port_kind,
            ) in ordinary_inputs:
                source_node = row_instances[source_name]
                if source_port_kind == 'output':
                    source_port = source_node.outputs[source_port_name]
                else:
                    source_port = source_node.inputs[source_port_name]
                source_port.connect(node.inputs[input_name])

            for param_name, source_name, source_param in param_edges:
                source_node = row_instances[source_name]
                source_node.param_ports[source_param].connect(
                    node.param_ports[param_name]
                )

            for predecessor_name in dependency_only_predecessors:
                predecessor = row_nodes[row_idx][predecessor_name]
                node._pred_nodes_without_io_connection.append(predecessor)

            # Configure once to resolve this target instance's group values.
            # Gathered inputs are connected immediately afterwards and the node
            # is configured again so its process identity includes membership.
            node.configure(node_config, cache=cache)

            for input_name, connection in gather_inputs:
                source_name = connection.source.parent.name
                source_output_name = connection.source.name
                candidates = list(instances_by_template[source_name].values())

                key_pairs = list(
                    zip(
                        connection.spec.source_keys(),
                        connection.spec.target_keys(),
                    )
                )
                for src_key, dst_key in key_pairs:
                    # Produce a focused error before attempting selection.
                    _node_param_value(node, dst_key)
                    if candidates:
                        _node_param_value(candidates[0], src_key)

                selected = [
                    source
                    for source in candidates
                    if all(
                        _node_param_value(source, src_key)
                        == _node_param_value(node, dst_key)
                        for src_key, dst_key in key_pairs
                    )
                ]
                if not selected:
                    group = {
                        dst_key: _node_param_value(node, dst_key)
                        for _src_key, dst_key in key_pairs
                    }
                    raise ValueError(
                        f'Gather {connection.source.key} -> '
                        f'{connection.target.key} found no source instances '
                        f'for group {group!r}'
                    )
                selected = _sort_gather_members(selected, connection.spec)
                gathered_input = node.inputs[input_name]
                gathered_input._gather_connection = GatherConnection(
                    source=connection.source,
                    target=gathered_input,
                    spec=connection.spec,
                )
                gathered_input._gather_members = [
                    source.outputs[source_output_name] for source in selected
                ]

            if gather_inputs:
                node.configure(node_config, cache=cache)

            process_id = node.process_id
            canonical = concrete_by_process_id.get(process_id)
            if canonical is None:
                canonical = node
                concrete_by_process_id[process_id] = canonical
                instances_by_template[template_name][process_id] = canonical
                canonical_row_idx[process_id] = row_idx
                canonical_snapshots[process_id] = execution_snapshot(node)
            else:
                # Execution state is popped off the config by ``configure``, so
                # it does not participate in process identity. Rows that agree
                # on identity but disagree on execution state would otherwise
                # be resolved by whichever row happened to come first, making
                # compilation row-order dependent.
                check_execution_agreement(
                    canonical_snapshots[process_id],
                    execution_snapshot(node),
                    template_name=template_name,
                    process_id=process_id,
                    canonical_label=f'row {canonical_row_idx[process_id]}',
                    duplicate_label=f'row {row_idx}',
                )
            row_nodes[row_idx][template_name] = canonical

    # The concrete execution graph, so effective predecessors: what each
    # command actually requires, not everything that was wired to it. Two
    # rows that compile to one node may have been wired to different
    # producers -- if the node reads neither, keeping whichever row came
    # first would make execution depend on matrix order.
    proc_graph = nx.DiGraph()
    for process_id, node in concrete_by_process_id.items():
        proc_graph.add_node(process_id, node=node)
    for process_id, node in concrete_by_process_id.items():
        for pred in node.effective_predecessor_process_nodes():
            proc_graph.add_edge(pred.process_id, process_id)

    if not nx.is_directed_acyclic_graph(proc_graph):
        raise ValueError('Compiled gather graph is not acyclic')

    collection_groups = 0
    collection_memberships = 0
    largest_collection = 0
    for node in concrete_by_process_id.values():
        for input_node in node.inputs.values():
            if input_node._gather_members is not None:
                size = len(input_node._gather_members)
                collection_groups += 1
                collection_memberships += size
                largest_collection = max(largest_collection, size)

    compile_summary = {
        'concrete_nodes': len(concrete_by_process_id),
        'collection_groups': collection_groups,
        'collection_memberships': collection_memberships,
        'largest_collection': largest_collection,
    }
    return CompiledPipeline(
        proc_graph=proc_graph,
        root_dpath=root_dpath,
        slurm_options=getattr(template, '__slurm_options__', {}),
        compile_summary=compile_summary,
    )
