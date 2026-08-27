"""
selection_helper.py

Generic "show -> select -> highlight -> confirm" workflow shared by every
parameter category (frame materials, shell materials, internal constraints,
external constraints).

The pattern is always the same, regardless of object type:
    1. List the available object IDs in the model (or a candidate subset).
    2. Ask the user which IDs to act on.
    3. Select and visually highlight those IDs in the open SAP2000 window.
    4. Ask the user to confirm (or re-enter) the selection.

This module knows nothing about materials, constraints, or E values -
it only deals with SAP2000 object IDs and the CLI interaction around them.

---------------------------------------------------------------------------
API verification status (checked against CSI_OAPI_Documentation.chm):

VERIFIED:
    - FrameObj.SetSelected(Name, Selected, ItemType=Object) -> Long
    - AreaObj.SetSelected(Name, Selected, ItemType=Object)  -> Long
    - PointObj.SetSelected(Name, Selected, ItemType=Object) -> Long
    - View.RefreshView(Window=0, Zoom=True) -> Long
    - FrameObj.GetNameList(NumberNames, MyName) -> Long (no MatType filter arg)
    - AreaObj.GetNameList(NumberNames, MyName)  -> Long
    - PointObj.GetNameList(NumberNames, MyName) -> Long
    - PointObj.GetCoordCartesian(Name, x, y, z, Csys="Global") -> Long
      x, y, z have no explicit ByVal -> ByRef by default -> unpacked as
      [x, y, z, ret] = ...

All functions used in this module are verified against the CHM.
---------------------------------------------------------------------------
"""

# Maps a friendly object type name to the corresponding SAP2000 API object
# attribute name (SapModel.FrameObj, SapModel.AreaObj, SapModel.PointObj).
_OBJECT_TYPE_MAP = {
    "frame": "FrameObj",
    "area": "AreaObj",
    "point": "PointObj",
}


def _get_api_object(sap_model, object_type: str):
    """
    Return the SAP2000 API sub-object (FrameObj / AreaObj / PointObj)
    corresponding to the given friendly object_type string.
    """
    object_type = object_type.lower()
    if object_type not in _OBJECT_TYPE_MAP:
        raise ValueError(
            f"Unsupported object_type '{object_type}'. "
            f"Expected one of: {list(_OBJECT_TYPE_MAP.keys())}"
        )
    return getattr(sap_model, _OBJECT_TYPE_MAP[object_type])


def get_all_ids(sap_model, object_type: str):
    """
    Return the list of all object IDs currently present in the model
    for the given object type ("frame", "area", or "point").

    Verified: GetNameList(NumberNames, MyName) -> Long
    """
    api_obj = _get_api_object(sap_model, object_type)
    number_names = 0
    names = []
    [number_names, names, ret] = api_obj.GetNameList(number_names, names)
    return list(names)


def clear_selection(sap_model):
    """Clear any current selection in the SAP2000 model window."""
    sap_model.SelectObj.ClearSelection()


def highlight_ids(sap_model, object_type: str, ids):
    """
    Select the given IDs in SAP2000 so the user can see them highlighted
    in the model window. Clears any previous selection first.

    Args:
        object_type: "frame", "area", or "point".
        ids: list of object ID strings to select.

    Verified: SetSelected(Name, Selected, ItemType=Object) -> Long
    Called once per ID with the default ItemType (Object), matching the
    single-object selection use case here (not Group / SelectedObjects).
    """
    api_obj = _get_api_object(sap_model, object_type)
    clear_selection(sap_model)
    for obj_id in ids:
        api_obj.SetSelected(obj_id, True)

    # Verified: RefreshView(Window=0, Zoom=True) -> Long
    # Defaults refresh all windows without resetting the current zoom level.
    sap_model.View.RefreshView()


def _parse_id_input(raw_input: str, available_ids):
    """
    Parse a comma/space-separated list of IDs typed by the user, with
    support for simple ranges (e.g. "12-15" -> ["12","13","14","15"]).
    Also supports the keyword "all" to select every available ID.

    Returns a list of ID strings. Raises ValueError if an entered ID
    is not present in available_ids.
    """
    raw_input = raw_input.strip()
    if raw_input.lower() == "all":
        return list(available_ids)

    tokens = raw_input.replace(",", " ").split()
    selected = []
    available_set = set(available_ids)

    for token in tokens:
        if "-" in token and not token.startswith("-"):
            start_str, end_str = token.split("-", 1)
            try:
                start, end = int(start_str), int(end_str)
            except ValueError:
                raise ValueError(f"Invalid range token: '{token}'")
            for i in range(start, end + 1):
                obj_id = str(i)
                if obj_id not in available_set:
                    raise ValueError(f"ID '{obj_id}' (from range '{token}') not found in model.")
                selected.append(obj_id)
        else:
            if token not in available_set:
                raise ValueError(f"ID '{token}' not found in model.")
            selected.append(token)

    return selected


def interactive_select(sap_model, object_type: str, candidate_ids=None,
                        prompt_label: str = None):
    """
    Full interactive selection workflow:
        1. Show the user the available IDs (all model IDs, or a restricted
           candidate subset if provided).
        2. Ask the user to type which IDs to act on.
        3. Highlight the chosen IDs in SAP2000.
        4. Ask for confirmation; if not confirmed, let the user re-enter.

    Args:
        sap_model: the active SapModel COM object.
        object_type: "frame", "area", or "point".
        candidate_ids: optional list restricting which IDs are offered
            (e.g. only the base-level nodes for external constraints).
            If None, all IDs of that object type in the model are shown.
        prompt_label: optional custom label to show in the prompt
            (e.g. "base nodes for external restraints").

    Returns:
        List of confirmed object ID strings, in the order entered by the user.
    """
    available_ids = candidate_ids if candidate_ids is not None else get_all_ids(sap_model, object_type)

    if not available_ids:
        raise RuntimeError(f"No '{object_type}' objects available to select.")

    label = prompt_label or f"{object_type} objects"

    while True:
        print(f"\nAvailable {label} IDs ({len(available_ids)} total):")
        print(", ".join(available_ids))

        raw = input(
            f"\nEnter the {object_type} IDs to select from the list above "
            f"(comma/space separated, ranges like 12-15 allowed, "
            f"or 'all' to select every ID listed above): "
        )

        try:
            chosen_ids = _parse_id_input(raw, available_ids)
        except ValueError as e:
            print(f"Input error: {e}. Please try again.")
            continue

        if not chosen_ids:
            print("No valid IDs entered. Please try again.")
            continue

        highlight_ids(sap_model, object_type, chosen_ids)

        if len(chosen_ids) > 15:
            preview = ", ".join(chosen_ids[:15]) + f", ... (+{len(chosen_ids) - 15} more)"
        else:
            preview = ", ".join(chosen_ids)

        confirm = input(
            f"\n{len(chosen_ids)} {object_type} object(s) highlighted in SAP2000: "
            f"{preview}\nConfirm this selection? (y/n): "
        ).strip().lower()

        if confirm == "y":
            return chosen_ids
        else:
            print("Selection discarded, please choose again.")


def ask_multi_choice(prompt_label: str, options: dict) -> list:
    """
    Ask the user to pick zero or more options from a checklist, so the
    top-level workflow can activate only the parameter categories the
    user actually wants, instead of cascading through every category in
    sequence.

    Args:
        prompt_label: text shown above the checklist.
        options: dict {key: description} - key is what gets returned,
            description is the text shown to the user.

    Returns:
        List of selected keys (in the order they appear in `options`),
        or an empty list if the user selects none.
    """
    keys = list(options.keys())
    print(f"\n{prompt_label}")
    for i, key in enumerate(keys, start=1):
        print(f"  {i}) {options[key]}")

    while True:
        raw = input(
            "Enter the number(s) of the categories to activate, comma separated "
            "(or 'all', or press Enter for none): "
        ).strip()

        if raw == "":
            return []
        if raw.lower() == "all":
            return keys

        tokens = raw.replace(",", " ").split()
        try:
            indices = [int(t) for t in tokens]
        except ValueError:
            print("Please enter valid numbers, 'all', or press Enter for none.")
            continue

        if any(i < 1 or i > len(keys) for i in indices):
            print(f"Please enter numbers between 1 and {len(keys)}.")
            continue

        # Preserve the options' original order, remove duplicates.
        selected_indices = sorted(set(indices))
        return [keys[i - 1] for i in selected_indices]


def ask_yes_no(question: str) -> bool:
    """
    Simple reusable yes/no CLI prompt, returns True for 'y', False for 'n'.
    Keeps re-asking until a valid answer is given.
    """
    while True:
        answer = input(f"{question} (y/n): ").strip().lower()
        if answer in ("y", "n"):
            return answer == "y"
        print("Please answer 'y' or 'n'.")


def ask_float(prompt: str, default: float = None) -> float:
    """
    Reusable float-input prompt with validation/retry, so a typo (e.g.
    typing text instead of a number) never crashes the workflow.

    Args:
        prompt: text shown to the user.
        default: if provided, pressing Enter with no input returns this
            value instead of forcing the user to retype it.
    """
    while True:
        raw = input(prompt).strip()
        if raw == "" and default is not None:
            return default
        try:
            return float(raw)
        except ValueError:
            print(f"'{raw}' is not a valid number, please try again.")


def ask_int(prompt: str, minimum: int = None, default: int = None) -> int:
    """
    Reusable integer-input prompt with validation/retry.

    Args:
        prompt: text shown to the user.
        minimum: if provided, re-asks until the value is >= minimum.
        default: if provided, pressing Enter with no input returns this
            value instead of forcing the user to retype it.
    """
    while True:
        raw = input(prompt).strip()
        if raw == "" and default is not None:
            return default
        try:
            value = int(raw)
        except ValueError:
            print(f"'{raw}' is not a valid integer, please try again.")
            continue
        if minimum is not None and value < minimum:
            print(f"Please enter an integer >= {minimum}.")
            continue
        return value


# ---------------------------------------------------------------------
# Z-coordinate based node selection
#
# Verified: PointObj.GetCoordCartesian(Name, x, y, z, Csys="Global") -> Long
# x, y, z have no explicit ByVal in the VB6 signature -> ByRef by default
# -> call must be unpacked as [x, y, z, ret] = ...
# ---------------------------------------------------------------------

# Percentage tolerance presets (of the target Z quota itself).
def get_z_coordinate(sap_model, node_id: str, csys: str = "Global") -> float:
    """
    Return the Z coordinate of a single point object, in the model's
    current Present Units.

    Verified: GetCoordCartesian(Name, x, y, z, Csys="Global") -> [x, y, z, ret]
    """
    x, y, z = 0.0, 0.0, 0.0
    [x, y, z, ret] = sap_model.PointObj.GetCoordCartesian(node_id, x, y, z, csys)
    if ret != 0:
        raise RuntimeError(f"Failed to read coordinates for node '{node_id}' (return code {ret}).")
    return z


def select_nodes_by_z(sap_model, target_z: float, tolerance: float, candidate_ids=None):
    """
    Return the list of point object IDs whose Z coordinate falls within
    target_z +/- tolerance.

    Args:
        target_z: the target Z quota to match, in the model's current units.
        tolerance: a plain distance, in the model's current length units
            (whatever SAP2000 is set to right now - meters, mm, feet, ...),
            entered directly by the user - not a percentage.
        candidate_ids: optional list restricting which point IDs are
            checked (defaults to every point object in the model).

    Returns:
        List of node ID strings whose Z coordinate matches within tolerance.
    """
    ids_to_check = candidate_ids if candidate_ids is not None else get_all_ids(sap_model, "point")

    matching_ids = []
    for node_id in ids_to_check:
        z = get_z_coordinate(sap_model, node_id)
        if abs(z - target_z) <= tolerance:
            matching_ids.append(node_id)

    return matching_ids


def interactive_select_by_z(sap_model, candidate_ids=None, prompt_label: str = None):
    """
    Ask the user for a target Z quota and a tolerance value (both in the
    model's current units), automatically find matching nodes, highlight
    them in SAP2000, and ask for confirmation (re-using interactive_select's
    highlight/confirm flow).

    This is an alternative to manually typing node IDs, useful when many
    nodes share the same elevation (e.g. all base-level nodes).

    If no nodes match the given target_z/tolerance, the user is asked to
    retry with different values instead of the function raising an error -
    a tight tolerance can easily find zero matches for irregular models,
    and that should not abort the whole workflow.

    Returns:
        List of confirmed node ID strings.
    """
    while True:
        target_z = ask_float("\nTarget Z coordinate (in the model's current units): ")
        tolerance = ask_float(
            "Tolerance (in the model's current units, e.g. 0.05 for 5 cm "
            "if the model uses meters, press Enter for default 0.05): ",
            default=0.05
        )

        matching_ids = select_nodes_by_z(sap_model, target_z, tolerance, candidate_ids)

        if matching_ids:
            break

        print(
            f"\nNo nodes found within tolerance of Z={target_z} "
            f"(tolerance={tolerance}). Try a different Z or a larger tolerance."
        )
        retry = ask_yes_no("Try again with different values?")
        if not retry:
            raise RuntimeError("No nodes found and the user chose not to retry.")

    print(f"\n{len(matching_ids)} node(s) found matching Z={target_z} "
          f"within tolerance {tolerance}: {matching_ids}")

    return interactive_select(
        sap_model, "point", candidate_ids=matching_ids,
        prompt_label=prompt_label or f"nodes near Z={target_z}"
    )


def select_points(sap_model, prompt_label: str = None):
    """
    Ask the user whether to select nodes by manually entering IDs, or by
    filtering automatically on a Z coordinate, then run the corresponding
    interactive selection flow. Shared entry point for any parameter
    category that needs to pick a set of nodes (internal constraints,
    external constraints, ...).

    Returns:
        List of confirmed node ID strings.
    """
    print(f"\nHow do you want to select {prompt_label or 'nodes'}?")
    print("  1) Enter node IDs manually")
    print("  2) Filter automatically by Z coordinate")
    while True:
        choice = input("Enter 1 or 2: ").strip()
        if choice == "1":
            return interactive_select(sap_model, "point", prompt_label=prompt_label)
        elif choice == "2":
            return interactive_select_by_z(sap_model, prompt_label=prompt_label)
        print("Please enter 1 or 2.")
