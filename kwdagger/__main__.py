#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
from __future__ import annotations

import kwconf as kw

# from module.cli.script import ScriptCLI


class KWDaggerModal(kw.ModalCLI):
    """
    KWDagger: define bash-centric DAGs and run large parameter sweeps.

    Use ``schedule`` to expand a pipeline over a parameter matrix and execute
    it on a serial, tmux, or Slurm backend, then ``aggregate`` to load the
    completed runs and report on parameter/metric relationships.
    """

    from kwdagger.aggregate import AggregateEvaluationConfig as aggregate
    from kwdagger.schedule import ScheduleEvaluationConfig as schedule
    # Either add other kwconf clis as class variables here
    # from module.cli.script import ScriptCLI as script


# Or register them here.
# TemplateModal.register(ScriptCLI)

__cli__: type[KWDaggerModal] = KWDaggerModal
main = __cli__.main


if __name__ == '__main__':
    main()
