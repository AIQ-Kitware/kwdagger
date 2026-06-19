from __future__ import annotations

__version__ = '0.2.5'

__autogen__ = """
mkinit  ~/code/kwdagger/kwdagger/__init__.py -w
"""

__submodules__ = {
    'aggregate': [],
    'schedule': [],
    'demo': [],
    'aggregate_loader': [],
    'aggregate_plots': [],
    'utils': [],
    'pipeline': ['Pipeline', 'ProcessNode'],
    'yaml_pipeline': ['dump_yaml_pipeline', 'load_yaml_pipeline'],
}

###
from kwdagger import (
    aggregate,
    aggregate_loader,
    aggregate_plots,
    demo,
    pipeline,
    schedule,
    utils,
    yaml_pipeline,
)
from kwdagger.pipeline import (
    Pipeline,
    ProcessNode,
)
from kwdagger.yaml_pipeline import (
    dump_yaml_pipeline,
    load_yaml_pipeline,
)

__all__ = [
    'Pipeline',
    'ProcessNode',
    'aggregate',
    'aggregate_loader',
    'aggregate_plots',
    'demo',
    'dump_yaml_pipeline',
    'load_yaml_pipeline',
    'pipeline',
    'schedule',
    'utils',
    'yaml_pipeline',
]
