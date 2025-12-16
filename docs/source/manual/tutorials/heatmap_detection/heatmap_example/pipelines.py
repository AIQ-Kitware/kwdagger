"""Heatmap-to-detections tutorial pipeline for kwdagger."""
from __future__ import annotations
import json
import kwdagger

from kwdagger.utils import util_dotdict


class PredictHeatmap(kwdagger.ProcessNode):
    """Simulate a segmentation model by writing saliency maps."""

    name = "predict_heatmap"
    executable = "python -m heatmap_example.cli.predict_heatmap"

    # Matches PredictHeatmapConfig.coco_fpath
    in_paths = {
        "coco_fpath",
    }

    # Matches PredictHeatmapConfig.dst_coco_fpath / asset_dpath
    out_paths = {
        "dst_coco_fpath": "heatmap.kwcoco.json",
        "asset_dpath": "assets/heatmaps",
    }
    primary_out_key = "dst_coco_fpath"

    # Scalar algorithm parameters for the CLI
    # Matches PredictHeatmapConfig: sigma, thresh, heatmap_channel
    algo_params = {
        "sigma": 7.0,
        "thresh": 0.0,
        "heatmap_channel": "salient",
    }


class ExtractBoxes(kwdagger.ProcessNode):
    """Turn the saliency auxiliary channel into box detections."""

    name = "extract_boxes"
    executable = "python -m heatmap_example.cli.extract_boxes"

    # Matches ExtractBoxesConfig.coco_fpath
    in_paths = {
        "coco_fpath",
    }
    # Matches ExtractBoxesConfig.dst_coco_fpath
    out_paths = {
        "dst_coco_fpath": "pred_boxes.kwcoco.json",
    }
    primary_out_key = "dst_coco_fpath"

    # Scalar algorithm parameters for the CLI
    # Matches ExtractBoxesConfig: threshold, min_area, heatmap_channel
    algo_params = {
        "threshold": 0.5,
        "min_area": 4,
        "heatmap_channel": "salient",
    }


class ScoreHeatmap(kwdagger.ProcessNode):
    """Call the kwcoco segmentation metrics CLI."""

    name = "score_heatmap"
    executable = "python -m kwcoco.metrics.segmentation_metrics"

    in_paths = {
        "true_dataset",
        "pred_dataset",
    }
    out_paths = {
        "eval_dpath": "heatmap_eval",
        "eval_fpath": "heatmap_metrics.json",
    }
    primary_out_key = "eval_fpath"

    algo_params = {
        'salient_channel': 'salient',
    }

    # Performance parameters can be changed without impacting results, thus
    # they do not contribute to the hash used in the output directory.
    perf_params = {
        'workers': 'auto',
    }

    def load_result(self, node_dpath):
        """
        Given the path to the output, we need to specify the logic to return
        summary measurements in a way kwdagger aggregate can handle.
        """
        eval_fpath = node_dpath / self.out_paths[self.primary_out_key]
        # Load raw json data
        data = json.loads(eval_fpath.read_text())

        # Grab the info written by process context
        # This is optional, but useful.
        from kwdagger.aggregate_loader import new_process_context_parser
        info = data['meta']['info'][-1]
        nested = {}
        nested_info = new_process_context_parser(info)
        # The important part is to put scalars in the "metrics" item

        # Binary classless foreground/background segmentation measures
        measures = data['nocls_measures']
        metrics = {}
        metrics['ap'] = measures['ap']
        metrics['auc'] = measures['auc']

        nested.update(nested_info)
        nested['metrics'] = metrics

        # The return structure is a flat dictionary.
        flat = util_dotdict.DotDict.from_nested(nested)
        flat = flat.insert_prefix(self.name, index=1)
        return flat

    def default_metrics(self):
        """
        Returns:
            List[Dict]: containing information on how to interpret and
            prioritize the metrics returned here.
        """
        metric_infos = [
            {
                'metric': 'ap',
                'objective': 'maximize',
                'primary': True,
            },
            {
                'metric': 'auc',
                'objective': 'maximize',
                'primary': True,
            },
        ]
        return metric_infos


class ScoreBoxes(kwdagger.ProcessNode):
    """Call kwcoco's detection evaluator CLI."""

    name = "score_boxes"
    executable = "python -m kwcoco evaluate_detections"

    in_paths = {
        "true_dataset",
        "pred_dataset",
    }
    out_paths = {
        "out_dpath": "detection_eval",
        "out_fpath": "box_metrics.json",
    }
    primary_out_key = "out_fpath"

    algo_params = {
        'compat': 'all',
        'iou_thresh': 0.1,
    }

    def load_result(self, node_dpath):
        output_fpath = node_dpath / self.out_paths[self.primary_out_key]

        import kwutil
        from kwcoco.coco_evaluator import CocoResults
        state = kwutil.Json.load(output_fpath)
        coco_result = CocoResults.from_json(state)
        assert len(coco_result) == 1, 'only expecting one area / iou result'
        single_result = list(coco_result.values())[0]

        metrics = {}

        # Use binary metrics AP and AUC are the main metrics
        metrics['ap'] = single_result.nocls_measures['ap']
        metrics['auc'] = single_result.nocls_measures['auc']

        # Also include peak F1 measures inside this curve
        submeasures = single_result.nocls_measures['max_f1_submeasures']
        metrics['precision'] = submeasures['ppv']
        metrics['recall'] = submeasures['tpr']
        metrics['f1'] = submeasures['f1']
        metrics['thresh'] = submeasures['thresh']
        # metrics['tp_count'] = submeasures['tp_count']
        # metrics['fp_count'] = submeasures['fp_count']
        # metrics['fn_count'] = submeasures['fn_count']

        nested = {}
        nested['metrics'] = metrics

        # The return structure is a flat dictionary.
        flat = util_dotdict.DotDict.from_nested(nested)
        flat = flat.insert_prefix(self.name, index=1)
        print(f'flat={flat}')
        return flat

    def default_metrics(self):
        """
        Returns:
            List[Dict]: containing information on how to interpret and
            prioritize the metrics returned here.
        """
        metric_infos = [
            {
                'metric': 'ap',
                'objective': 'maximize',
                'primary': True,
            },
            {
                'metric': 'auc',
                'objective': 'maximize',
                'primary': True,
            },
            {
                'metric': 'f1',
                'objective': 'maximize',
                'primary': False,
                'display': True,
            },
            {
                'metric': 'precision',
                'objective': 'maximize',
                'primary': False,
                'display': True,
            },
            {
                'metric': 'recall',
                'objective': 'maximize',
                'primary': False,
                'display': True,
            },
            {
                'metric': 'thresh',
                'objective': 'maximize',
                'primary': False,
                'display': True,
            },
        ]
        return metric_infos


def heatmap_detection_pipeline():
    nodes = {
        "predict_heatmap": PredictHeatmap(),
        "extract_boxes": ExtractBoxes(),
        "score_heatmap": ScoreHeatmap(),
        "score_boxes": ScoreBoxes(),
    }

    # Predict -> extract boxes
    nodes["predict_heatmap"].outputs["dst_coco_fpath"].connect(
        nodes["extract_boxes"].inputs["coco_fpath"]
    )

    # Predict -> score heatmap
    nodes["predict_heatmap"].outputs["dst_coco_fpath"].connect(
        nodes["score_heatmap"].inputs["pred_dataset"]
    )
    nodes["predict_heatmap"].inputs["coco_fpath"].connect(
        nodes["score_heatmap"].inputs["true_dataset"]
    )

    # Extract boxes -> score boxes
    nodes["extract_boxes"].outputs["dst_coco_fpath"].connect(
        nodes["score_boxes"].inputs["pred_dataset"]
    )
    nodes["predict_heatmap"].inputs["coco_fpath"].connect(
        nodes["score_boxes"].inputs["true_dataset"]
    )

    dag = kwdagger.Pipeline(nodes)
    dag.build_nx_graphs()
    return dag
