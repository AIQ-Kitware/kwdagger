## 2026-07-25 12:41:19 -0400

Implemented the first compile-time gather design for kwdagger. The central choice is that gather remains an edge cardinality rather than a runtime-discovered node: `GatherSpec(group_by=..., order_by=..., require='all_success')` partitions already-configured source instances and supplies a target input through a generated newline-delimited path manifest. This preserves the property that cmd_queue receives a complete static DAG and can serialize the whole run as Bash.

The difficult part was not manifest writing, but compilation and hashing. The historical scheduler configured one matrix row at a time, which cannot resolve a target whose input spans several rows. Gather pipelines now compile all rows into concrete, deduplicated process instances before queue submission. Gather members and policy are incorporated into the target process identity, and the target has direct dependencies on every selected source job. A recursion bug appeared when the gathered input path depended on the target process ID while `final_algo_config` eagerly resolved all inputs; resolving only unconnected inputs fixed it.

The main risks are compatibility with unusual custom `ProcessNode` subclasses because concrete instances are currently produced with `deepcopy`, and scale when collections contain very large or overlapping membership sets. The normal partitioning case is linear in the source instance count, and the compilation summary reports group count, total memberships, and largest collection. Version 1 intentionally supports only compile-time membership and `all_success`; partial-success snapshots and alternative collection encodings remain future work.

Validation includes Python and YAML API tests, hash and ordering tests, a static fan-out/gather/fan-out execution test, tutorial equivalence coverage, syntax compilation, YAML parsing, and a dependency-light end-to-end harness that executed the generated Bash commands. The full repository test environment could not be installed because the package index did not provide the runtime dependencies, so the complete `run_tests.py` suite still needs to be run in a normal development environment.

## 2026-07-25 tutorial round-trip repair

- Fixed the gather tutorial equivalence test to respect the existing serialization contract: a plain `ProcessNode` is emitted as data and reloads as `YamlProcessNode`, while custom subclasses retain exact class references.
- Added a focused regression test for plain `ProcessNode` YAML round-tripping.


## 2026-07-25 13:17:35 -0400

Strengthened compile-time gather around kwdagger's central portability promise.
The first implementation generated manifests in a generic ``before_*``
bookkeeper job. Although static, that made the exported job list look as if the
consumer depended on opaque preparation, and the per-node ``invoke.sh`` omitted
the manifest writer. Gather materialization now lives inside the consumer's
actual shell command as a quoted heredoc, and the same complete command is
written to ``invoke.sh``. This avoids command-length limits because collection
members are parsed as script input rather than passed through argv.

Graph diagnostics were also too lossy: the logical Process and IO graphs showed
a gather edge as if it were ordinary one-to-one wiring. Display-only gather
markers now expose ``group_by``, ``order_by``, and the collection-manifest
boundary. After all matrix rows compile, a second logical graph reports concrete
instance cardinalities, including gather fan-in and ordinary fan-out, before
queue submission. These display nodes are deliberately not execution nodes.

Added tests for graph visibility, compiled 3:1 and 1:2 cardinalities, very large
quoted-heredoc manifests, and independently rerunning a gathered consumer's
``invoke.sh`` after deleting its manifest and output. Updated the tutorial and
AGENTS.md to record the static-DAG, standalone-Bash, deterministic hashing, and
runtime-discovery boundaries as repository invariants.

## 2026-07-25 14:02:00 -0400

Followed the quoted-heredoc design through the supported scheduler backends and
found an important qualification: a heredoc avoids ``ARG_MAX`` only after Bash
is reading a script. Cmd-queue's Slurm backend normally sends the entire job as
one ``sbatch --wrap`` argument, so embedding a large gather there would recreate
the same submission-time limit. Gathered Slurm consumers now use a short
``bash invoke.sh`` payload; kwdagger materializes the visible standalone
``invoke.sh`` and large provenance config while compiling the queue. The
manifest itself remains runtime materialization inside the invocation script.

This introduces an intentional distinction between a single-file serial export
and a transparent Slurm script bundle. I believe this better preserves the core
value than pretending the generated queue driver is always sufficient by
itself: every execution detail is inspectable and can run without kwdagger, but
backends with argv-based command transport use file-backed scripts. Tests now
assert both the large-heredoc behavior and the short Slurm command. The main
remaining scale boundary is the scheduler's own dependency list for extremely
large fan-in, which is separate from manifest transport and should be measured
before adding another abstraction.
