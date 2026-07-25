Compile-Time Gather Tutorial
============================

This tutorial demonstrates a static fan-out, gather, and second fan-out:

.. code:: text

    train[algorithm, seed, fold]
        -> build_ensemble[algorithm, seed]
        -> evaluate[algorithm, seed, test_set]

Every concrete job and dependency is known before execution. The gather edge
only changes how configured source instances are connected to a target input;
it does not discover jobs or mutate the DAG at runtime.

Run the example
---------------

From this directory:

.. code:: bash

    ./run_pipeline.sh

Use ``--run=0`` in ``run_pipeline.sh`` to inspect the complete cmd-queue Bash
script without executing it.

YAML definition
---------------

``train`` produces one checkpoint for each algorithm, seed, and fold.
``build_ensemble`` consumes a collection of checkpoint paths. The mapping edge
uses ``gather`` instead of an ordinary one-to-one connection:

.. code:: yaml

    edges:
      - src: train.checkpoint_fpath
        dst: build_ensemble.checkpoints_fpath
        gather:
          group_by: [algorithm, seed]
          order_by: [fold]
          require: all_success

``group_by`` has the same intuition as a dataframe group-by. Source instances
with the same algorithm and seed are placed in one collection. ``fold`` is not
a grouping key, so it varies within each collection. ``order_by`` controls the
line order in the generated path manifest. Version 1 requires all source jobs
to succeed before the consumer can run.

The equivalent Python connection is:

.. code:: python

    train.outputs['checkpoint_fpath'].connect(
        build_ensemble.inputs['checkpoints_fpath'],
        gather=kwdagger.GatherSpec(
            group_by=['algorithm', 'seed'],
            order_by=['fold'],
            require='all_success',
        ),
    )

Generated command interface
---------------------------

The target executable still receives an ordinary ``--key=value`` argument:

.. code:: bash

    python gather_demo.py ensemble \
        --algorithm=linear \
        --seed=0 \
        --checkpoints_fpath=results/build_ensemble/.../_gather/checkpoints_fpath.txt \
        --ensemble_fpath=results/build_ensemble/.../ensemble.txt

The manifest writer is embedded in the ensemble's complete execution command
and in its ``invoke.sh``. It is not an opaque runtime node. The executable
artifact contains a quoted heredoc, similar to::

    # kwdagger gather: train.checkpoint_fpath -> \
    # build_ensemble.checkpoints_fpath | members=3 | ...
    mkdir -p results/build_ensemble/.../_gather
    cat > results/build_ensemble/.../_gather/checkpoints_fpath.txt \
        <<'KWDAGGER_GATHER_BUILD_ENSEMBLE_CHECKPOINTS_FPATH_<HASH>'
    results/train/.../checkpoint.txt
    results/train/.../checkpoint.txt
    results/train/.../checkpoint.txt
    KWDAGGER_GATHER_BUILD_ENSEMBLE_CHECKPOINTS_FPATH_<HASH>

    python gather_demo.py ensemble \
        --algorithm=linear \
        --seed=0 \
        --checkpoints_fpath=results/build_ensemble/.../_gather/checkpoints_fpath.txt \
        --ensemble_fpath=results/build_ensemble/.../ensemble.txt

The quoted heredoc body is script input rather than a list of command-line
arguments, so executing this file does not consume the operating system's
``ARG_MAX`` budget in proportion to the collection size. Quoting the delimiter
also prevents shell expansion inside the manifest body.

There is one backend-specific distinction. Serial and tmux exports are
file-backed Bash scripts already, so the complete heredoc can appear directly
in their generated job text. Slurm normally transports each job through
``sbatch --wrap``, whose payload is itself a command-line argument. For a
gathered Slurm consumer, kwdagger therefore materializes the visible
``invoke.sh`` artifact during queue compilation and submits only::

    bash results/build_ensemble/.../invoke.sh

This keeps the Slurm submission command short while preserving the same
standalone execution artifact. The manifest itself is still created by
``invoke.sh`` when the job runs; kwdagger does not precompute or hide it.

The resulting file contains one path per line:

.. code:: text

    results/train/.../checkpoint.txt
    results/train/.../checkpoint.txt
    results/train/.../checkpoint.txt

The ensemble job has ordinary cmd-queue dependencies on all three training
jobs. The manifest is a compact CLI representation of those known dependencies,
not a runtime query over the result directory.

Compile preview and graph cardinality
-------------------------------------

The logical Process Graph inserts a display-only ``gather N:1`` marker between
``train`` and ``build_ensemble``. The IO Graph shows the conversion from
``checkpoint_fpath`` outputs into a collection-valued path manifest input.
These markers explain edge semantics; they are not executable process nodes.

After matrix compilation, kwdagger prints a concrete cardinality graph before
queue submission. For this tutorial it reports:

.. code:: text

    train [12 instances]
        -> gather 3:1 | 12 -> 4 instances
        -> build_ensemble [4 instances]
        -> fan-out 1:2 | 4 -> 8 instances
        -> evaluate [8 instances]

Kwdagger also reports the number of concrete nodes, collection groups,
collection memberships, and the largest collection. Here there are four
algorithm/seed groups with three fold members each. This makes accidental
collection expansion visible before any commands run.

Expected artifacts
------------------

Each ensemble result directory contains:

.. code:: text

    build_ensemble/<process-id>/
    ├── _gather/
    │   └── checkpoints_fpath.txt
    ├── ensemble.txt
    ├── invoke.sh
    └── job_config.json

``job_config.json`` records the gather policy and exact source process IDs in
addition to the normal resolved parameters. Changing collection membership or
ordering changes the target process ID.

``invoke.sh`` includes both the quoted-heredoc manifest writer and the ensemble
command. After the upstream checkpoints exist, it can be copied or run without
invoking kwdagger. Cmd-queue's exported serial Bash contains every command
inline. A Slurm export is a transparent script bundle: its submission script
calls the generated per-node ``invoke.sh`` files, which can also be executed
directly.

Why the parameter file uses separate matrices
---------------------------------------------

The source and target both have ``algorithm`` and ``seed`` parameters. They
must vary in lockstep rather than as independent Cartesian products, so
``params.yaml`` uses one matrix for each algorithm/seed pair. Within each
matrix, ``fold`` fans out before the gather and ``test_set`` fans out after it.
