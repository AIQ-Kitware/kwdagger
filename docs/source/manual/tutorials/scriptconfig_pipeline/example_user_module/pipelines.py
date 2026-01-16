"""
Two-stage demo pipeline using scriptconfig schemas for node definitions.

The first stage performs a tiny keyword-based "model" and the second stage
evaluates it. Each node derives its IO/param groups from a scriptconfig schema.
"""

import kwdagger
import ubelt as ub

from example_user_module.cli.keyword_sentiment_predict import (
    KeywordSentimentPredictCLI,
)
from example_user_module.cli.sentiment_evaluate import SentimentEvaluateCLI

# Normally we want to invoke installed Python modules so we can abstract away
# hard coded paths, but for this example we will avoid that for simplicity.
try:
    EXAMPLE_DPATH = ub.Path(__file__).parent
except NameError:
    # for developer convenience
    EXAMPLE_DPATH = ub.Path(
        '~/code/kwdagger/docs/source/manual/tutorials/twostage_pipeline/example_user_module'
    ).expanduser()


class KeywordSentimentPredict(kwdagger.ProcessNode):
    """Run the lightweight keyword-based classifier."""

    name = 'keyword_sentiment_predict'
    executable = f'python {EXAMPLE_DPATH}/cli/keyword_sentiment_predict.py'
    params = KeywordSentimentPredictCLI

    def load_result(self, node_dpath):
        import json
        from kwdagger.aggregate_loader import new_process_context_parser
        from kwdagger.utils import util_dotdict

        output_fpath = node_dpath / self.out_paths[self.primary_out_key]
        result = json.loads(output_fpath.read_text())
        proc_item = result['info'][-1]
        nest_resolved = new_process_context_parser(proc_item)
        # TODO: it would be useful if the aggregator could be given some stats
        # about non-evaluation runs, but currently this does not work because
        # it does not conform to the output needed by load_results.
        # nest_resolved['result'] = {
        #     'keyword': result['result']['keyword'],
        #     'case_sensitive': result['result']['case_sensitive'],
        #     'num_predictions': len(result['result']['predictions']),
        # }
        flat_resolved = util_dotdict.DotDict.from_nested(nest_resolved)
        flat_resolved = flat_resolved.insert_prefix(self.name, index=1)
        return flat_resolved


class SentimentEvaluate(kwdagger.ProcessNode):
    """Score predictions against labels and expose metrics for aggregation."""

    name = 'sentiment_evaluate'
    executable = f'python {EXAMPLE_DPATH}/cli/sentiment_evaluate.py'
    params = SentimentEvaluateCLI

    def load_result(self, node_dpath):
        """
        Return metrics and configuration in a flattened dictionary.

        The returned dictionary should have a key structure that at the very
        least has keys that look like:
            "metrics.{node_name}.{metric_name}"

        Other keys like:

            "context.{node_name}.{key_name}"
            "resolved_params.{node_name}.{key_name}"
            "resources.{node_name}.{key_name}"
            "machine.{node_name}.{key_name}"

        Can be filled in by using the ``new_process_context_parser`` helper and
        kwutil.ProcessContext conventions shown in the CLI examples.
        """
        import json
        from kwdagger.aggregate_loader import new_process_context_parser
        from kwdagger.utils import util_dotdict

        output_fpath = node_dpath / self.out_paths[self.primary_out_key]
        result = json.loads(output_fpath.read_text())
        proc_item = result['info'][-1]
        nest_resolved = new_process_context_parser(proc_item)
        nest_resolved['metrics'] = result['result']['metrics']
        flat_resolved = util_dotdict.DotDict.from_nested(nest_resolved)
        flat_resolved = flat_resolved.insert_prefix(self.name, index=1)
        return flat_resolved

    def default_metrics(self):
        metric_infos = [
            {
                'metric': 'accuracy',
                'objective': 'maximize',
                'primary': True,
                'display': True,
            },
            {
                'metric': 'precision_positive',
                'objective': 'maximize',
                'primary': False,
                'display': True,
            },
            {
                'metric': 'recall_positive',
                'objective': 'maximize',
                'primary': False,
                'display': True,
            },
        ]
        return metric_infos

    @property
    def default_vantage_points(self):
        vantage_points = [
            {
                'metric1': 'metrics.sentiment_evaluate.accuracy',
                'metric2': 'metrics.sentiment_evaluate.precision_positive',
            },
        ]
        return vantage_points


def my_sentiment_pipeline():
    """Create the two-stage keyword review pipeline."""

    nodes = {
        'keyword_sentiment_predict': KeywordSentimentPredict(),
        'sentiment_evaluate': SentimentEvaluate(),
    }

    # Connect your nodes together: predictions feed into evaluation.
    nodes['keyword_sentiment_predict'].outputs['dst_fpath'].connect(
        nodes['sentiment_evaluate'].inputs['pred_fpath']
    )

    # Reuse the same labeled data for both prediction input and ground truth.
    nodes['keyword_sentiment_predict'].inputs['src_fpath'].connect(
        nodes['sentiment_evaluate'].inputs['true_fpath']
    )

    dag = kwdagger.Pipeline(nodes)
    dag.build_nx_graphs()
    return dag
