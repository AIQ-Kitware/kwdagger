"""
Compatibility shim for the cmd-queue CLI boilerplate base class.

cmd_queue >= 0.3.2 ships :class:`cmd_queue.cli_boilerplate.CmdQueueConfigMixin`,
a :mod:`kwconf`-native base class that carries the common cmd-queue CLI options
plus ``create_queue`` / ``run_queue`` helpers.

Older cmd_queue (<= 0.3.1) only ships the :mod:`scriptconfig`-based
``CMDQueueConfig``, which cannot host :mod:`kwconf` fields. To let kwdagger move
to kwconf *now* while still supporting those releases, this module provides a
local kwconf reimplementation of the same boilerplate. It targets only the
stable :class:`cmd_queue.Queue` API (``create`` / ``submit`` / ``print_commands``
/ ``print_graph`` / ``run``), which is compatible across cmd_queue 0.3.x.

Downstream code should not import the fallback directly; use the resolved base
from :mod:`kwdagger.schedule`, which prefers the official class when available.

This shim can be removed once kwdagger's minimum cmd_queue is raised to >=0.3.2.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

import kwconf as kw
import ubelt as ub

if TYPE_CHECKING:
    import cmd_queue


class CmdQueueConfigMixin(kw.Config):
    """
    Local kwconf reimplementation of
    :class:`cmd_queue.cli_boilerplate.CmdQueueConfigMixin`.

    Kept field- and behavior-compatible with the upstream class so that
    switching between this fallback and the official one (based on the
    installed cmd_queue version) is transparent to subclasses.
    """

    # NOTE: do NOT add a ``: bool`` annotation here. kwconf coerces a
    # bool-annotated flag, so ``--run=0`` becomes ``bool('0')`` -> True.
    # Leaving it unannotated keeps the historical semantics: ``--run=0`` is
    # falsy, ``--run=1`` truthy, and bare ``--run`` True.
    run = kw.Flag(
        False,
        help='if False, only prints the commands, otherwise executes them',
        group='cmd-queue',
    )

    backend: str = kw.Value(
        'tmux',
        help=('The cmd_queue backend. Can be tmux, slurm, or serial'),
        group='cmd-queue',
    )

    monitor: str = kw.Value(
        'inline',
        help=ub.paragraph(
            """
            Where the live status UI runs while jobs execute.
            hybrid = inline monitor + attachable tmux session (best for
            interactive use); inline = inline only (default); tmux =
            detached tmux session only (survives the calling shell); none
            = headless (reattach hint still printed).
            """
        ),
        group='cmd-queue',
        choices=['hybrid', 'inline', 'tmux', 'none'],
    )

    queue_name: Optional[str] = kw.Value(
        None, help='overwrite the default queue name', group='cmd-queue'
    )

    print_commands: Any = kw.Value(
        'auto',
        isflag=True,
        help='enable / disable rprint before exec',
        group='cmd-queue',
    )

    print_queue: Any = kw.Value(
        'auto', isflag=True, help='print the cmd queue DAG', group='cmd-queue'
    )

    with_textual: Any = kw.Value(
        'auto',
        isflag=True,
        help='setting for cmd-queue monitoring',
        group='cmd-queue',
    )

    other_session_handler: str = kw.Value(
        'ask',
        help='for tmux backend only. How to handle conflicting sessions. Can be ask, kill, or ignore, or auto',
        group='cmd-queue',
    )

    virtualenv_cmd: Optional[str] = kw.Value(
        None,
        parser=str,
        help=ub.paragraph(
            """
        Command to start the appropriate virtual environment if your bashrc
        does not start it by default."""
        ),
        group='cmd-queue',
    )

    tmux_workers: int = kw.Value(
        8,
        help='number of tmux workers in the queue for the tmux backend',
        group='cmd-queue',
    )

    slurm_options: Any = kw.Value(
        None,
        help=ub.paragraph(
            """
        if the backend is slurm, provide a YAML dictionary for things like
        partition / etc...
        """
        ),
        group='cmd-queue',
    )

    def __post_init__(self) -> None:
        try:
            from cmd_queue.util.util_yaml import Yaml
        except Exception:
            from kwutil import Yaml

        self.slurm_options = Yaml.coerce(self.slurm_options) or {}

    def create_queue(config, **kwargs: Any) -> 'cmd_queue.Queue':
        """
        Create an empty queue based on options specified in this config

        Args:
            **kwargs: extra args passed to cmd_queue.Queue.create

        Returns:
            cmd_queue.Queue
        """
        import cmd_queue

        queuekw: Dict[str, Any] = {}
        if config.backend == 'slurm':
            queuekw.update(config.slurm_options)
        elif config.backend == 'tmux':
            queuekw.update(
                {
                    'size': config.tmux_workers,
                }
            )
        queuekw.update(kwargs)
        if 'name' not in queuekw:
            queuekw['name'] = config.queue_name
        queue = cmd_queue.Queue.create(backend=config.backend, **queuekw)
        if config.virtualenv_cmd:
            # Experimental feature to automatically activate virtual
            # environments
            virtualenv_cmd: Optional[str] = config.virtualenv_cmd
            if virtualenv_cmd == 'auto':
                import os
                import shlex

                venv_path = os.environ.get('VIRTUAL_ENV', '')
                if venv_path:
                    virtualenv_cmd = 'source ' + shlex.quote(
                        str(ub.Path(venv_path) / 'bin/activate')
                    )
                else:
                    virtualenv_cmd = None
            if virtualenv_cmd:
                queue.add_preamble_command(virtualenv_cmd)
        return queue

    def run_queue(
        config,
        queue: 'cmd_queue.Queue',
        print_kwargs: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        """
        Execute a queue with options based on this config.

        Args:
            queue (cmd_queue.Queue): queue to run / report
            print_kwargs (None | Dict):
        """
        print_thresh = 30
        if config['print_commands'] == 'auto':
            if len(queue) < print_thresh:
                config['print_commands'] = 1
            else:
                print(
                    f'More than {print_thresh} jobs, skip queue.print_commands. '
                    'If you want to see them explicitly specify print_commands=1'
                )
                config['print_commands'] = 0

        if config['print_queue'] == 'auto':
            if len(queue) < print_thresh:
                config['print_queue'] = 1
            else:
                print(
                    f'More than {print_thresh} jobs, skip queue.print_graph. '
                    'If you want to see them explicitly specify print_queue=1'
                )
                config['print_queue'] = 0

        if config.print_commands:
            if print_kwargs is None:
                print_kwargs = {}
            queue.print_commands(**print_kwargs)

        if config.print_queue:
            queue.print_graph(vertical_chains=True)

        if config.run:
            queue.run(
                with_textual=config.with_textual,
                other_session_handler=config.other_session_handler,
                monitor=config.monitor,
                **kwargs,
            )
