"""
Logic for loading raw results from the MLops DAG root dir.

Used by ./aggregate.py
"""
import ubelt as ub
from kwutil import util_pattern
from kwutil import util_parallel
from kwdagger.utils import util_dotdict
import parse
import json


def build_tables(root_dpath, dag, io_workers, eval_nodes,
                 cache_resolved_results):
    import pandas as pd
    from kwutil import util_progress

    io_workers = util_parallel.coerce_num_workers(io_workers)
    print(f'io_workers={io_workers}')

    # Hard coded nodes of interest to gather. Should abstract later.
    # The user-defined pipelines should be responsible for providing the
    # methods needed to parse their outputs.
    node_eval_infos = [
        {'name': 'bas_pxl_eval', 'out_key': 'eval_pxl_fpath'},
        {'name': 'sc_poly_eval', 'out_key': 'eval_fpath'},
        {'name': 'bas_poly_eval', 'out_key': 'eval_fpath'},
        {'name': 'sv_poly_eval', 'out_key': 'eval_fpath'},
    ]
    lut = ub.udict({info['name']: info for info in node_eval_infos})

    DEVFLAG = 1
    if DEVFLAG:
        for node_name in eval_nodes:
            node = dag.nodes[node_name]
            if node_name not in lut:
                if len(node.out_paths) == 1:
                    primary_out_key = list(node.out_paths)[0]
                elif getattr(node, 'primary_out_key', None) is not None:
                    primary_out_key = node.primary_out_key
                else:
                    raise Exception(ub.paragraph(
                        '''
                        evaluation nodes must have a single item in out_paths
                        or define a primary_out_key
                        '''))
                node_eval_infos.append({
                    'name': node.name,
                    'out_key': primary_out_key,
                })

    lut = ub.udict({info['name']: info for info in node_eval_infos})

    if eval_nodes is None:
        node_eval_infos_chosen = node_eval_infos
    else:
        try:
            node_eval_infos_chosen = list(lut.take(eval_nodes))
        except Exception as ex:
            from kwutil.util_exception import add_exception_note
            raise add_exception_note(ex, ub.paragraph(
                f'''
                Unknown evaluation node. Evaluation nodes need to be
                connected to a function that can parse their results.

                Requested evaluation nodes were: {eval_nodes}.
                But available nodes are {list(lut.keys())}.
                '''))

    from concurrent.futures import as_completed
    pman = util_progress.ProgressManager(backend='rich')
    # pman = util_progress.ProgressManager(backend='progiter')
    with pman:
        eval_type_to_results = {}

        eval_node_prog = pman.progiter(node_eval_infos_chosen, desc='Loading node results')

        for node_eval_info in eval_node_prog:
            node_name = node_eval_info['name']
            out_key = node_eval_info['out_key']

            if node_name not in dag.nodes:
                continue

            node = dag.nodes[node_name]
            out_node = node.outputs[out_key]

            fpaths = out_node_matching_fpaths(out_node)

            # Pattern match
            # node.template_out_paths[out_node.name]
            cols = {
                'index': [],
                'metrics': [],
                'requested_params': [],
                'resolved_params': [],
                'specified_params': [],
                'other': [],
                'fpath': [],
                # 'json_info': [],
            }

            executor = ub.Executor(mode='process', max_workers=io_workers)
            jobs = []
            submit_prog = pman.progiter(
                fpaths, desc=f'  * submit load jobs: {node_name}',
                transient=True)
            for fpath in submit_prog:
                job = executor.submit(load_result_worker, fpath, node_name,
                                      node=node, dag=dag,
                                      use_cache=cache_resolved_results)
                jobs.append(job)

            num_ignored = 0
            job_iter = as_completed(jobs)
            del jobs
            collect_prog = pman.progiter(
                job_iter, total=len(fpaths),
                desc=f'  * loading node results: {node_name}')
            for job in collect_prog:
                result = job.result()
                if result['requested_params'] or True:
                    assert set(result.keys()) == set(cols.keys())
                    for k, v in result.items():
                        cols[k].append(v)
                else:
                    num_ignored += 1

            if num_ignored:
                print(f'num_ignored = {ub.urepr(num_ignored, nl=1)}')

            results = {
                'fpath': pd.DataFrame(cols['fpath'], columns=['fpath']),
                'index': pd.DataFrame(cols['index']),
                'metrics': pd.DataFrame(cols['metrics']),
                'requested_params': pd.DataFrame(cols['requested_params'], dtype=object),  # prevents nones from being read as nan
                'specified_params': pd.DataFrame(cols['specified_params']),
                'resolved_params': pd.DataFrame(cols['resolved_params'], dtype=object),
                'other': pd.DataFrame(cols['other']),
            }
            # print(results['resolved_params']['resolved_params.sc_poly.smoothing'])
            eval_type_to_results[node_name] = results

    return eval_type_to_results


def load_result_worker(fpath, node_name, node=None, dag=None, use_cache=True):
    """
    Main driver for loading results

    Args:
        fpath (str | PathLike):
            path to the primary output file of a pipeline node

        node_name (str):
            The name of the node (todo: deprecate and just require the node
            object).

        node (None | kwdagger.pipeline.Node):
            The node corresponding to the actual process.
            Note: will be required in the future.

        dag (None | kwdagger.pipeline.Pipeline):
            Used to lookup loading functions for predecessor nodes.
            Will be required in the future.

        use_cache (bool):
            if True, check for a cached resolved result, otherwise we need to
            iterate through the dag ancestors to gather full context.

    Returns:
        Dict: result
            containing keys:
                'fpath', 'index', 'metrics', 'requested_params',
                'resolved_params', 'specified_params', 'other'

    Example:
        >>> from kwdagger.aggregate_loader import *  # NOQA
        >>> from kwdagger.demo.demodata import run_demo_schedule
        >>> from kwdagger import pipeline
        >>> # Run a demo evaluation so data is populated
        >>> info = run_demo_schedule()
        >>> # Grab relevant information about one of the evaluation outputs
        >>> eval_dpath = info['eval_dpath']
        >>> dag = pipeline.coerce_pipeline(info['pipeline'])
        >>> dag.configure(root_dpath=eval_dpath)
        >>> node_name = 'stage1_evaluate'
        >>> node = dag.nodes[node_name]
        >>> out_key = node.primary_out_key
        >>> out_node = node.outputs[out_key]
        >>> fpaths = out_node_matching_fpaths(out_node)
        >>> assert len(fpaths) > 0
        >>> fpath = fpaths[0]
        >>> use_cache = False
        >>> result = load_result_worker(fpath, node_name, node=node, dag=dag, use_cache=use_cache)
    """
    import safer
    import rich
    from kwutil import util_json
    from kwutil.util_exception import add_exception_note
    fpath = ub.Path(fpath)

    resolved_json_fpath = fpath.parent / 'resolved_result_row_v012.json'

    use_cache_decision = False
    if use_cache and resolved_json_fpath.exists():
        use_cache_decision = True
        # Experimental feature to ensure results are up to date (not sure if it
        # will cause headaches, if not make it non experimental).
        USE_CACHE_TIMESTAMP_CHECK = True
        if USE_CACHE_TIMESTAMP_CHECK:
            if fpath.exists():
                # If the timestamp of the result file is newer than the cache
                # file, invalidate the cache. TODO: could use a
                # ubelt.CacheStamp for this?
                if fpath.stat().st_mtime > resolved_json_fpath.stat().st_mtime:
                    use_cache_decision = False

            else:
                print('warning: underlying data is missing, but the cache exists... could be in a weird state')

    if use_cache_decision:
        # Load the cached row data
        try:
            result = json.loads(resolved_json_fpath.read_text())
        except Exception as ex:
            raise add_exception_note(ex, f'Failed to read {resolved_json_fpath!r}')
    else:
        node_dpath = fpath.parent

        node_dpath = ub.Path(node_dpath)
        # Read the requested config
        job_config_fpath = node_dpath / 'job_config.json'
        if job_config_fpath.exists():
            try:
                job_config_text = job_config_fpath.read_text()
                _requested_params = json.loads(job_config_text)
            except Exception as ex:
                raise add_exception_note(ex, f'Failed to parse json job config {job_config_fpath}')
        else:
            _requested_params = {}

        requested_params = util_dotdict.DotDict(_requested_params).add_prefix('params')
        specified_params = {'specified.' + k: 1 for k in requested_params}

        # Read the resolved config
        # (Uses the DAG to trace the result lineage)
        try:
            flat = load_result_resolved(node_dpath, node=node, dag=dag)

            HACK_FOR_REGION_ID = True
            if HACK_FOR_REGION_ID:
                # Munge data to get the region ids we expect
                candidate_keys = list(flat.query_keys('region_ids'))
                region_ids = None
                for k in candidate_keys:
                    region_ids = flat[k]
                if region_ids is None:
                    if 0:
                        msg = (ub.paragraph(
                            '''
                            Warning: no region ids available, some assumptions may
                            be violated.
                            '''))
                        import warnings
                        warnings.warn(msg)
                    region_ids = 'unknown'

            resolved_params_keys = list(flat.query_keys('resolved_params'))
            metrics_keys = list(flat.query_keys('metrics'))
            resolved_params = flat & resolved_params_keys
            metrics = flat & metrics_keys

            other = flat - (resolved_params_keys + metrics_keys)

            index = {
                'node': node_name,
                'region_id': region_ids,
            }
            result = {
                'fpath': fpath,
                'index': index,
                'metrics': metrics,
                'requested_params': requested_params,
                'resolved_params': resolved_params,
                'specified_params': specified_params,
                'other': other,
            }

            # Cache this resolved row data
            result = util_json.ensure_json_serializable(result)
        except Exception as ex:
            rich.print(f'[red]Failed to load results for: {node_name}')
            rich.print(f'node_dpath={str(node_dpath)!r}')
            rich.print('ex = {}'.format(ub.urepr(ex, nl=1)))
            raise

        with safer.open(resolved_json_fpath, 'w') as file:
            json.dump(result, file, indent=4)

    return result


def load_result_resolved(node_dpath, node=None, dag=None):
    """
    Recurse through the DAG filesytem structure and load resolved
    configurations from each step.

    Args:
        node_dpath (str | PathLike):
            the path to the evaluation node directory. The specific type of
            evaluation node must have a known (currently hard-coded) condition
            in this function that knows how to parse it.

        node (None | ProcessNode):
            new experimental way to allow users to specify how results should
            be loaded. The node should have a "load_result" function that
            accepts node_dpath as a single argument and then returns a flat
            resolved dotdict of hyperparams, metrics, and context.

        dag (None | Pipeline):
            Used to lookup loading functions for predecessor nodes.

    Returns:
        Dict - flat_resolved - a flat dot-dictionary with resolved params

    TODO:
        Some mechanism to let the user specify how to parse an evaluation node
        of a given type.

    Ignore:
        >>> # To diagnose issues, construct a path to an evaluation node to get the
        >>> # relevant project-specific entrypoint data.
        >>> # TODO: need a demo pipeline that we can test for robustness here.
        >>> from kwdagger.aggregate_loader import *  # NOQA
        >>> from kwdagger.aggregate_loader import load_result_resolved
        >>> import kwdagger
        >>> import rich
        >>> expt_dpath = kwdagger.find_dvc_dpath(tags='phase3_expt')
        >>> # choose the location mlops schedule dumped results to
        >>> mlops_dpath = expt_dpath / '_preeval20_bas_grid'
        >>> # Search and pick a poly eval node, the specific path
        >>> # will depend on the pipeline structure, which may be revised
        >>> # in the future. At the start of SMART phase3, we keep all
        >>> # eval nodes grouped in eval/flat, so enumerate those
        >>> node_type_dpaths = list(mlops_dpath.glob('eval/flat/*'))
        >>> node_type_dpaths += list(mlops_dpath.glob('pred/flat/*'))
        >>> # For each eval node_type, choose a node in it.
        >>> for node_type_dpath in node_type_dpaths:
        >>>     for node_dpath in node_type_dpath.ls():
        >>>         if len(node_dpath.ls()) > 2:
        >>>             print(f'Found node_dpath={node_dpath}')
        >>>             break
        >>>     print(f'node_dpath={node_dpath}')
        >>>     flat_resolved = load_result_resolved(node_dpath)
        >>>     rich.print(f'flat_resolved = {ub.urepr(flat_resolved, nl=1)}')
    """
    # from kwdagger.utils.util_dotdict import explore_nested_dict
    node_dpath = ub.Path(node_dpath)
    node_type_dpath = node_dpath.parent
    node_type = node_type_dpath.name

    if dag is not None:
        if node is None:
            try:
                node = dag.nodes[node_type]
            except KeyError:
                print(f'node_dpath = {ub.urepr(node_dpath, nl=1)}')
                print(f'node_type = {ub.urepr(node_type, nl=1)}')
                print(f'dag.nodes = {ub.urepr(dag.nodes, nl=1)}')
                raise

    if node is not None and hasattr(node, 'load_result'):
        flat_resolved = node.load_result(node_dpath)
        if flat_resolved is None:
            raise AssertionError('node.load_result should have returned a dict')

    else:
        return {}
        raise NotImplementedError(ub.paragraph(
            f'''
            Attempted to load a result for {node_type} in {node_dpath}.
            But was unable to determine how to do so.
            In your pipeline class, define a method ``def load_result(self,
            node_dpath):`` which returns a flat dot-dictionary of params and
            results from the node. <TODO> point at single source of truth for
            how we expect the return type of load results.
            '''))

    # Determine if this node has any predecessor computations and load results
    # from those as well to have a flat and complete picture of the process
    # lineage for this node.
    predecessor_dpath = node_dpath / '.pred'
    for predecessor_node_type_dpath in predecessor_dpath.glob('*'):
        # predecessor_node_type = predecessor_node_type_dpath.name
        for predecessor_node_dpath in predecessor_node_type_dpath.glob('*'):
            if predecessor_node_dpath.exists():
                try:
                    predecessor_flat_resolved = load_result_resolved(predecessor_node_dpath, dag=dag)
                except FileNotFoundError:
                    print('Warning: ancestor information was not found in the filesystem graph')
                else:
                    flat_resolved |= predecessor_flat_resolved

    return flat_resolved


def out_node_matching_fpaths(out_node):
    out_template = out_node.template_value
    parser = parse.Parser(str(out_template))
    patterns = {n: '*' for n in parser.named_fields}
    pat = out_template.format(**patterns)
    mpat = util_pattern.Pattern.coerce(pat)
    fpaths = list(mpat.paths())
    return fpaths


def new_process_context_parser(proc_item):
    """
    Load parameters out of data saved by a ProcessContext object
    """
    from kwdagger import result_parser
    proc_item = result_parser._handle_process_item(proc_item)
    props = proc_item['properties']

    # Node-specific hacks
    params = props['config']
    resources = result_parser.parse_resource_item(proc_item, add_prefix=False)

    output = {
        # TODO: better name for this
        'context': {
            'task': props['name'],
            'uuid': props.get('uuid', None),
            'start_timestamp': props.get('start_timestamp', None),
            'stop_timestamp': props.get('stop_timestamp', props.get('end_timestamp', None)),
        },
        'resolved_params': params,
        'resources': resources,
        'machine': props.get('machine', {}),
    }
    return output


if 1:
    import numpy as np
    if np.bool_ is not bool:
        # Hack for a ubelt issue
        @ub.hash_data.register(np.bool_)
        def _hashnp_bool(data):
            from ubelt.util_hash import _int_to_bytes
            # warnings.warn('Hashing ints is slow, numpy is preferred')
            hashable = _int_to_bytes(bool(data))
            # hashable = data.to_bytes(8, byteorder='big')
            prefix = b'INT'
            return prefix, hashable
