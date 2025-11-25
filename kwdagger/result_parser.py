"""
This is a very task-specific file containing logic to parse fusion pipeline
metrics for BAS and SC.

Used by ./aggregate_loader.py
"""
import json
import re
from kwutil import util_time


# Do we need to memoize this?
def parse_json_header(fpath):
    """
    Ideally the information we need is in the first few bytes of the json file
    """
    from kwdagger.utils import ijson_ext
    import zipfile
    if zipfile.is_zipfile(fpath):
        # We have a compressed json file, but we can still read the header
        # fairly quickly.
        zfile = zipfile.ZipFile(fpath)
        names = zfile.namelist()
        assert len(names) == 1
        member = names[0]
        # Stream the header directly from the zipfile.
        file = zfile.open(member, 'r')
    else:
        # Normal json file
        file = open(fpath, 'r')

    with file:
        # import ijson
        # We only expect there to be one info section
        # try:
        #     # Try our extension if the main library fails (due to NaN)
        #     info_section_iter = ijson.items(file, prefix='info')
        #     info_section = next(info_section_iter)
        # except ijson.IncompleteJSONError:
        # Try our extension if the main library fails (due to NaN)
        # file.seek(0)

        # Nans are too frequent, only use our extension
        info_section_iter = ijson_ext.items(file, prefix='info')
        info_section = next(info_section_iter)
    return info_section


def _handle_process_item(item):
    """
    Json data written by the process context has changed over time slightly.
    Consolidate different usages until a consistent API and usage patterns are
    established.

    """
    assert item['type'] in {'process', 'process_context'}
    props = item['properties']

    needs_modify = 0

    config = props.get('config', None)
    args = props.get('args', None)
    if config is None:
        # Use args if config is not available
        config = args
        needs_modify = True

    FIX_BROKEN_SCRIPTCONFIG_HANDLING = 1
    if FIX_BROKEN_SCRIPTCONFIG_HANDLING:
        if '_data' in config:
            config = config['_data']
            needs_modify = True
        if '_data' in args:
            args = args['_data']
            needs_modify = True

    assert 'pred_info' not in item, 'should be in extra instead'

    if needs_modify:
        import copy
        item = copy.deepcopy(item)
        item['properties']['config'] = config
        item['properties']['args'] = args

    return item


class Found(Exception):
    pass


def _add_prefix(prefix, dict_):
    return {prefix + k: v for k, v in dict_.items()}


def parse_resource_item(item, arg_prefix='', add_prefix=True):
    import kwutil
    resources = {}
    ureg = kwutil.util_units.unit_registry()
    pred_prop = item['properties']

    start_time = util_time.coerce_datetime(pred_prop.get('start_timestamp', None))
    end_time = util_time.coerce_datetime(pred_prop.get('end_timestamp', pred_prop.get('stop_timestamp', None)))
    iters_per_second = pred_prop.get('iters_per_second', None)
    if start_time is None or end_time is None:
        total_hours = None
    else:
        total_hours = (end_time - start_time).total_seconds() / (60 * 60)
    resources['total_hours'] = total_hours
    if iters_per_second is not None:
        resources['iters_per_second'] = iters_per_second

    if 'duration' in pred_prop:
        resources['duration'] = pred_prop['duration']

    try:
        vram = pred_prop['device_info']['allocated_vram']
        vram_gb = ureg.parse_expression(f'{vram} bytes').to('gigabytes').m
        resources['vram_gb'] = vram_gb
    except KeyError:
        ...

    hardware_parts = []

    if 'machine' in pred_prop:
        cpu_name = pred_prop['machine']['cpu_brand']
        if cpu_name is not None:
            cpu_name = re.sub('.*Gen Intel.R. Core.TM. ', '', cpu_name)
        else:
            cpu_name = 'unknown'
        resources['cpu_name'] = cpu_name
        hardware_parts.append(cpu_name)

    try:
        gpu_name = pred_prop['device_info']['device_name']
        resources['gpu_name'] = gpu_name
        hardware_parts.append(gpu_name)
    except KeyError:
        ...

    if 'emissions' in pred_prop:
        co2_kg = pred_prop['emissions']['co2_kg']
        kwh = pred_prop['emissions']['total_kWH']
        resources['co2_kg'] = co2_kg
        resources['kwh'] = kwh

    if 'disk_info' in pred_prop:
        disk_type = pred_prop['disk_info']['filesystem']
        resources['disk_type'] = disk_type

    resources['hardware'] = ' '.join(hardware_parts)
    if add_prefix:
        resources = _add_prefix(arg_prefix + 'resource.', resources)
    return resources


# @ub.memoize
def _load_json(fpath):
    # memo hack for development
    with open(fpath, 'r') as file:
        data = json.load(file)
    return data
