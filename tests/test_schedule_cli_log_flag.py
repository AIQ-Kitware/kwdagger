"""
End-to-end CLI test: drive ``kwdagger schedule --run=0`` and verify the
written cmd_queue bash script tees node commands when ``--log=True``.

Why this exists
---------------
The in-process ``test_submit_jobs_log_flag.py`` covers the
``Pipeline.submit_jobs(log=...)`` boundary by inspecting BashJobs
directly. But there's a *separate* layer above that —
``kwdagger.schedule.build_schedule`` — which can mutate
``job.log`` after submission and silently override the flag. (And
historically did: an old ``for job in queue.jobs: job.log = False``
loop existed exactly here.)

The only way to catch a regression at that layer is to drive the
schedule CLI end-to-end with ``--run=0`` and assert against the
**rendered bash script that cmd_queue writes to disk**, which is the
artifact users actually inspect when something goes wrong.

This test does no model loads, no GPU, no network. It runs in well
under a second.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


def _run_schedule(tmp_path: Path, *, log_arg: str | None) -> Path:
    """Run ``kwdagger schedule --run=0`` and return the path to the
    rendered cmd_queue bash script.

    ``log_arg`` is ``"True"``, ``"False"``, or ``None`` (don't pass
    ``--log`` at all, exercising the default).
    """
    cmd: list[str] = [
        sys.executable,
        '-m',
        'kwdagger',
        'schedule',
        '--pipeline=kwdagger.pipeline.demodata_pipeline()',
        '--params={}',
        f'--root_dpath={tmp_path / "root"}',
        f'--queue_name=clitest_{log_arg or "default"}',
        '--backend=serial',
        '--run=0',
        # Quiet noisy output. We only care about side effects on disk.
        '--print_queue=0',
        '--print_commands=0',
    ]
    if log_arg is not None:
        cmd.append(f'--log={log_arg}')

    result = subprocess.run(
        cmd,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, 'PYTHONUNBUFFERED': '1'},
    )

    # ``schedule.py`` prints "Wrote script: to run execute:\n<path>"
    # when --run=0. Parse the path out of that line.
    match = re.search(
        r'Wrote script: to run execute:\s*\n(.*?)\n',
        result.stdout,
    )
    assert match, (
        "kwdagger schedule did not print the 'Wrote script: ...' line "
        'with --run=0. stdout was:\n' + result.stdout
    )
    script_path = Path(match.group(1).strip())
    assert script_path.is_file(), (
        f'reported script path does not exist: {script_path}'
    )
    return script_path


def _node_command_sections(script_text: str) -> list[str]:
    """Yield each ``# command: ... # after_command:`` block that
    represents a *real* (non-bookkeeper) node execution.

    Bookkeeper sections (which write ``invoke.sh`` files and create
    ``.succ`` / ``.pred`` symlinks) also use the same ``# command:``
    markers and can appear at the same indent depth as real-node
    sections once dependencies introduce ``if`` guards. The reliable
    distinguisher is the command content itself: bookkeeper sections
    contain the generated ``KWDAGGER_INVOKE_*`` heredoc that constructs the
    node's ``invoke.sh``, which never appears in a real-node command.

    Real-node BashJobs are the only ones whose ``self.log`` should
    drive the ``2>&1 | tee <log_fpath>`` wrapper. Bookkeeper jobs
    are explicitly excluded from tee in the BashJob renderer
    regardless of ``log``, so asserting tee on those sections would
    be wrong.
    """
    pattern = re.compile(
        r'(\s*# command:.*?# after_command:)',
        re.DOTALL,
    )
    sections = pattern.findall(script_text)
    return [s for s in sections if 'KWDAGGER_INVOKE_' not in s]


def test_schedule_cli_log_true_tees_node_commands(tmp_path):
    """``--log=True`` must produce ``2>&1 | tee <log_fpath>`` in every
    real node command's rendered bash.

    This is the regression that motivated the test. A previous
    ``schedule.py`` had a ``for job in queue.jobs: job.log = False``
    loop after submission that silently disabled tee even when the
    user explicitly opted in. That loop was deleted; this test
    locks in that it stays gone.
    """
    script_path = _run_schedule(tmp_path, log_arg='True')
    text = script_path.read_text()
    sections = _node_command_sections(text)
    assert sections, (
        'no real-node command sections found in rendered script. '
        "The renderer's output format may have changed; update the "
        '_node_command_sections regex. Script content:\n' + text
    )
    for i, section in enumerate(sections):
        assert '| tee ' in section, (
            f"node command section {i} did not contain '| tee '. "
            f'Section was:\n{section}\n'
            f'Full script:\n{text}'
        )


def test_schedule_cli_log_false_omits_tee(tmp_path):
    """``--log=False`` must skip the tee wrapper. Useful when the
    caller wants stdout/stderr to flow directly to a parent supervisor.
    """
    script_path = _run_schedule(tmp_path, log_arg='False')
    text = script_path.read_text()
    sections = _node_command_sections(text)
    assert sections, 'no real-node command sections found'
    for i, section in enumerate(sections):
        assert '| tee ' not in section, (
            f'--log=False but node command section {i} still contains '
            f"'| tee '. Section was:\n{section}"
        )


def test_schedule_cli_log_default_tees(tmp_path):
    """The current default for ``--log`` is True. Lock that in: omit
    the flag and verify tee still appears, so a future flip of the
    default surfaces here, not in production.
    """
    script_path = _run_schedule(tmp_path, log_arg=None)
    text = script_path.read_text()
    sections = _node_command_sections(text)
    assert sections, 'no real-node command sections found'
    for i, section in enumerate(sections):
        assert '| tee ' in section, (
            'default --log was expected to be True (tee on), but '
            f"section {i} has no '| tee '. If you intentionally flipped "
            'the default to False, update this test together with the '
            'kw.Value(...) default and submit_jobs default. '
            f'Section was:\n{section}'
        )
