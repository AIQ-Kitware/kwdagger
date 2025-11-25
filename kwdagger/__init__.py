__version__ = '0.2.1'

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
from kwdagger import aggregate
from kwdagger import aggregate_loader
from kwdagger import aggregate_plots
from kwdagger import demo
from kwdagger import pipeline
from kwdagger import schedule
from kwdagger import utils

from kwdagger.pipeline import (Pipeline, ProcessNode,)

__all__ = ['Pipeline', 'ProcessNode', 'aggregate', 'aggregate_loader',
           'aggregate_plots', 'demo', 'pipeline', 'schedule', 'utils']
