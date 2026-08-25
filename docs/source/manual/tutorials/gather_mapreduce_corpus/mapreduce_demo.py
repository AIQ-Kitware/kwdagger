#!/usr/bin/env python3
"""Tiny deterministic executables used by the advanced gather tutorial."""

from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path
from typing import Iterable


WORD_RE = re.compile(r"[A-Za-z']+")


def _read_json(fpath: str | Path) -> dict:
    return json.loads(Path(fpath).read_text())


def _write_json(fpath: str | Path, data: dict) -> None:
    fpath = Path(fpath)
    fpath.parent.mkdir(parents=True, exist_ok=True)
    fpath.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')


def _read_manifest(fpath: str | Path) -> list[Path]:
    return [Path(line) for line in Path(fpath).read_text().splitlines() if line]


def _top_items(counter: collections.Counter, n: int = 5) -> list[list]:
    return [[key, value] for key, value in counter.most_common(n)]


def map_chunk(args: argparse.Namespace) -> None:
    chunk_fpath = (
        Path(args.corpus_dpath) / args.document / f'{args.chunk_index:02d}.txt'
    )
    text = chunk_fpath.read_text()
    if args.method == 'words':
        units = [word.lower() for word in WORD_RE.findall(text)]
    elif args.method == 'letters':
        units = [char.lower() for char in text if char.isalpha()]
    else:  # pragma: no cover - argparse constrains this
        raise AssertionError(args.method)

    counts = collections.Counter(units)
    _write_json(
        args.partial_fpath,
        {
            'document': args.document,
            'collection': args.collection,
            'chunk_index': args.chunk_index,
            'method': args.method,
            'source': str(chunk_fpath),
            'total_units': len(units),
            'counts': dict(sorted(counts.items())),
        },
    )


def reduce_document(args: argparse.Namespace) -> None:
    partial_fpaths = _read_manifest(args.partials_fpath)
    partials = [_read_json(fpath) for fpath in partial_fpaths]

    documents = {item['document'] for item in partials}
    collections_ = {item['collection'] for item in partials}
    methods = {item['method'] for item in partials}
    chunk_indexes = [item['chunk_index'] for item in partials]
    if documents != {args.document}:
        raise ValueError(
            f'Gather crossed document boundaries: {documents!r} != {args.document!r}'
        )
    if collections_ != {args.collection}:
        raise ValueError(
            'Gather crossed collection boundaries: '
            f'{collections_!r} != {args.collection!r}'
        )
    if methods != {args.method}:
        raise ValueError(
            f'Gather crossed method boundaries: {methods!r} != {args.method!r}'
        )
    if chunk_indexes != sorted(chunk_indexes):
        raise ValueError(
            f'Gather manifest is not ordered by chunk_index: {chunk_indexes!r}'
        )
    if len(chunk_indexes) != len(set(chunk_indexes)):
        raise ValueError(f'Duplicate chunks in gather: {chunk_indexes!r}')

    total = collections.Counter()
    total_units = 0
    for partial in partials:
        total.update(partial['counts'])
        total_units += partial['total_units']

    _write_json(
        args.analysis_fpath,
        {
            'document': args.document,
            'collection': args.collection,
            'method': args.method,
            'num_chunks': len(partials),
            'ordered_chunks': chunk_indexes,
            'total_units': total_units,
            'counts': dict(sorted(total.items())),
            'top_items': _top_items(total),
        },
    )


def document_report(args: argparse.Namespace) -> None:
    analysis_fpaths = _read_manifest(args.analyses_fpath)
    analyses = [_read_json(fpath) for fpath in analysis_fpaths]

    documents = {item['document'] for item in analyses}
    collections_ = {item['collection'] for item in analyses}
    methods = [item['method'] for item in analyses]
    if documents != {args.document}:
        raise ValueError(
            f'Gather crossed document boundaries: {documents!r} != {args.document!r}'
        )
    if collections_ != {args.collection}:
        raise ValueError(
            'Gather crossed collection boundaries: '
            f'{collections_!r} != {args.collection!r}'
        )
    if methods != sorted(methods):
        raise ValueError(f'Gather manifest is not ordered by method: {methods!r}')
    if len(methods) != len(set(methods)):
        raise ValueError(f'Duplicate methods in gather: {methods!r}')

    chunk_orders = {tuple(item['ordered_chunks']) for item in analyses}
    if len(chunk_orders) != 1:
        raise ValueError(
            'Methods disagree about the ordered chunk membership: '
            f'{sorted(chunk_orders)!r}'
        )

    _write_json(
        args.report_fpath,
        {
            'document': args.document,
            'collection': args.collection,
            'num_chunks': analyses[0]['num_chunks'],
            'ordered_chunks': analyses[0]['ordered_chunks'],
            'methods': {item['method']: item for item in analyses},
        },
    )


def corpus_report(args: argparse.Namespace) -> None:
    report_fpaths = _read_manifest(args.reports_fpath)
    reports = [_read_json(fpath) for fpath in report_fpaths]

    documents = [item['document'] for item in reports]
    collections_ = {item['collection'] for item in reports}
    if collections_ != {args.collection}:
        raise ValueError(
            'Gather crossed collection boundaries: '
            f'{collections_!r} != {args.collection!r}'
        )
    if documents != sorted(documents):
        raise ValueError(
            f'Gather manifest is not ordered by document: {documents!r}'
        )
    if len(documents) != len(set(documents)):
        raise ValueError(f'Duplicate documents in gather: {documents!r}')

    corpus_counts: dict[str, collections.Counter[str]] = collections.defaultdict(
        collections.Counter
    )
    totals: collections.Counter[str] = collections.Counter()
    document_unit_totals: dict[str, dict[str, int]] = {}
    for report in reports:
        document_unit_totals[report['document']] = {}
        for method, analysis in report['methods'].items():
            corpus_counts[method].update(analysis['counts'])
            totals[method] += analysis['total_units']
            document_unit_totals[report['document']][method] = analysis['total_units']

    method_summary = {}
    for method in sorted(corpus_counts):
        counts = corpus_counts[method]
        method_summary[method] = {
            'total_units': totals[method],
            'counts': dict(sorted(counts.items())),
            'top_items': _top_items(counts),
        }

    _write_json(
        args.corpus_report_fpath,
        {
            'collection': args.collection,
            'documents': documents,
            'num_documents': len(documents),
            'document_chunk_counts': {
                report['document']: report['num_chunks'] for report in reports
            },
            'document_unit_totals': document_unit_totals,
            'methods': method_summary,
        },
    )


def corpus_metric(args: argparse.Namespace) -> None:
    corpus = _read_json(args.corpus_report_fpath)
    if corpus['collection'] != args.collection:
        raise ValueError(
            'Corpus input does not match requested collection: '
            f"{corpus['collection']!r} != {args.collection!r}"
        )

    word_by_document = {
        document: totals['words']
        for document, totals in corpus['document_unit_totals'].items()
    }
    if args.metric == 'words':
        value = corpus['methods']['words']['total_units']
        best_document = max(
            word_by_document, key=lambda key: (word_by_document[key], key)
        )
        details = {
            'largest_document': best_document,
            'largest_document_words': word_by_document[best_document],
        }
        objective = 'maximize'
    elif args.metric == 'letters':
        value = corpus['methods']['letters']['total_units']
        words = corpus['methods']['words']['total_units']
        details = {
            'letters_per_word': round(value / words, 6),
        }
        objective = 'maximize'
    elif args.metric == 'balance':
        sizes = list(word_by_document.values())
        value = max(sizes) - min(sizes)
        details = {
            'min_document_words': min(sizes),
            'max_document_words': max(sizes),
        }
        objective = 'minimize'
    else:  # pragma: no cover - argparse constrains this
        raise AssertionError(args.metric)

    _write_json(
        args.metric_fpath,
        {
            'collection': args.collection,
            'metric': args.metric,
            'objective': objective,
            'value': value,
            'details': details,
        },
    )


def metric_report(args: argparse.Namespace) -> None:
    metric_fpaths = _read_manifest(args.metrics_fpath)
    metrics = [_read_json(fpath) for fpath in metric_fpaths]
    metric_names = {item['metric'] for item in metrics}
    collections_ = [item['collection'] for item in metrics]
    if metric_names != {args.metric}:
        raise ValueError(
            f'Gather crossed metric boundaries: {metric_names!r} != {args.metric!r}'
        )
    if collections_ != sorted(collections_):
        raise ValueError(
            f'Gather manifest is not ordered by collection: {collections_!r}'
        )
    if len(collections_) != len(set(collections_)):
        raise ValueError(f'Duplicate collections in gather: {collections_!r}')

    objective = metrics[0]['objective']
    if {item['objective'] for item in metrics} != {objective}:
        raise ValueError('Metric members disagree about objective')
    values = {item['collection']: item['value'] for item in metrics}
    if objective == 'minimize':
        best_collection = min(values, key=lambda key: (values[key], key))
    else:
        best_collection = max(values, key=lambda key: (values[key], key))

    _write_json(
        args.metric_report_fpath,
        {
            'metric': args.metric,
            'objective': objective,
            'collections': collections_,
            'values': values,
            'best_collection': best_collection,
        },
    )


def final_report(args: argparse.Namespace) -> None:
    report_fpaths = _read_manifest(args.metric_reports_fpath)
    reports = [_read_json(fpath) for fpath in report_fpaths]
    metrics = [item['metric'] for item in reports]
    if metrics != sorted(metrics):
        raise ValueError(f'Gather manifest is not ordered by metric: {metrics!r}')
    if len(metrics) != len(set(metrics)):
        raise ValueError(f'Duplicate metrics in gather: {metrics!r}')

    _write_json(
        args.final_report_fpath,
        {
            'metrics': {item['metric']: item for item in reports},
            'ordered_metrics': metrics,
        },
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest='command', required=True)

    map_parser = subparsers.add_parser('map')
    map_parser.add_argument('--corpus_dpath', required=True)
    map_parser.add_argument('--document', required=True)
    map_parser.add_argument('--collection', required=True)
    map_parser.add_argument('--chunk_index', required=True, type=int)
    map_parser.add_argument('--method', choices=['words', 'letters'], required=True)
    map_parser.add_argument('--partial_fpath', required=True)
    map_parser.set_defaults(func=map_chunk)

    reduce_parser = subparsers.add_parser('reduce')
    reduce_parser.add_argument('--partials_fpath', required=True)
    reduce_parser.add_argument('--document', required=True)
    reduce_parser.add_argument('--collection', required=True)
    reduce_parser.add_argument('--method', choices=['words', 'letters'], required=True)
    reduce_parser.add_argument('--analysis_fpath', required=True)
    reduce_parser.set_defaults(func=reduce_document)

    report_parser = subparsers.add_parser('document-report')
    report_parser.add_argument('--analyses_fpath', required=True)
    report_parser.add_argument('--document', required=True)
    report_parser.add_argument('--collection', required=True)
    report_parser.add_argument('--report_fpath', required=True)
    report_parser.set_defaults(func=document_report)

    corpus_parser = subparsers.add_parser('corpus-report')
    corpus_parser.add_argument('--reports_fpath', required=True)
    corpus_parser.add_argument('--collection', required=True)
    corpus_parser.add_argument('--corpus_report_fpath', required=True)
    corpus_parser.set_defaults(func=corpus_report)

    metric_parser = subparsers.add_parser('corpus-metric')
    metric_parser.add_argument('--corpus_report_fpath', required=True)
    metric_parser.add_argument('--collection', required=True)
    metric_parser.add_argument(
        '--metric', choices=['words', 'letters', 'balance'], required=True
    )
    metric_parser.add_argument('--metric_fpath', required=True)
    metric_parser.set_defaults(func=corpus_metric)

    metric_report_parser = subparsers.add_parser('metric-report')
    metric_report_parser.add_argument('--metrics_fpath', required=True)
    metric_report_parser.add_argument(
        '--metric', choices=['words', 'letters', 'balance'], required=True
    )
    metric_report_parser.add_argument('--metric_report_fpath', required=True)
    metric_report_parser.set_defaults(func=metric_report)

    final_parser = subparsers.add_parser('final-report')
    final_parser.add_argument('--metric_reports_fpath', required=True)
    final_parser.add_argument('--final_report_fpath', required=True)
    final_parser.set_defaults(func=final_report)

    return parser


def main(argv: Iterable[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == '__main__':
    main()
