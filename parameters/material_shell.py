"""
material_shell.py

Handles the "Shell/Area elastic modulus" parameter category.

Same two-phase design as material_frame.py:

    Phase A - interactive_setup():
        - user selects target area (shell) elements
        - optionally meshes them into a grid of n1 x n2 sub-panels
        - creates one new isotropic material per final area element
        - assigns each new material to its area element (material overwrite)

    Phase B - interactive_define_values() + apply():
        - ask the user for E value(s) to test per material
        - apply() pushes one specific E value into the model

Note the key difference from frame elements: area objects are 2D, so
"dividing a portion" means meshing into an n1 x n2 grid (SAP2000's native
meshing paradigm for areas), not splitting along a single length like
EditFrame.DivideByRatio does for frames.

---------------------------------------------------------------------------
API verification status (checked against CSI_OAPI_Documentation.chm):

VERIFIED:
    - PropMaterial.SetMaterial(Name, MatType, Color=-1, Notes="", GUID="") -> Long
    - PropMaterial.SetMPIsotropic(Name, e, u, a, Temp=0) -> Long
    - AreaObj.SetMaterialOverwrite(Name, PropName, ItemType=Object) -> Long
    - EditArea.Divide(Name, MeshType, NumberAreas, AreaName, n1=2, n2=2, ...) -> Long
      Using MeshType=1 ("mesh into a specified number of objects"), which
      uses n1 (divisions along edge point1->point2) and n2 (divisions
      along edge point1->point3). NumberAreas and AreaName are ByRef
      outputs, positioned BEFORE n1/n2 in the parameter list.
    - AreaObj.GetProperty(Name, PropName) -> Long
    - PropArea.GetShell_1(Name, ShellType, IncludeDrillingDOF, MatProp,
      MatAng, Thickness, Bending, Color, Notes, GUID) -> Long
      (GetShell_1 used instead of the obsolete GetShell, per CHM release notes)
    - PropMaterial.GetMaterial(Name, MatType, Color, Notes, GUID) -> Long
    - PropMaterial.GetMPIsotropic(Name, e, u, a, g, Temp=0) -> Long

NOT USED / NOT VERIFIED:
    - MatProp does not apply when ShellType == 6 (layered/nonlinear) -
      automatic reading falls back to manual questions in that case.
---------------------------------------------------------------------------
"""

from core import selection_helper


# Mapping from friendly labels to the verified eMatType enum values.
_MAT_TYPE_OPTIONS = {
    "1": ("Steel", 1),
    "2": ("Concrete", 2),
    "3": ("NoDesign", 3),
    "4": ("Aluminum", 4),
    "5": ("ColdFormed", 5),
    "6": ("Rebar", 6),
    "7": ("Tendon", 7),
}

# MeshType = 1 -> mesh area into a specified number of objects (n1 x n2 grid)
_MESH_TYPE_SPECIFIED_NUMBER = 1


def _ask_mat_type():
    """
    Ask the user which eMatType to assign to the generated materials.
    SAP2000 has no dedicated "Masonry" type - "NoDesign" is the usual
    practical choice for materials only needed for mechanical properties.
    """
    print("\nSelect the material type for the generated materials "
          "(SAP2000 has no dedicated masonry type - use NoDesign for "
          "materials that only need mechanical properties, e.g. masonry):")
    for key, (label, _) in _MAT_TYPE_OPTIONS.items():
        print(f"  {key}) {label}")

    while True:
        choice = input("Enter number: ").strip()
        if choice in _MAT_TYPE_OPTIONS:
            label, value = _MAT_TYPE_OPTIONS[choice]
            return label, value
        print("Invalid choice, please try again.")


def _ask_float(prompt: str) -> float:
    """Thin wrapper kept for readability - delegates to the shared,
    typo-resilient helper in selection_helper."""
    return selection_helper.ask_float(prompt)


def _ask_int(prompt: str, minimum: int = 1) -> int:
    """Thin wrapper kept for readability - delegates to the shared,
    typo-resilient helper in selection_helper."""
    return selection_helper.ask_int(prompt, minimum=minimum)


def _mat_type_label_from_value(mat_type_value):
    for label, value in _MAT_TYPE_OPTIONS.values():
        if value == mat_type_value:
            return label
    return "Unknown"


def _read_original_material(sap_model, area_id):
    """
    Attempt to read the material type / Poisson ratio / thermal
    coefficient currently assigned to an area (shell) element, via the
    chain: AreaObj.GetProperty -> PropArea.GetShell_1 ->
    PropMaterial.GetMaterial -> PropMaterial.GetMPIsotropic.

    Returns (mat_type_label, mat_type_value, poisson_ratio, thermal_coeff)
    on success, or None if any step fails (e.g. the area property is not
    a shell-type section, or ShellType == 6 "layered/nonlinear" which
    has no single MatProp) - the caller should fall back to asking the
    user manually in that case.

    Verified: AreaObj.GetProperty(Name, PropName) -> Long
      PropName has no explicit ByVal -> ByRef by default ->
      [PropName, ret] = ...
    Verified: PropArea.GetShell_1(Name, ShellType, IncludeDrillingDOF,
      MatProp, MatAng, Thickness, Bending, Color, Notes, GUID) -> Long
      All output params ByRef -> full tuple unpack. MatProp does not
      apply when ShellType == 6 (layered/nonlinear).
    Verified: PropMaterial.GetMaterial / GetMPIsotropic - same as
      material_frame.py.
    """
    try:
        prop_name = ""
        [prop_name, ret] = sap_model.AreaObj.GetProperty(area_id, prop_name)
        if ret != 0 or not prop_name:
            return None

        shell_type = 0
        include_drilling_dof = False
        mat_prop = ""
        mat_ang = thickness = bending = 0.0
        color = 0
        notes, guid = "", ""
        [shell_type, include_drilling_dof, mat_prop, mat_ang, thickness, bending,
         color, notes, guid, ret] = sap_model.PropArea.GetShell_1(
            prop_name, shell_type, include_drilling_dof, mat_prop, mat_ang,
            thickness, bending, color, notes, guid
        )
        if ret != 0 or not mat_prop:
            return None  # e.g. ShellType == 6 (layered/nonlinear), no single MatProp

        mat_type_value = 0
        color2, notes2, guid2 = 0, "", ""
        [mat_type_value, color2, notes2, guid2, ret] = sap_model.PropMaterial.GetMaterial(
            mat_prop, mat_type_value, color2, notes2, guid2
        )
        if ret != 0:
            return None

        e_val, u_val, a_val, g_val = 0.0, 0.0, 0.0, 0.0
        [e_val, u_val, a_val, g_val, ret] = sap_model.PropMaterial.GetMPIsotropic(
            mat_prop, e_val, u_val, a_val, g_val
        )
        if ret != 0:
            return None

        return _mat_type_label_from_value(mat_type_value), mat_type_value, u_val, a_val

    except Exception:
        return None


def _divide_selected_areas(sap_model, area_ids):
    """
    For each area ID in area_ids, ask the user for the mesh divisions
    (n1 along edge point1->point2, n2 along edge point1->point3), then
    perform the meshing.

    Verified: EditArea.Divide(Name, MeshType, NumberAreas, AreaName,
                               n1=2, n2=2, ...) -> Long
    NumberAreas and AreaName are ByRef outputs - the call must be
    unpacked as [NumberAreas, AreaName, ret] = ...

    Returns the flat list of all new area object names resulting from
    every division (the original IDs no longer exist after meshing).
    """
    all_new_ids = []
    for area_id in area_ids:
        print(f"\nMesh divisions for area '{area_id}':")
        n1 = _ask_int("  n1 (divisions along edge point1->point2): ", minimum=1)
        n2 = _ask_int("  n2 (divisions along edge point1->point3): ", minimum=1)

        number_areas = 0
        area_name = []
        [number_areas, area_name, ret] = sap_model.EditArea.Divide(
            area_id, _MESH_TYPE_SPECIFIED_NUMBER, number_areas, area_name, n1, n2
        )
        if ret != 0:
            raise RuntimeError(f"Failed to divide area '{area_id}' (SAP2000 return code {ret}).")

        all_new_ids.extend(list(area_name))
        print(f"Area '{area_id}' divided into {number_areas} panels: {list(area_name)}")

    return all_new_ids


def interactive_setup(sap_model, base_material_name: str = None):
    """
    Run the full Phase A interactive workflow for the shell/area elastic
    modulus parameter category.

    Returns:
        dict with keys:
            "mat_type_label": str
            "poisson_ratio": float
            "thermal_coeff": float
            "elements": list of {"area_id": str, "material_name": str}
    """
    print("\n=== Shell/Area elastic modulus - setup ===")
    print(
        "TIP: in SAP2000, go to View > Set Display Options and enable "
        "'Labels' for Areas (and disable it for Points/Frames if it "
        "helps) so the area IDs are visible when you're asked to select them."
    )

    wants_category = selection_helper.ask_yes_no(
        "Do you want to configure Shell/Area elastic modulus for this run?"
    )
    if not wants_category:
        print("Skipping Shell/Area elastic modulus.")
        return None

    # Step 1: show all area IDs, let the user pick target elements.
    selected_ids = selection_helper.interactive_select(
        sap_model, "area", prompt_label="area (shell) elements"
    )

    # Step 2: ask whether the user wants to act on a portion of these
    # elements (i.e. mesh them into smaller panels first).
    wants_division = selection_helper.ask_yes_no(
        "\nDo you want to act on a portion of these elements "
        "(mesh them into smaller panels before assigning materials)?"
    )

    if wants_division:
        candidate_ids = _divide_selected_areas(sap_model, selected_ids)
        print(
            "\nMeshing complete. Area IDs have changed - "
            "please re-select the elements to act on from the updated list."
        )
        final_ids = selection_helper.interactive_select(
            sap_model, "area", candidate_ids=candidate_ids,
            prompt_label="area elements resulting from meshing"
        )
    else:
        final_ids = selected_ids

    # Step 3 (MOVED HERE per feedback, same as material_frame.py): ask
    # once whether E should be shared or independent per element, BEFORE
    # naming/creating materials.
    if len(final_ids) > 1:
        same_for_all_e = selection_helper.ask_yes_no(
            "\nUse the same E for all selected area elements? "
            "(a single value in single-run mode, or a shared candidate "
            "range in dataset mode - see the next question about "
            "independent vs synchronized variation)"
        )
    else:
        same_for_all_e = True  # moot with a single element

    # Step 4: try to read material type / Poisson ratio / thermal
    # coefficient automatically from the original material (works for
    # shell-type area sections with a single MatProp - falls back to
    # manual questions for ShellType==6 "layered/nonlinear" or if
    # reading otherwise fails).
    auto_read = _read_original_material(sap_model, final_ids[0])

    if auto_read is not None:
        mat_type_label, mat_type_value, poisson_ratio, thermal_coeff = auto_read
        print(
            f"\nRead from the original material (area '{final_ids[0]}'): "
            f"type={mat_type_label}, Poisson ratio={poisson_ratio}, "
            f"thermal coefficient={thermal_coeff}."
        )
        use_auto = selection_helper.ask_yes_no("Use these values for the generated materials?")
        if not use_auto:
            mat_type_label, mat_type_value = _ask_mat_type()
            poisson_ratio = _ask_float("Poisson's ratio for the generated materials: ")
            thermal_coeff = _ask_float("Thermal coefficient [1/T] for the generated materials: ")
    else:
        print(
            "\nCould not automatically read the original material properties "
            "(e.g. layered/nonlinear shell section, or reading failed) - "
            "please enter them manually."
        )
        mat_type_label, mat_type_value = _ask_mat_type()
        poisson_ratio = _ask_float("Poisson's ratio for the generated materials: ")
        thermal_coeff = _ask_float("Thermal coefficient [1/T] for the generated materials: ")

    base_name = base_material_name or input(
        "\nBase name for generated materials (e.g. 'CLS' -> 'CLS_12', 'CLS_13', ...): "
    ).strip()

    # Step 5: create one material per final area element and assign it.
    elements = []
    for area_id in final_ids:
        material_name = f"{base_name}_{area_id}"

        # Verified: SetMaterial(Name, MatType, Color=-1, Notes="", GUID="") -> Long
        ret = sap_model.PropMaterial.SetMaterial(material_name, mat_type_value)
        if ret != 0:
            raise RuntimeError(f"Failed to create material '{material_name}' (return code {ret}).")

        # Verified: AreaObj.SetMaterialOverwrite(Name, PropName, ItemType=Object) -> Long
        ret = sap_model.AreaObj.SetMaterialOverwrite(area_id, material_name)
        if ret != 0:
            raise RuntimeError(
                f"Failed to assign material '{material_name}' to area '{area_id}' "
                f"(return code {ret})."
            )

        elements.append({"area_id": area_id, "material_name": material_name})
        print(f"Created and assigned material '{material_name}' to area '{area_id}'.")

    return {
        "mat_type_label": mat_type_label,
        "poisson_ratio": poisson_ratio,
        "thermal_coeff": thermal_coeff,
        "elements": elements,
        "same_for_all_e": same_for_all_e,
    }


def interactive_define_values(setup_config: dict, mode: str):
    """
    Ask the user for the E value(s) to test for each material created
    during interactive_setup().

    Args:
        setup_config: the dict returned by interactive_setup().
        mode: "single_apply" or "dataset_creation".

    Returns:
        If mode == "single_apply":
            dict {material_name: E_value}
        If mode == "dataset_creation":
            list of dicts, one per material, each:
                {"material_name": str, "values": [E1, E2, E3, ...]}
    """
    if setup_config is None:
        # The category was skipped entirely during setup - nothing to define.
        return {} if mode == "single_apply" else []

    elements = setup_config["elements"]

    if mode not in ("single_apply", "dataset_creation"):
        raise ValueError("mode must be 'single_apply' or 'dataset_creation'")

    same_for_all = setup_config.get("same_for_all_e", True)

    if mode == "single_apply":
        if same_for_all:
            e_value = _ask_float("E value to apply to all selected elements [F/L^2]: ")
            return {el["material_name"]: e_value for el in elements}
        else:
            result = {}
            for el in elements:
                e_value = _ask_float(
                    f"E value for area '{el['area_id']}' (material '{el['material_name']}') [F/L^2]: "
                )
                result[el["material_name"]] = e_value
            return result

    # mode == "dataset_creation"
    def _ask_range():
        e_min = _ask_float("  Min E [F/L^2]: ")
        e_max = _ask_float("  Max E [F/L^2]: ")
        e_step = _ask_float("  Step [F/L^2]: ")
        values = []
        v = e_min
        while v <= e_max + 1e-9:
            values.append(round(v, 6))
            v += e_step
        return values

    dimensions = []
    if same_for_all:
        print("Define the shared E range:")
        shared_values = _ask_range()

        if len(elements) > 1:
            print(
                f"\nHow should these {len(elements)} elements vary in the "
                f"dataset grid?"
            )
            print(
                f"  1) IN SYNC - same E value applied to all of them together "
                f"each run (counts as ONE grid dimension)"
            )
            print(
                f"  2) INDEPENDENTLY - each element gets its own instance from "
                f"this range (multiplies combinations: {len(elements)} elements "
                f"x {len(shared_values)} values = "
                f"{len(shared_values) ** len(elements)} combinations from this "
                f"parameter alone)"
            )
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
                "material_names": [el["material_name"] for el in elements],
                "values": shared_values,
            })
        else:
            print(
                f"NOTE: each of the {len(elements)} elements will vary "
                f"INDEPENDENTLY using this same candidate list of "
                f"{len(shared_values)} values - this multiplies the grid by "
                f"{len(shared_values)}^{len(elements)} = "
                f"{len(shared_values) ** len(elements)} combinations from "
                f"this parameter alone, not just {len(shared_values)}."
            )
            for el in elements:
                dimensions.append({"material_name": el["material_name"], "values": shared_values})
    else:
        for el in elements:
            print(f"\nDefine E range for area '{el['area_id']}' (material '{el['material_name']}'):")
            values = _ask_range()
            dimensions.append({"material_name": el["material_name"], "values": values})

    return dimensions


def apply(sap_model, material_name: str, e_value: float,
          poisson_ratio: float, thermal_coeff: float):
    """
    Push a specific E value into the model for the given material, right
    before running an analysis.

    Verified: PropMaterial.SetMPIsotropic(Name, e, u, a, Temp=0) -> Long
    (This is the same material property table used by frame materials -
    shared function, no shell-specific variant needed.)
    """
    ret = sap_model.PropMaterial.SetMPIsotropic(material_name, e_value, poisson_ratio, thermal_coeff)
    if ret != 0:
        raise RuntimeError(
            f"Failed to set E={e_value} for material '{material_name}' (return code {ret})."
        )
