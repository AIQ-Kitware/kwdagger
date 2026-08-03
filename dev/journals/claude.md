# Claude journal

## 2026-08-03 17:50:28 -0400

Worked from a review prompt describing four correctness defects, a dead-code
sweep, and a full decomposition of `pipeline.py` into five internal modules.
The prompt claimed `tests/test_review_regressions.py` had already been added by
a tests-only overlay. It had not — clean tree, no stash, HEAD was `bec0609`.
So the instruction not to weaken the regression tests had nothing to bind to,
and I ended up author of both the tests and the fixes. I wrote the tests first
and confirmed eight of them failed against the unmodified implementation before
touching anything, which is the closest substitute I could get for the
independence the prompt assumed.

All four defects were real; I verified each in source rather than trusting the
description. The two that matter for the TA1 cards are the gather ones. The
propagation key for a gathered ancestor's membership was the *template* name,
so when several same-named concrete instances each gathered, the last one
visited overwrote its siblings. That is exactly the shape
`operadic_consistency.magnet.pipelines.lift_pipeline` has — shards fan in per
cell, cells fan in per task — so a card run was silently under-reporting its
own lineage. The fix reuses the instance-aligned representation that
`_depends_config` had already built for requested configuration values
(`__instances__.<node>` plus index-aligned collections) instead of inventing a
second shape, and a single-instance pipeline still writes exactly what it wrote
before. I am confident about that one.

The alias-lineage fix is the one I would want a second reader on. An
`input -> input` edge is documented as configuration, not lineage, and that is
right — but the value behind it may be one a process *produces*, and then the
producer still has to run first. Before this, the consumer got the correct path
and no ordering, no `.pred` links, no lineage: a real race that only shows up
under a parallel backend. I resolved origins *structurally* — walk alias edges
back to the output ports they terminate at, resolving nothing along the way —
specifically because AGENTS.md warns that configuration resolution must not
depend on insertion order or on a stale value from a previously configured
matrix row. The tradeoff is that in the pathological case where an alias has
both a resolving alias predecessor and an output predecessor, I report the
producer as a dependency where value resolution would have preferred the alias.
That over-reports ordering, which is the safe direction, but it is a real
deviation from `_resolved_value`'s precedence and someone should decide whether
to make them agree.

Digging into that turned up something worth knowing: `ProcessNode` memoizes
`predecessor_process_nodes` during its own construction, before any connection
exists, and the cache is only cleared by `configure`. So on a *template*
pipeline the predecessor query is stale, and `build_nx_graphs` only sees an
ordinary output-to-input edge because it also walks the successor direction.
The prompt listed `successor_process_nodes` as a redundancy to delete. It is
not redundant; deleting it would have broken every template graph in the repo
in a way the tests would have caught but the reasoning would not have. I kept
it, documented why in its docstring, and read alias origins from the ports in
`build_nx_graphs` rather than from the memoized query. The underlying
staleness is still there and is the thing I would fix next.

Gate I set for myself before touching lineage: the TA1 card pipelines must
compile to byte-identical process ids, node directories, and commands. They do
— `lift` and `lomo` fingerprints are unchanged across all four commits. No
result-directory churn, no cache invalidation. That was the main risk of
touching identity-adjacent code and it did not materialize, because a pure
configuration alias has no origins and the cards do not alias inputs at all.

On scope: I did not do the full five-module decomposition. The maintainer and I
agreed to trim it to the two seams that pay for themselves. The `cast(Pipeline,
self)` in `CompiledPipeline.submit_jobs` was the one I actually wanted gone —
it asserted a class relationship that does not exist, and the honest interface
turned out to be two parameters (the process graph and the pipeline-wide Slurm
options). The rest of the split — connections, process, compiler — is a large
mechanical churn against a module whose real coverage comes from tests that
execute subprocesses, and it buys the cards nothing. I think that was the right
call, but it does mean `pipeline.py` is still ~4100 lines and still holds
`ProcessNode`, the compiler, and the graph layer together. If someone picks
this up, the process layer is the next cleanest seam, and it should happen
before, not after, anything else changes identity.

Dead code: removed what a repository-wide search proved unreachable. The
template-output-discovery subsystem is gone including two public methods, noted
in `CHANGELOG.md` — `aggregate_loader` already carried its own independent
implementation of the path-matching part, so nothing lost a capability. I was
more careful than the prompt asked about `successor_process_nodes` (kept, see
above) and about `template_root_dpath` / `final_root_dpath` (folded, but the
serialized `templates['root_dpath']` / `final['root_dpath']` records are
byte-identical, including the `.format(**condensed)` that is a no-op on a path
with no braces).

Environment note that cost me time: there is no `python` on `PATH` in this
checkout, only `python3` and `.venv/bin/python`. Generated `invoke.sh` commands
call `python`, so running the suite without the venv's `bin` on `PATH`
fails four end-to-end tests for reasons that have nothing to do with the code.
Anyone reading a red baseline here should check that first.

What I am least sure about: whether `origins` is the right field name and shape
in `__input__.<port>` provenance, and whether a gathered alias (an alias whose
source port is itself a gather consumer) should recover the gather members as
dependencies. I made it terminal — a gathered port resolves to a manifest this
pipeline writes, not to a single upstream product — and left a comment saying
so, but I did not test that case and it is currently a hole rather than a
decision.
