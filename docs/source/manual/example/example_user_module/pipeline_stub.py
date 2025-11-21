"""
This file is just a stub for a concise overview in slides
"""

import kwdagger


class Stage1_Predict(kwdagger.ProcessNode):
    executable = 'python stage1_predict.py'

    in_paths = {
        'src_fpath',
    }
    out_paths = {
        'dst_fpath': 'stage1_prediction.json',
        'dst_dpath': '.',
    }
    primary_out_key = 'dst_fpath'

    algo_params = {
        'param1': 1,
    }
    perf_params = {
        'workers': 0,
    }


class Stage1_Evaluate(kwdagger.ProcessNode):
    executable = 'python stage1_evaluate.py'

    in_paths = {
        'true_fpath',
        'pred_fpath',
    }
    out_paths = {
        'out_fpath': 'stage1_evaluation.json',
    }

    def load_result(self, node_dpath) -> dict:
        """
        Return results as items in a dictionary in the form:

            ``"metrics.<node_name>.<metric>": <value>``.
        """
        ...


def my_demo_pipeline():
    nodes = {
        'stage1_predict': Stage1_Predict(),
        'stage1_evaluate': Stage1_Evaluate(),
    }
    nodes['stage1_predict'].outputs['dst_fpath'].connect(
        nodes['stage1_evaluate'].inputs['pred_fpath'])

    nodes['stage1_predict'].inputs['src_fpath'].connect(
        nodes['stage1_evaluate'].inputs['true_fpath'])

    return kwdagger.Pipeline(nodes)
