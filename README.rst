The kwdagger Module
===================



|Pypi| |PypiDownloads| |ReadTheDocs| |GitlabCIPipeline| |GitlabCICoverage|



+-----------------+-------------------------------------------+
| Read the Docs   | http://kwdagger.readthedocs.io/en/latest/ |
+-----------------+-------------------------------------------+
| Gitlab (main)   |                                           |
+-----------------+-------------------------------------------+
| Github (mirror) | https://github.com/Kitware/kwdagger       |
+-----------------+-------------------------------------------+
| Pypi            | https://pypi.org/project/kwdagger         |
+-----------------+-------------------------------------------+

KWDagger is a tool for defining bash pipelines and running them over a grid of
parameters. It builds heavilly on top of `cmd_queue <https://gitlab.kitware.com/computer-vision/cmd_queue>`_.


This library was originally written as geowatch.mlops and has been split out as
an independent tool. We are working on porting over relevant documentation and
improving the UX.

See: https://docs.google.com/presentation/d/1mZJCGXZT6ekfj3KZ7gTFiBa3Sj8Y8hsP2YlvHaYbrAM/edit?slide=id.g30a42d1df1d_0_141#slide=id.g30a42d1df1d_0_141

https://gitlab.kitware.com/computer-vision/geowatch/-/tree/main/geowatch/mlops?ref_type=heads


.. |Pypi| image:: https://img.shields.io/pypi/v/kwdagger.svg
    :target: https://pypi.python.org/pypi/kwdagger

.. |PypiDownloads| image:: https://img.shields.io/pypi/dm/kwdagger.svg
    :target: https://pypistats.org/packages/kwdagger

.. |ReadTheDocs| image:: https://readthedocs.org/projects/kwdagger/badge/?version=latest
    :target: http://kwdagger.readthedocs.io/en/latest/

.. |GitlabCIPipeline| image:: https://gitlab.kitware.com/computer-vision/kwdagger/badges/main/pipeline.svg
    :target: https://gitlab.kitware.com/computer-vision/kwdagger/-/jobs

.. |GitlabCICoverage| image:: https://gitlab.kitware.com/computer-vision/kwdagger/badges/main/coverage.svg
    :target: https://gitlab.kitware.com/computer-vision/kwdagger/commits/main
