#!/usr/bin/env python3
"""
Declarative (pure-YAML) pipeline specification.

This module lets a :class:`kwdagger.Pipeline` be described as data -- a mapping
of ``nodes`` and a list of ``edges`` -- instead of as Python code. The resulting
object is identical to one built via the Python API, so every downstream feature
(matrix expansion, hashing, scheduling, aggregation) works unchanged.

Unlike the ``"module.func()"`` pipeline form (see
:func:`kwdagger.pipeline._resolve_pipeline`) this loader never evaluates
arbitrary code, so it is safe to load from untrusted input. It is the "hardened
variant" referenced in ``pipeline.py``.

See ``docs/source/manual/technical/yaml_pipeline_spec.rst`` for the full schema.

Example:
    >>> from kwdagger.yaml_pipeline import load_yaml_pipeline
    >>> spec = {
    >>>     'nodes': {
    >>>         'predict': {
    >>>             'executable': 'python predict.py',
    >>>             'in_paths': ['src_fpath'],
    >>>             'out_paths': {'dst_fpath': 'predictions.json'},
    >>>             'algo_params': {'keyword': 'great'},
    >>>         },
    >>>         'evaluate': {
    >>>             'executable': 'python evaluate.py',
    >>>             'in_paths': ['true_fpath', 'pred_fpath'],
    >>>             'out_paths': {'out_fpath': 'metrics.json'},
    >>>         },
    >>>     },
    >>>     'edges': [
    >>>         'predict.dst_fpath -> evaluate.pred_fpath',
    >>>         'predict.src_fpath -> evaluate.true_fpath',
    >>>     ],
    >>> }
    >>> dag = load_yaml_pipeline(spec)
    >>> print(sorted(dag.node_dict))
    ['evaluate', 'predict']
    >>> # The predictions output is wired into the evaluator input.
    >>> assert dag.node_dict['evaluate'].inputs['pred_fpath'].pred
"""

from __future__ import annotations

from typing import Any, cast

import ubelt as ub

from kwdagger.pipeline import GatherSpec, Pipeline, ProcessNode

__all__ = ['YamlProcessNode', 'dump_yaml_pipeline', 'load_yaml_pipeline']


# Node-spec keys that are forwarded verbatim as ProcessNode keyword arguments.
_PROCESS_NODE_KEYS = {
    'executable',
    'command',  # alias for executable, handled by ProcessNode
    'in_paths',
    'out_paths',
    'primary_out_key',
    'algo_params',
    'perf_params',
    'group',
    'slurm_options',
    # Resource lifecycle forwarded to the underlying cmd_queue job. ``setup``
    # is a gating precondition run before the command; ``teardown`` is cleanup
    # that always runs after it (success, failure, or signal). Each may be a
    # single shell string or a list of strings.
    'setup',
    'teardown',
    'config',
    'node_dpath',
    'group_dpath',
}

# Node-spec keys consumed by YamlProcessNode itself (not by ProcessNode).
_YAML_NODE_KEYS = {
    'metrics',
    'vantage_points',
    'result',
    'load_result',
}

# Node-spec keys handled specially by the loader (neither constructor kwargs nor
# YamlProcessNode data).
_META_NODE_KEYS = {
    'class',
}

_ALLOWED_NODE_KEYS = _PROCESS_NODE_KEYS | _YAML_NODE_KEYS | _META_NODE_KEYS


def _dotted_get(data: Any, path: str, default: Any = None) -> Any:
    """
    Walk a nested mapping by a dotted ``a.b.c`` path.

    Example:
        >>> from kwdagger.yaml_pipeline import _dotted_get
        >>> data = {'result': {'metrics': {'acc': 1.0}}}
        >>> _dotted_get(data, 'result.metrics')
        {'acc': 1.0}
        >>> _dotted_get(data, 'result.missing', default='x')
        'x'
    """
    cur = data
    for part in path.split('.'):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def _import_callable(path: str) -> Any:
    """
    Import a callable from a dotted ``module.submodule.attr`` path.

    The longest importable module prefix is found first, then the remaining
    components are resolved as attributes. This supports both free functions
    (``my_pkg.loaders.score``) and methods borrowed from a class
    (``my_pkg.pipelines.ScoreNode.load_result``).

    Unlike the ``"module.func()"`` pipeline form, nothing is ``eval``\\ ed; this
    only ``import``\\ s the named module and walks attributes. It is an opt-in
    escape hatch (a node must explicitly set ``load_result``), so the core
    topology schema remains code-free.
    """
    import importlib

    parts = path.split('.')
    for idx in reversed(range(1, len(parts))):
        modname = '.'.join(parts[:idx])
        try:
            module = importlib.import_module(modname)
        except ImportError:
            continue
        obj = module
        for attr in parts[idx:]:
            obj = getattr(obj, attr)
        return obj
    raise ImportError(
        f'could not import a callable from {path!r}; no importable module '
        'prefix was found (is it on PYTHONPATH?)'
    )


class YamlProcessNode(ProcessNode):
    """
    A :class:`~kwdagger.ProcessNode` constructed from a declarative spec.

    Adds a generic :meth:`load_result` plus the ``metrics`` / ``vantage_points``
    hooks consumed by :mod:`kwdagger.aggregate`, so that evaluation nodes can be
    fully specified in YAML without subclassing in Python.
    """

    def __init__(
        self,
        *,
        metrics: Any = None,
        vantage_points: Any = None,
        result: Any = None,
        load_result: Any = None,
        **kwargs: Any,
    ) -> None:
        self._metrics_info = metrics
        self._vantage_points = vantage_points or []
        self._result_spec = result or {}
        self._load_result_ref = load_result
        self._load_result_func: Any = None
        super().__init__(**kwargs)

    def default_metrics(self) -> Any:
        """
        Metric metadata for ``kwdagger aggregate``.

        Raises ``AttributeError`` when no metrics were declared, which the
        aggregator interprets (and catches) as "this node has no metric info" --
        the same behavior as a Python node that does not define the method.
        """
        if not self._metrics_info:
            raise AttributeError(f'no metrics declared for node {self.name!r}')
        return [dict(info) for info in self._metrics_info]

    @property
    def default_vantage_points(self) -> Any:
        return [dict(vp) for vp in self._vantage_points]

    def load_result(self, node_dpath: Any) -> Any:
        """
        Load a node's result for ``kwdagger aggregate``.

        When the node spec sets ``load_result`` to a dotted path, that callable
        is imported and invoked as ``func(node, node_dpath)`` -- the escape
        hatch for tools whose output the generic loader cannot describe (it may
        be a free function or a method borrowed from an existing node class).
        Otherwise the generic loader runs: it reads the primary output JSON,
        parses the trailing ``ProcessContext`` for resolved params / resources /
        machine info, and attaches the metric values found at ``result.metrics``
        (configurable via the ``result`` spec) under ``metrics.<node_name>.<metric>``.
        """
        import json

        if self._load_result_ref is not None:
            if self._load_result_func is None:
                self._load_result_func = _import_callable(self._load_result_ref)
            return self._load_result_func(self, node_dpath)

        from kwdagger.aggregate_loader import new_process_context_parser
        from kwdagger.utils import util_dotdict

        node_dpath = ub.Path(node_dpath)
        if self.primary_out_key is None:
            raise ValueError(
                f'node {self.name!r} has no primary_out_key; cannot load result'
            )
        output_fpath = node_dpath / self.out_paths[self.primary_out_key]
        result = json.loads(output_fpath.read_text())

        nest_resolved: dict[str, Any] = {}

        # Parse the ProcessContext block (resolved params, resources, machine).
        info_path = self._result_spec.get('info', 'info')
        if info_path is not None:
            info_list = _dotted_get(result, info_path, None)
            if info_list:
                proc_item = info_list[-1]
                nest_resolved = new_process_context_parser(proc_item)

        # Attach metric values.
        metrics_path = self._result_spec.get('metrics', 'result.metrics')
        if metrics_path is not None:
            metric_values = _dotted_get(result, metrics_path, None)
            if metric_values is not None:
                nest_resolved['metrics'] = metric_values

        flat_resolved = util_dotdict.DotDict.from_nested(nest_resolved)
        # ``name`` is always set by the constructor for a configured node.
        flat_resolved = flat_resolved.insert_prefix(
            cast(str, self.name), index=1
        )
        return flat_resolved


def _coerce_node(name: str, spec: Any) -> ProcessNode:
    """
    Build a single node from a node spec mapping.

    Without a ``class`` key the node is a data-driven :class:`YamlProcessNode`.
    With ``class: "module.MySubclass"`` the node is an instance of that
    :class:`~kwdagger.ProcessNode` subclass, constructed from the YAML data --
    the way to bring *behavioral* overrides (a custom ``command`` /
    ``_make_argstr`` / ``load_result``) that cannot be expressed as data, by
    referencing the class instead of serializing its code.
    """
    if not isinstance(spec, dict):
        raise TypeError(
            f'node {name!r} spec must be a mapping, got {type(spec).__name__}'
        )

    spec = dict(spec)

    if 'name' in spec:
        raise ValueError(
            f'node {name!r}: do not set "name" inside a node spec; the node name '
            'is the key in the "nodes" mapping'
        )

    unknown = set(spec) - _ALLOWED_NODE_KEYS
    if unknown:
        raise ValueError(
            f'node {name!r}: unknown node spec key(s) {sorted(unknown)}. '
            f'Allowed keys are {sorted(_ALLOWED_NODE_KEYS)}'
        )

    # A YAML list of input names is the natural spelling of the Python ``set``
    # convention; normalize it so configure() behaves identically.
    in_paths = spec.get('in_paths')
    if isinstance(in_paths, (list, tuple)):
        spec['in_paths'] = set(in_paths)

    class_ref = spec.pop('class', None)
    proc_kwargs = {k: v for k, v in spec.items() if k in _PROCESS_NODE_KEYS}
    yaml_kwargs = {k: v for k, v in spec.items() if k in _YAML_NODE_KEYS}

    if class_ref is not None:
        node_cls = _import_callable(class_ref)
        if not (
            isinstance(node_cls, type) and issubclass(node_cls, ProcessNode)
        ):
            raise TypeError(
                f'node {name!r}: class {class_ref!r} must be a '
                f'kwdagger.ProcessNode subclass, got {node_cls!r}'
            )
        # The data-driven extras (metrics / load_result / ...) are only
        # understood by YamlProcessNode; a plain ProcessNode subclass is
        # expected to define that behavior itself.
        if yaml_kwargs and not issubclass(node_cls, YamlProcessNode):
            raise ValueError(
                f'node {name!r}: key(s) {sorted(yaml_kwargs)} are only valid '
                f'when "class" is a YamlProcessNode subclass, but {class_ref!r} '
                'is not. Drop them (let the class define that behavior), or make '
                'the class subclass kwdagger.yaml_pipeline.YamlProcessNode.'
            )
        # ``executable`` may be omitted -- the class can supply it (or override
        # ``command`` entirely).
        return node_cls(name=name, **proc_kwargs, **yaml_kwargs)

    if 'executable' not in spec and 'command' not in spec:
        raise ValueError(
            f'node {name!r}: an "executable" (or "command") is required '
            '(unless "class" supplies one)'
        )
    return YamlProcessNode(name=name, **proc_kwargs, **yaml_kwargs)


def _split_endpoint(endpoint: str) -> tuple[str, str]:
    """
    Split a ``"node.port"`` endpoint into ``(node, port)``.

    The node name is everything before the final dot, so node names themselves
    may not contain a dot (consistent with the dotted matrix-key convention).
    """
    if not isinstance(endpoint, str) or '.' not in endpoint:
        raise ValueError(
            f'edge endpoint {endpoint!r} must look like "node.port"'
        )
    node, port = endpoint.rsplit('.', 1)
    return node.strip(), port.strip()


def _resolve_endpoint(
    node_dict: dict[str, Any], node_name: str, port: str
) -> Any:
    """
    Resolve ``node_name.port`` to its IONode, preferring outputs over inputs.

    Checking outputs first lets ``a.out -> b.in`` connect an output to an input,
    while ``a.in -> b.in`` forwards a shared input (both legal in the Python API).
    """
    if node_name not in node_dict:
        raise ValueError(
            f'edge references unknown node {node_name!r}; '
            f'known nodes are {sorted(node_dict)}'
        )
    node = node_dict[node_name]
    if port in node.outputs:
        return node.outputs[port]
    if port in node.inputs:
        return node.inputs[port]
    raise ValueError(
        f'edge references unknown port {port!r} on node {node_name!r}; '
        f'outputs={sorted(node.outputs)}, inputs={sorted(node.inputs)}'
    )


def _connect_edge(node_dict: dict[str, Any], edge: Any) -> None:
    """
    Wire a single edge spec, accepting either the string or mapping form.
    """
    gather = None
    if isinstance(edge, str):
        if '->' not in edge:
            raise ValueError(
                f'edge string {edge!r} must contain "->" '
                '(e.g. "src.out -> dst.in")'
            )
        src_str, dst_str = edge.split('->', 1)
        src_node, src_port = _split_endpoint(src_str.strip())
        dst_node, dst_port = _split_endpoint(dst_str.strip())
    elif isinstance(edge, dict):
        gather = edge.get('gather', None)
        if 'src_key' in edge or 'dst_key' in edge:
            src_node = edge['src']
            dst_node = edge['dst']
            src_port = edge['src_key']
            dst_port = edge['dst_key']
        else:
            src_node, src_port = _split_endpoint(edge['src'])
            dst_node, dst_port = _split_endpoint(edge['dst'])
    else:
        raise TypeError(
            f'edge must be a string or mapping, got {type(edge).__name__}'
        )

    src_io = _resolve_endpoint(node_dict, src_node, src_port)
    dst_io = _resolve_endpoint(node_dict, dst_node, dst_port)
    src_io.connect(
        dst_io,
        gather=None if gather is None else GatherSpec.coerce(gather),
    )


def load_yaml_pipeline(spec: Any, root_dpath: Any = None) -> Pipeline:
    """
    Build a :class:`~kwdagger.Pipeline` from a declarative spec.

    Args:
        spec (dict | str | PathLike):
            Either an in-memory mapping with ``nodes`` / ``edges`` keys, an
            inline YAML/JSON string, or a path to a ``.yaml`` / ``.yml`` /
            ``.json`` file containing such a mapping.

        root_dpath (str | PathLike | None):
            Optional output root passed through to the constructed pipeline.

    Returns:
        Pipeline

    Example:
        >>> from kwdagger.yaml_pipeline import load_yaml_pipeline
        >>> text = '''
        ... nodes:
        ...   step1:
        ...     executable: "echo step1"
        ...     out_paths: {dst: out.txt}
        ...   step2:
        ...     executable: "echo step2"
        ...     in_paths: [src]
        ...     out_paths: {dst: out.txt}
        ... edges:
        ...   - step1.dst -> step2.src
        ... '''
        >>> dag = load_yaml_pipeline(text)
        >>> dag.print_graphs()
        >>> assert dag.node_dict['step2'].inputs['src'].pred
    """
    import kwutil

    if isinstance(spec, dict):
        data = spec
    else:
        data = kwutil.Yaml.coerce(spec)

    if not isinstance(data, dict):
        raise TypeError(
            'YAML pipeline spec must resolve to a mapping with a "nodes" key, '
            f'got {type(data).__name__}'
        )

    unknown_top = set(data) - {'nodes', 'edges'}
    if unknown_top:
        raise ValueError(
            f'unknown top-level pipeline key(s) '
            f'{sorted(unknown_top, key=repr)}; '
            'expected "nodes" and optionally "edges"'
        )

    nodes_spec = data.get('nodes')
    if not isinstance(nodes_spec, dict) or not nodes_spec:
        raise ValueError('YAML pipeline requires a non-empty "nodes" mapping')

    node_dict: dict[str, Any] = {}
    for name, node_spec in nodes_spec.items():
        node_dict[name] = _coerce_node(name, node_spec)

    for edge in data.get('edges', []) or []:
        _connect_edge(node_dict, edge)

    # Pass the node mapping (not a list) so ``dag.nodes`` is keyed by name, the
    # form ``aggregate`` relies on when looking up per-node result loaders.
    dag = Pipeline(node_dict, root_dpath=root_dpath)
    dag.build_nx_graphs()
    return dag


def _node_class_ref(node: Any) -> str:
    """
    Return the importable ``module.Qualname`` for a node's class, or raise if it
    is not importable (defined in ``__main__`` or a local scope).
    """
    cls = type(node)
    module = getattr(cls, '__module__', None)
    qualname = getattr(cls, '__qualname__', cls.__name__)
    if module in (None, '__main__', 'builtins') or '<locals>' in qualname:
        raise ValueError(
            ub.paragraph(
                f"""
            Cannot serialize node {node.name!r}: its class {qualname} is defined
            in {module!r}, which is not importable. Define the node class in a
            regular importable module (not __main__ or a local/nested scope) so
            the pipeline can round-trip through the declarative form.
            """
            )
        )
    return f'{module}.{qualname}'


def dump_yaml_pipeline(dag: Any) -> dict[str, Any]:
    """
    Serialize a :class:`~kwdagger.Pipeline` to the declarative dict form.

    This is the inverse of :func:`load_yaml_pipeline`: the returned mapping, fed
    back through ``load_yaml_pipeline`` (or :func:`kwdagger.pipeline.coerce_pipeline`),
    reconstructs an equivalent pipeline. A node that is a plain
    :class:`~kwdagger.ProcessNode` or :class:`YamlProcessNode` is emitted as pure
    data; a node that is a custom subclass is emitted with a ``class`` reference
    to that subclass (its behavior lives in code, not data -- see the spec doc).

    Raises:
        ValueError: if a node's class is not importable, or if the node carries
            an implicit ordering dependency that the edge form cannot express.

    Example:
        >>> from kwdagger.yaml_pipeline import load_yaml_pipeline, dump_yaml_pipeline
        >>> dag = load_yaml_pipeline({
        ...     'nodes': {
        ...         'a': {'executable': 'echo a', 'out_paths': {'dst': 'a.txt'}},
        ...         'b': {'executable': 'echo b', 'in_paths': ['src'],
        ...               'out_paths': {'dst': 'b.txt'}},
        ...     },
        ...     'edges': ['a.dst -> b.src'],
        ... })
        >>> spec = dump_yaml_pipeline(dag)
        >>> spec['edges']
        ['a.dst -> b.src']
        >>> # Round-trips: reloading the spec yields the same graph.
        >>> dag2 = load_yaml_pipeline(spec)
        >>> assert sorted(dag2.node_dict) == sorted(dag.node_dict)
    """
    nodes_spec: dict[str, Any] = {}
    for name, node in dag.node_dict.items():
        if getattr(node, '_pred_nodes_without_io_connection', None):
            raise ValueError(
                ub.paragraph(
                    f"""
                Cannot serialize node {name!r}: it has an implicit ordering
                dependency (_pred_nodes_without_io_connection) that the
                declarative ``src.port -> dst.port`` edge form cannot express.
                """
                )
            )
        cls = type(node)
        spec: dict[str, Any] = {}

        # Behavior that is not data (custom command / parsing) -> reference the
        # class rather than trying to serialize its methods.
        if cls is not ProcessNode and cls is not YamlProcessNode:
            spec['class'] = _node_class_ref(node)

        if node.executable is not None:
            spec['executable'] = node.executable

        in_paths = node.in_paths
        if in_paths:
            spec['in_paths'] = (
                dict(in_paths)
                if isinstance(in_paths, dict)
                else sorted(in_paths)
            )
        if node.out_paths:
            spec['out_paths'] = dict(node.out_paths)
        if node.primary_out_key is not None:
            spec['primary_out_key'] = node.primary_out_key

        algo_params = node.algo_params
        if algo_params:
            spec['algo_params'] = (
                dict(algo_params)
                if isinstance(algo_params, dict)
                else sorted(algo_params)
            )
        perf_params = node.perf_params
        if perf_params:
            spec['perf_params'] = (
                dict(perf_params)
                if isinstance(perf_params, dict)
                else sorted(perf_params)
            )
        if node.group not in (None, '.'):
            spec['group'] = node.group
        if node.slurm_options:
            spec['slurm_options'] = dict(node.slurm_options)
        # Resource lifecycle (forwarded to cmd_queue); a single string or list.
        if node.setup:
            spec['setup'] = node.setup
        if node.teardown:
            spec['teardown'] = node.teardown

        # Data-driven extras are meaningful only on the default YamlProcessNode
        # path; a custom subclass carries that behavior in its code.
        if 'class' not in spec and isinstance(node, YamlProcessNode):
            if node._metrics_info:
                spec['metrics'] = [dict(m) for m in node._metrics_info]
            if node._vantage_points:
                spec['vantage_points'] = [dict(v) for v in node._vantage_points]
            if node._result_spec:
                spec['result'] = dict(node._result_spec)
            if node._load_result_ref is not None:
                spec['load_result'] = node._load_result_ref

        nodes_spec[name] = spec

    # Invert each input port's predecessors into "src.port -> dst.port" edges.
    edges: list[Any] = []
    for name, node in dag.node_dict.items():
        for in_name, inode in node.inputs.items():
            for pred in inode.pred:
                edges.append(
                    f'{pred.parent.name}.{pred.name} -> {node.name}.{in_name}'
                )
            if inode._gather_connection is not None:
                connection = inode._gather_connection
                edges.append(
                    {
                        'src': connection.source.key,
                        'dst': inode.key,
                        'gather': connection.spec.to_dict(),
                    }
                )

    out: dict[str, Any] = {'nodes': nodes_spec}
    if edges:
        out['edges'] = sorted(
            edges,
            key=lambda edge: (
                'string',
                edge,
            )
            if isinstance(edge, str)
            else ('mapping', edge['src'], edge['dst']),
        )
    return out
