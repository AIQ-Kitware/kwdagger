"""
Queue submission and filesystem bookkeeping for a concrete process graph.

This is the layer that turns a fully configured graph of :class:`ProcessNode`
instances into submitted jobs: it builds (or reuses) a cmd_queue queue, walks
the graph in topological order, writes the ``invoke.sh`` and
``job_config.json`` artifacts each node is independently runnable from, and
creates the ``.pred`` / ``.succ`` links that make the result directory
navigable.

It deliberately knows nothing about :class:`~kwdagger.pipeline.Pipeline` or
:class:`~kwdagger.pipeline.CompiledPipeline`, and imports the leaf modules
directly rather than the package facade. Both of those own a
``proc_graph`` and both submit the same way; the only thing this function
needs is that graph. Preconditions that belong to one of them -- a logical
pipeline refusing to submit an uncompiled gather, for instance -- stay with
the class that owns the precondition.
"""

from __future__ import annotations

import os
from typing import Any, cast

import networkx as nx
import ubelt as ub

from kwdagger.pipeline._agreement import (
    check_execution_agreement,
    execution_snapshot,
)
from kwdagger.pipeline._shell import bash_heredoc_write_command
from kwdagger.pipeline._slurm import coerce_slurm_options


def _has_jq() -> str | list[str] | None:
    return ub.find_exe('jq')


def submit_jobs(
    proc_graph: nx.DiGraph,
    slurm_options: Any = None,
    queue: Any = None,
    skip_existing: bool = False,
    enable_links: bool = True,
    write_invocations: bool = True,
    write_configs: bool = True,
    log: bool = True,
) -> dict[str, Any]:
    """
    Submit every node of a configured process graph to a command queue.

    Also takes care of adding special bookkeeping jobs that add helper
    files and symlinks to node output paths.

    Args:
        proc_graph (nx.DiGraph):
            the configured process graph. Each node carries the concrete
            :class:`ProcessNode` under its ``'node'`` attribute.

        slurm_options (Any):
            pipeline-wide Slurm options applied to every job, before any
            per-node override. Ignored on non-Slurm backends.

        queue (Any):
            an existing cmd_queue queue, or a dict of keyword arguments used
            to construct one. Defaults to a serial queue.

        skip_existing (bool):
            if True, nodes whose outputs already exist are not submitted.

        enable_links (bool):
            if True, write the ``.pred`` / ``.succ`` symlinks that make the
            result graph navigable.

        write_invocations (bool):
            if True, write a standalone ``invoke.sh`` per node.

        write_configs (bool):
            if True, write ``job_config.json`` per node.

        log (bool):
            If True (default), each per-node job is submitted with
            ``log=True`` so cmd_queue tees the job's stdout/stderr
            to ``info_dpath/status/<pathid>.logs``. This makes
            post-mortem diagnosis of failed runs much easier. Set
            to False to skip the tee.

    Returns:
        dict: a summary including the ``'queue'`` that was submitted to.
    """
    import json
    import shlex

    import cmd_queue
    import networkx as nx

    if queue is None:
        queue = {}

    if isinstance(queue, dict):
        # Create a simple serial queue if an existing one isn't given.
        default_queue_kw = {
            'backend': 'serial',
            'name': 'unnamed-kwdagger-pipeline',
            'size': 1,
            'gres': None,
        }
        queue_kw = ub.udict(default_queue_kw) | queue
        # The merged mapping is heterogeneous, so its inferred value type
        # is too wide for ``create``'s ``backend: str``. Pull the backend
        # out by hand rather than relying on how a checker widens the merge.
        backend = cast('str', queue_kw.pop('backend'))
        queue = cmd_queue.Queue.create(backend=backend, **queue_kw)

    node_order = list(nx.topological_sort(proc_graph))

    assert isinstance(proc_graph, nx.DiGraph)
    for node_name in node_order:
        node_data = proc_graph.nodes[node_name]
        try:
            node = node_data['node']
        except KeyError:
            import rich

            rich.print('[red]ERROR')
            print('node_name = {}'.format(ub.urepr(node_name, nl=1)))
            print('node_data = {}'.format(ub.urepr(node_data, nl=1)))
            raise
        node.will_exist = None

    summary = {'queue': queue, 'node_status': {}}
    node_status = summary['node_status']

    assert isinstance(proc_graph, nx.DiGraph)
    # Identity says two requests with one process_id are one job, so anything
    # identity cannot arbitrate has to agree between them or whichever arrived
    # first silently decides what runs. The gather compiler checks this over
    # the whole matrix; this is where an ordinary pipeline gets the same
    # arbitration, because it configures and submits a row at a time. The
    # registry hangs off the queue since that is what survives between rows,
    # whoever is driving the loop.
    registry: dict[str, Any] | None = getattr(
        queue, '__kwdagger_requests__', None
    )
    if registry is None:
        # Counted per submission rather than per registered node: the number a
        # user can act on is which call to ``submit_jobs`` -- which row of
        # their loop -- not how many nodes happened to be registered before
        # this one.
        registry = {'submissions': 0, 'by_process_id': {}}
        queue.__kwdagger_requests__ = registry  # type: ignore
    registry['submissions'] += 1
    requests: dict[str, Any] = registry['by_process_id']
    submission_label = f'request {registry["submissions"]}'

    # State this call carries that no node does. An ordinary pipeline keeps
    # ``__slurm_options__`` on the Pipeline rather than on its nodes, and the
    # bookkeeping flags are arguments here; a duplicate request returns before
    # any of them is applied, so a disagreement is silently first-call-wins.
    # ``skip_existing`` is deliberately absent: it selects which requests are
    # made rather than what a request asks for, and reversing two calls that
    # differ in it leaves the same queue.
    submission_state = {
        'slurm_options': slurm_options,
        'log': log,
        'enable_links': enable_links,
        'write_invocations': write_invocations,
        'write_configs': write_configs,
    }

    for node_name in node_order:
        node = proc_graph.nodes[node_name]['node']
        # Snapshot before anything below can disable the node or rewrite its
        # state, so what is compared is what the user asked for.
        _procid = node.process_id
        _snapshot = execution_snapshot(node, submission_state)
        _previous = requests.get(_procid)
        if _previous is None:
            requests[_procid] = {
                'snapshot': _snapshot,
                'label': submission_label,
            }
        else:
            check_execution_agreement(
                _previous['snapshot'],
                _snapshot,
                template_name=node.name,
                process_id=_procid,
                canonical_label=_previous['label'],
                duplicate_label=submission_label,
            )
        # print('-----')
        # print(f'node_name={node_name}')
        # print(f'node.enabled={node.enabled}')
        if not node.enabled:
            node_status[node_name] = 'disabled'
            node.will_exist = node.does_exist
            continue

        assert isinstance(proc_graph, nx.DiGraph)
        pred_node_names = list(proc_graph.predecessors(node_name))
        pred_nodes = [proc_graph.nodes[n]['node'] for n in pred_node_names]

        ancestors_will_exist = all(n.will_exist for n in pred_nodes)
        if skip_existing and node.enabled != 'redo' and node.does_exist:
            node.enabled = False

        node.will_exist = (
            node.enabled and ancestors_will_exist
        ) or node.does_exist
        if 0:
            print(f'node.final_out_paths={node.final_out_paths}')
            print(f'Checking {node_name}, will_exist={node.will_exist}')

        skip_node = not (node.will_exist and node.enabled)

        if skip_node:
            node_status[node_name] = 'skipped'
        else:
            node_procid = node.process_id
            node_job = None
            pred_node_procids = [n.process_id for n in pred_nodes if n.enabled]
            is_slurm = 'slurm' in queue.__class__.__name__.lower()
            has_gather = any(
                input_node._gather_members is not None
                for input_node in node.inputs.values()
            )
            invoke_fpath = node.final_node_dpath / 'invoke.sh'
            invoke_text = node._invocation_script_text()
            invoke_prewritten = False

            # Slurm serializes each job through ``sbatch --wrap``. A large
            # gather heredoc would therefore become one large argv entry at
            # submission time even though the heredoc itself is safe once
            # Bash reads it. Materialize the complete standalone invocation
            # file while compiling the queue and submit only a short
            # ``bash invoke.sh`` command. This preserves the ability to run
            # the graph without importing or invoking kwdagger.
            if is_slurm and has_gather:
                invoke_fpath.parent.ensuredir()
                invoke_fpath.write_text(invoke_text)
                invoke_fpath.chmod(0o775)
                invoke_prewritten = True
                node_command = '\n'.join(
                    [
                        '# kwdagger gather is materialized in the '
                        'standalone invocation script below',
                        'bash ' + shlex.quote(os.fspath(invoke_fpath)),
                    ]
                )
            else:
                node_command = node.final_command()

            # Another configuration may have submitted this job already
            if node_procid not in queue.named_jobs:
                extra_submitkw: dict[str, Any] = {}
                # Forward the log flag so cmd_queue tees stdout/stderr
                # to info_dpath/status/<pathid>.logs for post-mortem
                # diagnosis of node failures.
                extra_submitkw['log'] = log

                # Forward the resource lifecycle to cmd_queue: ``setup`` is
                # a gating precondition run before the command (e.g. acquire
                # a GPU lease) and ``teardown`` is cleanup that always runs
                # after the command -- on success, failure, and SIGTERM --
                # provided setup succeeded (e.g. release the lease). This is
                # the job-level try/finally; it co-locates acquire+release
                # in the job rather than as separate, skippable DAG nodes.
                # Works uniformly on the serial/tmux and slurm backends.
                node_setup = getattr(node, 'setup', None)
                node_teardown = getattr(node, 'teardown', None)
                if node_setup:
                    extra_submitkw['setup'] = node_setup
                if node_teardown:
                    extra_submitkw['teardown'] = node_teardown
                if is_slurm:
                    # Global slurm options apply to every job.
                    extra_submitkw.update(coerce_slurm_options(slurm_options))
                    # Allow per-node overrides specified on the class or via
                    # configuration.
                    extra_submitkw.update(
                        coerce_slurm_options(
                            getattr(node, 'slurm_options', None)
                        )
                    )
                    # Set the slurm output file to be in the node directory
                    # to make debugging somewhat easier.  Need to see if
                    # there is a cleaner way to do this.
                    extra_submitkw.setdefault(
                        'output_fpath',
                        node.final_node_dpath
                        / f'slurm-output-{node_procid}.log',
                    )

                # Bash heredoc terminators must begin in column zero.
                # cmd_queue normally indents dependency-guarded jobs, which
                # would invalidate the gather manifest delimiter. The shell
                # does not require commands inside an ``if`` body to be
                # indented, so disable formatting indentation whenever the
                # concrete consumer command contains a gather heredoc.
                if has_gather and not is_slurm:
                    extra_submitkw['allow_indent'] = False

                # TODO: we need to be able to pass per-job slurm options
                node_job = queue.submit(
                    command=node_command,
                    depends=pred_node_procids,
                    name=node_procid,
                    **extra_submitkw,
                )
                node_status[node_name] = 'new_submission'
            else:
                # Some other config submitted this job, we can skip the
                # rest of the work for this node.
                node_status[node_name] = 'duplicate_submission'
                continue

            # We might want to execute a few boilerplate instructions
            # before running each node.
            before_node_commands = []

            # Add symlink jobs that make the graph structure traversable in
            # the flat output directories.
            if enable_links:
                # TODO: ability to bind jobs to be run in the same queue
                # together

                # ``pred_nodes`` comes from the execution graph, which is
                # effective and whose nodes are the canonical instances. A
                # structural query here would link a result to a producer it
                # never read, and in the compiled case could hand back a
                # deduplicated instance rather than the surviving one.
                for pred in pred_nodes:
                    link_path1 = (
                        pred.final_node_dpath
                        / '.succ'
                        / node.name
                        / node.process_id
                    )
                    target_path1 = node.final_node_dpath
                    link_path2 = (
                        node.final_node_dpath
                        / '.pred'
                        / pred.name
                        / pred.process_id
                    )
                    target_path2 = pred.final_node_dpath
                    target_path1 = os.path.relpath(
                        target_path1.absolute(),
                        link_path1.absolute().parent,
                    )
                    target_path2 = os.path.relpath(
                        target_path2.absolute(),
                        link_path2.absolute().parent,
                    )

                    parts = [
                        f'mkdir -p {link_path1.parent}',
                        f'mkdir -p {link_path2.parent}',
                        f'ln -sfT "{target_path1}" "{link_path1}"',
                        f'ln -sfT "{target_path2}" "{link_path2}"',
                    ]
                    # command = '(' + ' && '.join(parts) + ')'
                    before_node_commands.extend(parts)

            if write_invocations and not invoke_prewritten:
                # Write the exact independently executable command. For a
                # gathered consumer this includes the quoted manifest
                # heredoc and all cache guards.
                command = bash_heredoc_write_command(
                    invoke_text,
                    invoke_fpath,
                    label=f'KWDAGGER_INVOKE_{node.name}',
                )
                before_node_commands.extend(
                    [
                        command,
                        'chmod +x -- ' + shlex.quote(os.fspath(invoke_fpath)),
                    ]
                )

            if write_configs:
                depends_config = node._depends_config()
                # Add a job that writes a file with the command used to
                # execute this node.
                job_config_fpath = node.final_node_dpath / 'job_config.json'
                json_text = json.dumps(depends_config)
                if is_slurm and has_gather:
                    # Gather provenance can be as large as the manifest.
                    # Keep it out of a second Slurm ``--wrap`` argument.
                    job_config_fpath.parent.ensuredir()
                    if _has_jq():
                        json_text = json.dumps(depends_config, indent=4)
                    job_config_fpath.write_text(json_text)
                else:
                    command = bash_heredoc_write_command(
                        json_text,
                        job_config_fpath,
                        label=f'KWDAGGER_CONFIG_{node.name}',
                        filter_command='jq .' if _has_jq() else None,
                    )
                    before_node_commands.append(command)

            if before_node_commands:
                if has_gather:
                    before_node_commands.insert(
                        0,
                        '# kwdagger bookkeeping only; the gather manifest '
                        'is materialized by the consumer command/invoke.sh',
                    )
                # TODO: nicer infastructure mechanisms (make the code
                # prettier and easier to reason about)
                before_command = '\n'.join(
                    ['(', 'set -e', *before_node_commands, ')']
                )
                _procid = 'before_' + node_procid
                if _procid not in queue.named_jobs:
                    before_submitkw = {}
                    if not is_slurm:
                        # Invocation and config artifacts are serialized with
                        # quoted heredocs. Dependent jobs must not indent
                        # their terminators inside cmd_queue's status guard.
                        before_submitkw['allow_indent'] = False
                    _job = queue.submit(
                        command=before_command,
                        depends=pred_node_procids,
                        bookkeeper=1,
                        name=_procid,
                        tags=['boilerplate'],
                        **before_submitkw,
                    )
                    if node_job is not None:
                        if node_job.depends is None:
                            node_job.depends = []
                        cast(list, node_job.depends).append(_job)

    # print(f'queue={queue}')
    return summary
