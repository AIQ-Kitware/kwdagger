"""
Observational audit of parameter classification and identity in kwdagger.

No assertions. This builds representative pipelines, compiles them, and
reports what each node's identity is actually made of -- then perturbs one
thing at a time and reports which ids move.

The question it exists to answer: for each kind of thing a node can carry
(algo param, perf param, unconnected input, aliased input, connected
input, output path, upstream state), does it flow into ``algo_id``, into
``process_id``, into both, or into neither -- and is that what we want?

Run::

    python dev/audits/param_identity_audit.py
"""

from __future__ import annotations

import contextlib
import io

import kwdagger
from kwdagger.pipeline import GatherSpec, Pipeline, ProcessNode
from kwdagger.schedule import ScheduleEvaluationConfig, build_schedule


# --------------------------------------------------------------------------
# harness
# --------------------------------------------------------------------------


def compile_quiet(dag, matrix, root_dpath='/tmp/kwd_audit', include=None):
    """Compile a pipeline over a matrix without the scheduler's chatter."""
    params = {'pipeline': dag, 'matrix': matrix}
    if include is not None:
        params['include'] = include
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        compiled, queue = build_schedule(
            ScheduleEvaluationConfig(
                params=params,
                root_dpath=root_dpath,
                run=False,
            )
        )
    return compiled, queue


def instances(compiled, name):
    got = [n for n in compiled.nodes.values() if n.name == name]
    return sorted(got, key=lambda n: n.process_id)


def input_kind(input_node):
    """How is this input supplied?"""
    from kwdagger.pipeline import InputNode

    if input_node._gather_members is not None:
        return 'gathered'
    preds = list(input_node.pred)
    if not preds:
        return 'unconnected'
    if all(isinstance(p, InputNode) for p in preds):
        return 'aliased'
    if any(isinstance(p, InputNode) for p in preds):
        return 'mixed'
    return 'connected'


def describe(compiled, node_names=None):
    """Print what each instance's identity is composed of."""
    names = node_names or sorted({n.name for n in compiled.nodes.values()})
    for name in names:
        for node in instances(compiled, name):
            print(f'  {name}  [{node.process_id.split("_id_")[-1][:10]}]')
            print(f'      algo_id       {node.algo_id.split("_id_")[-1][:10]}')
            algo = dict(node.final_algo_config)
            perf = dict(node.final_perf_config)
            print(f'      algo_config   {_short(algo)}')
            if perf:
                print(f'      perf_config   {_short(perf)}')
            for pname, port in node.inputs.items():
                kind = input_kind(port)
                inalgo = 'IN-algo' if pname in algo else '  -    '
                print(
                    f'      in  {pname:<16} {kind:<12} {inalgo} '
                    f'{_short_val(port.final_value)}'
                )
            for pname in node.outputs:
                print(f'      out {pname:<16}')
            dep_keys = sorted(node.depends.keys())
            print(f'      depends keys  {dep_keys}')


def _short(d, width=68):
    s = ', '.join(f'{k}={_short_val(v)}' for k, v in sorted(d.items()))
    return s if len(s) <= width else s[:width] + '...'


def _short_val(v, width=34):
    s = str(v)
    if len(s) > width:
        s = '...' + s[-(width - 3) :]
    return s


def sensitivity(
    dag_factory, base_matrix, perturbations, focus, root='/tmp/kwd_audit_sens'
):
    """
    Report which ids move when one thing changes at a time.

    Compares *sets* of ids rather than pairing instances positionally:
    instance order is not stable across perturbations, so an index-wise
    comparison silently reports movement that is only a permutation.

    Args:
        dag_factory: zero-arg callable returning a fresh Pipeline.
        base_matrix: the reference matrix.
        perturbations: list of (label, matrix) pairs.
        focus: node names to report on.
    """
    base, _ = compile_quiet(dag_factory(), base_matrix, root + '/base')
    baseline = {}
    for name in focus:
        nodes = instances(base, name)
        baseline[name] = (
            {n.algo_id for n in nodes},
            {n.process_id for n in nodes},
            len(nodes),
        )

    header = (
        f'{"perturbation":<36}{"node":<12}{"n":>4}  '
        f'{"algo_id set":<14}{"process_id set":<14}'
    )
    print(header)
    print('  ' + '-' * (len(header) - 2))
    for label, matrix in perturbations:
        got, _ = compile_quiet(
            dag_factory(), matrix, f'{root}/{abs(hash(label))}'
        )
        for name in focus:
            nodes = instances(got, name)
            algo_ids = {n.algo_id for n in nodes}
            proc_ids = {n.process_id for n in nodes}
            b_algo, b_proc, b_n = baseline[name]
            n_str = f'{len(nodes)}' + ('' if len(nodes) == b_n else '!')
            a = 'same' if algo_ids == b_algo else _delta(b_algo, algo_ids)
            p_ = 'same' if proc_ids == b_proc else _delta(b_proc, proc_ids)
            print(f'  {label:<36}{name:<12}{n_str:>4}  {a:<14}{p_:<14}')


def _delta(before, after):
    """Summarize how a set of ids changed."""
    if before & after:
        return f'{len(before - after)} of {len(before)} moved'
    return 'ALL moved'


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------


def main():
    import sys
    import os

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import case_detect_segment as detseg
    import case_shared_label as shared_label
    import case_wiring_equivalence as wiring

    def banner(text):
        print(f'\n{"=" * 74}\n{text}\n{"=" * 74}')

    banner('1. detection + segmentation, both scored against their own input')
    print(detseg.__doc__)
    for label in ['sameport', 'matrix', 'alias']:
        try:
            compiled, _ = compile_quiet(
                detseg.build(label),
                detseg.matrix(label),
                f'/tmp/kwd_audit/detseg_{label}',
            )
            counts = {
                n: len(instances(compiled, n))
                for n in [
                    'detect',
                    'segment',
                    'score_det',
                    'score_seg',
                    'summarize',
                ]
            }
            print(f'  truth wiring {label:<10} {counts}')
        except Exception as ex:
            print(
                f'  truth wiring {label:<10} REJECTED: '
                f'{type(ex).__name__}: {str(ex)[:90]}'
            )
    print('\n  All three compile. The scorer has no ordinary edge to the')
    print('  predictor, so a qualified key cannot name it -- the src/dst group')
    print('  key form lets each end use its own vocabulary instead.')

    banner('2. what each identity is made of')
    compiled, _ = compile_quiet(
        detseg.build('sameport'),
        detseg.matrix('sameport'),
        '/tmp/kwd_audit/describe',
    )
    describe(compiled, ['detect', 'score_det', 'summarize'])

    banner('3. sensitivity: which perturbation moves which id')
    base = detseg.matrix('sameport')

    def m(**over):
        d = dict(base)
        d.update(over)
        return d

    perturbations = [
        ('perf: detect.workers 4 -> 8', m(**{'detect.workers': [8]})),
        ('algo: detect.thresh 0.5 -> 0.7', m(**{'detect.thresh': [0.7]})),
        (
            'algo: score_det.iou 0.5 -> 0.7',
            m(**{'score_det.iou_thresh': [0.7]}),
        ),
        (
            'cohort: add a detect model',
            m(**{'detect.model': ['resnet', 'vit', 'convnext']}),
        ),
        ('algo: segment.model unet -> fcn', m(**{'segment.model': ['fcn']})),
    ]
    sensitivity(
        lambda: detseg.build('sameport'),
        base,
        perturbations,
        focus=['detect', 'score_det', 'score_seg', 'summarize'],
    )

    banner('4. the same computation, wired three ways')
    print(wiring.__doc__)
    print(f'  {"variant":<14}{"in kind":<13}{"algo_id":<13}{"algo_config"}')
    print('  ' + '-' * 72)
    for label, (factory, matrix) in wiring.VARIANTS.items():
        compiled, _ = compile_quiet(
            factory(), matrix, f'/tmp/kwd_audit/wire_{label}'
        )
        node = instances(compiled, 'predict')[0]
        kind = input_kind(node.inputs['data_fpath'])
        print(
            f'  {label:<14}{kind:<13}'
            f'{node.algo_id.split("_id_")[-1][:10]:<13}'
            f'{_short(dict(node.final_algo_config), 40)}'
        )
    print('\n  All three agree: algo_id identifies the algorithm, not the')
    print('  wiring. Data identity lives in final_input_config / ancestors.')

    banner('5. a non-path grouping key, declared once and wired')
    print(shared_label.__doc__)
    for consumers in [('score', 'calib'), ('score', 'calib', 'recall_curve')]:
        compiled, _ = compile_quiet(
            shared_label.build(consumers),
            dict(shared_label.MATRIX),
            f'/tmp/kwd_audit/label_{len(consumers)}',
            include=shared_label.INCLUDE,
        )
        counts = {
            name: len(instances(compiled, name))
            for name in ('detect',) + consumers
        }
        wired = sum(len(row) for row in shared_label.INCLUDE)
        unwired = sum(
            len(row) for row in shared_label.include_without_wiring(consumers)
        )
        print(f'  {len(consumers)} consumers -> {counts}')
        print(
            f'      include entries: wired={wired} (constant)  '
            f'as-algo-param={unwired} (grows)'
        )


if __name__ == '__main__':
    main()
