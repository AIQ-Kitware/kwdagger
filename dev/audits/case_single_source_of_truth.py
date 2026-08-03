"""
Case: the dataset list is declared exactly once.

Every other node that needs that path has its input port *connected* to
the one that carries it. Nothing is restated in the matrix.

    detect.dataset_fpath          <- the only place DATASETS appears
        |-> segment.dataset_fpath      (input -> input)
        |-> score_det.truth_fpath      (input -> input)
        `-> score_seg.truth_fpath      (input -> input)

The predictors still fan out over their own models; the scorers still
gather per dataset. The dataset axis is declared once and flows.
"""

from __future__ import annotations

from kwdagger.pipeline import GatherSpec, Pipeline, ProcessNode

DATASETS = ['/data/train.kwcoco.json', '/data/val.kwcoco.json']


def build():
    detect = ProcessNode(
        name='detect',
        executable='python detect.py',
        in_paths={'dataset_fpath'},
        out_paths={'dets_fpath': 'dets.json'},
        algo_params={'model': 'baseline', 'thresh': 0.5},
        perf_params={'workers': 4},
    )
    segment = ProcessNode(
        name='segment',
        executable='python segment.py',
        in_paths={'dataset_fpath'},
        out_paths={'masks_fpath': 'masks.json'},
        algo_params={'model': 'unet'},
        perf_params={'workers': 4},
    )
    score_det = ProcessNode(
        name='score_det',
        executable='python score_det.py',
        in_paths={'truth_fpath', 'dets_fpath'},
        out_paths={'score_det_fpath': 'score_det.json'},
        algo_params={'iou_thresh': 0.5},
    )
    score_seg = ProcessNode(
        name='score_seg',
        executable='python score_seg.py',
        in_paths={'truth_fpath', 'masks_fpath'},
        out_paths={'score_seg_fpath': 'score_seg.json'},
        algo_params={'iou_thresh': 0.5},
    )
    summarize = ProcessNode(
        name='summarize',
        executable='python summarize.py',
        in_paths={'det_scores_fpath', 'seg_scores_fpath'},
        out_paths={'report_fpath': 'report.json'},
    )

    # One source of truth for the dataset path; everyone else is wired to it.
    source = detect.inputs['dataset_fpath']
    source.connect(segment.inputs['dataset_fpath'])
    source.connect(score_det.inputs['truth_fpath'])
    source.connect(score_seg.inputs['truth_fpath'])

    detect.outputs['dets_fpath'].connect(
        score_det.inputs['dets_fpath'],
        gather=GatherSpec(
            group_by=[{'src': 'dataset_fpath', 'dst': 'truth_fpath'}],
            order_by=['model'],
        ),
    )
    segment.outputs['masks_fpath'].connect(
        score_seg.inputs['masks_fpath'],
        gather=GatherSpec(
            group_by=[{'src': 'dataset_fpath', 'dst': 'truth_fpath'}],
            order_by=['model'],
        ),
    )
    score_det.outputs['score_det_fpath'].connect(
        summarize.inputs['det_scores_fpath'], gather=GatherSpec(group_by=[])
    )
    score_seg.outputs['score_seg_fpath'].connect(
        summarize.inputs['seg_scores_fpath'], gather=GatherSpec(group_by=[])
    )

    dag = Pipeline(
        {
            'detect': detect,
            'segment': segment,
            'score_det': score_det,
            'score_seg': score_seg,
            'summarize': summarize,
        }
    )
    dag.build_nx_graphs()
    return dag


#: Note what is *absent*: no segment.dataset_fpath, no score_*.truth_fpath.
MATRIX = {
    'detect.dataset_fpath': list(DATASETS),
    'detect.model': ['resnet', 'vit'],
    'detect.thresh': [0.5],
    'detect.workers': [4],
    'segment.model': ['unet'],
    'segment.workers': [4],
    'score_det.iou_thresh': [0.5],
    'score_seg.iou_thresh': [0.5],
}
