"""Python alternative to ``pipeline.yaml`` for the advanced gather tutorial.

This module and ``pipeline.yaml`` define the same pipeline.  They are alternatives,
not two pieces that must be used together.  Use either::

    kwdagger schedule --pipeline=./pipeline.yaml --params=params.yaml ...

or::

    kwdagger schedule --pipeline='pipelines.py::build_pipeline()' \
        --params=params.yaml ...

The YAML version is useful when the graph can be represented declaratively; this
file demonstrates the equivalent Python API and is kept equivalent by tests.
"""

from __future__ import annotations

import kwdagger


def build_pipeline():
    analyze_chunk = kwdagger.ProcessNode(
        name='analyze_chunk',
        executable='python mapreduce_demo.py map',
        in_paths={'corpus_dpath'},
        out_paths={'partial_fpath': 'partial.json'},
        algo_params={
            'document': 'alpha',
            'collection': 'red',
            'chunk_index': 0,
            'method': 'words',
        },
    )
    reduce_document = kwdagger.ProcessNode(
        name='reduce_document',
        executable='python mapreduce_demo.py reduce',
        in_paths={'partials_fpath'},
        out_paths={'analysis_fpath': 'analysis.json'},
        algo_params={
            'document': 'alpha',
            'collection': 'red',
            'method': 'words',
        },
    )
    document_report = kwdagger.ProcessNode(
        name='document_report',
        executable='python mapreduce_demo.py document-report',
        in_paths={'analyses_fpath'},
        out_paths={'report_fpath': 'report.json'},
        algo_params={'document': 'alpha', 'collection': 'red'},
    )
    corpus_report = kwdagger.ProcessNode(
        name='corpus_report',
        executable='python mapreduce_demo.py corpus-report',
        in_paths={'reports_fpath'},
        out_paths={'corpus_report_fpath': 'corpus_report.json'},
        algo_params={'collection': 'red'},
    )
    corpus_metric = kwdagger.ProcessNode(
        name='corpus_metric',
        executable='python mapreduce_demo.py corpus-metric',
        in_paths={'corpus_report_fpath'},
        out_paths={'metric_fpath': 'metric.json'},
        algo_params={'collection': 'red', 'metric': 'words'},
    )
    metric_report = kwdagger.ProcessNode(
        name='metric_report',
        executable='python mapreduce_demo.py metric-report',
        in_paths={'metrics_fpath'},
        out_paths={'metric_report_fpath': 'metric_report.json'},
        algo_params={'metric': 'words'},
    )
    final_report = kwdagger.ProcessNode(
        name='final_report',
        executable='python mapreduce_demo.py final-report',
        in_paths={'metric_reports_fpath'},
        out_paths={'final_report_fpath': 'final_report.json'},
    )

    analyze_chunk.param_ports['document'].connect(
        reduce_document.param_ports['document']
    )
    analyze_chunk.param_ports['collection'].connect(
        reduce_document.param_ports['collection']
    )
    analyze_chunk.param_ports['method'].connect(
        reduce_document.param_ports['method']
    )
    analyze_chunk.param_ports['document'].connect(
        document_report.param_ports['document']
    )
    analyze_chunk.param_ports['collection'].connect(
        document_report.param_ports['collection']
    )
    document_report.param_ports['collection'].connect(
        corpus_report.param_ports['collection']
    )
    corpus_report.param_ports['collection'].connect(
        corpus_metric.param_ports['collection']
    )
    corpus_metric.param_ports['metric'].connect(metric_report.param_ports['metric'])

    analyze_chunk.outputs['partial_fpath'].connect(
        reduce_document.inputs['partials_fpath'],
        gather=kwdagger.GatherSpec(
            group_by=[
                {
                    'src': 'analyze_chunk.document',
                    'dst': 'reduce_document.document',
                },
                {
                    'src': 'analyze_chunk.collection',
                    'dst': 'reduce_document.collection',
                },
                {
                    'src': 'analyze_chunk.method',
                    'dst': 'reduce_document.method',
                },
            ],
            order_by=['analyze_chunk.chunk_index'],
            require='all_success',
        ),
    )
    reduce_document.outputs['analysis_fpath'].connect(
        document_report.inputs['analyses_fpath'],
        gather=kwdagger.GatherSpec(
            group_by=[
                {
                    'src': 'reduce_document.document',
                    'dst': 'document_report.document',
                },
                {
                    'src': 'reduce_document.collection',
                    'dst': 'document_report.collection',
                },
            ],
            order_by=['reduce_document.method'],
            require='all_success',
        ),
    )
    document_report.outputs['report_fpath'].connect(
        corpus_report.inputs['reports_fpath'],
        gather=kwdagger.GatherSpec(
            group_by=[
                {
                    'src': 'document_report.collection',
                    'dst': 'corpus_report.collection',
                }
            ],
            order_by=['document_report.document'],
            require='all_success',
        ),
    )
    corpus_report.outputs['corpus_report_fpath'].connect(
        corpus_metric.inputs['corpus_report_fpath']
    )
    corpus_metric.outputs['metric_fpath'].connect(
        metric_report.inputs['metrics_fpath'],
        gather=kwdagger.GatherSpec(
            group_by=[
                {
                    'src': 'corpus_metric.metric',
                    'dst': 'metric_report.metric',
                }
            ],
            order_by=['corpus_metric.collection'],
            require='all_success',
        ),
    )
    metric_report.outputs['metric_report_fpath'].connect(
        final_report.inputs['metric_reports_fpath'],
        gather=kwdagger.GatherSpec(
            group_by=[],
            order_by=['metric_report.metric'],
            require='all_success',
        ),
    )
    return kwdagger.Pipeline(
        [
            analyze_chunk,
            reduce_document,
            document_report,
            corpus_report,
            corpus_metric,
            metric_report,
            final_report,
        ]
    )
