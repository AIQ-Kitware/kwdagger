# 2025-12-05

Documenting high level changes between geowatch.mlops and kwdagger.

* removed the "flat" directory
* changed `group_dname` to `group`
* Removed the deprecated `enabled` argument

# 2025-12-09

There is a problem in the way that parameter grid is expanded. If the actual
input files are YAML files, then the way geowatch.mlops worked is that we would
always try to read it and treat it like an extension to the parameter grid
list. 

We are going to introduce a backwards incompatible change such that we will now
only expand an item if it matches a ``__include__: <yaml-path>`` pattern.
