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


class KeywordSentimentPredictCLI(scfg.DataConfig):
    """Minimal "model" that tags texts containing a keyword."""

    src_fpath = scfg.Value(None, help='path to labeled jsonl review file')
    dst_fpath = scfg.Value(None, help='path to prediction file')
    dst_dpath = scfg.Value(None, help='path to output directory')

    keyword = scfg.Value('great', help='word that marks a review as positive')
    case_sensitive = scfg.Value(False, help='toggle case sensitivity')
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
            name='keyword_sentiment_predict',
            type='process',
            config=kwutil.Json.ensure_serializable(dict(config)),
            track_emissions=False,
        )
        proc_context.start()

        reviews = _load_reviews(config.src_fpath)
        keyword = config.keyword if config.case_sensitive else config.keyword.lower()

        predictions = []
        for idx, record in enumerate(reviews):
            haystack = record['text'] if config.case_sensitive else record['text'].lower()
            predicted_label = 'positive' if keyword in haystack else 'negative'
            predictions.append({
                'id': idx,
                'text': record['text'],
                'predicted_label': predicted_label,
            })

        data['result'] = {
            'keyword': config.keyword,
            'case_sensitive': bool(config.case_sensitive),
            'predictions': predictions,
        }

        obj = proc_context.stop()
        data['info'].append(obj)

        dst_fpath = ub.Path(config.dst_fpath)
        dst_fpath.parent.ensuredir()
        dst_fpath.write_text(json.dumps(data, indent=2))
        print(f'Wrote to: dst_fpath={dst_fpath}')


__cli__ = KeywordSentimentPredictCLI

if __name__ == '__main__':
    __cli__.main()

    r"""
    CommandLine:
        python ~/code/kwdagger/docs/source/manual/tutorials/twostage_pipeline/example_user_module/cli/keyword_sentiment_predict.py \
            --src_fpath ~/code/kwdagger/docs/source/manual/tutorials/twostage_pipeline/data/toy_reviews_movies.jsonl \
            --dst_fpath ./keyword_predictions.json
    """
