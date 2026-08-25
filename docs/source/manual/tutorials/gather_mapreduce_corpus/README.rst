Advanced Gather Tutorial: Mini MapReduce Corpus
================================================

This tutorial is a runnable stress test for compile-time gathers.  It is
intentionally different from the cross-validation example: collections have
unequal sizes, the grouping key changes between reductions, a gathered result
fans back out over a new matrix axis, and the graph ends with another global
gather.

The complete graph is:

.. code:: text

    analyze_chunk[document, collection, chunk_index, method]
        |
        | gather by (document, collection, method), order by chunk_index
        v
    reduce_document[document, collection, method]
        |
        | gather by (document, collection), order by method
        v
    document_report[document, collection]
        |
        | gather by collection, order by document
        v
    corpus_report[collection]
        |
        | fan out over metric
        v
    corpus_metric[collection, metric]
        |
        | gather by metric, order by collection
        v
    metric_report[metric]
        |
        | global gather, order by metric
        v
    final_report[]

All workloads are small real Python programs. ``mapreduce_demo.py`` reads text,
counts words or letters, writes JSON, consumes the exact path manifests produced
by kwdagger, computes collection-level metrics, and creates a final report.  The
independent ``verify.py`` checks the observable artifacts without importing
kwdagger.

Two equivalent pipeline definitions
-----------------------------------

This tutorial deliberately ships the graph in two forms:

``pipeline.yaml``
    A declarative, code-free definition.

``pipelines.py``
    The equivalent graph built with the Python API.

**They are alternatives; neither file is required by the other.** The test suite
keeps them equivalent. ``params.yaml`` contains only the parameter matrix so the
same matrix can be used with either definition.

The YAML file starts with a multiline top-level ``__doc__`` value describing the
pipeline. ``kwdagger.load_yaml_pipeline`` accepts this key as descriptive
metadata while retaining strict rejection of other unknown top-level keys.

Run the YAML definition, which is the default:

.. code:: bash

    ./run_pipeline.sh

Run the same matrix through the Python definition instead:

.. code:: bash

    ./run_pipeline.sh 'pipelines.py::build_pipeline()'

Equivalently, the underlying commands are:

.. code:: bash

    kwdagger schedule \
        --pipeline=./pipeline.yaml \
        --params=params.yaml \
        --root_dpath=results \
        --backend=serial \
        --run=1

and:

.. code:: bash

    kwdagger schedule \
        --pipeline='pipelines.py::build_pipeline()' \
        --params=params.yaml \
        --root_dpath=results \
        --backend=serial \
        --run=1

Why this example is useful
--------------------------

The example exercises all of the following in one static compilation:

* non-uniform chunk gathers with 1, 2, 3, 4, and 5 members;
* two uneven document collections with 2 and 3 members;
* multiple nested gather levels;
* a gather followed by a new fan-out;
* regrouping by a different key after that fan-out;
* a final ``group_by: []`` global collection;
* explicit ``order_by`` at every collection boundary;
* shared parameter edges, so known grouping values are declared once;
* asymmetric source/destination keys in ``GatherSpec.group_by``;
* canonicalization of 90 authored matrix rows into 57 concrete processes;
* a declarative YAML pipeline and equivalent Python API definition.

The tiny corpus
---------------

Five documents have deliberately uneven chunk counts and belong to two
collections:

.. code:: text

    red:
        alpha    2 chunks
        beta     3 chunks

    blue:
        gamma    5 chunks
        delta    4 chunks
        epsilon  1 chunk

Every chunk is processed with two methods:

``words``
    Tokenize alphabetic words, lowercase them, and count occurrences.

``letters``
    Count individual alphabetic characters, case-insensitively.

After collection reports are built, each collection fans out over three metrics:

``words``
    Total word tokens in the collection.

``letters``
    Total alphabetic characters in the collection.

``balance``
    Difference between the largest and smallest document word counts. Smaller
    is considered better.

Authored rows versus concrete jobs
----------------------------------

The parameter matrix includes ``corpus_metric.metric`` alongside the chunk axes.
That deliberately repeats requests for all upstream work three times. The
expanded matrix therefore contains 90 authored rows:

.. code:: text

    15 chunks * 2 methods * 3 metrics = 90 rows

Those rows should canonicalize to only the distinct computations:

.. code:: text

    analyze_chunk     30
    reduce_document   10
    document_report    5
    corpus_report      2
    corpus_metric      6
    metric_report      3
    final_report       1
                      --
                      57

This makes the tutorial exercise process identity and duplicate request handling
at the same time as gather compilation.

First gather: chunks -> document/method
---------------------------------------

Each map job writes ``partial.json``. The first gather selects chunks with the
same document, collection, and method and orders them by ``chunk_index``:

.. code:: yaml

    - src: analyze_chunk.partial_fpath
      dst: reduce_document.partials_fpath
      gather:
        group_by:
          - {src: analyze_chunk.document, dst: reduce_document.document}
          - {src: analyze_chunk.collection, dst: reduce_document.collection}
          - {src: analyze_chunk.method, dst: reduce_document.method}
        order_by: [analyze_chunk.chunk_index]
        require: all_success

The ten collection sizes are:

.. code:: text

    1, 1, 2, 2, 3, 3, 4, 4, 5, 5

``reduce_document`` validates the document, collection, method, uniqueness, and
chunk ordering before combining counts.

Second gather: methods -> document
----------------------------------

Each document has two reductions, one for letters and one for words. They gather
into ``document_report`` ordered by method:

.. code:: text

    reduce_document[document, collection, method]
        -> gather 2:1
    document_report[document, collection]

Each document report verifies that both methods saw the same ordered set of
chunks.

Third gather: documents -> collection corpus
--------------------------------------------

Document reports next gather by ``collection`` rather than globally:

.. code:: yaml

    - src: document_report.report_fpath
      dst: corpus_report.reports_fpath
      gather:
        group_by:
          - {src: document_report.collection, dst: corpus_report.collection}
        order_by: [document_report.document]
        require: all_success

This produces two unequal collections:

.. code:: text

    red  <- alpha, beta                 # 2 members
    blue <- delta, epsilon, gamma       # 3 members

At this point the graph has reduced to two ``corpus_report`` processes, but the
pipeline is not finished.

Fan out again: collection corpus -> metrics
-------------------------------------------

A gathered output is an ordinary output. Each ``corpus_report`` fans out over a
new ``metric`` axis:

.. code:: text

    corpus_report[red]  -> words, letters, balance
    corpus_report[blue] -> words, letters, balance

This creates six ``corpus_metric`` processes. The checked-in data gives:

.. code:: text

              words  letters  balance
    blue         43      222       11
    red          26      106        4

So blue wins the maximize metrics while red wins the minimize ``balance``
metric.

Fourth gather: regroup across collections by metric
---------------------------------------------------

The graph then changes grouping keys. Instead of grouping by collection, it
collects the same metric across collections:

.. code:: yaml

    - src: corpus_metric.metric_fpath
      dst: metric_report.metrics_fpath
      gather:
        group_by:
          - {src: corpus_metric.metric, dst: metric_report.metric}
        order_by: [corpus_metric.collection]
        require: all_success

The three collections are therefore:

.. code:: text

    balance <- blue, red
    letters <- blue, red
    words   <- blue, red

This is the main advanced feature of the example: a key that defined an earlier
group survives a reduction, the graph fans out over a new key, and then the old
key is reduced away while the new key becomes the grouping identity.

Final gather: metric reports -> one result
------------------------------------------

Finally the three metric reports are globally gathered in metric order:

.. code:: yaml

    - src: metric_report.metric_report_fpath
      dst: final_report.metric_reports_fpath
      gather:
        group_by: []
        order_by: [metric_report.metric]
        require: all_success

The final manifest is ordered:

.. code:: text

    balance
    letters
    words

and ``final_report.json`` records all three cross-collection comparisons.

What ``verify.py`` checks
-------------------------

The verifier independently checks:

* exactly 30 map, 10 reduction, 5 document, 2 corpus, 6 corpus-metric,
  3 metric-report, and 1 final artifact;
* first-level manifest sizes ``1,1,2,2,3,3,4,4,5,5``;
* no chunk gather crosses document, collection, or method boundaries;
* every chunk manifest is ordered by ``chunk_index``;
* each document gather contains ``letters`` then ``words``;
* collection gathers contain ``red=[alpha,beta]`` and
  ``blue=[delta,epsilon,gamma]``;
* metric gathers contain ``blue`` then ``red``;
* the global manifest contains ``balance``, ``letters``, ``words``;
* all counts and collection metrics agree with a fresh independent pass over
  the source text.

A successful run ends with output similar to:

.. code:: text

    MapReduce gather tutorial verified successfully
      authored rows: 90
      concrete jobs: 30 map + 10 reduce + 5 document + 2 corpus + 6 corpus-metric + 3 metric-report + 1 final = 57
      chunk gather sizes: 1,1,2,2,3,3,4,4,5,5
      method gather sizes: 2,2,2,2,2
      collection gather sizes: 2,3
      cross-collection metric gather sizes: 2,2,2
      final gather size: 3
      all-document totals: 69 words, 328 letters

Inspect the artifacts
---------------------

Every gather target keeps the path manifest next to its output. For example:

.. code:: text

    results/reduce_document/<process-id>/_gather/partials_fpath.txt
    results/document_report/<process-id>/_gather/analyses_fpath.txt
    results/corpus_report/<process-id>/_gather/reports_fpath.txt
    results/metric_report/<process-id>/_gather/metrics_fpath.txt
    results/final_report/<process-id>/_gather/metric_reports_fpath.txt

Those manifests are the most direct way to audit which concrete upstream
artifacts were selected and in which order.
