"""
Equivalence tests for the per-tutorial ``pipeline.yaml`` files.

Each tutorial ships both a Python pipeline and a declarative ``pipeline.yaml``.
These tests assert the two are equivalent: same graph, same node IDs, same
resolved command arguments, and same metric metadata.

Several tutorials use a package literally named ``example_user_module``, so they
cannot all be imported into one interpreter. Each comparison therefore runs in a
subprocess with the tutorial directory on ``PYTHONPATH``.
"""

import json
import subprocess
import sys

import pytest
import ubelt as ub

TUTORIALS = ub.Path(
    __file__
).parent.parent / 'docs/source/manual/tutorials'

# (tutorial dir, module, builder func, yaml file, representative matrix row)
CASES = [
    (
        'scriptconfig_pipeline',
        'example_user_module.pipelines',
        'my_sentiment_pipeline',
        'pipeline.yaml',
        {
            'keyword_sentiment_predict.src_fpath': 'data/toy_reviews_movies.jsonl',
            'keyword_sentiment_predict.keyword': 'love',
        },
    ),
    (
        'slurm_options',
        'example_user_module.pipelines',
        'build_pipeline',
        'pipeline.yaml',
        {
            'cpu_prepare.src_fpath': 'data/input.json',
            'torch_infer.device': 'cpu',
        },
    ),
    (
        'ollama_benchmark',
        'pipelines',
        'ollama_benchmark_pipeline',
        'pipeline.yaml',
        {
            'ollama_benchmark.prompt_fpath': 'prompts_5.yaml',
            'ollama_benchmark.model': 'qwen2:7b',
        },
    ),
    (
        'heatmap_detection',
        'heatmap_example.pipelines',
        'heatmap_detection_pipeline',
        'pipeline.yaml',
        {
            'predict_heatmap.coco_fpath': 'demo.kwcoco.json',
            'predict_heatmap.sigma': 1.0,
            'extract_boxes.threshold': 0.25,
            'score_boxes.iou_thresh': 0.5,
        },
    ),
]

# Runs inside the subprocess; compares the Python and YAML pipelines.
CHECK_SCRIPT = r'''
import importlib, json, sys
import ubelt as ub
import kwdagger

tut_dpath, module, func, yaml_name, row_json = sys.argv[1:6]
row = json.loads(row_json)

py_dag = getattr(importlib.import_module(module), func)()
yaml_dag = kwdagger.load_yaml_pipeline(ub.Path(tut_dpath) / yaml_name)

# 1. Same graph structure.
assert sorted(py_dag.node_dict) == sorted(yaml_dag.node_dict), 'node set differs'
assert sorted(py_dag.proc_graph.edges()) == sorted(yaml_dag.proc_graph.edges()), 'proc edges differ'
assert sorted(py_dag.io_graph.edges()) == sorted(yaml_dag.io_graph.edges()), 'io edges differ'

root = ub.Path.appdir('kwdagger/unit_tests/tutorial_equiv', module).ensuredir()

def arg_lines(node):
    return sorted(ln.strip() for ln in node.final_command().splitlines()
                  if ln.strip().startswith('--'))

def norm_metrics(node):
    fn = getattr(node, 'default_metrics', None)
    if fn is None:
        return None
    try:
        infos = fn()
    except AttributeError:
        return None
    return sorted((i.get('metric', i.get('suffix')), i.get('objective', 'maximize'),
                   bool(i.get('primary', False)), bool(i.get('display', False)))
                  for i in infos)

def vantage(node):
    try:
        vps = node.default_vantage_points
    except AttributeError:
        vps = []
    return [dict(v) for v in (vps or [])]

py_dag.configure(config=row, root_dpath=root, cache=False)
yaml_dag.configure(config=row, root_dpath=root, cache=False)

for name in py_dag.node_dict:
    pn, yn = py_dag.node_dict[name], yaml_dag.node_dict[name]
    assert pn.algo_id == yn.algo_id, f'{name}: algo_id {pn.algo_id} != {yn.algo_id}'
    assert pn.process_id == yn.process_id, f'{name}: process_id differs'
    assert arg_lines(pn) == arg_lines(yn), f'{name}: command args differ\n{arg_lines(pn)}\n{arg_lines(yn)}'
    assert norm_metrics(pn) == norm_metrics(yn), f'{name}: default_metrics differ'
    assert vantage(pn) == vantage(yn), f'{name}: vantage_points differ'

# Round-trip: serialize the Python pipeline to a spec and reload it. Because the
# serialized spec keeps each node's exact executable (and class references for
# custom subclasses), this reproduces the FULL command, not just the args.
rt_dag = kwdagger.load_yaml_pipeline(
    getattr(importlib.import_module(module), func)().to_yaml_spec())
assert sorted(rt_dag.proc_graph.edges()) == sorted(py_dag.proc_graph.edges()), 'round-trip proc edges differ'
rt_dag.configure(config=row, root_dpath=root, cache=False)
for name in py_dag.node_dict:
    pn, rn = py_dag.node_dict[name], rt_dag.node_dict[name]
    assert type(pn).__name__ == type(rn).__name__, f'{name}: round-trip class differs'
    assert pn.process_id == rn.process_id, f'{name}: round-trip process_id differs'
    assert pn.final_command() == rn.final_command(), f'{name}: round-trip command differs'
    assert norm_metrics(pn) == norm_metrics(rn), f'{name}: round-trip metrics differ'
    assert vantage(pn) == vantage(rn), f'{name}: round-trip vantage differs'

print('EQUIV_OK')
'''


@pytest.mark.parametrize('tut,module,func,yaml_name,row', CASES,
                         ids=[c[0] for c in CASES])
def test_tutorial_yaml_matches_python(tut, module, func, yaml_name, row):
    tut_dpath = TUTORIALS / tut
    assert (tut_dpath / yaml_name).exists(), f'missing {tut_dpath / yaml_name}'

    repo = ub.Path(__file__).parent.parent
    env = ub.dict_union(dict(__import__('os').environ),
                        {'PYTHONPATH': f'{tut_dpath}:{repo}'})
    proc = subprocess.run(
        [sys.executable, '-c', CHECK_SCRIPT, str(tut_dpath), module, func,
         yaml_name, json.dumps(row)],
        capture_output=True, text=True, env=env,
    )
    if proc.returncode != 0 or 'EQUIV_OK' not in proc.stdout:
        raise AssertionError(
            f'{tut} equivalence failed\n'
            f'--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}'
        )


if __name__ == '__main__':
    import xdoctest
    xdoctest.doctest_module(__file__)
