"""
internal_constraints.py

Handles the "Internal constraints" parameter category: diaphragm
membership per node, and equal-displacement constraints.

REDESIGNED WORKFLOW (v2) - key differences from the first version:

    1. Node selection now offers a choice between manual ID entry and
       automatic Z-coordinate filtering (selection_helper.select_points),
       for both diaphragm and equal constraint node groups.

    2. Diaphragm assignment is done IN BULK per diaphragm: the user
       selects the whole group of nodes for a diaphragm at once, instead
       of being asked node-by-node. After all diaphragms are created, an
       EXPLICIT follow-up question asks whether any nodes should vary
       between multiple diaphragms across dataset scenarios - if yes,
       the user picks which nodes and which additional diaphragm(s) each
       one can also belong to. This is deliberately explicit (not
       inferred automatically from overlapping selections) to avoid
       accidental variability from an unintentional double-selection.

    3. Diaphragm axis (X/Y/Z/AutoAxis) and coordinate system are now
       user-selectable per diaphragm, defaulting to Z (the common case
       for horizontal floor diaphragms).

    4. IMPORTANT BUG FIX: PointObj.SetConstraint's `Replace` parameter
       defaults to True, which deletes ALL previous constraints on a
       node before assigning the new one - this was silently wiping out
       a node's diaphragm assignment when an Equal constraint was
       applied afterwards on the same node. apply() now explicitly
       passes Replace=False for every SetConstraint call (the upfront
       DeleteConstraint pass already handles clearing stale state), so
       a single node can hold both a diaphragm and an Equal constraint
       at the same time.

Two independent sub-categories, both operating on point (node) objects:

    1. Diaphragm membership:
       One or more diaphragm constraint DEFINITIONS are created once
       (Phase A), each with a user-chosen axis/CSys. For each, the user
       selects the group of nodes belonging to it. A node appearing in
       more than one group becomes a variable-membership node for
       Phase B (dataset_creation varies which diaphragm it belongs to;
       single_apply asks the user to pick one for that run).

    2. Equal-displacement constraints:
       One or more Equal constraint DEFINITIONS are created once, each
       with a fixed set of DOFs (UX,UY,UZ,RX,RY,RZ) and a fixed group of
       member nodes (Phase A). What varies (Phase B) is only whether the
       constraint is active (ON: nodes assigned to it) or inactive (OFF:
       nodes not assigned to it) - SAP2000 has no master/slave concept
       for Equal constraints, all assigned nodes become equivalent in
       the chosen DOFs.

apply() ordering: every time a new combination is applied, ALL involved
nodes (diaphragm + equal) are first cleared of their existing
constraints (DeleteConstraint), then diaphragm assignments are
re-applied, then active Equal groups are re-applied - each SetConstraint
call uses Replace=False so a node can accumulate both a diaphragm and an
Equal assignment without one wiping the other.

---------------------------------------------------------------------------
API verification status (checked against CSI_OAPI_Documentation.chm AND
confirmed empirically via manual test - see manual_test_internal_constraints.py):

VERIFIED (signature + real calling convention confirmed by test output):
    - ConstraintDef.SetDiaphragm(Name, Axis=AutoAxis, CSys="Global") -> Long
      All params ByVal -> simple `ret = ...` call. Confirmed: returns plain int.
      eConstraintAxis values used here: X=1, Y=2, Z=3, AutoAxis=4.
    - ConstraintDef.SetEqual(Name, Value() as Boolean, CSys="Global") -> Long
      Value() is ByRef -> call returns [Value_echo, ret].
    - PointObj.SetConstraint(Name, ConstraintName, ItemType=Object, Replace=True) -> Long
      ConstraintName has NO explicit ByVal -> ByRef by default -> call
      returns [ConstraintName_echo, ret]. Replace defaults to True, which
      DELETES all previous constraints on the node - apply() below always
      passes Replace=False explicitly to avoid this (see bug fix note above).
    - PointObj.DeleteConstraint(Name, ItemType=Object) -> Long
      Confirmed by test: returns plain int (0).

General rule (corrects an earlier wrong assumption): ANY parameter
without an explicit "ByVal" keyword in the VB6 signature is ByRef by
default and gets echoed back in the comtypes call result, even if it is
a single string and not an array. Always check the literal VB6
signature, not just "is this an array or not".
---------------------------------------------------------------------------
"""

from core import selection_helper


# eConstraintAxis enum values (verified via CHM)
_AXIS_OPTIONS = {
    "x": ("X", 1),
    "y": ("Y", 2),
    "z": ("Z", 3),
    "auto": ("AutoAxis", 4),
}


def _ask_axis(default: str = "z"):
    """
    Ask the user which axis is perpendicular to the diaphragm plane.
    Defaults to Z (the common case for horizontal floor diaphragms).

    Returns (axis_label, eConstraintAxis_value).
    """
    print("Select the constraint axis (perpendicular to the diaphragm plane):")
    print("  x)    X axis")
    print("  y)    Y axis")
    print("  z)    Z axis (default - typical for horizontal floor diaphragms)")
    print("  auto) Automatically determined from the assigned joints")

    raw = input(f"Enter x/y/z/auto (press Enter for default '{default}'): ").strip().lower()
    if raw == "":
        raw = default
    if raw not in _AXIS_OPTIONS:
        print(f"Unrecognized input '{raw}', using default '{default}'.")
        raw = default
    return _AXIS_OPTIONS[raw]


def _ask_dof_flags(prompt_label: str = "constraint"):
    """
    Ask the user which of the 6 DOFs (UX, UY, UZ, RX, RY, RZ) to include
    in an Equal constraint. Returns a list of 6 booleans in that order.
    """
    dof_names = ["UX", "UY", "UZ", "RX", "RY", "RZ"]
    print(f"\nSelect which DOFs to include in {prompt_label} (y/n for each):")
    flags = []
    for dof in dof_names:
        flags.append(selection_helper.ask_yes_no(f"  Include {dof}?"))
    return flags


def _ask_on_off(prompt: str) -> str:
    """Reusable ON/OFF prompt with validation/retry - never crashes on a typo."""
    while True:
        raw = input(prompt).strip().upper()
        if raw in ("ON", "OFF"):
            return raw
        print("Please enter 'ON' or 'OFF'.")


def _setup_diaphragms(sap_model):
    """
    Interactive setup for diaphragm membership - bulk, group-based, with
    an explicit follow-up step for variable membership.

    Step 1: for each diaphragm, the user selects the whole group of
    nodes belonging to it in one step. Each node gets a single "base"
    diaphragm assignment from this step.

    Step 2 (explicit, only if more than one diaphragm was created): the
    user is asked whether any nodes should vary between multiple
    diaphragms across dataset scenarios. If yes, the user picks a subset
    of the already-assigned nodes and, for each, adds the additional
    diaphragm(s) it can also belong to.

    Returns a dict:
        {
            "diaphragm_names": [str, ...],
            "nodes": [{"node_id": str, "candidates": [str, ...]}, ...]
        }
    or None if the user does not want any diaphragm parameters.
    """
    wants_diaphragm = selection_helper.ask_yes_no(
        "\nDo you want to define diaphragm constraints as a varying parameter?"
    )
    if not wants_diaphragm:
        return None

    print("\n=== Diaphragm constraints - setup ===")

    diaphragm_names = []
    node_primary = {}  # node_id -> primary/base diaphragm name

    while True:
        name = input("\nName for this diaphragm constraint: ").strip()
        axis_label, axis_value = _ask_axis()
        csys = input("Coordinate system (press Enter for default 'Global'): ").strip() or "Global"

        # Verified: SetDiaphragm(Name, Axis=AutoAxis, CSys="Global") -> Long
        ret = sap_model.ConstraintDef.SetDiaphragm(name, axis_value, csys)
        if ret != 0:
            raise RuntimeError(f"Failed to create diaphragm constraint '{name}' (return code {ret}).")
        print(f"Created diaphragm constraint '{name}' (axis={axis_label}, CSys={csys}).")

        diaphragm_names.append(name)

        node_ids = selection_helper.select_points(
            sap_model, prompt_label=f"nodes belonging to diaphragm '{name}'"
        )
        for node_id in node_ids:
            if node_id in node_primary and node_primary[node_id] != name:
                print(
                    f"Note: node '{node_id}' was already assigned to diaphragm "
                    f"'{node_primary[node_id]}'; its base assignment is now updated to '{name}'. "
                    f"Use the variability step below if you want it to alternate between both."
                )
            node_primary[node_id] = name

        print(f"{len(node_ids)} node(s) assigned to diaphragm '{name}'.")

        more = selection_helper.ask_yes_no("\nCreate another diaphragm constraint?")
        if not more:
            break

    node_candidates = {node_id: [primary] for node_id, primary in node_primary.items()}

    # Explicit variability step - only meaningful with more than one diaphragm.
    if len(diaphragm_names) > 1:
        wants_variability = selection_helper.ask_yes_no(
            "\nDo you want any of these nodes to vary between multiple "
            "diaphragms across dataset scenarios?"
        )
        while wants_variability:
            all_assigned_ids = list(node_primary.keys())
            subset_ids = selection_helper.interactive_select(
                sap_model, "point", candidate_ids=all_assigned_ids,
                prompt_label="nodes to make variable between diaphragms"
            )

            same_for_all = selection_helper.ask_yes_no(
                f"\nApply the same additional diaphragm(s) to all "
                f"{len(subset_ids)} selected node(s)?"
            )

            if same_for_all:
                print(f"Available diaphragms: {diaphragm_names}")
                while True:
                    raw = input(
                        "Enter the additional diaphragm(s) for ALL selected nodes, "
                        "comma separated: "
                    )
                    extra = [n.strip() for n in raw.split(",") if n.strip()]
                    invalid = [e for e in extra if e not in diaphragm_names]
                    if invalid:
                        print(f"Unknown diaphragm name(s): {invalid}. Please re-enter.")
                        continue
                    if not extra:
                        print("No valid diaphragm names entered. Please re-enter.")
                        continue
                    break

                for node_id in subset_ids:
                    for e in extra:
                        if e not in node_candidates[node_id]:
                            node_candidates[node_id].append(e)
                print(f"Applied additional candidates {extra} to all {len(subset_ids)} node(s).")
            else:
                for node_id in subset_ids:
                    print(f"\nNode '{node_id}' is currently fixed to '{node_primary[node_id]}'.")
                    print(f"Available diaphragms: {diaphragm_names}")
                    while True:
                        raw = input(
                            "Enter the additional diaphragm(s) this node can also belong to, "
                            "comma separated: "
                        )
                        extra = [n.strip() for n in raw.split(",") if n.strip()]
                        invalid = [e for e in extra if e not in diaphragm_names]
                        if invalid:
                            print(f"Unknown diaphragm name(s): {invalid}. Please re-enter.")
                            continue
                        break

                    for e in extra:
                        if e not in node_candidates[node_id]:
                            node_candidates[node_id].append(e)
                    print(f"Node '{node_id}' candidates now: {node_candidates[node_id]}")

            wants_variability = selection_helper.ask_yes_no("\nMake more nodes variable?")

    nodes = [{"node_id": nid, "candidates": cands} for nid, cands in node_candidates.items()]
    return {"diaphragm_names": diaphragm_names, "nodes": nodes}


def _setup_equal_constraints(sap_model):
    """
    Interactive setup for equal-displacement constraints.

    Returns a dict:
        {
            "constraints": [
                {"name": str, "dofs": [bool]*6, "node_ids": [str, ...], "fixed_state": "ON"/"OFF"/None},
                ...
            ],
            "simultaneous_groups": [[name, name, ...], ...]
        }
    or None if the user does not want any equal constraints.
    """
    wants_equal = selection_helper.ask_yes_no(
        "\nDo you want to define equal-displacement constraints as a varying parameter?"
    )
    if not wants_equal:
        return None

    print("\n=== Equal-displacement constraints - setup ===")

    constraints = []
    while True:
        name = input("\nName for this Equal constraint: ").strip()
        dofs = _ask_dof_flags(prompt_label=f"constraint '{name}'")

        node_ids = selection_helper.select_points(
            sap_model, prompt_label=f"nodes belonging to Equal constraint '{name}'"
        )

        # Verified: SetEqual(Name, Value(), CSys="Global") -> [Value_echo, ret]
        value_echo = []
        [value_echo, ret] = sap_model.ConstraintDef.SetEqual(name, dofs)
        if ret != 0:
            raise RuntimeError(f"Failed to create Equal constraint '{name}' (return code {ret}).")
        print(f"Created Equal constraint '{name}' with DOFs {list(value_echo)} "
              f"and {len(node_ids)} member node(s).")

        # NOTE: previously asked "Should '{name}' vary ON/OFF across scenarios?"
        # here, defaulting to fixed ON if answered 'n'. Removed based on the
        # design principle: every parameter the user explicitly defines
        # through this tool is meant to vary - "fixed" only applies to
        # whatever was already in the original model and left untouched.
        # Kept commented below in case we want to reintroduce an opt-out:
        #
        # will_vary = selection_helper.ask_yes_no(
        #     f"Should '{name}' vary ON/OFF across scenarios? "
        #     f"(answer 'n' to keep it always ON)"
        # )
        # fixed_state = None if will_vary else "ON"
        fixed_state = None

        constraints.append({
            "name": name,
            "dofs": dofs,
            "node_ids": node_ids,
            "fixed_state": fixed_state,
        })

        more = selection_helper.ask_yes_no("\nDefine another Equal constraint?")
        if not more:
            break

    # Simultaneous groups: constraints whose ON/OFF state must move together.
    simultaneous_groups = []
    varying_names = [c["name"] for c in constraints if c["fixed_state"] is None]
    if len(varying_names) > 1:
        print(
            f"\nThe following constraints will vary ON/OFF across scenarios: {varying_names}"
        )
        print(
            "By default, each varies INDEPENDENTLY (its own ON/OFF dimension in the grid). "
            "If two or more of them should always switch ON/OFF TOGETHER (as a single "
            "combined dimension instead of separate ones), you can group them below."
        )
        wants_groups = selection_helper.ask_yes_no(
            "Do you want to define any simultaneous group?"
        )
        if not wants_groups:
            print("OK - all varying constraints will vary independently in the grid.")

        remaining_names = list(varying_names)
        while wants_groups and len(remaining_names) > 1:
            print(f"Constraints still available to group: {remaining_names}")
            raw = input(
                "Enter the names in this simultaneous group, comma separated "
                "(they will always share the same ON/OFF state): "
            )
            group = [name.strip() for name in raw.split(",") if name.strip()]
            invalid = [g for g in group if g not in remaining_names]
            if invalid:
                print(f"Unknown or already-grouped constraint name(s): {invalid}, please re-enter.")
                continue
            if len(group) < 2:
                print("A simultaneous group needs at least 2 constraints, please re-enter.")
                continue

            simultaneous_groups.append(group)
            for name in group:
                remaining_names.remove(name)
            print(f"Group created: {group}. Remaining ungrouped: {remaining_names}")

            if len(remaining_names) > 1:
                wants_groups = selection_helper.ask_yes_no("Define another simultaneous group?")
            else:
                wants_groups = False

        if remaining_names and len(varying_names) > 1:
            print(f"These constraints remain independent (their own ON/OFF dimension): {remaining_names}")

    return {"constraints": constraints, "simultaneous_groups": simultaneous_groups}


def interactive_setup(sap_model):
    """
    Run the full Phase A interactive workflow for the internal
    constraints parameter category (diaphragm + equal, both optional).

    Returns:
        dict with keys "diaphragm" and "equal", each either the config
        dict from the corresponding sub-setup function, or None if the
        user opted out of that sub-category.

        If the user opts out of BOTH sub-categories (e.g. they selected
        "internal constraints" at the top-level checklist by mistake,
        or changed their mind), returns None entirely - the caller
        should treat this category as skipped rather than an error.
    """
    print("\n=== Internal constraints - setup ===")
    print(
        "TIP: in SAP2000, go to View > Set Display Options and enable "
        "'Labels' for Points (and disable it for Frames/Areas if it "
        "helps) so the node IDs are visible when you're asked to select them."
    )
    diaphragm_config = _setup_diaphragms(sap_model)
    equal_config = _setup_equal_constraints(sap_model)

    if diaphragm_config is None and equal_config is None:
        print(
            "\nNo internal constraint parameters were defined - "
            "this category will be skipped."
        )
        return None

    return {"diaphragm": diaphragm_config, "equal": equal_config}


def interactive_define_values(setup_config: dict, mode: str):
    """
    Build the parameter dimensions (dataset_creation) or the single
    chosen combination (single_apply) from the setup config.

    Fixed elements (diaphragm nodes with a single candidate, Equal
    constraints with fixed_state set) are NOT grid dimensions - they are
    always re-applied at their fixed value on every run. Only nodes/
    constraints the user explicitly made variable (multiple diaphragm
    candidates, or "should it vary ON/OFF" answered yes) become
    dimensions of the combination grid.

    Returns:
        If mode == "single_apply":
            dict {"diaphragm": {node_id: diaphragm_name, ...},
                  "equal_states": {constraint_name: "ON"/"OFF", ...}}
        If mode == "dataset_creation":
            list of dimensions, each:
                {"kind": "diaphragm", "node_id": str, "values": [diaphragm_name, ...]}
                or
                {"kind": "equal_group", "names": [constraint_name, ...], "values": ["ON","OFF"]}
    """
    if mode not in ("single_apply", "dataset_creation"):
        raise ValueError("mode must be 'single_apply' or 'dataset_creation'")

    if setup_config is None:
        # The category was skipped entirely during setup (user opted out
        # of both diaphragm and equal) - nothing to define.
        return {"diaphragm": {}, "equal_states": {}} if mode == "single_apply" else []

    diaphragm_config = setup_config.get("diaphragm")
    equal_config = setup_config.get("equal")

    if mode == "single_apply":
        result = {"diaphragm": {}, "equal_states": {}}

        if diaphragm_config:
            fixed_nodes = [n for n in diaphragm_config["nodes"] if len(n["candidates"]) == 1]
            variable_nodes = [n for n in diaphragm_config["nodes"] if len(n["candidates"]) > 1]

            for node in fixed_nodes:
                result["diaphragm"][node["node_id"]] = node["candidates"][0]

            if variable_nodes:
                print(f"\nThere are {len(variable_nodes)} node(s) with more than one diaphragm candidate.")
                review_individually = selection_helper.ask_yes_no(
                    "Review each one individually? (answer 'n' to apply the same "
                    "diaphragm choice to all of them at once)"
                )

                if not review_individually:
                    all_names = sorted({c for n in variable_nodes for c in n["candidates"]})
                    print(f"Available diaphragms across these nodes: {all_names}")
                    while True:
                        bulk_choice = input(
                            "Enter the diaphragm to assign to ALL variable nodes for this run: "
                        ).strip()
                        if bulk_choice in all_names:
                            break
                        print(f"'{bulk_choice}' is not among {all_names}, please try again.")

                    for node in variable_nodes:
                        if bulk_choice in node["candidates"]:
                            result["diaphragm"][node["node_id"]] = bulk_choice
                        else:
                            # This node doesn't have the bulk choice as a candidate -
                            # ask individually just for this exception.
                            print(
                                f"\nNode '{node['node_id']}' does not have '{bulk_choice}' "
                                f"as a candidate. Its candidates: {node['candidates']}"
                            )
                            while True:
                                fallback = input("Choose the diaphragm for this node: ").strip()
                                if fallback in node["candidates"]:
                                    result["diaphragm"][node["node_id"]] = fallback
                                    break
                                print(f"'{fallback}' is not one of {node['candidates']}, please try again.")
                else:
                    for node in variable_nodes:
                        print(f"\nNode '{node['node_id']}' candidates: {node['candidates']}")
                        while True:
                            choice = input("Choose the diaphragm to assign for this run: ").strip()
                            if choice in node["candidates"]:
                                result["diaphragm"][node["node_id"]] = choice
                                break
                            print(f"'{choice}' is not one of {node['candidates']}, please try again.")

        if equal_config:
            grouped_names = {n for g in equal_config["simultaneous_groups"] for n in g}
            for group in equal_config["simultaneous_groups"]:
                state = _ask_on_off(f"State for simultaneous group {group} (ON/OFF): ")
                for name in group:
                    result["equal_states"][name] = state
            for constraint in equal_config["constraints"]:
                name = constraint["name"]
                if name in grouped_names:
                    continue
                if constraint["fixed_state"] is not None:
                    result["equal_states"][name] = constraint["fixed_state"]
                else:
                    state = _ask_on_off(f"State for constraint '{name}' (ON/OFF): ")
                    result["equal_states"][name] = state

        return result

    # mode == "dataset_creation"
    dimensions = []
    fixed_summary = []

    if diaphragm_config:
        for node in diaphragm_config["nodes"]:
            if len(node["candidates"]) > 1:
                dimensions.append({
                    "kind": "diaphragm",
                    "node_id": node["node_id"],
                    "values": node["candidates"],
                })
            else:
                fixed_summary.append(f"node '{node['node_id']}' fixed to '{node['candidates'][0]}'")

    if equal_config:
        grouped_names = {n for g in equal_config["simultaneous_groups"] for n in g}
        for group in equal_config["simultaneous_groups"]:
            dimensions.append({
                "kind": "equal_group",
                "names": group,
                "values": ["ON", "OFF"],
            })
        for constraint in equal_config["constraints"]:
            name = constraint["name"]
            if name in grouped_names:
                continue
            if constraint["fixed_state"] is not None:
                fixed_summary.append(f"constraint '{name}' fixed {constraint['fixed_state']}")
            else:
                dimensions.append({
                    "kind": "equal_group",
                    "names": [name],
                    "values": ["ON", "OFF"],
                })

    print("\n=== Grid dimensions summary ===")
    print(f"Variable dimensions ({len(dimensions)}):")
    for dim in dimensions:
        print(f"  {dim}")
    print(f"Fixed elements (always re-applied, not part of the grid): {len(fixed_summary)}")
    for line in fixed_summary:
        print(f"  {line}")

    return dimensions


def apply(sap_model, setup_config: dict, diaphragm_assignment: dict, equal_states: dict):
    """
    Apply one specific combination of internal constraints to the model,
    right before running an analysis.

    Args:
        setup_config: the dict returned by interactive_setup().
        diaphragm_assignment: dict {node_id: diaphragm_name} - the fixed
            candidate is used for nodes not present in this dict (single
            candidate case).
        equal_states: dict {constraint_name: "ON"/"OFF"} for every
            constraint defined in setup_config["equal"].

    Ordering: clear ALL involved nodes first, then reapply diaphragm
    assignments, then reapply active ("ON") Equal groups. Every
    SetConstraint call uses Replace=False (see bug fix note in the
    module docstring), so a node with both a diaphragm and an Equal
    assignment keeps both instead of the second call wiping the first.
    """
    if setup_config is None:
        # The category was skipped entirely during setup - nothing to apply.
        return

    diaphragm_config = setup_config.get("diaphragm")
    equal_config = setup_config.get("equal")

    # Collect every node that could be touched, to clear it first.
    nodes_to_clear = set()
    if diaphragm_config:
        nodes_to_clear.update(node["node_id"] for node in diaphragm_config["nodes"])
    if equal_config:
        for constraint in equal_config["constraints"]:
            nodes_to_clear.update(constraint["node_ids"])

    for node_id in nodes_to_clear:
        # Verified: DeleteConstraint(Name, ItemType=Object) -> Long (simple ret)
        ret = sap_model.PointObj.DeleteConstraint(node_id, 0)
        if ret != 0:
            raise RuntimeError(f"Failed to clear constraints on node '{node_id}' (return code {ret}).")

    # Reapply diaphragm assignments.
    if diaphragm_config:
        for node in diaphragm_config["nodes"]:
            node_id = node["node_id"]
            target_diaphragm = diaphragm_assignment.get(node_id, node["candidates"][0])

            # Verified: SetConstraint(Name, ConstraintName, ItemType=Object, Replace=True)
            # -> [ConstraintName_echo, ret]
            # IMPORTANT: Replace=False explicitly, so this does NOT wipe out
            # an Equal constraint that might be applied to the same node
            # right after (see module docstring bug fix note).
            constraint_name_echo = ""
            [constraint_name_echo, ret] = sap_model.PointObj.SetConstraint(
                node_id, target_diaphragm, 0, False
            )
            if ret != 0:
                raise RuntimeError(
                    f"Failed to assign diaphragm '{target_diaphragm}' to node '{node_id}' "
                    f"(return code {ret})."
                )

    # Reapply active Equal constraints.
    if equal_config:
        for constraint in equal_config["constraints"]:
            name = constraint["name"]
            state = equal_states.get(name, constraint["fixed_state"] or "ON")
            if state != "ON":
                continue
            for node_id in constraint["node_ids"]:
                # Replace=False here too, for the same reason: do not wipe
                # a diaphragm constraint just assigned to this same node.
                constraint_name_echo = ""
                [constraint_name_echo, ret] = sap_model.PointObj.SetConstraint(
                    node_id, name, 0, False
                )
                if ret != 0:
                    raise RuntimeError(
                        f"Failed to assign Equal constraint '{name}' to node '{node_id}' "
                        f"(return code {ret})."
                    )
