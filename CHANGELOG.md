# Changelog
We [keep a changelog](https://keepachangelog.com/en/1.0.0/).
We aim to adhere to [semantic versioning](https://semver.org/spec/v2.0.0.html).

## Version 0.2.5 - Unreleased


## Version 0.2.4 - Released 2026-06-19


## Version 0.2.3 - Released 2026-03-26


## Version 0.2.2 - Released 2026-01-15

### Added

* Support deriving ProcessNode IO/parameter groups from a scriptconfig schema via the new ``params`` class variable.

### Changed

* Increased hash size to reduce collision chance. This is a backwards incompatible change.


## Version 0.2.1 - Released 2026-01-07


### Changed

* YAML paths in grid values no longer auto-expand unless explicitly behind an `__include__` key. See docs for details.

### Fixed

* Dictionaries now work correctly as "scalar" values in a parameter grid


## [Version 0.0.1] -

### Added
* Initial version
