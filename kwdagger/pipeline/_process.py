"""
Concrete process behavior: :class:`ProcessNode`.

Everything about turning a node's parameters into a command and a place to put
the results lives here -- configuration resolution, the identity and provenance
payloads, path template finalization, gather manifest materialization, and the
existence checks that decide whether work can be skipped.

Imports the connection layer; is imported by the compiler and the logical
layer. Never the other way around.
"""

from __future__ import annotations

import functools
import os
import warnings
from collections import defaultdict
from functools import cached_property
from typing import Any, cast

import ubelt as ub

from kwdagger.pipeline._connections import (
    IONode,
    InputNode,
    Node,
    OutputNode,
    ParamNode,
    _UNSET,
    _alias_preds,
    _origin_identity_bindings,
    _origin_kind,
    _produced_origins,
)
from kwdagger.pipeline._shell import bash_heredoc_write_command
from kwdagger.pipeline._slurm import coerce_slurm_options


def _classvar_init(self: Any, args: Any, fallbacks: Any) -> None:
    """
    Helps initialize class instance variables from class variable defaults.

    Not sure what a good name for this is. The idea is that we will get a
    dictionary containing all init args, and anything that is None will be
    replaced by the class level attribute with the same name. Additionally, a
    dictionary of fallback defaults is used if the class variable is also None.

    Usage should look something like

    .. code:: python

        class MyClass:
            def __init__(self, a, b, c):
                args = locals()
                fallback = {
                    'a': [],
                }
                _classvar_init(self, args, fallbacks)

    """
    # Be careful of the magic '__class__' attribute that inherited classes will
    # get in the locals() of their `__init__` method. Workaround this by not
    # processing any '_'-prefixed name.
    cls = self.__class__
    args = cast(dict[str, Any], args)
    fallbacks = cast(dict[str, Any], fallbacks)
    for key, value in list(args.items()):
        if value is self or key.startswith('_'):
            continue
        if value is None:
            # Default to the class-level variable
            value = getattr(cls, key, value)
            if value is None:
                value = fallbacks.get(key, value)
            args[key] = value
        setattr(self, key, value)


class memoize_configured_method(object):
    """
    ubelt memoize_method but uses a special cache name
    """

    def __init__(self, func: Any) -> None:
        self._func = func
        self._cache_name = '_cache__' + func.__name__
        # Mimic attributes of a bound method
        self.__func__ = func
        functools.update_wrapper(self, func)

    def __get__(self, instance: Any, cls: Any = None) -> Any:
        """
        Descriptor get method. Called when the decorated method is accessed
        from an object instance.

        Args:
            instance (object): the instance of the class with the memoized method
            cls (type | None): the type of the instance
        """
        self._instance = instance
        return self

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """
        The wrapped function call
        """
        from ubelt.util_memoize import _make_signature_key

        func_cache = self._instance._configured_cache
        # func_cache = self._instance.__dict__
        cache = func_cache.setdefault(self._cache_name, {})
        key = _make_signature_key(args, kwargs)
        if key in cache:
            return cache[key]
        else:
            value = cache[key] = self._func(self._instance, *args, **kwargs)
            return value


def memoize_configured_property(fget: Any) -> Any:
    """
    ubelt memoize_property but uses a special cache name
    """
    # Unwrap any existing property decorator
    while hasattr(fget, 'fget'):
        fget = fget.fget

    attr_name = '_' + fget.__name__

    @functools.wraps(fget)
    def fget_memoized(self: Any) -> Any:
        cache = self._configured_cache
        if attr_name not in cache:
            cache[attr_name] = fget(self)
        return cache[attr_name]

    return property(fget_memoized)


# Uncomment for debugging
# memoize_configured_method = ub.identity
# memoize_configured_property = property
class ProcessNode(Node):
    """
    Represents a process in the pipeline.

    ProcessNodes are connected via their input / output nodes.

    You can create an instance of this directly, or inherit from it and set its
    class variables.

    For examples on how to define a full pipeline see the
    :doc:`the kwdagger tutorial <manual/tutorial/examples/README.rst>

    CommandLine:
        xdoctest -m kwdagger.pipeline ProcessNode

    Notes:
        When a ProcessNode is used for an evaluation node, it can / should be
        extended with the following methods:

            * load_result - for evaluation nodes
                In your pipeline class you a method ``def load_result(self,
                node_dpath):`` which returns a flat dot-dictionary of params and
                results from the node.

            * _default_metrics - returns Tuple of primary and display metric
                suffixes for the node that will be interpreted as the metrics.
                Note: this is likely to change so the user can specify if
                metrics need to be minimized / maximized.

            * _default_metrics2 - experimental new way of specifying metric info.
               Should return a list of dictionaries with keys

                   suffix (str): the name of the metric

                   objective (Optional[str]):
                       minimize or maximize (defaults to maximize)

                   primary (Optional[bool]):
                       if the metric is primary (defaults to False, unless no
                       other metric is primary in which case the first one
                       defaults to True).

                   display (Optional[bool]):
                       show the column in the stdout table. Defaults to False.
                       Note: any column marked as primary is also displayed.

                   aggregator (Optional[bool]):
                       how to aggregate this metric when computing macro averages.
                       Can be "mean", "gmean", "sum", "max", "min", or "ignore".
                       Defaults to "mean".

            * default_vantage_points - should return a List[Dict] with
                  metric1 and metric2, used for inspecting relationships
                  between metrics.

        These methods are currently used in aggregate_loader and aggregate, but
        they need to be more clearly defined here. Currently they are hacked in
        with hasattr, so we can't define them as abstract methods yet, but in
        the future we will refactor to make it more clear that these methods
        should be implemented for evaluation nodes (i.e. nodes that produce
        metric results).

    Example:
        >>> from kwdagger.pipeline import ProcessNode
        >>> import ubelt as ub
        >>> from kwdagger.pipeline import _classvar_init
        >>> dpath = ub.Path.appdir('kwdagger/tests/pipeline/TestProcessNode')
        >>> dpath.delete().ensuredir()
        >>> pycode = ub.codeblock(
        ...     '''
        ...     import ubelt as ub
        ...     src_fpath = ub.Path(ub.argval('--src'))
        ...     dst_fpath = ub.Path(ub.argval('--dst'))
        ...     foo = ub.argval('--foo')
        ...     bar = ub.argval('--bar')
        ...     new_text = foo + src_fpath.read_text() + bar
        ...     dst_fpath.write_text(new_text)
        ...     ''')
        >>> src_fpath = dpath / 'here.txt'
        >>> src_fpath.write_text('valid input')
        >>> dst_fpath = dpath / 'there.txt'
        >>> self = ProcessNode(
        >>>     name='proc1',
        >>>     config={
        >>>         'foo': 'baz',
        >>>         'bar': 'biz',
        >>>         'num_workers': 3,
        >>>         'src': src_fpath,
        >>>         'dst': dst_fpath
        >>>     },
        >>>     in_paths={'src'},
        >>>     out_paths={'dst': 'there.txt'},
        >>>     primary_out_key='dst',
        >>>     perf_params={'num_workers'},
        >>>     group='predictions',
        >>>     #node_dname='proc1/{proc1_algo_id}/{proc1_id}',
        >>>     executable=f'python -c "{chr(10)}{pycode}{chr(10)}"',
        >>>     root_dpath=dpath,
        >>> )
        >>> self._finalize_templates()
        >>> print('self.command = {}'.format(ub.urepr(self.command, nl=1, sv=1)))
        >>> print(f'self.algo_id={self.algo_id}')
        >>> print(f'self.root_dpath={self.root_dpath}')
        >>> print(f'self.template_node_dpath={self.template_node_dpath}')
        >>> print('self.templates = {}'.format(ub.urepr(self.templates, nl=2)))
        >>> print('self.final = {}'.format(ub.urepr(self.final, nl=2)))
        >>> print('self.condensed = {}'.format(ub.urepr(self.condensed, nl=2)))
        >>> print('self.primary_out_key = {}'.format(ub.urepr(self.primary_out_key, nl=2)))

    Example:
        >>> # How to use a ProcessNode to handle an arbitrary process call
        >>> # First let's write a program to disk
        >>> from kwdagger.pipeline import ProcessNode
        >>> import ubelt as ub
        >>> import stat
        >>> dpath = ub.Path.appdir('kwdagger/tests/pipeline/TestProcessNode2')
        >>> dpath.delete().ensuredir()
        >>> pycode = ub.codeblock(
                '''
                #!/usr/bin/env python3
                import scriptconfig as scfg
                import ubelt as ub

                class MyCLI(scfg.DataConfig):
                    src = None
                    dst = None
                    foo = None
                    bar = None

                    @classmethod
                    def main(cls, cmdline=1, **kwargs):
                        config = cls.cli(cmdline=cmdline, data=kwargs, strict=True)
                        print('config = ' + ub.urepr(config, nl=1))

                if __name__ == '__main__':
                    MyCLI.main()
        ...     ''')
        >>> fpath = dpath / 'mycli.py'
        >>> fpath.write_text(pycode)
        >>> fpath.chmod(fpath.stat().st_mode | stat.S_IXUSR)
        >>> # Now that we have a script that accepts some cli arguments
        >>> # Create a process node to represent it. We assume that
        >>> # everything is passed as key/val style params, which you *should*
        >>> # use for new programs, but this doesnt apply to a lot of programs
        >>> # out there, so we will show how to handle non key/val arguments
        >>> # later (todo).
        >>> mynode = ProcessNode(command=str(fpath))
        >>> # Get the invocation by runnning
        >>> command = mynode.final_command()
        >>> print(command)
        >>> # Use a dictionary to configure key/value pairs
        >>> mynode.configure({'src': 'a.txt', 'dst': 'b.txt'})
        >>> command = mynode.final_command()
        >>> # Note: currently because of backslash formatting
        >>> # we need to use shell=1 or system=1 with ub.cmd
        >>> # in the future we will fix this in ubelt (todo).
        >>> # Similarly this class should be able to provide the arglist
        >>> # style of invocation.
        >>> print(command)
        >>> ub.cmd(command, verbose=3, shell=1)

    """

    __node_type__ = 'process'

    name: str | None = None

    # A path that will specified directly after the DAG root dpath.
    group: str | None = None

    # resources : Collection = None  # Unused?

    executable: str | None = None

    # TODO: maybe we want the idea of "unstable" params the user can mark if
    # there is a paramter that had its meaning change, but that wasn't captured
    # in a config spec (a common thing to happen). These parameters aren't
    # relied on for the hashid, but maybe other special handling can do
    # something interesting with them. There might be other flavors of this,
    # "dynamic params", "volitle params", "hardcoded params"

    algo_params: Any = None  # algorithm parameters - impacts output

    perf_params: Any = None  # performance parameters - no output impact

    # input paths
    # Should be specified as a set of names wrt the config or as dict mapping
    # from names to absolute paths.
    in_paths: Any = None

    # output paths
    # Should be specified as templates
    out_paths: Any = None

    primary_out_key: str | None = None

    # Optional job-level slurm options. Can be overridden via configuration.
    slurm_options: dict[str, Any] | None = None

    # Optional resource lifecycle forwarded to the underlying cmd_queue job
    # (see :meth:`Pipeline.submit_jobs`). ``setup`` is a gating precondition run
    # before the command -- e.g. acquire a GPU lease -- and a failing setup
    # skips the command and fails the node. ``teardown`` is cleanup that always
    # runs after the command (on success, failure, and signal) provided setup
    # succeeded -- e.g. release the lease. Together they are the node-level
    # try/finally for bracketing an external resource, rather than modeling
    # acquire/release as separate, skippable DAG nodes. Requires cmd_queue with
    # BashJob/SlurmJob setup/teardown support (>= 0.3.1).
    setup: Any = None
    teardown: Any = None

    # Optional scriptconfig schema for deriving path/param groups. This is the
    # preferred mechanism; _from_scriptconfig remains for legacy compatibility.
    params: Any = None
    root_dpath: Any = None

    def __init__(
        self,
        *,  # TODO: allow positional arguments after we find a good order
        name: str | None = None,
        executable: str | None = None,
        algo_params: Any = None,
        perf_params: Any = None,
        # resources=None,
        in_paths: Any = None,
        out_paths: Any = None,
        group: str | None = None,
        root_dpath: Any = None,
        config: Any = None,
        slurm_options: Any = None,
        setup: Any = None,
        teardown: Any = None,
        node_dpath: Any = None,  # overwrites configured node dapth
        group_dpath: Any = None,  # overwrites configured node dapth
        primary_out_key: str | None = None,
        _overwrite_node_dpath: Any = None,  # overwrites the configured node dpath
        _overwrite_group_dpath: Any = None,  # overwrites the configured group dpath
        _no_outarg: bool = False,
        _no_inarg: bool = False,
        **aliases: Any,
    ) -> None:
        if aliases:
            if 'perf_config' in aliases:
                raise ValueError('You probably meant perf_params')
            if 'algo_config' in aliases:
                raise ValueError('You probably meant algo_params')

            if 'command' in aliases:
                executable = aliases['command']

        if node_dpath is not None:
            _overwrite_node_dpath = node_dpath
        if group_dpath is not None:
            _overwrite_group_dpath = group_dpath

        del node_dpath
        del group_dpath
        del aliases

        # if name is None and executable is None:
        #     name = f'unnamed_process_node_{id(self)}'
        if name is None:
            # Not sure exactly what's going on here, (i.e. why our smart nodes
            # are getting created without a name)
            if (
                executable is not None
                or self.__class__.__name__ == 'ProcessNode'
            ):
                name = 'unnamed_process_node_' + str(
                    id(self)
                )  # ub.hash_data(executable)[0:8]

        args = locals()
        fallbacks: dict[str, Any] = {
            # 'resources': {
            #     'cpus': 2,
            #     'gpus': 0,
            # },
            'config': {},
            'in_paths': {},  # should this be a set instead?
            'out_paths': {},
            'perf_params': {},
            'algo_params': {},
            'primary_out_key': None,
            'slurm_options': None,
            # Resource lifecycle forwarded to cmd_queue: ``setup`` is a gating
            # precondition (e.g. acquire a GPU lease) and ``teardown`` is an
            # always-run cleanup (e.g. release it) -- see submit_jobs.
            'setup': None,
            'teardown': None,
        }
        _classvar_init(self, args, fallbacks)
        super().__init__(args['name'])

        self._configured_cache: dict[str, Any] = {}
        # Preserve the baseline slurm options so repeated configure calls start
        # from the class / instance defaults.
        self._base_slurm_options = coerce_slurm_options(self.slurm_options)
        self.slurm_options = dict(self._base_slurm_options)

        if self.params is not None:
            derived = self._derive_groups_from_params_spec(self.params)
            (
                derived_in_paths,
                derived_out_paths,
                derived_algo_params,
                derived_perf_params,
                derived_primary_out_key,
            ) = derived
            if self.in_paths is None:
                self.in_paths = set()
            if self.out_paths is None:
                self.out_paths = {}
            if self.algo_params is None:
                self.algo_params = {}
            if self.perf_params is None:
                self.perf_params = {}
            if isinstance(self.in_paths, dict):
                in_paths = cast(dict[str, Any], self.in_paths)
                for key in derived_in_paths:
                    in_paths.setdefault(key, None)
            else:
                self.in_paths = set(self.in_paths) | set(derived_in_paths)
            for key, value in derived_out_paths.items():
                assert isinstance(self.out_paths, dict)
                self.out_paths.setdefault(key, value)
            if isinstance(self.algo_params, dict):
                for key, value in derived_algo_params.items():
                    self.algo_params.setdefault(key, value)
            else:
                self.algo_params = set(self.algo_params or set()) | set(
                    derived_algo_params
                )
            if isinstance(self.perf_params, dict):
                for key, value in derived_perf_params.items():
                    self.perf_params.setdefault(key, value)
            else:
                self.perf_params = set(self.perf_params or set()) | set(
                    derived_perf_params
                )
            if self.primary_out_key is None:
                self.primary_out_key = derived_primary_out_key

        if self.primary_out_key is None:
            if len(self.out_paths) == 1:
                self.primary_out_key = ub.peek(self.out_paths)

        if self.group is None:
            self.group = '.'

        root_dpath_: Any = self.root_dpath
        if root_dpath_ is None:
            root_dpath_ = '.'
        self.root_dpath = ub.Path(root_dpath_)

        self.templates = None

        self.final_outdir = None
        self.final_opaths = None
        self.enabled = True
        self.cache = True
        self._no_outarg = _no_outarg
        self._no_inarg = _no_inarg

        # TODO: make specifying these overloads more natural
        # Basically: use templates unless the user gives these
        self._overwrite_node_dpath = _overwrite_node_dpath
        self._overwrite_group_dpath = _overwrite_group_dpath

        # TODO: need a better name for this.
        # This is just a list of nodes that must be run before us, but we don't
        # have an explicit connection between the inputs / outputs.
        # This is currently used as a workaround, but we should support it
        self._pred_nodes_without_io_connection: list[Any] = []

        self.configure(self.config)

        if self.primary_out_key is not None:
            assert self.out_paths is not None
            if self.primary_out_key not in self.out_paths:
                raise KeyError(
                    ub.paragraph(
                        f"""
                    The specified primary_out_key={self.primary_out_key} is not
                    a member of out_paths={self.out_paths} for pipeline node:
                    {self}.
                    """
                    )
                )

    @classmethod
    def _from_scriptconfig(cls, config_cls: Any, **kwargs: Any) -> Any:
        """
        EXPERIMENTAL

        Wrap a scriptconfig object to define a baseline process node.
        This is a legacy helper; prefer defining ``params`` on the node class.

        Ignore:
            >>> import scriptconfig as scfg
            >>> class Step1CLI(scfg.DataConfig):
            >>>     src = scfg.Value(None, tags=['in_path', 'primary'])
            >>>     dst = scfg.Value('step1_output.txt', tags=['out_path', 'primary'])
            >>>     extra_dpath = scfg.Value('some_dpath', tags=['out_path'])
            >>>     optional_path = scfg.Value(None, tags=['out_path'])
            >>>     foo = scfg.Value(None, tags=['algo_param'])
            >>>     bar = scfg.Value(None, tags=['algo_param'])
            >>>     workers = scfg.Value(None, tags=['perf_param'])
            >>>     verbose = scfg.Value(None, tags=['perf_param'])
            >>>     #
            >>>     @classmethod
            >>>     def main(cls, cmdline=1, **kwargs):
            >>>         config = cls.cli(cmdline=cmdline, data=kwargs, strict=True)
            >>>         print('config = ' + ub.urepr(config, nl=1))
            >>> #
            >>> class Step2CLI(scfg.DataConfig):
            >>>     src = scfg.Value(None, tags=['in_path', 'primary'])
            >>>     dst = scfg.Value('step2_output.txt', tags=['out_path', 'primary'])
            >>>     thresh = 0.5
            >>>     io_workers = scfg.Value(None, tags=['perf_param'])
            >>>     verbose = scfg.Value(None, tags=['perf_param'])
            >>>     #
            >>>     @classmethod
            >>>     def main(cls, cmdline=1, **kwargs):
            >>>         config = cls.cli(cmdline=cmdline, data=kwargs, strict=True)
            >>>         print('config = ' + ub.urepr(config, nl=1))
            >>> #
            >>> config_cls = Step1CLI
            >>> from kwdagger.pipeline import Pipeline, ProcessNode
            >>> import ubelt as ub
            >>> step1 = self = ProcessNode._from_scriptconfig(Step1CLI, executable='python step1.py', name='step1')
            >>> step2 = ProcessNode._from_scriptconfig(Step2CLI, executable='python step2.py', name='step2')
            >>> step1.outputs['dst'].connect(step2.inputs['src'])
            >>> print(step1.command)
            >>> print(step2.command)
            >>> nodes = [step1, step2]
            >>> dag = Pipeline(nodes)
            >>> dag.configure(
            >>> )
            >>> dag.print_io_graph()
            >>> dag.print_commands(with_status=0, exclude_tags='boilerplate')
            >>> param_basis = {
            >>>     'step1.foo': [1, 2, 3],
            >>>     'step2.thresh': [0.2],
            >>> }
            >>> param_grid = list(ub.named_product(**param_basis))
            >>> queue = dag.make_queue()['queue']
            >>> for config in param_grid:
            >>>     dag.configure(config)
            >>>     dag.submit_jobs(queue)
            >>> queue.print_commands(with_status=0, exclude_tags='boilerplate')

        Example:
            >>> import warnings
            >>> import scriptconfig as scfg
            >>> class DemoCfg(scfg.DataConfig):
            >>>     src = scfg.Value('ignored.txt', tags=['in_path'])
            >>>     dst = scfg.Value('schema.txt', tags=['out_path', 'primary'])
            >>>     foo = 1
            >>> #
            >>> class DemoNode(ProcessNode):
            >>>     name = 'demo'
            >>>     params = DemoCfg
            >>>     out_paths = {'dst': 'explicit.txt'}
            >>> #
            >>> with warnings.catch_warnings(record=True) as warns:
            >>>     warnings.simplefilter('always')
            >>>     node = DemoNode()
            >>>     found = any('src' in str(w.message) for w in warns)
            >>> found
            True
            >>> node.algo_params['foo'] == 1
            True
            >>> node.out_paths['dst'] == 'explicit.txt'
            True
            >>> derived = ProcessNode._derive_groups_from_params_spec(DemoCfg)
            >>> legacy = ProcessNode._from_scriptconfig(DemoCfg, name='demo')
            >>> legacy.in_paths == derived[0]
            True
            >>> legacy.out_paths == derived[1]
            True
        """
        derived = cls._derive_groups_from_params_spec(config_cls)
        path_kwargs: dict[str, Any] = {
            'in_paths': derived[0],
            'out_paths': derived[1],
            'algo_params': derived[2],
            'perf_params': derived[3],
        }
        if derived[4] is not None:
            path_kwargs['primary_out_key'] = derived[4]

        name = kwargs.get('name', None)
        if name is None:
            name = getattr(config_cls, '__command__', name)
        if name is None:
            name = config_cls.__name__
        node_kwargs: dict[str, Any] = {}
        node_kwargs['name'] = name
        node_kwargs['executable'] = '<EXECUTABLE UNSPECIFIED>'
        node_kwargs.update(path_kwargs)
        node_kwargs.update(kwargs)
        self = cls(**node_kwargs)
        return self

    @staticmethod
    def _derive_groups_from_params_spec(
        params_spec: Any,
    ) -> tuple[Any, Any, Any, Any, Any]:
        tag_to_group = {
            'in_path': 'in_paths',
            'in': 'in_paths',
            'out_path': 'out_paths',
            'out': 'out_paths',
            'algo_param': 'algo_params',
            'algo': 'algo_params',
            'perf_param': 'perf_params',
            'perf': 'perf_params',
        }
        path_kwargs: dict[str, Any] = {
            'in_paths': set(),
            'out_paths': {},
            'perf_params': {},
            'algo_params': {},
        }
        primary_out_key = None

        if params_spec is None:
            return (
                path_kwargs['in_paths'],
                path_kwargs['out_paths'],
                path_kwargs['algo_params'],
                path_kwargs['perf_params'],
                primary_out_key,
            )

        instance_values = None
        if isinstance(params_spec, dict):
            items = params_spec.items()
        elif hasattr(params_spec, '__default__'):
            config_cls: Any = (
                params_spec
                if isinstance(params_spec, type)
                else params_spec.__class__
            )
            defaults = config_cls.__default__
            if not isinstance(params_spec, type):
                instance_values = {}
                if hasattr(params_spec, 'items'):
                    try:
                        instance_values = dict(params_spec.items())
                    except Exception:
                        instance_values = {}
                if not instance_values:
                    instance_values = {
                        key: value
                        for key, value in getattr(
                            params_spec, '__dict__', {}
                        ).items()
                        if not key.startswith('_')
                    }
            items = defaults.items()
        else:
            raise TypeError(
                f'Unsupported params_spec type: {type(params_spec)}'
            )

        for key, value in items:
            if instance_values is not None and key in instance_values:
                default_value = instance_values[key]
            else:
                default_value = (
                    value.value if hasattr(value, 'value') else value
                )
            tags = set(getattr(value, 'tags', []) or [])
            have_tags = tag_to_group.keys() & tags
            if len(have_tags) > 1:
                raise ValueError(
                    f'Parameter "{key}" has conflicting tags: {sorted(have_tags)}'
                )
            have_groups = {tag_to_group[t] for t in have_tags}
            if 'primary' in tags and 'out_paths' in have_groups:
                primary_out_key = key
            if not have_groups:
                have_groups = {'algo_params'}
            for group_key in have_groups:
                if group_key == 'in_paths':
                    if default_value is not None:
                        warnings.warn(
                            f'Ignoring default for in_path "{key}" defined in params.'
                        )
                    path_kwargs[group_key].add(key)
                elif group_key == 'out_paths':
                    if isinstance(default_value, str) and default_value:
                        path_kwargs[group_key][key] = default_value
                else:
                    path_kwargs[group_key][key] = default_value

        return (
            path_kwargs['in_paths'],
            path_kwargs['out_paths'],
            path_kwargs['algo_params'],
            path_kwargs['perf_params'],
            primary_out_key,
        )

    def configure(
        self, config: Any = None, cache: bool = True, enabled: bool = True
    ) -> None:
        """
        Update the node configuration.

        This rebuilds the templates and formats them so the "final" variables
        take on directory names based on the given configuration. This a

        FIXME:
            Gotcha: Calling this twice with a new config will reset the
            configuration. These nodes are stateful, so we should maintain and
            update state, not reset it. If we need to reset to defaults, there
            should be a method for that. Can work around by passing self.config
            as the first argument.
        """
        self.cache = cache
        self._configured_cache.clear()  # Reset memoization caches
        # ProcessNode is deliberately reused across matrix rows. Port-local
        # values therefore must be reset before applying each row, otherwise
        # an omitted value inherits state from the previously configured row.
        for input_node in self.inputs.values():
            input_node._final_value = _UNSET
        for param_port in self.param_ports.values():
            param_port._final_value = _UNSET
        if config is None:
            config = {}
        # print(f'config = {ub.urepr(config, nl=1)}')
        config = _fixup_config_serializability(config)
        self.enabled = config.pop('__enabled__', enabled)
        # Special case for process specific slurm options
        _raw_slurm_opts = config.pop('__slurm_options__', None)
        _configured_slurm_options = coerce_slurm_options(_raw_slurm_opts)
        self.slurm_options = (
            ub.udict(self._base_slurm_options) | _configured_slurm_options
        )
        self.__slurm_options__ = dict(self.slurm_options)
        self.config = ub.udict(config)

        # self.algo_params = set(self.config) - non_algo_keys
        in_path_keys = self.config & set(self.in_paths)  # type: ignore
        for key in in_path_keys:
            self.inputs[key].final_value = self.config[key]

        # Same for parameters that have ports. A consumer whose parameter is
        # wired has no value in its own config, so its port stays unset and
        # resolves through its predecessor instead.
        for key, port in self.param_ports.items():
            if key in self.config:
                port.final_value = self.config[key]

        self._build_templates()
        self._finalize_templates()

    @memoize_configured_property
    def condensed(self) -> Any:
        """
        This is the dictionary that supplies the templated strings with the
        values we will finalize them with. We may want to change the name.
        """
        condensed = {}
        for node in self.predecessor_process_nodes():
            condensed.update(node.condensed)
        assert isinstance(self.name, str)
        condensed.update(
            {
                self.name + '_algo_id': self.algo_id,
                self.name + '_id': self.process_id,
            }
        )
        return condensed

    @memoize_configured_method
    def _build_templates(self) -> dict[str, Any]:
        templates = {}
        templates['root_dpath'] = str(self.root_dpath)
        templates['node_dpath'] = str(self.template_node_dpath)
        templates['out_paths'] = self.template_out_paths
        self.templates = templates
        return self.templates

    @memoize_configured_method
    def _finalize_templates(self) -> dict[str, Any]:
        templates = self.templates
        condensed = self.condensed
        final = {}
        try:
            # The root has no template components, but formatting it is what
            # the recorded value has always been.
            final['root_dpath'] = ub.Path(
                str(self.root_dpath).format(**condensed)
            )
            final['node_dpath'] = self.final_node_dpath
            final['out_paths'] = self.final_out_paths
            final['in_paths'] = self.final_in_paths
        except KeyError as ex:
            print('ERROR: {}'.format(ub.urepr(ex, nl=1)))
            print(
                'condensed = {}'.format(ub.urepr(condensed, nl=1, sort=False))
            )
            print(
                'templates = {}'.format(ub.urepr(templates, nl=1, sort=False))
            )
            raise
        self.final = final
        return self.final

    @memoize_configured_property
    def final_config(self) -> Any:
        """
        This is not really "final" in the aggregate sense.
        It is more of a "finalized" requested config.
        """
        final_config = self.config.copy()
        if not self._no_inarg:
            final_config.update(self.final_in_paths)
        if not self._no_outarg:
            # Hacky option, improve the API to make this not necessary
            # when the full command is given and we dont need to
            # add the extra args
            final_config.update(self.final_out_paths)
        final_config.update(self.final_perf_config)
        final_config.update(self.final_algo_config)
        return final_config

    def _ordinary_input_provenance(self) -> dict[str, Any]:
        """Describe the exact upstream port bound to each ordinary input."""
        provenance = {}
        for input_name, input_node in self.inputs.items():
            bindings = []
            for source_port in input_node.pred:
                assert isinstance(source_port, IONode)
                if isinstance(source_port, OutputNode):
                    # A produced value: identity lives in the producing
                    # instance, which ancestor hashing captures.
                    bindings.append(
                        {
                            'source_process_id': source_port.parent.process_id,
                            'source_port': source_port.name,
                            'source_kind': 'output',
                        }
                    )
                else:
                    # An alias carries an already-known value, so it is not
                    # itself a process dependency: record the fully-qualified
                    # binding and effective value, and deliberately omit a
                    # process id for the lender.
                    assert isinstance(source_port, InputNode)
                    binding = {
                        'source': source_port.key,
                        'target': input_node.key,
                        'source_port': source_port.name,
                        'source_kind': 'input',
                    }
                    binding.update(_source_value_record(source_port))
                    origins = _produced_origins(source_port)
                    if origins:
                        # ... but if that value is produced, the reader still
                        # needs to know which process made it.
                        binding['origins'] = [
                            {
                                'source_process_id': port.parent.process_id,
                                'source_port': port.name,
                            }
                            for port in origins
                        ]
                    bindings.append(binding)
            if bindings:
                bindings.sort(
                    key=lambda item: (
                        item.get('source_process_id', ''),
                        item['source_kind'],
                        item['source_port'],
                    )
                )
                provenance[input_name] = (
                    bindings[0] if len(bindings) == 1 else bindings
                )
        return provenance

    def _parameter_provenance(self) -> dict[str, Any]:
        """Describe shared algorithm-parameter bindings on this node."""
        provenance = {}
        for param_name, param_port in self.param_ports.items():
            bindings = []
            for source_port in param_port.pred:
                assert isinstance(source_port, ParamNode)
                binding = {
                    'source': source_port.key,
                    'target': param_port.key,
                    'source_kind': 'parameter',
                }
                binding.update(_source_value_record(source_port))
                bindings.append(binding)
            if bindings:
                bindings.sort(key=lambda item: item['source'])
                provenance[param_name] = (
                    bindings[0] if len(bindings) == 1 else bindings
                )
        return provenance

    def _shared_value_config(self) -> dict[str, Any]:
        """
        Values a shared-value port supplied rather than this node's own config.

        A shared value never appears in ``self.config`` -- the whole point of
        forwarding it is that the consumer does not restate it. It is still
        part of what this process was asked to run, so it has to be recoverable
        from the dotted configuration record, including from a *descendant's*
        record: the source process is not lineage, so nothing else downstream
        would ever mention the value.

        Only what the wire actually carried belongs here. When the source
        supplies nothing and this node falls back to its own declaration
        default, the value was defaulted, not specified, and recording it would
        misreport it as requested configuration.
        """
        shared: dict[str, Any] = {}
        for input_name, input_node in self.inputs.items():
            if not _alias_preds(input_node):
                continue
            value = input_node._shared_value()
            if value is not _UNSET:
                shared[input_name] = _jsonable_config_value(value)
        for param_name, param_port in self.param_ports.items():
            if not param_port.pred:
                continue
            value = param_port._shared_value()
            if value is not _UNSET:
                shared[param_name] = _jsonable_config_value(value)
        return shared

    def _gather_provenance(self) -> dict[str, Any]:
        """
        Describe every gathered input port on this node, keyed by port.

        Each record names the concrete instance the gather happened on, so a
        descendant that inherits several same-named gathering ancestors can
        still tell their memberships apart.
        """
        provenance = {}
        for input_name, input_node in self.inputs.items():
            if input_node._gather_members is None:
                continue
            connection = input_node._gather_connection
            assert connection is not None
            # One public serializer decides how a spec is written down;
            # ``to_dict`` omits defaults, but a provenance reader wants the
            # effective policy stated outright.
            spec_record: dict[str, Any] = {
                'order_by': [],
                'require': 'all_success',
            }
            spec_record.update(connection.spec.to_dict())
            provenance[input_name] = {
                'consumer_process_id': self.process_id,
                'source': connection.source.key,
                'group_by': spec_record['group_by'],
                'order_by': spec_record['order_by'],
                'require': spec_record['require'],
                'manifest_fpath': os.fspath(input_node.gather_manifest_fpath),
                'members': [
                    {
                        'process_id': member.parent.process_id,
                        'output': member.name,
                        'path': os.fspath(member.final_value),
                    }
                    for member in input_node._gather_members
                ],
            }
        return provenance

    def _depends_config(self) -> Any:
        """
        The dag config that specifies the parameters this node depends on.
        This is what we write to "job_config.json". Note: this output must be
        passed to dag.config, not node.config.
        """
        # A gather puts several concrete instances of one template name in the
        # lineage. Flattening them into one dotted namespace would let the
        # last-visited fold overwrite its siblings, so group by template name
        # first and only then decide how each key must be represented.
        by_name: dict[str, list[ProcessNode]] = defaultdict(list)
        for depend_node in list(self.ancestor_process_nodes()) + [self]:
            by_name[depend_node.name].append(depend_node)

        depends_config: dict[str, Any] = {}
        for name, instances in by_name.items():
            # One deterministic instance order, shared by every key, so the
            # i-th value of two collection-valued keys describe one instance.
            instances = sorted(set(instances), key=lambda n: n.process_id)
            requested = []
            for instance in instances:
                # An explicitly requested value outranks a forwarded one; they
                # can only differ if the pipeline already raised a conflict.
                merged = dict(instance._shared_value_config())
                merged.update(instance.config)
                requested.append(merged)
            if len(instances) > 1:
                # Name the instances the collections are aligned to. Without
                # this the reader cannot correlate a value with the concrete
                # process that used it.
                depends_config[f'__instances__.{name}'] = [
                    instance.process_id for instance in instances
                ]
            keys: set[str] = set()
            keys.update(*[set(item) for item in requested])
            for key in sorted(keys):
                values = [item.get(key, None) for item in requested]
                agree = len({repr(value) for value in values}) == 1
                depends_config[f'{name}.{key}'] = values[0] if agree else values

            # Gather membership is the only record of which concrete
            # instances a collection was built from, and a descendant of the
            # consumer needs it as much as the consumer does. Several
            # same-named instances may each have gathered, so these records
            # follow the same instance-aligned shape as the values above
            # rather than collapsing into one dotted key.
            gathered = [instance._gather_provenance() for instance in instances]
            gather_keys: set[str] = set()
            gather_keys.update(*[set(item) for item in gathered] or [set()])
            # Self describes its own ports without qualifying them, which is
            # the shape readers have always seen.
            prefix = '' if name == self.name else f'{name}.'
            for input_name in sorted(gather_keys):
                # ``None`` for an instance that does not gather this port
                # keeps the list index-aligned with ``__instances__``.
                records = [item.get(input_name) for item in gathered]
                depends_config[f'__gather__.{prefix}{input_name}'] = (
                    records[0] if len(records) == 1 else records
                )

        for input_name, binding in self._ordinary_input_provenance().items():
            depends_config[f'__input__.{input_name}'] = binding
        for param_name, binding in self._parameter_provenance().items():
            depends_config[f'__parameter__.{param_name}'] = binding
        return depends_config

    @memoize_configured_property
    def final_perf_config(self) -> Any:
        assert self.perf_params is not None
        final_perf_config = self.config & set(self.perf_params)  # type: ignore
        if isinstance(self.perf_params, dict):
            for k, v in self.perf_params.items():
                if k not in final_perf_config:
                    final_perf_config[k] = v
        return final_perf_config

    @memoize_configured_property
    def final_input_config(self) -> Any:
        """
        Resolved values of inputs that no ancestor produced.

        An input supplied by the matrix (``unconnected``) or shared from a
        peer's input (``aliased``) has no producing instance, so nothing
        upstream carries its identity. It has to enter ``depends``
        directly, or two runs over different data would be
        indistinguishable.

        A ``connected`` input is excluded: the producing instance's
        identity is already folded in through ancestor hashing, and
        including the path as well would double-count it. A ``gathered``
        input is excluded because its manifest path is derived from
        ``process_id``, which would be circular; membership enters
        identity through ``depends['__gather__.<port>']`` instead.
        """
        if self._no_inarg:
            return ub.udict({})
        values = {}
        for name, input_node in self.inputs.items():
            if input_node._gather_members is not None:
                continue
            if _produced_origins(input_node):
                # Something upstream makes this. Its identity belongs to the
                # producer, which ancestor hashing already captures, and the
                # produced path is rooted in a cache directory that must not
                # reach identity.
                continue
            values[name] = input_node.final_value
        return ub.udict(values)

    @memoize_configured_property
    def final_algo_config(self) -> Any:
        """
        The parameters that define *what algorithm* this node runs.

        Deliberately excludes paths. ``algo_id`` hashes this, so folding
        input paths in here would make the same algorithm on the same data
        hash differently depending on whether the path came from the matrix
        or from an upstream node -- which made ``algo_id`` incomparable
        across pipelines that wire a computation differently.

        Data identity is not lost: it lives in
        :func:`final_input_config` for inputs nothing produced, and in
        ancestor hashing for inputs something did. Both reach
        ``process_id`` through ``depends``.
        """
        # Paths and performance knobs are not part of the algorithm.
        non_algo_sets = [self.out_paths, self.perf_params, self.in_paths]
        non_algo_keys = (
            set.union(*[set(s) for s in non_algo_sets if s is not None])
            if non_algo_sets
            else set()
        )
        self.non_algo_keys = non_algo_keys

        final_algo_config = self.config - self.non_algo_keys  # type: ignore
        if isinstance(self.algo_params, dict):
            for k, v in self.algo_params.items():
                if k not in final_algo_config:
                    final_algo_config[k] = v

        # A wired parameter is supplied by its port, which outranks both the
        # row config and the declared default. Applied after defaults so it
        # is not overwritten by them. A port whose source never resolved a
        # value supplies nothing: writing its ``None`` would put a literal
        # ``--key=None`` on the consumer's command line that the source node
        # itself does not pass.
        for key, port in self.param_ports.items():
            if not port.pred:
                continue
            value = port._resolved_value()
            if value is _UNSET:
                continue
            final_algo_config[key] = value
        return final_algo_config

    @memoize_configured_property
    def final_in_paths(self) -> Any:
        in_paths = self.in_paths
        final_in_paths: dict[str, Any]
        if in_paths is None:
            final_in_paths = {}
        elif isinstance(in_paths, dict):
            final_in_paths = cast(dict[str, Any], in_paths.copy())
        else:
            final_in_paths = {k: None for k in in_paths}

        for key, input_node in self.inputs.items():
            final_in_paths[key] = input_node.final_value
        return final_in_paths

    @memoize_configured_property
    def template_out_paths(self) -> Any:
        """
        Note: template out paths are not impacted by out path config overrides,
        but the final out paths are.

        SeeAlso:
            :func:`ProcessNode.final_out_paths`
        """
        if not isinstance(self.out_paths, dict):
            out_paths = self.config & self.out_paths
        else:
            out_paths = self.out_paths
        template_node_dpath = self.template_node_dpath
        # Can we handle the case where one out path isn't specified, or is
        # given a special non-string value?
        template_out_paths = {
            k: None if v is None else str(template_node_dpath / v)
            for k, v in out_paths.items()
        }
        return template_out_paths

    @memoize_configured_property
    def final_out_paths(self) -> Any:
        """
        These are the locations each output will actually be written to.

        This is based on :func:`ProcessNode.template_out_paths` as well as any
        manual overrides specified in ``self.config``.
        """
        condensed = self.condensed
        template_out_paths = self.template_out_paths
        final_out_paths = {
            k: None if v is None else ub.Path(v.format(**condensed))
            for k, v in template_out_paths.items()
        }
        # The use config is allowed to overload outpaths
        overloads = self.config & final_out_paths.keys()
        if overloads:
            final_out_paths.update(overloads)
        return final_out_paths

    @memoize_configured_property
    def final_node_dpath(self) -> Any:
        """
        The configured directory where all outputs are relative to.
        """
        return ub.Path(str(self.template_node_dpath).format(**self.condensed))

    @property
    def template_group_dpath(self) -> Any:
        """
        The template for the directory where the configured node dpath will be placed.

        Note:
            Maybe this could be renamed to final_group_dpath, because there
            isn't any template components to this.
        """
        if self._overwrite_group_dpath is not None:
            return ub.Path(self._overwrite_group_dpath)
        assert isinstance(self.root_dpath, ub.Path)
        assert isinstance(self.name, str)
        if self.group is None:
            return self.root_dpath / self.name
        else:
            return self.root_dpath / self.group / self.name

    @memoize_configured_property
    def template_node_dpath(self) -> Any:
        """
        The template for the configured directory where all outputs are relative to.
        """
        if self._overwrite_node_dpath is not None:
            return ub.Path(self._overwrite_node_dpath)
        assert isinstance(self.name, str)
        key = self.name + '_id'
        return self.template_group_dpath / ('{' + key + '}')

    @memoize_configured_method
    def predecessor_process_nodes(self) -> Any:
        """
        Process nodes that this one depends on.
        """
        nodes = [
            pred.parent
            for k, v in self.inputs.items()
            for pred in _produced_origins(v)
        ] + self._pred_nodes_without_io_connection
        for input_node in self.inputs.values():
            if input_node._gather_members is not None:
                nodes.extend(
                    member.parent for member in input_node._gather_members
                )
        # Preserve order while avoiding duplicate object references.
        nodes = list(dict.fromkeys(nodes))
        return nodes

    @memoize_configured_method
    def successor_process_nodes(self) -> Any:
        """
        Process nodes that depend on this one.

        This looks like the mirror image of
        :meth:`predecessor_process_nodes`, and on a fully configured pipeline
        it is. It is not redundant on a *template* pipeline: a node memoizes
        its predecessors while it is still being constructed, before any
        connection exists, and that cache is only cleared by ``configure``.
        Reading the edges from the producing side is what makes
        :meth:`Pipeline.build_nx_graphs` see an ordinary output-to-input edge
        before configuration. Do not fold the two directions together without
        first fixing that staleness.
        """
        nodes = [
            succ.parent for k, v in self.outputs.items() for succ in v.succ
        ]
        return nodes

    @memoize_configured_method
    def ancestor_process_nodes(self) -> Any:
        """
        Example:
            >>> from kwdagger.pipeline import Pipeline
            >>> import ubelt as ub
            >>> pipe = Pipeline.demo()
            >>> self = pipe.node_dict['node_C1']
            >>> ancestors = self.ancestor_process_nodes()
            >>> print('ancestors = {}'.format(ub.urepr(ancestors, nl=1)))
        """
        # TODO: we need to ensure that this returns a consistent order
        seen = {}
        stack = [self]
        while stack:
            node = stack.pop()
            node_id = id(node)
            if node_id not in seen:
                seen[node_id] = node
                nodes = node.predecessor_process_nodes()
                stack.extend(nodes)
        seen.pop(id(self))  # remove self
        ancestors = list(seen.values())
        return ancestors

    @memoize_configured_property
    def depends(self) -> Any:
        """
        Identity inputs for this process, including exact connected bindings.
        """
        ancestors = self.ancestor_process_nodes()
        grouped_depends: dict[str, list[str]] = defaultdict(list)
        for node in ancestors:
            grouped_depends[node.name].append(node.algo_id)
        depends: dict[str, Any] = {}
        for name, algo_ids in grouped_depends.items():
            unique_ids = sorted(set(algo_ids))
            depends[name] = (
                unique_ids[0] if len(unique_ids) == 1 else unique_ids
            )
        for input_name, input_node in self.inputs.items():
            # Whatever produces this input identifies it, whether it is wired
            # straight in or reached through an alias. ``__inputs__`` cannot
            # stand in for this: a produced path is rooted in a cache
            # directory and is deliberately kept out of identity, and the
            # ancestor payload above records only ``algo_id``, which is blind
            # to the producer's own inputs. Two producers running one
            # algorithm over different data would otherwise be
            # indistinguishable here, and their consumers would collide.
            #
            # The process that merely *lends* an aliased input stays out: it
            # consumes the value, it does not make it. That is what keeps a
            # pure configuration alias identical to writing the value
            # directly on the consumer.
            identity_bindings = _origin_identity_bindings(input_node)
            if identity_bindings:
                depends[f'__input__.{input_name}'] = (
                    identity_bindings[0]
                    if len(identity_bindings) == 1
                    else identity_bindings
                )
        for input_name, input_node in self.inputs.items():
            if input_node._gather_members is not None:
                connection = input_node._gather_connection
                assert connection is not None
                depends[f'__gather__.{input_name}'] = {
                    'spec': connection.spec.to_dict(),
                    'members': [
                        (member.parent.process_id, member.name)
                        for member in input_node._gather_members
                    ],
                }
        # Inputs nothing upstream produced. Without this they would be
        # invisible to identity entirely, since they are no longer part of
        # final_algo_config and have no ancestor to speak for them.
        input_config = self.final_input_config
        if input_config:
            depends['__inputs__'] = dict(sorted(input_config.items()))
        dependency_only: dict[str, list[str]] = defaultdict(list)
        for predecessor in self._pred_nodes_without_io_connection:
            dependency_only[predecessor.name].append(predecessor.process_id)
        for name, process_ids in dependency_only.items():
            unique_ids = sorted(set(process_ids))
            depends[f'__dependency__.{name}'] = (
                unique_ids[0] if len(unique_ids) == 1 else unique_ids
            )
        assert isinstance(self.name, str)
        depends[self.name] = self.algo_id
        depends = ub.udict(sorted(depends.items()))
        return depends

    @memoize_configured_property
    def algo_id(self) -> str:
        """
        A unique id to represent the output of a deterministic process.

        This does NOT have a dependency on the larger the DAG.
        """
        from kwdagger.utils.reverse_hashid import condense_config

        assert isinstance(self.name, str)
        # The node name is part of the hashed payload, not merely a prefix
        # on the resulting string. Hashing the config alone made every node
        # with an empty algo config share one hash across unrelated
        # pipelines, so the hash portion could not be used on its own.
        payload = {'__node__': self.name, **self.final_algo_config}
        algo_id = condense_config(
            payload, self.name + '_algo_id', register=False
        )
        return algo_id

    @memoize_configured_property
    def process_id(self) -> str:
        """
        A unique id to represent the output of a deterministic process in a
        pipeline. This id combines the hashes of all ancestors in the DAG with
        its own hashed id.

        This DOES have a dependency on the larger DAG.
        """
        from kwdagger.utils.reverse_hashid import condense_config

        depends = self.depends
        assert isinstance(self.name, str)
        proc_id = condense_config(depends, self.name + '_id', register=False)
        return proc_id

    @staticmethod
    def _make_argstr(config: Any) -> str:
        # parts = [f'    --{k}="{v}" \\' for k, v in config.items()]
        parts = []
        import shlex

        # Emit arguments in a deterministic (sorted) order. ``config`` keys can
        # originate from set-valued ``in_paths`` / ``algo_params`` / etc., whose
        # iteration order is hash-seed dependent; sorting keeps the generated
        # command (and the invoke.sh written to disk) reproducible across runs
        # and Python versions. Argument order does not affect node identity --
        # the algo_id / process_id hashes normalize independently of this.
        for k, v in sorted(config.items()):
            if isinstance(v, list):
                # Handle variable-args params
                quoted_varargs = [shlex.quote(str(x)) for x in v]
                preped_varargs = [
                    '        ' + x + ' \\' for x in quoted_varargs
                ]
                parts.append(f'    --{k} \\')
                parts.extend(preped_varargs)
            else:
                if isinstance(v, dict):
                    # This relies on the underlying program being able to
                    # interpret YAML specified on the commandline.
                    from kwutil.util_yaml import Yaml

                    vstr = Yaml.dumps(v)
                    vstr = shlex.quote(vstr)
                    if '\n' in vstr and vstr[0] == "'":
                        # hack to prevent yaml indent errors
                        vstr = "'\n" + vstr[1:]
                    parts.append(f'    --{k}={vstr} \\')
                else:
                    import shlex

                    vstr = shlex.quote(str(v))
                    parts.append(f'    --{k}={vstr} \\')

        return '\n'.join(parts).lstrip().rstrip('\\')

    @cached_property
    def inputs(self) -> dict[str, InputNode]:
        """
        Input nodes representing specific input locations.

        The output nodes of other processes can be connected to these.
        Also input nodes for one process can connect to input nodes of another
        process representing that they share the same input data.

        Returns:
            Dict[str, InputNode]
        """
        assert self.in_paths is not None
        defaults = self.in_paths if isinstance(self.in_paths, dict) else {}
        inputs = {
            k: InputNode(
                name=k,
                parent=self,
                default_value=defaults.get(k, _UNSET),
            )
            for k in self.in_paths
        }
        return inputs

    @cached_property
    def param_ports(self) -> dict[str, ParamNode]:
        """
        Ports for algorithm parameters, so they can be wired between nodes.

        ``in_paths`` and ``out_paths`` have always been connectable because
        the IO graph was built to track files. An algorithm parameter had
        no port, so there was nothing for an edge to attach to -- and a
        value every consumer needs had to be restated for each of them in
        the parameter grid, growing as consumers x values.

        These are deliberately *not* part of :func:`inputs`. They are not
        data the node reads, so they must not appear in ``final_in_paths``,
        must not render as paths, and must not create a scheduling
        dependency. A wired parameter is an alias: it carries a value, and
        the value lands in ``final_algo_config`` like any other parameter.

        Returns:
            Dict[str, ParamNode]
        """
        keys = self.algo_params if self.algo_params is not None else {}
        defaults = (
            self.algo_params if isinstance(self.algo_params, dict) else {}
        )
        return {
            k: ParamNode(
                name=k,
                parent=self,
                default_value=defaults.get(k, _UNSET),
            )
            for k in keys
        }

    @cached_property
    def outputs(self) -> dict[str, OutputNode]:
        """
        Output nodes representing specific output locations. These can be
        connected to the input nodes of other processes.

        Returns:
            Dict[str, OutputNode]
        """
        assert self.out_paths is not None
        outputs = {k: OutputNode(name=k, parent=self) for k in self.out_paths}
        return outputs

    @property
    def command(self) -> str:
        """
        Returns the string shell command that will execute the process.

        Basic version of command, can be overwritten
        """
        argstr = self._make_argstr(self.final_config)
        assert self.executable is not None
        if argstr:
            command = self.executable + ' \\\n    ' + argstr
        else:
            command = self.executable
        return command

    def test_is_computed_command(self) -> str | None:
        r"""
        Generate a bash command that will test if all output paths exist

        Example:
            >>> from kwdagger.pipeline import ProcessNode
            >>> self = ProcessNode(out_paths={
            >>>     'foo': 'foo.txt',
            >>>     'bar': 'bar.txt',
            >>>     'baz': 'baz.txt',
            >>>     'biz': 'biz.txt',
            >>> }, node_dpath='.')
            >>> test_cmd = self.test_is_computed_command()
            >>> print(test_cmd)
            test -e foo.txt -a \
                 -e bar.txt -a \
                 -e baz.txt -a \
                 -e biz.txt
            >>> self = ProcessNode(out_paths={
            >>>     'foo': 'foo.txt',
            >>>     'bar': 'bar.txt',
            >>> }, node_dpath='.')
            >>> test_cmd = self.test_is_computed_command()
            >>> print(test_cmd)
            test -e foo.txt -a \
                 -e bar.txt
            >>> self = ProcessNode(out_paths={
            >>>     'foo': 'foo.txt',
            >>> }, node_dpath='.')
            >>> test_cmd = self.test_is_computed_command()
            >>> print(test_cmd)
            test -e foo.txt
            >>> self = ProcessNode(out_paths={}, node_dpath='.')
            >>> test_cmd = self.test_is_computed_command()
            >>> print(test_cmd)
            None
        """
        if not self.final_out_paths:
            return None
        import shlex

        if self.primary_out_key is not None:
            try:
                quoted_paths = [
                    shlex.quote(str(p))
                    for p in [self.final_out_paths[self.primary_out_key]]
                ]
            except KeyError as ex:
                from kwutil.util_exception import add_exception_note

                raise add_exception_note(
                    ex,
                    ub.paragraph(
                        f"""
                    In {self}.
                    """
                    ),
                )
        else:
            quoted_paths = [
                shlex.quote(str(p)) for p in self.final_out_paths.values()
            ]
        # Make the command look nicer
        tmp_paths = [f'-e {p}' for p in quoted_paths]
        tmp_paths = [p + ' -a' for p in tmp_paths[:-1]] + tmp_paths[-1:]
        *tmp_first, tmp_last = tmp_paths
        tmp_paths = [p + ' \\' for p in tmp_first] + [tmp_last]
        test_expr = '\n     '.join(tmp_paths)
        test_cmd = 'test ' + test_expr

        # test_expr = ' -a '.join(
        #     [f'-e "{p}"' for p in self.final_out_paths.values()])
        # test_cmd = 'test ' +  test_expr
        return test_cmd

    @memoize_configured_property
    def does_exist(self) -> bool:
        """
        Check if all of the output paths that would be written by this node
        already exists.
        """
        if len(self.final_out_paths) == 0:
            # Can only cache if we know what output paths are
            return False
        # return all(self.out_paths.map_values(lambda p: p.exists()).values())
        return all(
            ub.Path(p).expand().exists() if p is not None else True
            for p in self.final_out_paths.values()
        )

    def _raw_command(self) -> Any:
        command = self.command
        if not isinstance(command, str):
            assert callable(command)
            command = command()
        return command

    def _gather_manifest_commands(self) -> list[str]:
        """Return static manifest writers for configured collection inputs."""
        commands = []
        for input_node in self.inputs.values():
            if input_node._gather_members is None:
                continue
            connection = input_node._gather_connection
            assert connection is not None
            spec = connection.spec
            description = (
                f'# kwdagger gather: {connection.source.key} -> '
                f'{input_node.key} | members={len(input_node._gather_members)} '
                f'| group_by={list(spec.display_keys())!r} '
                f'| order_by={list(spec.order_by)!r} '
                f'| require={spec.require}'
            )
            writer = bash_heredoc_write_command(
                input_node.gather_manifest_text(),
                input_node.gather_manifest_fpath,
                label=f'KWDAGGER_GATHER_{self.name}_{input_node.name}',
                command_indent='    ',
                chain=True,
            )
            commands.extend(['    ' + description, writer])
        return commands

    @staticmethod
    def _cleanup_raw_command(command: str) -> str:
        """Normalize an executable command without touching heredoc bodies."""
        base_command = command.rstrip().rstrip('\\').rstrip()
        lines = base_command.split('\n')
        return '\n'.join([line for line in lines if line.strip() != '\\'])

    def _invocation_script_text(self) -> str:
        """Build the complete standalone ``invoke.sh`` file contents."""
        invoke_lines = ['#!/bin/bash']
        depend_nodes = list(self.ancestor_process_nodes())
        if depend_nodes:
            invoke_lines.append('# See Also:')
            for depend_node in depend_nodes:
                invoke_lines.append(
                    '# ' + os.fspath(depend_node.final_node_dpath)
                )
        else:
            invoke_lines.append('# Root node')
        invoke_lines.append(self.final_command())
        return '\n'.join(invoke_lines) + '\n'

    def _raw_command_with_gather(self) -> Any:
        """Return a standalone command including any gather materialization."""
        raw_command = self._cleanup_raw_command(self._raw_command())
        gather_commands = self._gather_manifest_commands()
        if not gather_commands:
            return raw_command
        # Use a brace group instead of a leading subshell. cmd_queue wraps
        # logged commands in ``(...)``; a command that itself begins with ``(``
        # would therefore become Bash arithmetic syntax ``((...))``. Explicit
        # ``&&`` chaining preserves fail-fast behavior without leaking
        # ``set -e`` into the surrounding queue script. Commands remain
        # indented for readability, while heredoc bodies and terminators stay
        # in column zero as required by Bash.
        indented_raw_command = ub.indent(raw_command, '    ')
        return '\n'.join(['{', *gather_commands, indented_raw_command, '}'])

    def final_command(self) -> Any:
        """
        Wraps ``self.command`` with optional checks to prevent the command from
        executing if its outputs already exist.
        """
        base_command = self._raw_command_with_gather()

        if self.cache or (not self.enabled and self.enabled != 'redo'):
            test_cmd = self.test_is_computed_command()
            if test_cmd is None:
                return base_command
            else:
                return test_cmd + ' || \\\n' + base_command
        else:
            return base_command


def _source_value_record(source_port: Any) -> dict[str, Any]:
    """
    Describe what a shared-value source port actually supplied.

    A source that resolves nothing is not the same as a source that resolves
    to ``None``: the first leaves the target on its own declaration default,
    the second is a value someone asked for. Reporting both as ``value: None``
    would make them indistinguishable in the requested record, so an
    unresolved source omits ``value`` and says so instead.
    """
    value = source_port._resolved_value()
    if value is _UNSET:
        return {'unresolved': True}
    return {'value': _jsonable_config_value(value)}


def _jsonable_config_value(value: Any) -> Any:
    """Convert common configuration values into JSON-compatible forms."""
    if isinstance(value, os.PathLike):
        return os.fspath(value)
    if isinstance(value, dict):
        return {
            key: _jsonable_config_value(item) for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_jsonable_config_value(item) for item in value]
    return value


def _fixup_config_serializability(config: Any) -> dict[str, Any]:
    # Do minor chanes to make the config json serializable.
    fixed_config = {}
    for k, v in config.items():
        fixed_config[k] = _jsonable_config_value(v)
    return fixed_config
