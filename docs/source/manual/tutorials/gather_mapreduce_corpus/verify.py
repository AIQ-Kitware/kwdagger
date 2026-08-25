#!/usr/bin/env python3
"""Verify the advanced nested-gather tutorial without importing kwdagger."""

from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path


WORD_RE = re.compile(r"[A-Za-z']+")
EXPECTED_CHUNKS = {
    'alpha': 2,
    'beta': 3,
    'gamma': 5,
    'delta': 4,
    'epsilon': 1,
}
DOCUMENT_COLLECTION = {
    'alpha': 'red',
    'beta': 'red',
    'gamma': 'blue',
    'delta': 'blue',
    'epsilon': 'blue',
}
EXPECTED_COLLECTIONS = {
    'red': ['alpha', 'beta'],
    'blue': ['delta', 'epsilon', 'gamma'],
}
METHODS = {'letters', 'words'}
METRICS = {'balance', 'letters', 'words'}


def _load(fpath: Path) -> dict:
    return json.loads(fpath.read_text())


def _artifact_files(results: Path, node: str, name: str) -> list[Path]:
    return sorted((results / node).glob(f'*/{name}'))


def _manifest_files(results: Path, node: str, name: str) -> list[Path]:
    return sorted((results / node).glob(f'*/_gather/{name}'))


def _manifest_members(fpath: Path) -> list[Path]:
    return [Path(line) for line in fpath.read_text().splitlines() if line]


def _independent_document_counts(data_dpath: Path) -> dict[str, dict[str, dict]]:
    result = {}
    for document, expected_num_chunks in EXPECTED_CHUNKS.items():
        chunk_fpaths = sorted((data_dpath / document).glob('*.txt'))
        assert len(chunk_fpaths) == expected_num_chunks
        counters = {
            'words': collections.Counter(),
            'letters': collections.Counter(),
        }
        totals = collections.Counter()
        for chunk_fpath in chunk_fpaths:
            text = chunk_fpath.read_text()
            words = [word.lower() for word in WORD_RE.findall(text)]
            letters = [char.lower() for char in text if char.isalpha()]
            counters['words'].update(words)
            counters['letters'].update(letters)
            totals['words'] += len(words)
            totals['letters'] += len(letters)
        result[document] = {
            method: {
                'counts': dict(sorted(counters[method].items())),
                'total_units': totals[method],
            }
            for method in METHODS
        }
    return result


def _expected_collection_metrics(document_counts: dict) -> dict[str, dict]:
    expected = {}
    for collection, documents in EXPECTED_COLLECTIONS.items():
        word_sizes = {
            document: document_counts[document]['words']['total_units']
            for document in documents
        }
        total_words = sum(word_sizes.values())
        total_letters = sum(
            document_counts[document]['letters']['total_units']
            for document in documents
        )
        expected[collection] = {
            'words': total_words,
            'letters': total_letters,
            'balance': max(word_sizes.values()) - min(word_sizes.values()),
        }
    return expected


def verify(results: Path, data_dpath: Path) -> None:
    partial_fpaths = _artifact_files(results, 'analyze_chunk', 'partial.json')
    analysis_fpaths = _artifact_files(results, 'reduce_document', 'analysis.json')
    document_fpaths = _artifact_files(results, 'document_report', 'report.json')
    corpus_fpaths = _artifact_files(results, 'corpus_report', 'corpus_report.json')
    metric_fpaths = _artifact_files(results, 'corpus_metric', 'metric.json')
    metric_report_fpaths = _artifact_files(
        results, 'metric_report', 'metric_report.json'
    )
    final_fpaths = _artifact_files(results, 'final_report', 'final_report.json')

    assert len(partial_fpaths) == 30, len(partial_fpaths)
    assert len(analysis_fpaths) == 10, len(analysis_fpaths)
    assert len(document_fpaths) == 5, len(document_fpaths)
    assert len(corpus_fpaths) == 2, len(corpus_fpaths)
    assert len(metric_fpaths) == 6, len(metric_fpaths)
    assert len(metric_report_fpaths) == 3, len(metric_report_fpaths)
    assert len(final_fpaths) == 1, len(final_fpaths)

    partials = [_load(fpath) for fpath in partial_fpaths]
    partial_groups = collections.defaultdict(list)
    for item in partials:
        partial_groups[(item['document'], item['method'])].append(item)
        assert item['collection'] == DOCUMENT_COLLECTION[item['document']]
    assert set(partial_groups) == {
        (document, method)
        for document in EXPECTED_CHUNKS
        for method in METHODS
    }
    for (document, _method), items in partial_groups.items():
        assert len(items) == EXPECTED_CHUNKS[document]

    first_manifests = _manifest_files(
        results, 'reduce_document', 'partials_fpath.txt'
    )
    assert len(first_manifests) == 10
    first_sizes = []
    for manifest in first_manifests:
        mapped = [_load(member) for member in _manifest_members(manifest)]
        assert mapped
        documents = {item['document'] for item in mapped}
        collections_ = {item['collection'] for item in mapped}
        methods = {item['method'] for item in mapped}
        indexes = [item['chunk_index'] for item in mapped]
        assert len(documents) == 1
        assert len(collections_) == 1
        assert len(methods) == 1
        assert indexes == sorted(indexes)
        document = next(iter(documents))
        assert collections_ == {DOCUMENT_COLLECTION[document]}
        assert len(mapped) == EXPECTED_CHUNKS[document]
        first_sizes.append(len(mapped))
    assert sorted(first_sizes) == [1, 1, 2, 2, 3, 3, 4, 4, 5, 5]

    analyses = [_load(fpath) for fpath in analysis_fpaths]
    for item in analyses:
        assert item['collection'] == DOCUMENT_COLLECTION[item['document']]
        assert item['num_chunks'] == EXPECTED_CHUNKS[item['document']]
        assert item['ordered_chunks'] == list(range(item['num_chunks']))

    second_manifests = _manifest_files(
        results, 'document_report', 'analyses_fpath.txt'
    )
    assert len(second_manifests) == 5
    for manifest in second_manifests:
        reduced = [_load(member) for member in _manifest_members(manifest)]
        assert [item['method'] for item in reduced] == ['letters', 'words']
        documents = {item['document'] for item in reduced}
        collections_ = {item['collection'] for item in reduced}
        assert len(documents) == 1
        document = next(iter(documents))
        assert collections_ == {DOCUMENT_COLLECTION[document]}

    documents = [_load(fpath) for fpath in document_fpaths]
    assert {item['document'] for item in documents} == set(EXPECTED_CHUNKS)
    for item in documents:
        assert item['collection'] == DOCUMENT_COLLECTION[item['document']]
        assert set(item['methods']) == METHODS
        assert item['num_chunks'] == EXPECTED_CHUNKS[item['document']]

    third_manifests = _manifest_files(
        results, 'corpus_report', 'reports_fpath.txt'
    )
    assert len(third_manifests) == 2
    third_sizes = []
    for manifest in third_manifests:
        reports = [_load(member) for member in _manifest_members(manifest)]
        collections_ = {item['collection'] for item in reports}
        assert len(collections_) == 1
        collection = next(iter(collections_))
        observed_documents = [item['document'] for item in reports]
        assert observed_documents == EXPECTED_COLLECTIONS[collection]
        third_sizes.append(len(reports))
    assert sorted(third_sizes) == [2, 3]

    independent = _independent_document_counts(data_dpath)
    corpora = [_load(fpath) for fpath in corpus_fpaths]
    assert {item['collection'] for item in corpora} == set(EXPECTED_COLLECTIONS)
    for corpus in corpora:
        collection = corpus['collection']
        expected_documents = EXPECTED_COLLECTIONS[collection]
        assert corpus['documents'] == expected_documents
        assert corpus['num_documents'] == len(expected_documents)
        assert corpus['document_chunk_counts'] == {
            document: EXPECTED_CHUNKS[document] for document in expected_documents
        }
        for method in METHODS:
            expected_total = sum(
                independent[document][method]['total_units']
                for document in expected_documents
            )
            assert corpus['methods'][method]['total_units'] == expected_total

    expected_metrics = _expected_collection_metrics(independent)
    metrics = [_load(fpath) for fpath in metric_fpaths]
    assert {(item['collection'], item['metric']) for item in metrics} == {
        (collection, metric)
        for collection in EXPECTED_COLLECTIONS
        for metric in METRICS
    }
    for item in metrics:
        assert item['value'] == expected_metrics[item['collection']][item['metric']]

    fourth_manifests = _manifest_files(
        results, 'metric_report', 'metrics_fpath.txt'
    )
    assert len(fourth_manifests) == 3
    for manifest in fourth_manifests:
        members = [_load(member) for member in _manifest_members(manifest)]
        assert [item['collection'] for item in members] == ['blue', 'red']
        assert len({item['metric'] for item in members}) == 1

    metric_reports = [_load(fpath) for fpath in metric_report_fpaths]
    assert {item['metric'] for item in metric_reports} == METRICS
    for report in metric_reports:
        metric = report['metric']
        assert report['collections'] == ['blue', 'red']
        assert report['values'] == {
            collection: expected_metrics[collection][metric]
            for collection in ['blue', 'red']
        }

    final_manifests = _manifest_files(
        results, 'final_report', 'metric_reports_fpath.txt'
    )
    assert len(final_manifests) == 1
    final_members = [_load(member) for member in _manifest_members(final_manifests[0])]
    assert [item['metric'] for item in final_members] == [
        'balance',
        'letters',
        'words',
    ]

    final = _load(final_fpaths[0])
    assert final['ordered_metrics'] == ['balance', 'letters', 'words']
    assert set(final['metrics']) == METRICS

    total_words = sum(
        independent[document]['words']['total_units'] for document in EXPECTED_CHUNKS
    )
    total_letters = sum(
        independent[document]['letters']['total_units']
        for document in EXPECTED_CHUNKS
    )

    print('MapReduce gather tutorial verified successfully')
    print('  authored rows: 90')
    print(
        '  concrete jobs: 30 map + 10 reduce + 5 document + 2 corpus + '
        '6 corpus-metric + 3 metric-report + 1 final = 57'
    )
    print('  chunk gather sizes: 1,1,2,2,3,3,4,4,5,5')
    print('  method gather sizes: 2,2,2,2,2')
    print('  collection gather sizes: 2,3')
    print('  cross-collection metric gather sizes: 2,2,2')
    print('  final gather size: 3')
    print(f'  all-document totals: {total_words} words, {total_letters} letters')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--results', type=Path, default=Path('results'))
    parser.add_argument('--data', type=Path, default=Path('data'))
    args = parser.parse_args()
    verify(args.results, args.data)


if __name__ == '__main__':
    main()
