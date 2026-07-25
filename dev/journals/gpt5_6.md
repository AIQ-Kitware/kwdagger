## 2026-07-25 12:41:19 -0400

Implemented the first compile-time gather design for kwdagger. The central choice is that gather remains an edge cardinality rather than a runtime-discovered node: `GatherSpec(group_by=..., order_by=..., require='all_success')` partitions already-configured source instances and supplies a target input through a generated newline-delimited path manifest. This preserves the property that cmd_queue receives a complete static DAG and can serialize the whole run as Bash.

The difficult part was not manifest writing, but compilation and hashing. The historical scheduler configured one matrix row at a time, which cannot resolve a target whose input spans several rows. Gather pipelines now compile all rows into concrete, deduplicated process instances before queue submission. Gather members and policy are incorporated into the target process identity, and the target has direct dependencies on every selected source job. A recursion bug appeared when the gathered input path depended on the target process ID while `final_algo_config` eagerly resolved all inputs; resolving only unconnected inputs fixed it.

The main risks are compatibility with unusual custom `ProcessNode` subclasses because concrete instances are currently produced with `deepcopy`, and scale when collections contain very large or overlapping membership sets. The normal partitioning case is linear in the source instance count, and the compilation summary reports group count, total memberships, and largest collection. Version 1 intentionally supports only compile-time membership and `all_success`; partial-success snapshots and alternative collection encodings remain future work.

Validation includes Python and YAML API tests, hash and ordering tests, a static fan-out/gather/fan-out execution test, tutorial equivalence coverage, syntax compilation, YAML parsing, and a dependency-light end-to-end harness that executed the generated Bash commands. The full repository test environment could not be installed because the package index did not provide the runtime dependencies, so the complete `run_tests.py` suite still needs to be run in a normal development environment.

## 2026-07-25 tutorial round-trip repair

- Fixed the gather tutorial equivalence test to respect the existing serialization contract: a plain `ProcessNode` is emitted as data and reloads as `YamlProcessNode`, while custom subclasses retain exact class references.
- Added a focused regression test for plain `ProcessNode` YAML round-tripping.
