"""Heatmap-to-detections tutorial pipeline for kwdagger."""

from __future__ import annotations

import json
from typing import Dict

import kwdagger
import scriptconfig as scfg
import ubelt as ub

EXAMPLE_DPATH = ub.Path(__file__).parent
PREDICT_HEATMAP_EXE = "python -m heatmap_example.cli.predict_heatmap"
EXTRACT_BOXES_EXE = "python -m heatmap_example.cli.extract_boxes"


class PredictHeatmap(kwdagger.ProcessNode):
    """Simulate a segmentation model by writing saliency maps."""

    name = "predict_heatmap"
    executable = PREDICT_HEATMAP_EXE

    in_paths = {
        "coco_fpath",
    }
    out_paths = {
        "dst_coco_fpath": "pred_saliency.kwcoco.json",
        "aux_dpath": "saliency",
    }
    primary_out_key = "dst_coco_fpath"

    algo_params = {
        "sigma": 7.0,
        "noise": 0.01,
        "saliency_channel": "saliency",
        "seed": 0,
    }

    def load_result(self, node_dpath):
        output_fpath = node_dpath / self.out_paths[self.primary_out_key]
        if output_fpath.exists():
            return {f"metrics.{self.name}.exists": True}
        return {}


class ExtractBoxes(kwdagger.ProcessNode):
    """Turn the saliency auxiliary channel into box detections."""

    name = "extract_boxes"
    executable = EXTRACT_BOXES_EXE

    in_paths = {
        "coco_fpath",
    }
    out_paths = {
        "dst_coco_fpath": "pred_boxes.kwcoco.json",
    }
    primary_out_key = "dst_coco_fpath"

    algo_params = {
        "threshold": 0.5,
        "min_area": 4,
        "saliency_channel": "saliency",
    }

    def load_result(self, node_dpath):
        output_fpath = node_dpath / self.out_paths[self.primary_out_key]
        if output_fpath.exists():
            return {f"metrics.{self.name}.exists": True}
        return {}


class ScoreHeatmap(kwdagger.ProcessNode):
    """Call the kwcoco segmentation metrics CLI."""

    name = "score_heatmap"
    executable = "python -m kwcoco.metrics.segmentation_metrics"

    in_paths = {
        "true_dataset",
        "pred_dataset",
    }
    out_paths = {
        "eval_dpath": "segmentation_eval",
    }
    primary_out_key = "eval_dpath"

    def load_result(self, node_dpath):
        eval_dpath = node_dpath / self.out_paths[self.primary_out_key]
        metrics = _gather_json_metrics(eval_dpath)
        flat = {f"metrics.{self.name}.{k}": v for k, v in metrics.items()}
        return flat


class ScoreBoxes(kwdagger.ProcessNode):
    """Call kwcoco's detection evaluator CLI."""

    name = "score_boxes"
    executable = "python -m kwcoco evaluate_detections"

    in_paths = {
        "true",
        "pred",
    }
    out_paths = {
        "out": "detection_eval",
    }
    primary_out_key = "out"

    def load_result(self, node_dpath):
        eval_dpath = node_dpath / self.out_paths[self.primary_out_key]
        metrics = _gather_json_metrics(eval_dpath)
        flat = {f"metrics.{self.name}.{k}": v for k, v in metrics.items()}
        return flat


def _gather_json_metrics(eval_dpath: ub.Path) -> Dict[str, float]:
    metrics: Dict[str, float] = {}
    if eval_dpath is None or not eval_dpath.exists():
        return metrics
    for json_fpath in eval_dpath.rglob("*.json"):
        try:
            data = json.loads(json_fpath.read_text())
        except Exception:
            continue
        key_prefix = json_fpath.relative_to(eval_dpath).as_posix().replace("/", ".").removesuffix(".json")
        if isinstance(data, dict):
            for k, v in data.items():
                metrics[f"{key_prefix}.{k}"] = v
    return metrics


def heatmap_detection_pipeline():
    nodes = {
        "predict_heatmap": PredictHeatmap(),
        "extract_boxes": ExtractBoxes(),
        "score_heatmap": ScoreHeatmap(),
        "score_boxes": ScoreBoxes(),
    }

    nodes["predict_heatmap"].outputs["dst_coco_fpath"].connect(nodes["extract_boxes"].inputs["coco_fpath"])
    nodes["predict_heatmap"].outputs["dst_coco_fpath"].connect(nodes["score_heatmap"].inputs["pred_dataset"])
    nodes["predict_heatmap"].inputs["coco_fpath"].connect(nodes["score_heatmap"].inputs["true_dataset"])

    nodes["extract_boxes"].outputs["dst_coco_fpath"].connect(nodes["score_boxes"].inputs["pred"])
    nodes["predict_heatmap"].inputs["coco_fpath"].connect(nodes["score_boxes"].inputs["true"])

    dag = kwdagger.Pipeline(nodes)
    dag.build_nx_graphs()
    return dag


class HeatmapDetectionPipelineConfig(scfg.DataConfig):
    """Configuration helper for scheduling the tutorial pipeline."""

    coco_fpath = scfg.Value(None, help="Path to ground-truth kwcoco dataset")
    workdir = scfg.Value("./heatmap_tutorial", help="Experiment directory")

    saliency_coco_name = scfg.Value("pred_saliency.kwcoco.json", help="Filename for saliency kwcoco")
    boxes_coco_name = scfg.Value("pred_boxes.kwcoco.json", help="Filename for detection kwcoco")
    saliency_dirname = scfg.Value("saliency", help="Folder for saliency rasters")
    heatmap_eval_dirname = scfg.Value("segmentation_eval", help="Folder for segmentation metrics")
    det_eval_dirname = scfg.Value("detection_eval", help="Folder for detection metrics")

    sigma = scfg.Value(7.0, help="Gaussian sigma for smoothing")
    noise = scfg.Value(0.01, help="Noise level added to heatmaps")
    threshold = scfg.Value(0.5, help="Threshold for box extraction")
    min_area = scfg.Value(4, help="Minimum blob size for detections")
    saliency_channel = scfg.Value("saliency", help="Channel name used in auxiliary assets")

    def make_params(self) -> Dict:
        workdir = ub.Path(self.workdir)
        saliency_coco = workdir / self.saliency_coco_name
        boxes_coco = workdir / self.boxes_coco_name
        params = {
            "pipeline": "heatmap_example.pipelines.heatmap_detection_pipeline()",
            "matrix": {
                "predict_heatmap.coco_fpath": [self.coco_fpath],
                "predict_heatmap.dst_coco_fpath": [saliency_coco],
                "predict_heatmap.aux_dpath": [workdir / self.saliency_dirname],
                "predict_heatmap.sigma": [self.sigma],
                "predict_heatmap.noise": [self.noise],
                "predict_heatmap.saliency_channel": [self.saliency_channel],
                "extract_boxes.coco_fpath": [saliency_coco],
                "extract_boxes.dst_coco_fpath": [boxes_coco],
                "extract_boxes.threshold": [self.threshold],
                "extract_boxes.min_area": [self.min_area],
                "extract_boxes.saliency_channel": [self.saliency_channel],
                "score_heatmap.true_dataset": [self.coco_fpath],
                "score_heatmap.pred_dataset": [saliency_coco],
                "score_heatmap.eval_dpath": [workdir / self.heatmap_eval_dirname],
                "score_boxes.true": [self.coco_fpath],
                "score_boxes.pred": [boxes_coco],
                "score_boxes.out": [workdir / self.det_eval_dirname],
            },
        }
        return params
