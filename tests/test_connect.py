"""
The connection model: one primitive, and one convenience that resolves to it.

``_connect_port`` is the only thing that creates an edge, and the only legal
edges are ``output -> input``, ``input -> input``, and ``param -> param``.
``ProcessNode.connect`` is sugar for the common linear shape -- a stage's
outputs and the next stage's inputs named alike -- and resolves to a set of
those pairs before anything is mutated.

This file exists because the previous implementation fused the two: it added a
process-level edge nothing read, manufactured a dictionary key so a
port-to-port connection could be "discovered" by name matching, and paired the
matched names *positionally*, which silently crossed the ports of any two nodes
that enumerated the same names in a different order.
"""

from __future__ import annotations

import pytest

from kwdagger.pipeline import Pipeline, ProcessNode


def _node(name, in_paths=None, out_paths=None, algo_params=None):
    return ProcessNode(
        name=name,
        executable=f'python {name}.py',
        in_paths=in_paths or set(),
        out_paths=out_paths or {},
        algo_params=algo_params or {},
    )


# ---------------------------------------------------------------------------
# Node-level connection resolves to port pairs, by name
# ---------------------------------------------------------------------------


def test_matching_names_connect_and_nothing_else():
    src = _node('src', out_paths={'shared': 'a.txt', 'private': 'b.txt'})
    dst = _node('dst', in_paths={'shared', 'unrelated'})
    src.connect(dst)
    assert [p.name for p in dst.inputs['shared'].pred] == ['shared']
    assert dst.inputs['unrelated'].pred == []
    assert src.outputs['private'].succ == []


def test_several_matching_names_pair_by_name_not_by_position():
    """
    The defect this file is named for. Two nodes may enumerate the same names
    in different orders -- a set-declared ``in_paths`` makes the order
    arbitrary -- and the old ``zip`` over two independently ordered dicts wired
    ``alpha`` to ``beta``.
    """
    src = _node('src', out_paths={'alpha': 'a.txt', 'beta': 'b.txt'})
    dst = _node('dst', in_paths={'beta': None, 'alpha': None})
    assert list(src.outputs) != list(dst.inputs), 'setup: differing orders'
    src.connect(dst)
    for name in ['alpha', 'beta']:
        assert [p.name for p in dst.inputs[name].pred] == [name]


def test_a_node_connection_ignores_algorithm_parameters():
    """
    A parameter is not data the node reads, so it is not part of ``inputs``
    and a node-level connection never forwards one. Sharing a parameter is an
    explicit ``param -> param`` edge.
    """
    src = _node('src', out_paths={'thresh': 'a.txt'}, algo_params={'thresh': 1})
    dst = _node('dst', in_paths={'other'}, algo_params={'thresh': 2})
    with pytest.raises(ValueError, match='share no port name'):
        src.connect(dst)
    assert dst.param_ports['thresh'].pred == []


def test_no_shared_name_names_both_sides():
    src = _node('src', out_paths={'only_out': 'a.txt'})
    dst = _node('dst', in_paths={'only_in'})
    with pytest.raises(ValueError) as excinfo:
        src.connect(dst)
    message = str(excinfo.value)
    assert 'only_out' in message and 'only_in' in message


def test_one_endpoint_may_be_a_port():
    """Pinning one side is the same rule with one name fixed."""
    src = _node('src', out_paths={'shared': 'a.txt', 'other': 'b.txt'})
    dst = _node('dst', in_paths={'shared'})
    src.connect(dst.inputs['shared'])
    assert [p.name for p in dst.inputs['shared'].pred] == ['shared']

    src2 = _node('src2', out_paths={'shared': 'a.txt'})
    dst2 = _node('dst2', in_paths={'shared', 'other'})
    src2.outputs['shared'].connect(dst2)
    assert [p.name for p in dst2.inputs['shared'].pred] == ['shared']


# ---------------------------------------------------------------------------
# Only three port pairs mean anything
# ---------------------------------------------------------------------------


def test_the_three_legal_edges_are_accepted():
    a = _node(
        'a', in_paths={'x'}, out_paths={'x': 'x.txt'}, algo_params={'p': 1}
    )
    b = _node(
        'b', in_paths={'x'}, out_paths={'x': 'x.txt'}, algo_params={'p': 2}
    )
    a.outputs['x'].connect(b.inputs['x'])
    a.inputs['x'].connect(b.inputs['x'])
    a.param_ports['p'].connect(b.param_ports['p'])
    assert len(b.inputs['x'].pred) == 2
    assert [p.name for p in b.param_ports['p'].pred] == ['p']


@pytest.mark.parametrize(
    'source_port, target_port',
    [
        ('outputs', 'outputs'),
        ('inputs', 'outputs'),
        ('param_ports', 'inputs'),
        ('inputs', 'param_ports'),
        ('outputs', 'param_ports'),
    ],
)
def test_a_meaningless_port_pair_is_refused(source_port, target_port):
    """
    These used to be recorded silently and then mean nothing to anything that
    reads the graph -- or, for the parameter cases, fail later during
    configuration, somewhere else entirely.
    """
    a = _node(
        'a', in_paths={'x'}, out_paths={'x': 'x.txt'}, algo_params={'x': 1}
    )
    b = _node(
        'b', in_paths={'x'}, out_paths={'x': 'x.txt'}, algo_params={'x': 2}
    )
    source = getattr(a, source_port)['x']
    target = getattr(b, target_port)['x']
    with pytest.raises(TypeError, match='may not supply'):
        source.connect(target)
    assert target.pred == []


# ---------------------------------------------------------------------------
# A process holds no edges of its own
# ---------------------------------------------------------------------------


def test_a_process_has_no_ports_of_its_own_to_connect():
    """
    Process-level ``pred`` / ``succ`` used to be written alongside the port
    edges and read by nothing. Lineage is derived from the ports.
    """
    src = _node('src', out_paths={'shared': 'a.txt'})
    dst = _node('dst', in_paths={'shared'})
    src.connect(dst)
    assert not hasattr(src, 'succ')
    assert not hasattr(dst, 'pred')
    assert dst.predecessor_process_nodes() == [src]
    assert src.successor_process_nodes() == [dst]


def test_a_failed_connection_leaves_nothing_behind():
    """
    Resolution and validation happen for every target before any edge is
    added, so a call that raises part-way through does not half-connect.
    """
    src = _node('src', out_paths={'shared': 'a.txt'})
    good = _node('good', in_paths={'shared'})
    bad = _node('bad', in_paths={'nothing_shared'})
    with pytest.raises(ValueError, match='share no port name'):
        src.connect(good, bad)
    assert good.inputs['shared'].pred == []
    assert src.outputs['shared'].succ == []


def test_connecting_twice_does_not_duplicate_an_edge():
    src = _node('src', out_paths={'shared': 'a.txt'})
    dst = _node('dst', in_paths={'shared'})
    src.connect(dst)
    src.connect(dst)
    assert len(dst.inputs['shared'].pred) == 1


def test_the_resulting_graph_is_what_the_pipeline_sees(tmp_path):
    """The sugar and the explicit ports build the same pipeline."""
    ids = []
    for explicit in [False, True]:
        src = _node('src', out_paths={'shared': 'a.txt'})
        dst = _node('dst', in_paths={'shared'}, out_paths={'out': 'o.txt'})
        if explicit:
            src.outputs['shared'].connect(dst.inputs['shared'])
        else:
            src.connect(dst)
        dag = Pipeline({'src': src, 'dst': dst})
        dag.configure({}, root_dpath=tmp_path, cache=False)
        assert dag.proc_graph.has_edge('src', 'dst')
        ids.append(dst.process_id)
    assert ids[0] == ids[1]
