"""Compilation checks for the advanced nested-gather MapReduce tutorial."""

from __future__ import annotations

import ubelt as ub


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


def _tutorial_dpath():
    import kwdagger

    dpath = (
        ub.Path(kwdagger.__file__).parent.parent
        / 'docs/source/manual/tutorials/gather_mapreduce_corpus'
    )
    if not (dpath / 'params.yaml').exists():
        import pytest

        pytest.skip('tutorial sources are not part of the installed package')
    return dpath


def _rows(dpath):
    import kwutil

    from kwdagger.utils.util_param_grid import expand_param_grid

    data = kwutil.Yaml.coerce((dpath / 'params.yaml').read_text())
    return list(expand_param_grid(data))


def test_gather_mapreduce_tutorial_compiles_expected_nested_collections(tmp_path):
    from kwdagger.pipeline import coerce_pipeline

    dpath = _tutorial_dpath()
    rows = _rows(dpath)
    assert len(rows) == 90

    # pipeline.yaml contains a top-level multiline __doc__; loading it through
    # the normal coerce path proves that descriptive metadata is accepted.
    dag = coerce_pipeline(str(dpath / 'pipeline.yaml'))
    compiled = dag.compile_configurations(
        rows, root_dpath=tmp_path / 'results', cache=False
    )
    by_name = ub.group_items(compiled.nodes.values(), key=lambda node: node.name)

    assert {name: len(nodes) for name, nodes in by_name.items()} == {
        'analyze_chunk': 30,
        'reduce_document': 10,
        'document_report': 5,
        'corpus_report': 2,
        'corpus_metric': 6,
        'metric_report': 3,
        'final_report': 1,
    }
    assert len(compiled.nodes) == 57

    for node in by_name['reduce_document']:
        members = node.inputs['partials_fpath']._gather_members
        assert members is not None
        document = node.final_algo_config['document']
        collection = node.final_algo_config['collection']
        method = node.final_algo_config['method']
        assert collection == DOCUMENT_COLLECTION[document]
        assert len(members) == EXPECTED_CHUNKS[document]
        assert [
            member.parent.final_algo_config['chunk_index'] for member in members
        ] == list(range(EXPECTED_CHUNKS[document]))
        assert {
            member.parent.final_algo_config['document'] for member in members
        } == {document}
        assert {
            member.parent.final_algo_config['collection'] for member in members
        } == {collection}
        assert {
            member.parent.final_algo_config['method'] for member in members
        } == {method}

    for node in by_name['document_report']:
        members = node.inputs['analyses_fpath']._gather_members
        assert members is not None
        assert len(members) == 2
        document = node.final_algo_config['document']
        collection = node.final_algo_config['collection']
        assert collection == DOCUMENT_COLLECTION[document]
        assert [
            member.parent.final_algo_config['method'] for member in members
        ] == ['letters', 'words']
        assert {
            member.parent.final_algo_config['document'] for member in members
        } == {document}
        assert {
            member.parent.final_algo_config['collection'] for member in members
        } == {collection}

    for node in by_name['corpus_report']:
        members = node.inputs['reports_fpath']._gather_members
        assert members is not None
        collection = node.final_algo_config['collection']
        assert [
            member.parent.final_algo_config['document'] for member in members
        ] == EXPECTED_COLLECTIONS[collection]
        assert {
            member.parent.final_algo_config['collection'] for member in members
        } == {collection}

    assert {
        (node.final_algo_config['collection'], node.final_algo_config['metric'])
        for node in by_name['corpus_metric']
    } == {
        (collection, metric)
        for collection in {'blue', 'red'}
        for metric in {'balance', 'letters', 'words'}
    }

    for node in by_name['metric_report']:
        members = node.inputs['metrics_fpath']._gather_members
        assert members is not None
        metric = node.final_algo_config['metric']
        assert [
            member.parent.final_algo_config['collection'] for member in members
        ] == ['blue', 'red']
        assert {
            member.parent.final_algo_config['metric'] for member in members
        } == {metric}

    final = by_name['final_report'][0]
    members = final.inputs['metric_reports_fpath']._gather_members
    assert members is not None
    assert [member.parent.final_algo_config['metric'] for member in members] == [
        'balance',
        'letters',
        'words',
    ]
