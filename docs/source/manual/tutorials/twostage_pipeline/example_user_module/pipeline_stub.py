"""
This file is just a stub for a concise overview in slides.
It mirrors :mod:`example_user_module.pipelines` without the helper methods.
"""

import kwdagger


class KeywordSentimentPredict(kwdagger.ProcessNode):
    """Define the prediction node (what to run and how data flows in/out)."""

    executable = 'python keyword_sentiment_predict.py'

    in_paths = {
        'src_fpath',
    }
    out_paths = {
        'dst_fpath': 'keyword_predictions.json',
        'dst_dpath': '.',
    }
    primary_out_key = 'dst_fpath'

    algo_params = {
        'keyword': 'great',
        'case_sensitive': False,
    }
    perf_params = {
        'workers': 0,
    }


class SentimentEvaluate(kwdagger.ProcessNode):
    """Define the evaluation node and what artifacts it writes."""

    executable = 'python sentiment_evaluate.py'

    in_paths = {
        'true_fpath',
        'pred_fpath',
    }
    out_paths = {
        'out_fpath': 'sentiment_metrics.json',
    }
    primary_out_key = 'out_fpath'

    def load_result(self, node_dpath) -> dict:
        """
        Return results as items in a dictionary in the form:

            ``"metrics.<node_name>.<metric>": <value>``.
        """
        ...


def my_sentiment_pipeline():
    """Connect the prediction and evaluation nodes into a pipeline."""
    nodes = {
        'keyword_sentiment_predict': KeywordSentimentPredict(),
        'sentiment_evaluate': SentimentEvaluate(),
    }
    nodes['keyword_sentiment_predict'].outputs['dst_fpath'].connect(
        nodes['sentiment_evaluate'].inputs['pred_fpath']
    )

    nodes['keyword_sentiment_predict'].inputs['src_fpath'].connect(
        nodes['sentiment_evaluate'].inputs['true_fpath']
    )

    return kwdagger.Pipeline(nodes)
