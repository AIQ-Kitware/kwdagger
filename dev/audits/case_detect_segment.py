"""
Case: detection + segmentation, both scored against the truth they came from.

    dataset (an input path, supplied by the matrix)
        |
        +--> detect[dataset, model]  --> dets
        |        |  gather by dataset
        |        v
        +--> score_det[dataset]  <-- ALSO reads `dataset` as truth
        |
        +--> segment[dataset, model] --> masks
                 |  gather by dataset
                 v
             score_seg[dataset]  <-- ALSO reads `dataset` as truth
                 |
             (both scores) --> summarize

The interesting part is `dataset` appearing in three roles at once: the
thing a predictor consumes, the grouping key for the gather, and the truth
a scorer measures against. Three plausible wirings for the scorer's copy
of it are compared, because they classify differently.
"""

from __future__ import annotations

import kwdagger
from kwdagger.pipeline import GatherSpec, Pipeline, ProcessNode


def _predictor(name, out_name):
    return ProcessNode(
        name=name,
        executable=f'python {name}.py',
        in_paths={'dataset_fpath'},
        out_paths={out_name: f'{out_name}.json'},
        algo_params={'model': 'baseline', 'thresh': 0.5},
        perf_params={'workers': 4},
    )


def _scorer(name, pred_port, truth_port='truth_fpath'):
    return ProcessNode(
        name=name,
        executable=f'python {name}.py',
        # truth_fpath is the SAME dataset the predictor consumed
        in_paths={truth_port, pred_port},
        out_paths={f'{name}_fpath': f'{name}.json'},
        algo_params={'iou_thresh': 0.5},
    )


def build(truth_wiring='matrix'):
    """
    Args:
        truth_wiring: how the scorer gets the truth path.
            'matrix'  -- declared independently in the matrix (unconnected)
            'alias'   -- aliased from the predictor's input (input -> input)
            'ignored' -- scorer has no truth input at all (control)
    """
    detect = _predictor('detect', 'dets_fpath')
    segment = _predictor('segment', 'masks_fpath')
    truth_port = 'dataset_fpath' if truth_wiring == 'sameport' else 'truth_fpath'
    score_det = _scorer('score_det', 'dets_fpath', truth_port)
    score_seg = _scorer('score_seg', 'masks_fpath', truth_port)
    summarize = ProcessNode(
        name='summarize',
        executable='python summarize.py',
        in_paths={'det_scores_fpath', 'seg_scores_fpath'},
        out_paths={'report_fpath': 'report.json'},
    )

    group_key = 'dataset_fpath'
    nodes = {
        'detect': detect, 'segment': segment,
        'score_det': score_det, 'score_seg': score_seg,
        'summarize': summarize,
    }

    if truth_wiring == 'alias':
        detect.inputs['dataset_fpath'].connect(
            score_det.inputs[truth_port])
        segment.inputs['dataset_fpath'].connect(
            score_seg.inputs[truth_port])

    # predictions fan in per dataset
    detect.outputs['dets_fpath'].connect(
        score_det.inputs['dets_fpath'],
        gather=GatherSpec(group_by=[group_key],
                          order_by=['model']))
    segment.outputs['masks_fpath'].connect(
        score_seg.inputs['masks_fpath'],
        gather=GatherSpec(group_by=[group_key],
                          order_by=['model']))

    # both scorers fan in to one report
    score_det.outputs['score_det_fpath'].connect(
        summarize.inputs['det_scores_fpath'],
        gather=GatherSpec(group_by=[], order_by=[]))
    score_seg.outputs['score_seg_fpath'].connect(
        summarize.inputs['seg_scores_fpath'],
        gather=GatherSpec(group_by=[], order_by=[]))

    dag = Pipeline(nodes)
    dag.build_nx_graphs()
    return dag


DATASETS = ['/data/train.kwcoco.json', '/data/val.kwcoco.json']

def matrix(truth_wiring='matrix', **over):
    m = {
        'detect.dataset_fpath': list(DATASETS),
        'detect.model': ['resnet', 'vit'],
        'detect.thresh': [0.5],
        'detect.workers': [4],
        'segment.dataset_fpath': list(DATASETS),
        'segment.model': ['unet'],
        'segment.workers': [4],
        'score_det.iou_thresh': [0.5],
        'score_seg.iou_thresh': [0.5],
    }
    port = 'dataset_fpath' if truth_wiring == 'sameport' else 'truth_fpath'
    if truth_wiring in {'matrix', 'sameport'}:
        m[f'score_det.{port}'] = list(DATASETS)
        m[f'score_seg.{port}'] = list(DATASETS)
    m.update(over)
    return m
