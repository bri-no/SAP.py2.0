"""
external_constraints.py

Handles the "External constraints" parameter category: for each of the
6 DOFs (U1,U2,U3,R1,R2,R3), the user decides whether it is a
variable-stiffness spring or a rigid restraint.

STRUCTURAL DIFFERENCE FROM OTHER PARAMETER MODULES: unlike
material_frame.py / material_shell.py / internal_constraints.py, this
module does NOT separate "setup" (Phase A) from "define values"
(Phase B) into two independently callable functions. Per an explicit
UX request, the workflow is a single loop per node group:

    select nodes -> choose DOF spring/restraint -> apply restraint
    -> immediately ask the stiffness value(s) for THIS group's variable
       DOFs (single value if single_apply, range if dataset_creation)
    -> ask whether to configure another group of nodes -> repeat

This means interactive_setup() takes `mode` as an argument and returns
BOTH the setup config and the values/dimensions together, instead of a
separate interactive_define_values() call. main.py must call this
module differently from the others (see main.py's run_single_apply /
future dataset_generator.py handling of this category).

Design:
    - For each DOF, the user answers "is this a variable spring?".
    - DOFs answered "no" are rigidly restrained (True) via
      PointObj.SetRestraint - applied immediately, does not vary across
      runs. This also OVERWRITES whatever restraint the node had before.
    - DOFs answered "yes" become variable-stiffness SPRINGS via
      PointObj.SetSpring - these are what varies across single_apply
      values or the dataset_creation grid.
    - No DOF is ever left completely free: it is either a spring or a
      rigid restraint.

Coordinate system: hard-coded to Global for every restraint/spring (see
commented-out question below) - can be reintroduced later if needed.

Node selection offers the same dual choice as internal_constraints.py:
manual ID entry or automatic Z-coordinate filtering
(selection_helper.select_points).

IMPORTANT ON COMBINATIONS: in dataset_creation mode, this module still
returns one independent dimension per (node, variable DOF) - the full
Cartesian product across ALL of them (and across every other parameter
category) is computed later by dataset_generator.py via itertools.product.

---------------------------------------------------------------------------
API verification status: see previous manual tests
(manual_test_external_constraints.py) - SetRestraint and SetSpring
calling conventions and Replace=True overwrite behavior were both
confirmed working. This rewrite only reorders the interactive questions
and merges setup+values into one loop; the underlying SAP2000 API calls
are unchanged from the already-tested version.
---------------------------------------------------------------------------
"""

from core import selection_helper


_DOF_NAMES = ["U1", "U2", "U3", "R1", "R2", "R3"]

# Discretization presets for dataset_creation ranges - same "accuracy"
# concept used for Z-coordinate tolerance elsewhere in the tool.
_RANGE_DISCRETIZATION_PRESETS = {"low": 2, "medium": 4, "high": 8}


def _ask_variable_dofs():
    """
    Ask, for each of the 6 DOFs (U1,U2,U3,R1,R2,R3), whether it should
    be treated as a variable-stiffness spring. Any DOF answered "no" is
    instead rigidly restrained (True).

    Returns [bool]*6 in the order [U1,U2,U3,R1,R2,R3].
    """
    print("\nFor each DOF, choose whether it is a variable-stiffness spring. "
          "Any DOF you answer 'no' to will instead be rigidly restrained (fixed).")
    flags = []
    for dof in _DOF_NAMES:
        flags.append(selection_helper.ask_yes_no(f"  Make {dof} a variable spring?"))
    return flags


def _ask_range():
    """
    Ask for min/max plus a discretization level (low/medium/high).

    low    -> range split into 2 segments -> 3 values (min, mid, max)
    medium -> range split into 4 segments -> 5 values
    high   -> range split into 8 segments -> 9 values
    """
    k_min = selection_helper.ask_float("  Min stiffness: ")
    k_max = selection_helper.ask_float("  Max stiffness: ")

    print("  Select discretization level:")
    print("    low)    3 values (min, mid, max)")
    print("    medium) 5 values")
    print("    high)   9 values")

    while True:
        raw = input("  Enter low/medium/high: ").strip().lower()
        if raw in _RANGE_DISCRETIZATION_PRESETS:
            break
        elif raw[:1] in ("l", "m", "h"):
            raw = {"l": "low", "m": "medium", "h": "high"}[raw[:1]]
            print(f"  (Interpreted as '{raw}')")
            break
        print("  Please enter 'low', 'medium', or 'high'.")

    segments = _RANGE_DISCRETIZATION_PRESETS[raw]
    step = (k_max - k_min) / segments
    values = [round(k_min + i * step, 6) for i in range(segments + 1)]
    return values


def _ask_group_values(node_ids, variable_directions, mode, single_values, dimensions):
    """
    Ask for the stiffness value(s) of the current group's variable DOFs,
    right after the restraint has been applied for that group.

    Mutates `single_values` (dict) in single_apply mode, or appends to
    `dimensions` (list) in dataset_creation mode.
    """
    group_items = [(node_id, dof) for node_id in node_ids for dof in variable_directions]

    if not group_items:
        print("No DOF was configured as a variable spring for this group - nothing to define.")
        return

    if len(group_items) > 1:
        same_for_all = selection_helper.ask_yes_no(
            f"\n{len(group_items)} (node, DOF) item(s) in this group. "
            "Use the same stiffness "
            + ("value" if mode == "single_apply" else "range")
            + " for all of them?"
        )
    else:
        same_for_all = True

    if mode == "single_apply":
        if same_for_all:
            k_value = selection_helper.ask_float("Stiffness value to apply to this group: ")
            for item in group_items:
                single_values[item] = k_value
        else:
            for node_id, dof_name in group_items:
                k_value = selection_helper.ask_float(
                    f"Stiffness for node '{node_id}' - {dof_name}: "
                )
                single_values[(node_id, dof_name)] = k_value
    else:
        if same_for_all:
            print("Define the shared stiffness range for this group:")
            shared_values = _ask_range()

            if len(group_items) > 1:
                print(f"\nHow should these {len(group_items)} (node, DOF) items vary in the dataset grid?")
                print(f"  1) IN SYNC - same stiffness value applied to all of them together "
                      f"each run (counts as ONE grid dimension)")
                print(f"  2) INDEPENDENTLY - each item gets its own instance from this range "
                      f"(multiplies combinations: {len(group_items)} items x {len(shared_values)} "
                      f"values = {len(shared_values) ** len(group_items)} combinations from this "
                      f"group alone)")
                while True:
                    sync_choice = input("Enter 1 or 2: ").strip()
                    if sync_choice in ("1", "2"):
                        break
                    print("Please enter 1 or 2.")
                vary_in_sync = (sync_choice == "1")
            else:
                vary_in_sync = False

            if vary_in_sync:
                dimensions.append({
                    "node_directions": list(group_items),
                    "values": shared_values,
                })
            else:
                print(
                    f"NOTE: each of the {len(group_items)} items will vary "
                    f"INDEPENDENTLY using this same candidate list of "
                    f"{len(shared_values)} values - this multiplies the grid by "
                    f"{len(shared_values)}^{len(group_items)} = "
                    f"{len(shared_values) ** len(group_items)} combinations from "
                    f"this group alone, not just {len(shared_values)}."
                )
                for node_id, dof_name in group_items:
                    dimensions.append({"node_id": node_id, "direction": dof_name, "values": shared_values})
        else:
            for node_id, dof_name in group_items:
                print(f"\nDefine stiffness range for node '{node_id}' - {dof_name}:")
                values = _ask_range()
                dimensions.append({"node_id": node_id, "direction": dof_name, "values": values})


def interactive_setup(sap_model, mode: str):
    """
    Run the full workflow for the external constraints parameter
    category - a loop over node groups, asking for stiffness values
    immediately after each group's DOF configuration.

    Args:
        sap_model: the active SapModel COM object.
        mode: "single_apply" or "dataset_creation" - determines whether
            _ask_group_values asks for a single value or a range.

    For each group:
        1. select nodes (manual ID or Z-coordinate filter)
        2. for each of the 6 DOFs, choose spring (variable) vs restraint (fixed)
        3. immediately apply the restraint (overwrites any prior state)
        4. immediately ask the stiffness value(s) for this group's variable DOFs
        5. ask whether to configure another group of nodes

    Returns:
        (setup_config, values) tuple:
            setup_config: {"node_ids": [...], "node_local_csys": {...},
                            "node_variable_directions": {...}}
                or None if the user opted out of this category entirely.
            values:
                if mode == "single_apply": dict {(node_id, dof_name): value}
                if mode == "dataset_creation": list of dimension dicts
                (empty dict/list if the category was skipped)
    """
    if mode not in ("single_apply", "dataset_creation"):
        raise ValueError("mode must be 'single_apply' or 'dataset_creation'")

    print("\n=== External constraints - setup ===")
    print(
        "TIP: in SAP2000, go to View > Set Display Options and enable "
        "'Labels' for Points (and disable it for Frames/Areas if it "
        "helps) so the node IDs are visible when you're asked to select them."
    )

    wants_category = selection_helper.ask_yes_no(
        "Do you want to configure External constraints for this run?"
    )
    if not wants_category:
        print("Skipping External constraints.")
        return None, ({} if mode == "single_apply" else [])

    node_local_csys = {}
    node_variable_directions = {}
    single_values = {}
    dimensions = []

    while True:
        node_ids = selection_helper.select_points(
            sap_model, prompt_label="base nodes for external restraints"
        )

        variable_flags = _ask_variable_dofs()

        # Coordinate system: hard-coded to Global for now. Uncomment
        # below to reintroduce the interactive local/global choice.
        is_local_csys = False
        # is_local_csys = selection_helper.ask_yes_no(
        #     "\nApply restraints/springs in the point's LOCAL coordinate system? "
        #     "(answer 'n' to use the Global coordinate system)"
        # )

        restraint_value = [not v for v in variable_flags]
        variable_directions = [_DOF_NAMES[i] for i in range(6) if variable_flags[i]]

        # Verified: SetRestraint(Name, Value(), ItemType=Object) -> [Value_echo, ret]
        for node_id in node_ids:
            value_echo = []
            [value_echo, ret] = sap_model.PointObj.SetRestraint(node_id, restraint_value, 0)
            if ret != 0:
                raise RuntimeError(f"Failed to set restraint on node '{node_id}' (return code {ret}).")
            node_local_csys[node_id] = is_local_csys
            node_variable_directions[node_id] = variable_directions

        print(f"Restraint {dict(zip(_DOF_NAMES, restraint_value))} applied to "
              f"{len(node_ids)} node(s). Variable spring DOFs: {variable_directions}.")

        # Ask for this group's values right away, before looping to the
        # next group.
        _ask_group_values(node_ids, variable_directions, mode, single_values, dimensions)

        more = selection_helper.ask_yes_no(
            "\nDo you want to configure restraints for other nodes too?"
        )
        if not more:
            break

    node_ids_final = list(node_local_csys.keys())
    setup_config = {
        "node_ids": node_ids_final,
        "node_local_csys": node_local_csys,
        "node_variable_directions": node_variable_directions,
    }

    if mode == "single_apply":
        return setup_config, single_values
    else:
        return setup_config, dimensions


def apply(sap_model, setup_config: dict, stiffness_values: dict):
    """
    Push specific stiffness values into the model, right before running
    an analysis.

    Args:
        setup_config: the setup_config dict returned by interactive_setup().
        stiffness_values: dict {(node_id, dof_name): value} covering
            every (node, DOF) pair that was configured as variable.

    Verified: SetSpring(Name, k(), ItemType=Object, IsLocalCSys=False,
                         Replace=False) -> [k_echo, ret]
    Replace=True is passed explicitly (confirmed by manual test to
    correctly overwrite rather than accumulate).

    DOFs NOT configured as variable for a given node are left at k=0 in
    the spring array - they have no effect anyway since that DOF is
    already rigidly restrained (set once during setup).
    """
    if setup_config is None:
        # The category was skipped entirely during setup - nothing to apply.
        return

    node_local_csys = setup_config["node_local_csys"]
    node_variable_directions = setup_config["node_variable_directions"]

    for node_id in setup_config["node_ids"]:
        variable_dirs = node_variable_directions[node_id]
        if not variable_dirs:
            continue

        k_value = []
        for dof_name in _DOF_NAMES:
            if dof_name in variable_dirs:
                k_value.append(stiffness_values.get((node_id, dof_name), 0.0))
            else:
                k_value.append(0.0)

        is_local_csys = node_local_csys[node_id]

        k_echo = []
        [k_echo, ret] = sap_model.PointObj.SetSpring(
            node_id, k_value, 0, is_local_csys, True
        )
        if ret != 0:
            raise RuntimeError(
                f"Failed to set spring stiffness on node '{node_id}' (return code {ret})."
            )
