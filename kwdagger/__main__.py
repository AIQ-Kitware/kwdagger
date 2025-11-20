#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
import scriptconfig as scfg
# from module.cli.script import ScriptCLI


class KWDaggerModal(scfg.ModalCLI):
    """
    Your description here
    """
    from kwdagger.schedule import ScheduleEvaluationConfig as schedule
    from kwdagger.aggregate import AggregateEvluationConfig as aggregate
    # Either add other scriptconfig clis as class variables here
    # from module.cli.script import ScriptCLI as script

# Or register them here.
# TemplateModal.register(ScriptCLI)

__cli__ = KWDaggerModal
main = __cli__.main


if __name__ == '__main__':
    main()
