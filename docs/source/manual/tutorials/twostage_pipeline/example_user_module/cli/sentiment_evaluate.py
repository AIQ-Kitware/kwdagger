#!/usr/bin/env python3
import json
import scriptconfig as scfg
import ubelt as ub
import kwutil
import rich
from rich.markup import escape


def _load_reviews(fpath):
    records = []
    for line in ub.Path(fpath).read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        records.append(json.loads(line))
    return records


def _safe_div(num, den):
    return num / den if den else 0.0


class SentimentEvaluateCLI(scfg.DataConfig):
    """Evaluate predictions produced by :mod:`keyword_sentiment_predict`."""

    pred_fpath = scfg.Value(None, help='path to predictions JSON')
    true_fpath = scfg.Value(None, help='path to labeled jsonl file')
    out_fpath = scfg.Value(None, help='path to evaluation file')
    workers = scfg.Value(0, help='number of parallel workers (unused)')

    @classmethod
    def main(cls, cmdline=1, **kwargs):
        config = cls.cli(cmdline=cmdline, data=kwargs, strict=True)
        rich.print('config = ' + escape(ub.urepr(config, nl=1)))

        data = {
            'info': [],
            'result': None,
        }

        proc_context = kwutil.ProcessContext(
            name='sentiment_evaluate',
            type='process',
            config=kwutil.Json.ensure_serializable(dict(config)),
            track_emissions=False,
        )
        proc_context.start()

        reviews = _load_reviews(config.true_fpath)
        pred_data = json.loads(ub.Path(config.pred_fpath).read_text())
        predictions = pred_data['result']['predictions']

        if len(predictions) != len(reviews):
            raise AssertionError('Predictions and truths must have the same length')

        num_correct = 0
        confusion = {
            'tp': 0,
            'fp': 0,
            'tn': 0,
            'fn': 0,
        }
        detailed = []

        for record, pred in zip(reviews, predictions):
            true_label = record['label']
            pred_label = pred['predicted_label']
            correct = int(true_label == pred_label)
            num_correct += correct
            detailed.append({
                'text': record['text'],
                'true_label': true_label,
                'predicted_label': pred_label,
            })

            if true_label == 'positive' and pred_label == 'positive':
                confusion['tp'] += 1
            elif true_label == 'negative' and pred_label == 'positive':
                confusion['fp'] += 1
            elif true_label == 'negative' and pred_label == 'negative':
                confusion['tn'] += 1
            elif true_label == 'positive' and pred_label == 'negative':
                confusion['fn'] += 1

        accuracy = _safe_div(num_correct, len(reviews))
        precision = _safe_div(confusion['tp'], (confusion['tp'] + confusion['fp']))
        recall = _safe_div(confusion['tp'], (confusion['tp'] + confusion['fn']))

        metrics = {
            'accuracy': accuracy,
            'precision_positive': precision,
            'recall_positive': recall,
            'num_examples': len(reviews),
            'keyword_used': pred_data['result']['keyword'],
        }

        data['result'] = {
            'metrics': metrics,
            'confusion': confusion,
            'detailed': detailed,
        }

        obj = proc_context.stop()
        data['info'].append(obj)

        out_fpath = ub.Path(config.out_fpath)
        out_fpath.parent.ensuredir()
        out_fpath.write_text(json.dumps(data, indent=2))
        print(f'wrote to: out_fpath={out_fpath}')


__cli__ = SentimentEvaluateCLI

if __name__ == '__main__':
    __cli__.main()
    r"""
    CommandLine:
        python ~/code/kwdagger/docs/source/manual/tutorials/twostage_pipeline/example_user_module/cli/sentiment_evaluate.py \
            --true_fpath ~/code/kwdagger/docs/source/manual/tutorials/twostage_pipeline/data/toy_reviews_movies.jsonl \
            --pred_fpath ./keyword_predictions.json \
            --out_fpath out.json

        python -m example_user_module.cli.keyword_sentiment_predict
    """
