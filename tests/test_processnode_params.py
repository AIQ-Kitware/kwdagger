import kwconf as kw
import pytest

from kwdagger.pipeline import ProcessNode


class DemoCfg(kw.Config):
    src = kw.Value('ignored.txt', tags=['in_path'])
    dst = kw.Value('schema.txt', tags=['out_path', 'primary'])
    extra = kw.Value('', tags=['out_path'])
    foo = 1
    workers = kw.Value(2, tags=['perf_param'])


class DemoNode(ProcessNode):
    name = 'demo'
    params = DemoCfg
    out_paths = {'dst': 'explicit.txt'}


def test_params_schema_derivation():
    with pytest.warns(UserWarning, match='in_path "src"'):
        node = DemoNode()

    assert node.in_paths['src'] is None
    assert node.out_paths['dst'] == 'explicit.txt'
    assert node.algo_params['foo'] == 1
    assert node.perf_params['workers'] == 2
    assert node.primary_out_key == 'dst'

    derived = ProcessNode._derive_groups_from_params_spec(DemoCfg)
    with pytest.warns(UserWarning, match='in_path "src"'):
        legacy = ProcessNode._from_kwconf(DemoCfg, name='demo')

    assert legacy.in_paths == derived[0]
    assert legacy.out_paths == derived[1]
    assert legacy.algo_params == derived[2]
    assert legacy.perf_params == derived[3]
    assert legacy.primary_out_key == derived[4]


def test_params_conflicting_tags():
    class BadCfg(kw.Config):
        foo = kw.Value(1, tags=['in', 'out'])

    with pytest.raises(ValueError, match='conflicting tags'):
        ProcessNode._derive_groups_from_params_spec(BadCfg)
