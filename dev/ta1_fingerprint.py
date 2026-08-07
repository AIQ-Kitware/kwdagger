#!/usr/bin/env python3
"""
Compile real TA1 card pipelines and print a stable fingerprint of the result.

The gate used throughout the 0.3.x review rounds, written down. Its question
is narrow and worth stating exactly: **did this change move any process id,
node directory, or command for a pipeline someone actually runs?** A change
that does moves every affected result directory and invalidates the cache
sitting in it, which is a cost no test suite reports.

It is not a correctness check. A byte-identical fingerprint means "did not
change what I care about", not "is correct" -- several real defects found
during 0.3.x were invisible to it, because they lived in arbitration, in
provenance, or in Slurm options, none of which reach a process id.

Usage, from anywhere::

    python dev/ta1_fingerprint.py <card.yaml> [<card.yaml> ...]

Each card is a MAGNET evaluation card with a ``kwdagger:`` block. Relative
paths inside it are resolved against the card's own repository, so the cards
can be pointed at from another checkout. The cache root is fixed to a literal
so that two runs under different temporary directories still compare equal.

The established pair, from ``aiq-eval-runner``::

    python submodules/kwdagger/dev/ta1_fingerprint.py \\
        ta1/aiq-ta1-incubilate/cards/oc_lift_kwdagger.yaml \\
        ta1/aiq-ta1-incubilate/cards/oc_lomo_kwdagger.yaml

Compare across a change by writing each run to a file and diffing them. Run it
from the repository holding the *card*, not from kwdagger: a previous session
committed to the wrong repository after doing exactly that.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys

#: A literal rather than a real directory. Identity is root-relative, so this
#: only has to be stable, and pinning it keeps two runs comparable.
FIXED_ROOT = '/kwdagger-fingerprint-root'


def _card_repo_root(card_fpath):
    """The repository a card's relative paths are written against."""
    import ubelt as ub

    dpath = ub.Path(card_fpath).absolute().parent
    for candidate in [dpath, *dpath.parents]:
        if (candidate / '.git').exists() or (
            candidate / 'pyproject.toml'
        ).exists():
            return candidate
    return dpath


def compile_card(card_fpath):
    """
    Compile one card's pipeline and return its concrete processes.

    Returns:
        list[dict]: one record per concrete process, sorted, containing only
            what must not move: the template name, the process id, the node
            directory relative to the cache root, and the command.
    """
    import kwutil
    import ubelt as ub

    from kwdagger.schedule import ScheduleEvaluationConfig, build_schedule

    card_fpath = ub.Path(card_fpath).absolute()
    spec = kwutil.Yaml.load(card_fpath)
    block = spec.get('kwdagger')
    if not block:
        raise ValueError(f'{card_fpath} has no kwdagger block')
    # ``terminal_node`` is a MAGNET declaration; the scheduler does not take it.
    params = {k: v for k, v in block.items() if k != 'terminal_node'}

    config = ScheduleEvaluationConfig(
        params=params,
        root_dpath=FIXED_ROOT,
        backend='serial',
        run=False,
    )
    # Card paths are relative to the card's own repository, and the pipeline
    # reference is an importable module in it.
    repo_root = _card_repo_root(card_fpath)
    old_cwd = os.getcwd()
    sys.path.insert(0, os.fspath(repo_root))
    os.chdir(repo_root)
    try:
        # ``build_schedule`` prints graphs and a compilation summary. That is
        # for a human running the scheduler, not part of the fingerprint, so
        # keep it off the stream the fingerprint is written to.
        with contextlib.redirect_stdout(sys.stderr):
            dag, _queue = build_schedule(config)
    finally:
        os.chdir(old_cwd)
        sys.path.remove(os.fspath(repo_root))

    records = []
    for node in dag.nodes.values():
        records.append(
            {
                'name': node.name,
                'process_id': node.process_id,
                'node_dpath': str(node.final_node_dpath).replace(
                    FIXED_ROOT, '{root}'
                ),
                'command': node.final_command().replace(FIXED_ROOT, '{root}'),
            }
        )
    records.sort(key=lambda record: (record['name'], record['process_id']))
    return records


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cards', nargs='+', help='MAGNET evaluation cards')
    args = parser.parse_args(argv)

    fingerprint = {}
    for card in args.cards:
        name = os.path.basename(card)
        records = compile_card(card)
        fingerprint[name] = records
        print(
            f'# {name}: {len(records)} concrete processes',
            file=sys.stderr,
        )
    print(json.dumps(fingerprint, indent=4, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
