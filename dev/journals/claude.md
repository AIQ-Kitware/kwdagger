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

## 2026-08-03 18:12:00 -0400

The tests-only overlay I said was missing showed up: the maintainer pulled
`7924762` and it collided with the file I had written at the same path. Both
sides "added" `tests/test_review_regressions.py`.

Before resolving anything I ran the incoming tests against my implementation
unchanged: 4 passed. Against `bec0609`: 4 failed. So the two of us converged on
the same four defects and on semantics that agree, which is the independent
confirmation I explicitly said I could not get when I wrote my own tests. That
is a much better result than I expected -- the incoming test for structured
group values is *stronger* than mine (it uses a qualified **mapped** group key
with a nested dict value, where mine used an unmapped qualified key), and my
recursive canonicalizer handles it without modification.

Resolution: the incoming file stays byte-identical at its path, and mine moved
to `test_review_regressions_extra.py`. I did not merge them into one file on
purpose. The incoming file is deliberately shape-agnostic -- it searches the
provenance record recursively for gather records rather than naming a key, and
uses `nx.has_path` rather than asserting a specific edge -- which is exactly
what makes it credible as a specification written without knowledge of the
implementation. Keeping it untouched preserves that property and makes it
obvious to a reviewer that it was not bent to fit what I built. My file now
says up front that it pins the representations the other file leaves open, plus
the complements (a pure configuration alias must stay configuration-only),
which is where the real risk of the alias change lives.

One thing worth noting for whoever reviews this: the incoming test asserts
`nx.has_path(dag.proc_graph, 'producer', 'consumer')`, which the pre-existing
`producer -> middle` edge alone would satisfy if `middle -> consumer` were also
an edge. It is not -- an alias must not create that edge -- so the assertion
does bite. My file asserts the direct `producer -> consumer` edge and the
absence of `middle -> consumer` explicitly, so between the two the intent is
fully pinned.

## 2026-08-03 19:05:00 -0400

Did the decomposition after all, as a package rather than as `_pipeline_*.py`
siblings. `kwdagger/pipeline.py` is now `kwdagger/pipeline/`, which is the
better shape: the namespace users already import from becomes the facade, and
the internals get real names instead of a prefix convention.

Layering, bottom up: `_shell` (shell text) and `_slurm` (option coercion) are
leaves because both the process layer and the runtime submitter need them and
neither should import the other to get there; `_runtime`; `_connections`
(ports, edges, gather specs); `_process` (`ProcessNode`); `_compile`;
`_logical` (`Pipeline`, graph views, coercion). Only one upward reference
exists — `_compile` names `Pipeline` in a signature — and it stays behind
`TYPE_CHECKING`, so there is no runtime cycle to work around. No submodule
imports the facade.

I wanted the split to be *provably* mechanical rather than
mechanical-looking, so I checked it two ways. First, line coverage: every line
of the old file lands in exactly one segment, and the only gaps were the three
lines holding `@dataclass(frozen=True)` and the trailing `__getattr__`. Second,
an AST comparison of all 48 top-level definitions old versus new — the only
differences were the two intended import-path renames inside `submit_jobs`.

The first check earned its keep immediately. My initial slice started at
`class GatherSpec:` and silently dropped the decorator on the line above, so
`GatherSpec` stopped being a frozen dataclass and lost `__eq__`. Three tests
caught it, but the failure mode is worth remembering: a line-range split can
decapitate a decorated definition and the result still imports cleanly.

Star imports in doctests were the other real hazard. `from kwdagger.pipeline
import *` on a module pulled in `ub`, `nx`, and everything else the module
imported; on a package `__init__` it does not. Rather than paper over that by
re-importing `ubelt` in the facade, I replaced each one with the explicit
imports that doctest actually uses. Doctest counts are unchanged (88 passed,
14 skipped, plus the pre-existing `util_kwplot` failure), so nothing lost
coverage.

`tests/test_import_compat.py` now encodes the layering as an ordered list and
fails if any module imports something at or above it. That is the part I most
want to survive: boundaries that are not enforced stop being true within a
release or two. It also asserts the one upward reference is annotation-only, so
if someone promotes it to a runtime import the test says why that is a problem.

Checked the things a package split can quietly break: setuptools discovery
(`packages.find.include = ["kwdagger*"]` picks up `kwdagger.pipeline`
automatically — verified with `find_packages`, not assumed), and the TA1 card
fingerprints, which are still byte-identical. 156 tests pass.

`_process.py` is still 1750 lines, and that is the honest remaining problem.
`ProcessNode` does configuration resolution, identity, provenance, path
templating, command construction, and gather materialization. Splitting it is a
real design question rather than a relocation, so I left it whole rather than
guess at a seam.

## 2026-08-03 20:40:00 -0400

GPT-5.6 reviewed the branch and found two P1s. Both are real; I reproduced each
before changing anything, and the first is a regression I introduced.

**Recovered producers reached scheduling but not identity.** Two producers that
run the same algorithm over different data have the same `algo_id` and
different `process_id`. The ancestor payload in `depends` records only
`algo_id`, and the `__input__` identity binding only accepted
`source_kind == 'output'`, so a consumer behind an alias had nothing tying it
to a specific producer instance. Two consumers with visibly different commands
hashed to one `process_id` and one result directory.

What makes this mine: before my alias change, `final_input_config` still
contained the aliased input, and a produced path has the producer's
`process_id` in it, so the consumers were distinguished by accident. My
`_produced_origins` exclusion removed that anchor and put nothing in its place.
I checked this rather than assumed it -- ran the same probe against `bec0609`
in a worktree (having to strip the editable-install finder out of
`sys.meta_path` to get the right kwdagger imported) and got distinct ids
there, colliding ids at HEAD.

The lesson I should have applied: when you remove something from an identity
payload, the question is not "was this the right thing to hash" but "what was
it doing that nothing else does". A produced path is bad identity material
because it embeds a cache root, but it was also carrying the producer's
instance, and only the first half of that was replaced.

Identity bindings are now built from `_produced_origins` directly rather than
filtered out of the provenance dict, so the same helper answers scheduling,
provenance, and identity. Direct output-to-input identity is unchanged --
sorted by the same key as before, and the TA1 fingerprints are still
byte-identical, which is the check I trust most here.

**Aliasing a gathered input.** I flagged this as an untested hole in the first
entry and left it. That was the wrong call: it is not an ambiguity, it is a
race. The borrower gets the path to a manifest that the *lending* job writes as
part of its own command, with no queue dependency, so under tmux or Slurm it
can read a file that does not exist. Serial happens to survive because compile
order usually puts the writer first, which is exactly the kind of accident that
hides a bug until someone changes backend.

The fix follows the model rather than fighting it. A gathered port is not an
ordinary alias: the manifest is genuinely produced by the job that owns the
port, so that port is returned as an origin and its process becomes a real
dependency. The traversal stops there instead of recursing to the members --
whatever they are, they are already that job's own ancestors, so depending on
the writer is both sufficient and minimal. `_origin_kind` distinguishes
`'output'` from `'gather_manifest'` so identity and provenance do not conflate
a declared output path with a generated manifest.

Both now have tests. The first varies only an upstream external input and
asserts the consumers stay distinct, which is the assertion the review asked
for and the one my original tests were missing: everything I wrote used a
producer with no inputs of its own, so `algo_id` and `process_id` moved
together and the gap was invisible. Worth remembering that a fixture too simple
to distinguish two mechanisms will not test either.

## 2026-08-03 22:15:00 -0400

Third review round. Three findings, all reproduced before I touched anything,
and this time the interesting part was that two of them were one bug.

**Identity ignored an override on a connected input.** Two rows configuring
`consumer.data_fpath` to different paths produced different commands, one
`process_id`, one result directory. Checked provenance before assuming: it
collides at `bec0609` and does *not* collide at `v0.2.6`, so this arrived with
the `final_input_config` split earlier on the branch rather than with my work.
My alias change widened the same hole to forwarded values without creating it.

The review offered "reject the combination" as an option. That one is not
available -- explicit-beats-produced is documented precedence in
`_resolved_value`, and pointing a stage at a precomputed artifact is a real
workflow. So the resolver it is.

**The two bugs are one design gap.** Identity was asking a structural question
("what could supply this port?") where it needed an effective one ("what does?").
The template graph had the mirror problem: it asked about `_gather_members`,
which only exists after compilation, where it needed `_gather_connection`,
which exists as soon as the edge is drawn. Structural belongs to the template,
effective belongs to identity, and both were reaching for the wrong one.
`_produced_origins` and `_effective_origins` now say which is which in their
names and docstrings, because this is the kind of distinction that erodes
silently.

The maintainer asked whether an override should also drop the *scheduling*
edge. I looked rather than guessed: the compiled graph is rebuilt per row from
`predecessor_process_nodes()` on configured nodes, so dropping it there is one
call site -- but the single-row `configure()` path takes its queue
dependencies from `Pipeline.proc_graph`, which is the template graph and is
never rebuilt after configure. Dropping the edge would make the two execution
routes disagree about the DAG, and reconciling them means rebuilding the graph
after configure, which is where the memoization staleness lives. So: keep the
edge, fix identity. Over-ordering is harmless; the collision is not. That was
the maintainer's own instinct about structural baking, and it was right --
just more specifically true than "structural": it is structural *at template
time*.

**The staleness, finally.** Fixing the template graph left
`ancestor_process_nodes()` still empty, because that was never about origins --
it was the construction-time memoization I wrote a lesson about two entries ago
and deferred. `build_nx_graphs` is the first moment the complete connection
state exists, so it now clears every node's cache before reading. Three lines.
I should have done it when I found it rather than documenting it as future
work; leaving a known-wrong answer in place because it was not the bug I was
chasing is how the next person inherits it.

That also falsified my own docstring on `successor_process_nodes`, which said
the two directions in `build_nx_graphs` were not redundant *because* of the
staleness. With the cache cleared they are redundant. I corrected the docstring
and superseded the lesson rather than leaving a confident claim that is no
longer true -- and I am still not removing the method, because it is public API
and that is a separate decision from whether it is load-bearing.

**Same-node gather alias** now raises a `ValueError` naming both ports at
pipeline construction, instead of `RecursionError` deep in identity. It had to
land in this commit rather than after: teaching the template side to recognize
`_gather_connection` would otherwise have turned the recursion into a
`merge -> merge` self-edge and a confusing DAG-validation failure. I did not
try to design what that composition should mean, because nothing asks for it.

The reviewer's last item -- `git diff --check v0.2.6..HEAD` failing on a
trailing blank line in `_runtime.py` -- does not reproduce. That command exits
0 here and the file ends with a single newline after `return summary`. Probably
a checkout from before the package split, when it was
`kwdagger/_pipeline_runtime.py`.

TA1 fingerprints are still byte-identical, which continues to be the check that
tells me whether I have moved something I did not mean to.

## 2026-08-04 00:30:00 -0400

Fourth review round. Two findings, both real, both reproduced first.

**My last fix was half a fix.** `_effective_origins` asked the precedence
question of the consumer's own port and then handed off to `_alias_origins`,
which is deliberately structural. So an override on the consumer was honoured
and an override one alias hop upstream was not: `producer -> lender -> consumer`
with the override on `lender` still collided. Precedence is not a property of
a port, it is a question you have to ask of every source in the chain, and I
built the recursive case out of a non-recursive helper. `_supplying_ports`
now carries the recursion, and the one place that genuinely differs -- a
gathered port is an origin to a borrower but has no origin of its own -- is
stated there rather than inferred.

**The identity-ancestry finding was mine to own too.** `depends` folds in every
ancestor's `algo_id`, and ancestry was structural, so a producer whose output
was overridden before anyone read it still moved the consumer's `process_id`.
Same command, different result directory; a producer sweep fans out identical
consumer jobs. I had fixed the per-input binding last round and stopped there,
which left ports effective and ancestry structural -- internally inconsistent
in exactly the way that invites the next bug.

Worth recording how I first read this: as the cost of the maintainer's decision
to keep the conservative scheduling edge, and therefore as something to defer
rather than fix. That was wrong, and the review was clearer than I was. The
edge decision is about *scheduling*, where over-ordering is free. Identity is a
different question asked at a different time -- always on a configured node,
where the effective answer is knowable. Applying the structural/effective split
at ancestry is the same split we already made at the port level, one level up,
not a workaround for the compromise. The maintainer chose to fix it and was
right to.

The result is three answers where there was one, and they need to stay
distinguishable: `predecessor_process_nodes` (structural, scheduling),
`effective_predecessor_process_nodes` (identity), and the template graph, which
is structural because it must be -- nothing is configured when it is built. I
gave each a docstring saying which question it answers and why, because the
failure mode here is not a wrong line of code, it is someone reaching for
whichever helper is closest.

One thing I deliberately did not do: `_depends_config` still starts from
structural ancestry. AGENTS.md calls `job_config.json` the record of the
*requested* experiment, and a producer that was scheduled is part of what was
requested even if its output went unread. What was actually wrong there was the
contradiction -- the record claimed the producer supplied the input while also
recording the override. The wiring stays, marked `supplied: false`.

TA1 fingerprints byte-identical again, which is what I would expect: the cards
never override a connected input, so effective and structural coincide for
them. That is also why none of these four rounds of identity bugs would have
shown up in the work this branch exists to support -- worth remembering that a
green fingerprint means "did not change what I care about", not "is correct".

## 2026-08-04 02:10:00 -0400

Fifth round, and the first one I had a genuine back-and-forth with the reviewer
about rather than just implementing.

The finding: two matrix rows that compile to one consumer can be wired behind
different producers, and canonicalization kept the first row's structural
predecessors. Reversing the matrix changed which producer the surviving
consumer was attached to -- and since a disabled predecessor suppresses its
successor, that decided whether the consumer ran at all. Same command, same
process_id, opposite outcome. Reproduced both orders: `skipped` versus
`new_submission`.

Two things I found while reproducing that the reviewer could not have known
without running it, and that turned out to matter. Their repro as written
cannot execute -- `compile_configurations` rejects gather-free pipelines and
`build_schedule` only takes the full-matrix path when a gather exists -- so a
gather has to be present somewhere. And the single-row path does not share the
defect, because it has no canonical instance and re-gates per row. I raised
both, along with having tested their "cleaner long-term" option: one line, and
it makes both orders identical *and* correct.

Where the dialog earned its keep was the question I asked at the end. I
proposed compiled-path-effective plus single-row-path-conservative as an
acceptable release boundary. The reviewer said no, and the reason was better
than my reasoning: it would make the same configured consumer run in one
pipeline shape and be skipped in another, depending on whether an unrelated
gather elsewhere pushed `build_schedule` onto the full-matrix path. That is a
semantic split, not conservatism. I had been treating "conservative" as
self-evidently safe, and it is not, because the gate is not an ordering hint --
it is a hard existence check that can suppress valid work. Once that is true,
"conservative" and "wrong" are the same thing.

So the split is now: structural for the template graph, configuration
diagnostics, and requested wiring provenance; effective for everything a
configured command actually does -- gating, queue dependencies, `.pred` /
`.succ` links, and the compiled graph. They asked me to audit every runtime use
rather than patch the `will_exist` expression, which was right: the links block
was still calling the structural query directly, and would have written a
`.pred` entry for a producer the result never read.

One thing the reviewer's spec caught that my first attempt missed. After fixing
the graph, statuses and process_id matched across row orders but provenance
still did not: `supplied: false` named whichever producer instance the
canonical consumer happened to be wired to. The fix is not to union the
instances but to stop naming one. An unsupplied source is a statement about
*wiring*, which is a template fact -- so it records the port, `producer.data_fpath`,
and no `process_id`. Naming a concrete instance there was always meaningless,
and only stopped being obviously so because nothing had read it.

Also worth writing down: `condensed` still walks structural predecessors, so a
custom `node_dpath` template that interpolates another node's id could still
pull an unread producer into a path. I left it, because changing it moves
result directories and nothing asks for it, but it is the one place the
structural/effective boundary is still drawn by inertia rather than by
argument.

TA1 fingerprints identical for the fifth time. That streak is starting to feel
like evidence of nothing: the cards never override a connected input, so every
bug this review process has found sits outside what the fingerprint can see.

## 2026-08-04 04:30:00 -0400

The maintainer caught that the reviewer and I had been wrong for three rounds
in the same direction, and gave an authoritative identity model. Writing down
what happened, because the failure was mine and it was a reasoning failure
rather than a coding one.

**The mistake.** Starting from a real bug -- two consumers reading different
files hashing alike -- I concluded that the consumer's identity had to record
*which producer* supplied its input. That produced `_origin_identity_bindings`,
`__input__.<port>` records carrying `source_process_id` / `source_port` /
`source_kind`, and eventually effective-ancestry `algo_id` folding. Each round
the reviewer confirmed the direction and asked for more of it, and I supplied
it.

**Why it seemed right.** The symptom really was under-identification, and
lineage really does distinguish the colliding cases. It also felt principled:
"produced artifacts define execution lineage" is in AGENTS.md, and I read that
as a statement about identity when it is a statement about *scheduling*. The
thing I never questioned was the premise underneath the original bug --
`final_input_config` excluded produced inputs, so the effective value was
missing from the hash and something had to stand in for it. I reached for
lineage as the substitute instead of asking why the value was absent.

**The actual defect** was that exclusion. Produced paths were being kept out of
identity because they are cache-rooted, and the fix for a missing *value* was
to put the value back, not to hash the provenance of the value. Once
`__inputs__` carries every effective input, a producer reaches its consumer
through the path it writes -- which contains its own `process_id` -- and every
collision I was chasing is handled without a single lineage field.

**What that conflated.** Provenance answers "how was this obtained"; identity
answers "what will this compute". They are different questions and I merged
them, which is why the fixes kept generating new problems at the seams: a
producer sweep fanning out identical consumers, row-order-dependent
canonicalization, path templates disagreeing with identity. Those were not
separate bugs. They were the same category error surfacing in four places.

**The tradeoff, stated plainly** so nobody re-repairs it: a produced path and
the same path typed by hand now hash identically. That is *not* an assertion
that the bytes are equal. kwdagger's data identity is value/path based unless
the user supplies an explicit content identifier, and the docs now say so in a
warning rather than leaving it implied.

**Compatibility.** Ancestor-id placeholders in `node_dpath` are gone. They were
documented in `hashing_scheme.rst` but did not work: a node configures itself
during construction, before any connection exists, so `{producer_id}` raised
`KeyError` there first, and setting the template afterwards was ignored. So
nothing working was removed -- but the mechanism behind them, `condensed`
walking predecessors, was live and was the last route by which an unread
producer could change where a node's results land. Removing it also removed the
construction-time memoization staleness I have written about twice: nothing
asks a lineage question during `__init__` any more.

**TA1 fingerprints changed**, for the first time in six rounds, and the shape
is exactly right: `per_question_features` and `extract_model_scores` -- the
nodes with no produced inputs -- are unchanged, and every node downstream of a
produced or gathered input moved. Node counts, command shapes, and predecessor
counts are identical, so the DAG is the same and only the hashes moved. Worth
saying that the five previous byte-identical fingerprints were not evidence of
correctness: the cards never override a connected input, so every bug in this
whole review sequence lived outside what that check can see.

**What I would do differently.** When a reviewer confirms my direction three
times and the fixes keep spawning adjacent problems, that is the signal to
re-examine the premise rather than to keep extending. I had the evidence in
hand -- I wrote in an earlier entry that `condensed` pulling structural
ancestors into paths was "drawn by inertia rather than by argument" -- and
treated it as a loose end instead of as the contradiction it was.

The invariant is now in `AGENTS.md` with an explicit "do not add producer ids
to consumer hashes" instruction, in `hashing_scheme.rst` with a worked example
and the byte-equality warning, and in the docstrings of every helper that
answers one of the three questions. `tests/test_identity_model.py` pins the
matrix: produced vs manual equality, two producers exposing one path, different
paths, producer-derived path changes, four delivery mechanisms, overridden
connections, row reversal over the complete record, and a guard that fails if
anything ever reaches the command without reaching identity.

## 2026-08-04 05:40:00 -0400

Cleaned up after the identity change: `ty`, `ruff check`, `ruff format`, and a
wider `flake8` selection all pass now.

Two of the three `ty`/`flake8` findings were debris from the package split
rather than from the identity work. The script that generated each new module's
import header computed what each segment *used*, which counted names that are
only imported locally inside functions -- so `_compile` and `_logical` got
module-level `util_dotdict` and `os` that nothing at module scope wanted, and
which then shadowed the real local imports (F811). Worth noting as a hazard of
mechanical splitting: the code ran fine and the tests passed, so only a linter
was ever going to catch it. `CompiledPipeline` was the mirror image -- declared
as a return type but never imported, invisible because
`from __future__ import annotations` makes the annotation a string.

`_origin_kind` was genuinely dead: its only caller was
`_origin_identity_bindings`, which the identity correction removed. Deleted
rather than left as a helper with no question to answer.

One finding I did not "fix". Ruff reports F823 in `util_kwplot.build_collections`
-- a local `import matplotlib.collections` next to attribute access on the
module-level `mpl` alias. That idiom is correct: importing the submodule is what
makes `mpl.collections` resolvable. I called the method to confirm it works
before deciding, then suppressed the rule with the reasoning written down.
Changing working code to satisfy a linter would have been the worse outcome, and
the next person deserves to know which it was.

Also resolved a loose end I had been misreporting for several entries: the
`util_kwplot.py Palette:0` doctest failure is not a code defect, it is
`ModuleNotFoundError: No module named 'kwimage'` -- an optional dependency
missing from this environment. I had been carrying it as "pre-existing failure"
without ever reading the reason.

## 2026-08-04 07:20:00 -0400

Three integration consequences of the identity correction, all confirmed by
reproduction before fixing, plus a set of docstrings that still described the
discarded model.

**Absolute cache roots had entered identity.** Putting effective input values
back into the hash brought the root they sit under with them, so the same
pipeline under `/cache/a` and `/cache/b` produced different downstream ids. I
noticed this risk while implementing and decided to "flag it and proceed"; the
reviewer was right that it contradicts the gather contract in `AGENTS.md`
outright. Paths under the kwdagger root now hash relative to it, external paths
hash as given, and a hand-supplied path pointing inside the root canonicalizes
exactly like a produced one -- so the fix costs nothing of the produced/manual
equality it might have threatened.

**My collision guard outlawed `perf_params`.** I wrote "equal process_id
implies equal command" as an invariant and asserted it. But `perf_params` are
excluded from identity *by design* and do change the command -- so a matrix
sweeping `workers` would have failed with "Internal consistency error", telling
a user their configuration was an internal defect. The existing
`test_perf_params_are_not_identity_bearing` only escaped because it compiles one
row at a time. That is what an over-strong invariant costs: it does not fail in
tests, it fails on a real user's matrix.

The correction is that `perf_params` and output-path overrides belong in the
same family as `__enabled__` and Slurm options -- declared state identity cannot
arbitrate, so rows sharing an identity must *agree* on it, reported as a
user-facing `ValueError`. The docs now name the exceptions instead of asserting
an invariant with holes in it.

**Delivery mechanism is the third thing identity cannot arbitrate.** Now that a
produced path and the same manual path are one computation, two rows can be the
same process and still need different jobs first. I rejected rather than
unioned. Union is defensible -- depending on the producer is conservative, and
`will_exist` handles the already-exists case -- but I have twice this week
reached for a clever aggregate and been wrong about a seam, so a clear error is
the better default. The message names both rows and both prerequisite sets, and
the aggregation option is written down for whoever wants it.

TA1 ids moved again, only for nodes downstream of a produced or gathered input,
which is the root-relative canonicalization landing. Structure unchanged.

Two process notes worth keeping. First: this is the fifth review round, and the
reviewer has now caught three things I saw and set aside. The pattern is not
that I miss them -- it is that I treat "known and noted" as equivalent to
"handled". A journal entry is not where a risk goes to be resolved.

Second, a plain mistake: I ran the TA1 fingerprint from the parent repository
and the shell stayed there, so the previous commit for this work landed in
`aiq-eval-runner` instead of `kwdagger` -- taking two deliberately-uncommitted
submodule pointers with it. Reset and redone here. `cd` inside a long-running
session is state, and I should treat an absolute path as the default rather
than assuming where I am.

## 2026-08-04 09:05:00 -0400

The reviewer found that every safeguard I had built for identity conflicts
lived in the gather compiler, and `build_schedule` only compiles the full
matrix when a gather exists. Ordinary pipelines configure and submit a row at
a time, dedup by "is this process_id already in the queue", and label the
second request `duplicate_submission` without comparing anything. So a
gather-free matrix sweeping `predict.workers` over 4 and 16 queued whichever
row came first and silently dropped the other -- reproduced in both orders.

That is the second time this week I fixed something in one of two code paths
and reported it as fixed. The first was the runtime gate. Both times the
second path was reachable and I had already been told the paths differ; in
this case I wrote the sentence "a pipeline containing *any* gather is compiled
across the whole matrix first" in a test comment three commits ago. Knowing the
split exists is not the same as checking both sides of it, and "where else does
this decision get made?" is now a question I should be asking before claiming
a fix, not after a reviewer asks it.

The fix moves arbitration into `_agreement.py`, a leaf both scheduling paths
can import -- they sit at opposite ends of the layering, so neither could have
owned it. It works on *snapshots* rather than nodes because the row-at-a-time
scheduler reconfigures one `ProcessNode` object in place: read the state lazily
and you compare a request against itself. The registry hangs off the queue,
which is the object that survives between rows regardless of who drives the
loop.

One thing that only showed up because the reviewer listed `__enabled__`
explicitly: my first placement of the check was at the dedup site, which a
disabled node never reaches -- `submit_jobs` short-circuits it at the top of
the loop. So enabled/disabled conflicts still passed silently until I moved the
snapshot to the very top, before anything can disable a node or `skip_existing`
can rewrite its state. Taking the snapshot early also means what gets compared
is what the user asked for rather than what the scheduler decided.

Root canonicalization was scalar-only, so a structured input -- a list or
mapping of produced paths -- kept the absolute cache root in the hash. It is
now recursive, and containment is decided by path components rather than string
prefix, so `/cache/a-backup` is no longer treated as living inside `/cache/a`.
It only rewrites strings that look like paths, so a bare parameter value is
never captured even when the working directory happens to sit inside the root.

The remaining P2 was documentation still teaching the discarded model in four
more places -- `parameter_identity.rst`, `yaml_pipeline_spec.rst`, two
docstrings, and a CHANGELOG entry that flatly contradicted the entry above it
by saying scheduling stays structural. Those are exactly the sentences a future
reviewer would cite while putting lineage back, which is the whole reason the
maintainer asked for the invariant to be written down in the first place. I
corrected the model in the code and in two documents and then stopped looking;
"grep for every place that states the old rule" should have been part of the
original change.

TA1 fingerprints unchanged this round: the cards use absolute roots and scalar
paths, so neither the recursive canonicalization nor the new arbitration
touches them.
