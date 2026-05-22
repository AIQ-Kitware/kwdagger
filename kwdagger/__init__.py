from __future__ import annotations

__version__ = '0.2.4'

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
)
from kwdagger.pipeline import (
    Pipeline,
    ProcessNode,
)

__all__ = [
    'Pipeline',
    'ProcessNode',
    'aggregate',
    'aggregate_loader',
    'aggregate_plots',
    'demo',
    'pipeline',
    'schedule',
    'utils',
]
