"""
Handles github actions like parameter matrices

The main function of interest here is :func:`expand_param_grid` and
its underlying workhorse: :func:`extended_github_action_matrix`.
"""
import ubelt as ub
import kwutil


def coerce_list_of_action_matrices(arg):
    """
    Preprocess the parameter grid input into a standard form

    CommandLine:
        xdoctest -m kwdagger.utils.util_param_grid coerce_list_of_action_matrices

    Example:
        >>> from kwdagger.utils.util_param_grid import *  # NOQA
        >>> arg = ub.codeblock(
            '''
            matrices:
              - matrix:
                    foo: bar
              - matrix:
                    foo: baz
            '''
            )
        >>> arg = coerce_list_of_action_matrices(arg)
        >>> print(arg)
        >>> assert len(arg) == 2
    """
    if isinstance(arg, str):
        data = kwutil.Yaml.loads(arg)
    else:
        data = arg.copy()
    action_matrices = []
    if isinstance(data, dict):
        if 'matrices' in data:
            data = data["matrices"]
    if isinstance(data, list):
        for item in data:
            action_matrices.append(item)
    elif isinstance(data, dict):
        if not len(ub.udict(data) & {'matrix', 'include'}):
            data = {'matrix': data}
        action_matrices.append(data)
    return action_matrices


def prevalidate_param_grid(arg):
    """
    Determine if something may go wrong
    """

    def validate_pathlike(p):
        if isinstance(p, str):
            p = ub.Path(p)
        else:
            p = ub.Path(p)
        if p.expand().exists():
            return True
        return False

    action_matrices = coerce_list_of_action_matrices(arg)

    # TODO: this doesn't belong in a utils folder.
    # Do we want to inject prevalidation into this process?
    src_pathlike_keys = [
        'trk.pxl.model',
        'trk.pxl.data.test_dataset',
        'crop.src',
        'act.pxl.model',
        'act.pxl.data.test_dataset',
    ]

    logs = []

    def log_issue(k, p, msg):
        logs.append((k, p, msg))
        print(f'Key {k} with {p=} {msg}')

    for item in action_matrices:
        matrix = item.get('matrix', {})
        for k in src_pathlike_keys:
            if k in matrix:
                v = matrix[k]
                v = [v] if not ub.iterable(v) else v
                for p in v:
                    if not validate_pathlike(p):
                        log_issue(k, p, 'might not be a valid path')


def expand_param_grid(arg, max_configs=None):
    """
    Our own method for specifying many combinations. Uses the github actions
    method under the hood with our own

    Args:
        arg (str | Dict):
            text or parsed yaml that defines the grid.
            Handled by :func:`coerce_list_of_action_matrices`.

        max_configs (int | None): if specified restrict to generating
            at most this number of configs.
            NOTE: may be removed in the future to reduce complexity.
            It is easy enough to get this behavior with
            :func:`itertools.islice`.

    Yields:
        dict : a concrete item from the grid

    Example:
        >>> from kwdagger.utils.util_param_grid import *  # NOQA
        >>> arg = ub.codeblock(
            '''
            - matrix:
                trk.pxl.model: [trk_a, trk_b]
                trk.pxl.data.tta_time: [0, 4]
                trk.pxl.data.set_cover_algo: [None, approx]
                trk.pxl.data.test_dataset: [D4_S2_L8]

                act.pxl.model: [act_a, act_b]
                act.pxl.data.test_dataset: [D4_WV_PD, D4_WV]
                act.pxl.data.input_space_scale: [1GSD, 4GSD]

                trk.poly.thresh: [0.17]
                act.poly.thresh: [0.13]

                exclude:
                  #
                  # The BAS A should not run with tta
                  - trk.pxl.model: trk_a
                    trk.pxl.data.tta_time: 4
                  # The BAS B should not run without tta
                  - trk.pxl.model: trk_b
                    trk.pxl.data.tta_time: 0
                  #
                  # The SC B should not run on the PD dataset when GSD is 1
                  - act.pxl.model: act_b
                    act.pxl.data.test_dataset: D4_WV_PD
                    act.pxl.data.input_space_scale: 1GSD
                  # The SC A should not run on the WV dataset when GSD is 4
                  - act.pxl.model: act_a
                    act.pxl.data.test_dataset: D4_WV
                    act.pxl.data.input_space_scale: 4GSD
                  #
                  # The The BAS A and SC B model should not run together
                  - trk.pxl.model: trk_a
                    act.pxl.model: act_b
                  # Other misc exclusions to make the output cleaner
                  - trk.pxl.model: trk_b
                    act.pxl.data.input_space_scale: 4GSD
                  - trk.pxl.data.set_cover_algo: None
                    act.pxl.data.input_space_scale: 1GSD

                include:
                  # only try the 10GSD scale for trk model A
                  - trk.pxl.model: trk_a
                    trk.pxl.data.input_space_scale: 10GSD
            ''')
        >>> grid_items = list(expand_param_grid(arg))
        >>> print('grid_items = {}'.format(ub.urepr(grid_items, nl=1, sort=0)))
        >>> from kwdagger.utils.util_dotdict import dotdict_to_nested
        >>> print(ub.urepr([dotdict_to_nested(p) for p in grid_items], nl=-3, sort=0))
        >>> print(len(grid_items))

    Example:
        >>> # Check that dictionaries are treated as scalar values
        >>> from kwdagger.utils.util_param_grid import *  # NOQA
        >>> arg = ub.codeblock(
            '''
            - matrix:
                trk.param1:
                    key1: value1
                    key2: value2
                trk.param2_list_of_dict:
                    - key1: value1
                      key2: value2
                    - key1: value11
                      key2: value22
            ''')
        >>> grid_items = list(expand_param_grid(arg))
        >>> print('grid_items = {}'.format(ub.urepr(grid_items, nl=1, sort=0)))
        grid_items = [
            {'trk.param1': {'key1': 'value1', 'key2': 'value2'}, 'trk.param2_list_of_dict': {'key1': 'value1', 'key2': 'value2'}},
            {'trk.param1': {'key1': 'value1', 'key2': 'value2'}, 'trk.param2_list_of_dict': {'key1': 'value11', 'key2': 'value22'}},
        ]

    """
    prevalidate_param_grid(arg)  # TODO: may remove prevalidate in the future
    action_matrices = coerce_list_of_action_matrices(arg)
    num_yeilded = 0
    for item in action_matrices:
        for grid_item in extended_github_action_matrix(item):
            yield grid_item
            num_yeilded += 1
            if max_configs is not None:
                if num_yeilded >= max_configs:
                    return


def github_action_matrix(arg):
    """
    Implements the github action matrix strategy exactly as described.

    Unless I've implemented something incorrectly, I believe this method is
    limited and have extended it in :func:`extended_github_action_matrix`.

    Args:
        arg (Dict | str): a dictionary or a yaml file that resolves to a
            dictionary containing the keys "matrix", which maps parameters to a
            list of possible values. For convinieince if a single scalar value
            is detected it is converted to a list of 1 item. The matrix may
            also include an "include" and "exclude" item, which are lists of
            dictionaries that modify existing / add new matrix configurations
            or remove them. The "include" and "exclude" parameter can also be
            specified at the same level of "matrix" for convinience.

    Yields:
        dict: a single entry in the grid.

    References:
        https://docs.github.com/en/actions/using-jobs/using-a-matrix-for-your-jobs#expanding-or-adding-matrix-configurations

    CommandLine:
        xdoctest -m kwdagger.utils.util_param_grid github_action_matrix:2

    Example:
        >>> from kwdagger.utils.util_param_grid import *  # NOQA
        >>> arg = ub.codeblock(
                 '''
                   matrix:
                     fruit: [apple, pear]
                     animal: [cat, dog]
                     include:
                       - color: green
                       - color: pink
                         animal: cat
                       - fruit: apple
                         shape: circle
                       - fruit: banana
                       - fruit: banana
                         animal: cat
                 ''')
        >>> grid_items = list(github_action_matrix(arg))
        >>> print('grid_items = {}'.format(ub.urepr(grid_items, nl=1)))
        grid_items = [
            {'fruit': 'apple', 'animal': 'cat', 'color': 'pink', 'shape': 'circle'},
            {'fruit': 'apple', 'animal': 'dog', 'color': 'green', 'shape': 'circle'},
            {'fruit': 'pear', 'animal': 'cat', 'color': 'pink'},
            {'fruit': 'pear', 'animal': 'dog', 'color': 'green'},
            {'fruit': 'banana'},
            {'fruit': 'banana', 'animal': 'cat'},
        ]

    Example:
        >>> from kwdagger.utils.util_param_grid import *  # NOQA
        >>> arg = ub.codeblock(
                '''
                  matrix:
                    os: [macos-latest, windows-latest]
                    version: [12, 14, 16]
                    environment: [staging, production]
                    exclude:
                      - os: macos-latest
                        version: 12
                        environment: production
                      - os: windows-latest
                        version: 16
            ''')
        >>> grid_items = list(github_action_matrix(arg))
        >>> print('grid_items = {}'.format(ub.urepr(grid_items, nl=1)))
        grid_items = [
            {'os': 'macos-latest', 'version': 12, 'environment': 'staging'},
            {'os': 'macos-latest', 'version': 14, 'environment': 'staging'},
            {'os': 'macos-latest', 'version': 14, 'environment': 'production'},
            {'os': 'macos-latest', 'version': 16, 'environment': 'staging'},
            {'os': 'macos-latest', 'version': 16, 'environment': 'production'},
            {'os': 'windows-latest', 'version': 12, 'environment': 'staging'},
            {'os': 'windows-latest', 'version': 12, 'environment': 'production'},
            {'os': 'windows-latest', 'version': 14, 'environment': 'staging'},
            {'os': 'windows-latest', 'version': 14, 'environment': 'production'},
        ]

    Example:
        >>> from kwdagger.utils.util_param_grid import *  # NOQA
        >>> arg = ub.codeblock(
                 '''
                 matrix:
                   old_variable:
                       - null
                       - auto
                 include:
                     - old_variable: null
                       new_variable: 1
                     - old_variable: null
                       new_variable: 2
                 ''')
        >>> grid_items = list(github_action_matrix(arg))
        >>> print('grid_items = {}'.format(ub.urepr(grid_items, nl=1)))
    """
    if isinstance(arg, str):
        data = kwutil.Yaml.loads(arg)
    else:
        data = arg.copy()

    matrix = data.pop('matrix', {}).copy()

    include = matrix.pop('include', data.pop('include', []))
    exclude = matrix.pop('exclude', data.pop('exclude', []))
    include = list(map(ub.udict, include))
    exclude = list(map(ub.udict, exclude))

    matrix_ = {k: (v if ub.iterable(v) else [v])
               for k, v in matrix.items()}

    orig_keys = set(matrix.keys())
    include_idx_to_nvariants = {idx: 0 for idx in range(len(include))}

    def include_modifiers(mat_item):
        """
        For each object in the include list, the key:value pairs in the object
        will be added to each of the matrix combinations if none of the
        key:value pairs overwrite any of the original matrix values. If the
        object cannot be added to any of the matrix combinations, a new matrix
        combination will be created instead. Note that the original matrix
        values will not be overwritten, but added matrix values can be
        overwritten.
        """
        grid_item = ub.udict(mat_item)
        for include_idx, include_item in enumerate(include):
            common_orig1 = (mat_item & include_item) & orig_keys
            common_orig2 = (include_item & mat_item) & orig_keys
            if common_orig1 == common_orig2:
                include_idx_to_nvariants[include_idx] += 1
                grid_item = grid_item | include_item
        return grid_item

    def is_excluded(grid_item):
        """
        An excluded configuration only has to be a partial match for it to be
        excluded. For example, the following workflow will run nine jobs: one
        job for each of the 12 configurations, minus the one excluded job that
        matches {os: macos-latest, version: 12, environment: production}, and
        the two excluded jobs that match {os: windows-latest, version: 16}.
        """
        for exclude_item in exclude:
            common1 = exclude_item & grid_item
            if common1:
                common2 = grid_item & exclude_item
                if common1 == common2 == exclude_item:
                    return True

    for mat_item in map(ub.udict, ub.named_product(matrix_)):
        grid_item = include_modifiers(mat_item)
        if not is_excluded(grid_item):
            yield grid_item

    for idx, n in include_idx_to_nvariants.items():
        if n == 0:
            grid_item = include[idx]
            yield grid_item


def extended_github_action_matrix(arg):
    """
    A variant of the github action matrix for our mlops framework that
    overcomes some of the former limitations.

    This keeps the same weird include / exclude semantics, but
    adds an additional "submatrix" component that has the following semantics.

    A submatrices is a list of dictionaries, but each dictionary may have more
    than one value, and are expanded into a list of items, similarly to a
    dictionary. In this respect the submatrix is "resolved" to a list of
    dictionary items just like "include". The difference is that when a
    common elements of a submatrix grid item matches a matrix grid item, it
    updates it with its new values and yields it immediately. Subsequent
    submatrix grid items can yield different variations of this item.
    The actions include rules are then applied on top of this.

    Args:
        arg (Dict | str): See github_action_matrix, but with new submatrices

    Yields:
        dict: a single entry in the grid.

    CommandLine:
        xdoctest -m kwdagger.utils.util_param_grid extended_github_action_matrix:2

    Example:
        >>> from kwdagger.utils.util_param_grid import *  # NOQA
        >>> from kwdagger.utils import util_param_grid
        >>> arg = ub.codeblock(
                 '''
                   matrix:
                     fruit: [apple, pear]
                     animal: [cat, dog]
                     submatrices1:
                       - color: green
                       - color: pink
                         animal: cat
                       - fruit: apple
                         shape: circle
                       - fruit: banana
                       - fruit: banana
                         animal: cat
                 ''')
        >>> grid_items = list(extended_github_action_matrix(arg))
        >>> print('grid_items = {}'.format(ub.urepr(grid_items, nl=1)))

    Example:
        >>> from kwdagger.utils.util_param_grid import *  # NOQA
        >>> arg = ub.codeblock(
                '''
                  matrix:
                    os: [macos-latest, windows-latest]
                    version: [12, 14, 16]
                    environment: [staging, production]
                    exclude:
                      - os: macos-latest
                        version: 12
                        environment: production
                      - os: windows-latest
                        version: 16
            ''')
        >>> grid_items = list(extended_github_action_matrix(arg))
        >>> print('grid_items = {}'.format(ub.urepr(grid_items, nl=1)))

    Example:
        >>> from kwdagger.utils.util_param_grid import *  # NOQA
        >>> from kwdagger.utils import util_param_grid
        >>> # Specifying an explicit list of things to run
        >>> arg = ub.codeblock(
                 '''
                 submatrices:
                    - common_variable: a
                      old_variable: a
                    - common_variable: a
                      old_variable: null
                      new_variable: 1
                    - common_variable: a
                      old_variable: null
                      new_variable: 11
                    - common_variable: a
                      old_variable: null
                      new_variable: 2
                    - common_variable: b
                      old_variable: null
                      new_variable: 22
                 ''')
        >>> grid_items = list(extended_github_action_matrix(arg))
        >>> print('grid_items = {}'.format(ub.urepr(grid_items, nl=1)))
        >>> assert len(grid_items) == 5

    Example:
        >>> from kwdagger.utils.util_param_grid import *  # NOQA
        >>> from kwdagger.utils import util_param_grid
        >>> arg = ub.codeblock(
                 '''
                 matrix:
                   common_variable:
                       - a
                       - b
                   old_variable:
                       - null
                       - auto
                 submatrices:
                     - old_variable: null
                       new_variable1:
                           - 1
                           - 2
                       new_variable2:
                           - 3
                           - 4
                     - old_variable: null
                       new_variable2:
                           - 33
                           - 44
                     # These wont be used because blag doesn't exist
                     - old_variable: blag
                       new_variable:
                           - 10
                           - 20
                 ''')
        >>> grid_items = list(extended_github_action_matrix(arg))
        >>> print('grid_items = {}'.format(ub.urepr(grid_items, nl=1)))
        >>> assert len(grid_items) == 14

    Example:
        >>> from kwdagger.utils.util_param_grid import *  # NOQA
        >>> from kwdagger.utils import util_param_grid
        >>> arg = ub.codeblock(
                 '''
                 matrix:
                   step1.src:
                       - dset1
                       - dset2
                       - dset3
                       - dset4
                   step1.resolution:
                       - 10
                       - 20
                       - 30
                 submatrices1:
                    - step1.resolution: 10
                      step2.resolution: [10, 15]
                    - step1.resolution: 20
                      step2.resolution: 20
                 submatrices2:
                    - step1.src: dset1
                      step2.src: big_dset1A
                    - step1.src: dset2
                      step2.src:
                         - big_dset2A
                         - big_dset2B
                    - step1.src: dset3
                      step2.src: big_dset3A
                 ''')
        >>> grid_items = list(extended_github_action_matrix(arg))
        >>> print('grid_items = {}'.format(ub.urepr(grid_items, nl=1)))
        >>> assert len(grid_items) == 20


    Example:
        >>> # Test that __include__ expands YAML files, while plain YAML values remain
        >>> # literal arguments.
        >>> from kwdagger.utils.util_param_grid import *  # NOQA
        >>> dpath = ub.Path.appdir('kwdagger/tests/param_grid').ensuredir()
        >>> # Create a subgrid file that will be included
        >>> subgrid_fpath = dpath / 'subgrid.yaml'
        >>> subgrid_content = [
        ...     'subgrid-value1',
        ...     'subgrid-value2',
        ... ]
        >>> subgrid_fpath.write_text(kwutil.Yaml.dumps(subgrid_content))
        >>> # Create a main grid file that uses __include__
        >>> main_fpath = dpath / 'main.yaml'
        >>> main_content = {
        ...     'matrix': {
        ...         'foo': [
        ...             {'__include__': str(subgrid_fpath)},  # explicit include
        ...             str(subgrid_fpath),                   # should remain literal
        ...         ]
        ...     }
        ... }
        >>> main_fpath.write_text(kwutil.Yaml.dumps(main_content))
        >>> # Load and expand
        >>> grid = kwutil.Yaml.load(main_fpath)
        >>> values = list(extended_github_action_matrix(grid))
        >>> # The include should expand into 2 entries from the subgrid
        >>> print(f'values = {ub.urepr(values, nl=1)}')
        values = [
            {'foo': 'subgrid-value1'},
            {'foo': 'subgrid-value2'},
            {'foo': '.../subgrid.yaml'},
        ]
        >>> # Confirm the literal YAML file is *not* expanded
        >>> assert 'subgrid.yaml' in values[-1]['foo']
        >>> # Confirm expansion order is preserved
        >>> assert values[0]['foo'] == 'subgrid-value1'
        >>> assert values[1]['foo'] == 'subgrid-value2'
    """
    import os
    if isinstance(arg, str):
        data = kwutil.Yaml.loads(arg)
    else:
        data = arg.copy()

    matrix = data.pop('matrix', {}).copy()

    include = matrix.pop('include', data.pop('include', []))
    exclude = matrix.pop('exclude', data.pop('exclude', []))

    submatrices = matrix.pop('submatrices', data.pop('submatrices', []))
    submatrices = list(map(ub.udict, submatrices))

    include = list(map(ub.udict, include))
    exclude = list(map(ub.udict, exclude))

    def coerce_matrix_value(v):
        """
        Normalize values in the param grid / submatrices.

        This now supports an explicit include directive of the form::

            some_param:
              - __include__: path/to/grid.yaml

        or a list of includes::

            some_param:
              - __include__:
                  - path/to/grid1.yaml
                  - path/to/grid2.yaml

        In these cases the referenced YAML file(s) are loaded and their
        contents are spliced into the grid. Plain YAML filenames are now
        treated as literal argument values.
        """
        if not ub.iterable(v) or isinstance(v, dict):
            # I think what we want here is to just check that its not list-like
            # anything that isn't list like should be treated as a "scalar"
            # value, even if its a dictionary.
            v = [v]
        final = []
        for item in v:
            # Explicit include syntax: {'__include__': 'path/to/grid.yaml'}
            if isinstance(item, dict) and set(item.keys()) == {'__include__'}:
                include_val = item['__include__']

                # Allow a single path or a list/tuple of paths
                if isinstance(include_val, (str, os.PathLike)) or not ub.iterable(include_val):
                    include_paths = [include_val]
                else:
                    include_paths = list(include_val)

                for include_path in include_paths:
                    # use Yaml.coerce instead?
                    loaded = kwutil.Yaml.load(include_path)
                    # If the loaded object is a sequence (e.g. list of
                    # matrices), splice it into the grid; otherwise keep it
                    # as a single value.
                    if ub.iterable(loaded) and not isinstance(loaded, (str, bytes)):
                        final.extend(loaded)
                    else:
                        final.append(loaded)
            else:
                final.append(item)
        return final

    # HACK:
    # Special submatrices for more cartesian products, it would be good to come
    # up with a solution that does not require hard coded and a fixed number of
    # variables.
    numbered_submatrices = [
        matrix.pop('submatrices1', data.pop('submatrices1', [])),
        matrix.pop('submatrices2', data.pop('submatrices2', [])),
        matrix.pop('submatrices3', data.pop('submatrices3', [])),
        matrix.pop('submatrices4', data.pop('submatrices4', [])),
        matrix.pop('submatrices5', data.pop('submatrices5', [])),
        matrix.pop('submatrices6', data.pop('submatrices6', [])),
        matrix.pop('submatrices7', data.pop('submatrices7', [])),
        matrix.pop('submatrices8', data.pop('submatrices8', [])),
        matrix.pop('submatrices9', data.pop('submatrices9', [])),
    ]

    MULTI_SUBMATRICES = 1
    if MULTI_SUBMATRICES:
        # Try allowing for more variations. The idea is we effectively
        # want to take the cross product of multiple lists of submatrices.
        multi_submatrices = [submatrices] + numbered_submatrices
        multi_submatrices_ = []
        for submats in multi_submatrices:
            submats[:] = list(map(ub.udict, submats))
            submats_ = []
            for submatrix in submats:
                submatrix_ = {k: coerce_matrix_value(v)
                              for k, v in submatrix.items()}
                submats_.extend(list(map(ub.udict, ub.named_product(submatrix_))))
            multi_submatrices_.append(submats_)
    else:
        submatrices_ = []
        for submatrix in submatrices:
            submatrix_ = {k: coerce_matrix_value(v)
                          for k, v in submatrix.items()}
            submatrices_.extend(list(map(ub.udict, ub.named_product(submatrix_))))

    if len(data) != 0:
        raise Exception(f'Unexpected top level keys: {list(data.keys())}')

    matrix_ = {k: coerce_matrix_value(v)
               for k, v in matrix.items()}

    orig_keys = set(matrix.keys())
    include_idx_to_nvariants = {idx: 0 for idx in range(len(include))}

    def include_modifiers(mat_item):
        """
        For each object in the include list, the key:value pairs in the object
        will be added to each of the matrix combinations if none of the
        key:value pairs overwrite any of the original matrix values. If the
        object cannot be added to any of the matrix combinations, a new matrix
        combination will be created instead. Note that the original matrix
        values will not be overwritten, but added matrix values can be
        overwritten.
        """
        grid_item = ub.udict(mat_item)
        for include_idx, include_item in enumerate(include):
            common_orig1 = (mat_item & include_item) & orig_keys
            common_orig2 = (include_item & mat_item) & orig_keys
            if common_orig1 == common_orig2:
                include_idx_to_nvariants[include_idx] += 1
                grid_item = grid_item | include_item
        return grid_item

    def multisubmatrix_variants(mat_item, multi_submatrices_):
        # New version: every group of submatrices has the opportunity to
        # modify the item before yielding.
        curr_items = [mat_item]
        for submatrices_ in multi_submatrices_:
            curr_items = _submatrix_variants_loop(curr_items, submatrices_)
        yield from curr_items

    def _submatrix_variants_loop(mat_items, submatrices_):
        for item in mat_items:
            yield from submatrix_variants(item, submatrices_)

    def submatrix_variants(mat_item, submatrices_):
        grid_item = ub.udict(mat_item)
        any_modified = False
        for submat_item in submatrices_:
            common_orig1 = (mat_item & submat_item) & orig_keys
            common_orig2 = (submat_item & mat_item) & orig_keys
            if common_orig1 == common_orig2:
                grid_item = mat_item | submat_item
                yield grid_item
                any_modified = True
        if not any_modified:
            yield grid_item

    def is_excluded(grid_item):
        """
        An excluded configuration only has to be a partial match for it to be
        excluded. For example, the following workflow will run nine jobs: one
        job for each of the 12 configurations, minus the one excluded job that
        matches {os: macos-latest, version: 12, environment: production}, and
        the two excluded jobs that match {os: windows-latest, version: 16}.
        """
        for exclude_item in exclude:
            common1 = exclude_item & grid_item
            if common1:
                common2 = grid_item & exclude_item
                if common1 == common2 == exclude_item:
                    return True

    for mat_item in map(ub.udict, ub.named_product(matrix_)):
        if MULTI_SUBMATRICES:
            submat_gen = multisubmatrix_variants(mat_item, multi_submatrices_)
        else:
            submat_gen = submatrix_variants(mat_item, submatrices_)
        for item in submat_gen:
            item = include_modifiers(item)
            if not is_excluded(item):
                yield item

    for idx, n in include_idx_to_nvariants.items():
        if n == 0:
            grid_item = include[idx]
            yield grid_item
