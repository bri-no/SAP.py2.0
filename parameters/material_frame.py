"""
material_frame.py

Handles the "Frame elastic modulus" parameter category.

Two-phase design (see project architecture discussion):

    Phase A - interactive_setup():
        One-time, permanent model modifications:
        - user selects target frame elements
        - optionally divides them into N equal parts
        - creates one new isotropic material per final frame element
        - assigns each new material to its frame element (material overwrite)
        Returns a config describing which frame element maps to which
        material name - this never changes again during the batch.

    Phase B - interactive_define_values() + apply():
        - interactive_define_values() asks the user for the E value(s) to
          test for each material (single value for single_apply mode, or a
          range + step for dataset_creation mode).
        - apply() pushes one specific E value into the model for a given
          material name, right before running an analysis.

---------------------------------------------------------------------------
API verification status (checked against CSI_OAPI_Documentation.chm):

VERIFIED:
    - PropMaterial.SetMaterial(Name, MatType, Color=-1, Notes="", GUID="") -> Long
    - PropMaterial.SetMPIsotropic(Name, e, u, a, Temp=0) -> Long
    - FrameObj.SetMaterialOverwrite(Name, PropName, ItemType=Object) -> Long
    - EditFrame.DivideByRatio(Name, Num, Ratio, NewName()) -> Long
      (Ratio=1.0 gives Num equal-length pieces)
    - EditFrame.DivideAtDistance(Name, Dist, IEnd, NewName()) -> Long
      Splits into exactly 2 pieces at Dist from the I-end (IEnd=True) or
      J-end (IEnd=False). Used repeatedly (on the remaining piece each
      time) to support custom, non-equal segment lengths.
    - FrameObj.GetSection(Name, PropName, SAuto) -> Long
    - PropFrame.GetGeneral(...) -> Long (works for "General" sections)
    - PropFrame.GetISection_1(...) -> Long (works for I-Type sections)
    - PropFrame.GetRectangle(...) -> Long (works for Rectangular sections)
    - PropFrame.GetCircle(...) -> Long (works for Circular sections)
    - PropMaterial.GetMaterial(Name, MatType, Color, Notes, GUID) -> Long
    - PropMaterial.GetMPIsotropic(Name, e, u, a, g, Temp=0) -> Long

NOT YET WIRED IN (present as commented reference code only):
    - FrameObj.SetReleases(...) -> Long - verified signature, kept as a
      commented-out future feature for optionally introducing a
      rotational release (hinge) between divided pieces instead of full
      continuity. Not asked about in the interactive flow yet.

NOT USED / NOT VERIFIED:
    - Section-type getters other than General, I-Section, Rectangle, and
      Circle (e.g. Tee, Channel, Angle) are not yet verified - for
      those, automatic material reading falls back to manual questions.
---------------------------------------------------------------------------
"""

import itertools

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


def _ask_mat_type():
    """
    Ask the user which eMatType to assign to the generated materials.
    SAP2000 has no dedicated "Masonry" type - for masonry (or any material
    only needed for its mechanical properties, not code-design checks),
    "NoDesign" is the usual practical choice.
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


def _mat_type_label_from_value(mat_type_value):
    for label, value in _MAT_TYPE_OPTIONS.values():
        if value == mat_type_value:
            return label
    return "Unknown"


def _try_get_mat_prop(sap_model, prop_name):
    """
    Try to read the material property name (MatProp) assigned to a frame
    section, trying each known section-type getter in turn (General,
    I-Section, Rectangular, Circular). Returns the MatProp string on
    success, or None if none of the known getters apply to this section
    (e.g. Tee/Channel/Angle sections, for which no getter has been
    verified yet in this project).

    Each attempt is wrapped in try/except AttributeError: some getters
    are version-dependent (e.g. GetISection_1 requires SAP2000 v25+ -
    earlier versions such as v24 only expose the older, superseded
    GetISection, which is not yet handled here) and comtypes raises
    AttributeError if the method doesn't exist in the installed SAP2000
    version's type library. Catching this lets the chain move on to the
    next attempt instead of crashing.

    Verified: PropFrame.GetGeneral(...) -> Long
    Verified: PropFrame.GetISection_1(...) -> Long (v25+ only - replaces
      the older GetISection per the CHM release notes)
    Verified: PropFrame.GetRectangle(...) -> Long
    Verified: PropFrame.GetCircle(...) -> Long
    """
    # Attempt 1: General section.
    try:
        file_name, mat_prop = "", ""
        t3 = t2 = area = as2 = as3 = torsion = 0.0
        i22 = i33 = s22 = s33 = z22 = z33 = r22 = r33 = 0.0
        color = 0
        notes, guid = "", ""
        [file_name, mat_prop, t3, t2, area, as2, as3, torsion, i22, i33,
         s22, s33, z22, z33, r22, r33, color, notes, guid, ret] = sap_model.PropFrame.GetGeneral(
            prop_name, file_name, mat_prop, t3, t2, area, as2, as3, torsion, i22, i33,
            s22, s33, z22, z33, r22, r33, color, notes, guid
        )
        if ret == 0 and mat_prop:
            return mat_prop
    except AttributeError:
        pass

    # Attempt 2: I-Type section (GetISection_1 requires SAP2000 v25+).
    try:
        file_name, mat_prop = "", ""
        t3 = t2 = tf = tw = t2b = tfb = fillet_radius = 0.0
        color = 0
        notes, guid = "", ""
        [file_name, mat_prop, t3, t2, tf, tw, t2b, tfb, fillet_radius,
         color, notes, guid, ret] = sap_model.PropFrame.GetISection_1(
            prop_name, file_name, mat_prop, t3, t2, tf, tw, t2b, tfb,
            fillet_radius, color, notes, guid
        )
        if ret == 0 and mat_prop:
            return mat_prop
    except AttributeError:
        pass

    # Attempt 3: Rectangular section.
    try:
        file_name, mat_prop = "", ""
        t3 = t2 = 0.0
        color = 0
        notes, guid = "", ""
        [file_name, mat_prop, t3, t2, color, notes, guid, ret] = sap_model.PropFrame.GetRectangle(
            prop_name, file_name, mat_prop, t3, t2, color, notes, guid
        )
        if ret == 0 and mat_prop:
            return mat_prop
    except AttributeError:
        pass

    # Attempt 4: Circular section.
    try:
        file_name, mat_prop = "", ""
        t3 = 0.0
        color = 0
        notes, guid = "", ""
        [file_name, mat_prop, t3, color, notes, guid, ret] = sap_model.PropFrame.GetCircle(
            prop_name, file_name, mat_prop, t3, color, notes, guid
        )
        if ret == 0 and mat_prop:
            return mat_prop
    except AttributeError:
        pass

    # Add further section-type getters here as they get verified
    # (Tee, Channel, Angle, ...), each wrapped in its own try/except.
    return None


def _read_original_material(sap_model, frame_id):
    """
    Attempt to read the material type / Poisson ratio / thermal
    coefficient currently assigned to a frame element, via the chain:
        FrameObj.GetSection -> _try_get_mat_prop (General or I-Section)
        -> PropMaterial.GetMaterial -> PropMaterial.GetMPIsotropic

    Returns (mat_type_label, mat_type_value, poisson_ratio, thermal_coeff)
    on success, or None if any step fails (including an unsupported
    section type) - the caller should fall back to asking the user
    manually in that case.

    Verified: FrameObj.GetSection(Name, PropName, SAuto) -> Long
      PropName, SAuto have no explicit ByVal -> ByRef by default ->
      [PropName, SAuto, ret] = ...
    Verified: PropMaterial.GetMaterial(Name, MatType, Color, Notes, GUID) -> Long
    Verified: PropMaterial.GetMPIsotropic(Name, e, u, a, g, Temp=0) -> Long
      e,u,a,g have no explicit ByVal -> ByRef by default ->
      [e, u, a, g, ret] = ...
    """
    try:
        prop_name, s_auto = "", ""
        [prop_name, s_auto, ret] = sap_model.FrameObj.GetSection(frame_id, prop_name, s_auto)
        if ret != 0 or not prop_name:
            return None

        mat_prop = _try_get_mat_prop(sap_model, prop_name)
        if not mat_prop:
            return None  # unsupported section type - fall back to manual

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


# ---------------------------------------------------------------------
# FUTURE FEATURE (not active) - end releases between divided pieces
#
# The user's original request (b): after dividing a frame into pieces,
# optionally introduce a release (hinge) at the cut points instead of
# leaving them fully rotationally continuous - this could simulate
# another kind of damage/discontinuity scenario. This adds real
# complexity (need to track which two pieces share a given cut point,
# and apply the release to the correct end - I-end or J-end - of each
# side), so it is left here as a commented reference only, not wired
# into interactive_setup() or asked about yet.
#
# Verified: FrameObj.SetReleases(Name, ii(), jj(), StartValue(), EndValue(),
#                                 ItemType=Object) -> Long
#   ii, jj: arrays of 6 booleans [U1,U2,U3,R1,R2,R3] - I-end / J-end release flags.
#   StartValue, EndValue: arrays of 6 doubles - partial fixity springs,
#     only meaningful for DOFs that are released (0.0 = fully released).
#   All four arrays are ByRef -> comtypes call returns a full tuple:
#     [ii_echo, jj_echo, StartValue_echo, EndValue_echo, ret] = ...
#   WARNING (from CHM): certain release combinations cause instability
#   (e.g. U1 released at both ends of every member meeting at a joint) -
#   SAP2000 returns a nonzero code in that case, must be checked.
#
# def _ask_end_release_for_cut(sap_model, piece_a_id, piece_b_id):
#     """
#     Ask whether to introduce a moment release (hinge) at the cut point
#     between two adjacent divided pieces, releasing R3 (typically the
#     main bending direction) at the J-end of piece_a and the I-end of
#     piece_b. Extend to other DOFs (R1, R2) if needed.
#     """
#     wants_release = selection_helper.ask_yes_no(
#         f"Introduce a rotational release (hinge) between '{piece_a_id}' "
#         f"and '{piece_b_id}' instead of full continuity?"
#     )
#     if not wants_release:
#         return
#
#     # Release R3 at the J-end of piece_a (no release at its I-end).
#     ii_a = [False, False, False, False, False, False]
#     jj_a = [False, False, False, False, False, True]
#     start_value_a = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
#     end_value_a = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
#     [ii_e, jj_e, sv_e, ev_e, ret] = sap_model.FrameObj.SetReleases(
#         piece_a_id, ii_a, jj_a, start_value_a, end_value_a
#     )
#     if ret != 0:
#         raise RuntimeError(f"Failed to set end release on '{piece_a_id}' (code {ret}).")
#
#     # Release R3 at the I-end of piece_b (no release at its J-end).
#     ii_b = [False, False, False, False, False, True]
#     jj_b = [False, False, False, False, False, False]
#     start_value_b = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
#     end_value_b = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
#     [ii_e, jj_e, sv_e, ev_e, ret] = sap_model.FrameObj.SetReleases(
#         piece_b_id, ii_b, jj_b, start_value_b, end_value_b
#     )
#     if ret != 0:
#         raise RuntimeError(f"Failed to set end release on '{piece_b_id}' (code {ret}).")
# ---------------------------------------------------------------------


def _divide_by_equal_parts(sap_model, frame_id):
    """
    Divide a single frame into N equal-length parts.

    Verified: EditFrame.DivideByRatio(Name, Num, Ratio, NewName) -> Long
    Ratio = 1.0 -> all resulting pieces have equal length.
    """
    num_parts = selection_helper.ask_int(
        f"How many equal parts should frame '{frame_id}' be divided into? ",
        minimum=2
    )
    new_name = []
    [new_name, ret] = sap_model.EditFrame.DivideByRatio(frame_id, num_parts, 1.0, new_name)
    if ret != 0:
        raise RuntimeError(f"Failed to divide frame '{frame_id}' (SAP2000 return code {ret}).")
    print(f"Frame '{frame_id}' divided into {num_parts} equal parts: {list(new_name)}")
    return list(new_name)


def _divide_by_custom_lengths(sap_model, frame_id):
    """
    Divide a single frame into segments of user-specified lengths,
    measured from the I-end, by repeatedly calling DivideAtDistance on
    the remaining piece after each cut.

    Verified: EditFrame.DivideAtDistance(Name, Dist, IEnd, NewName) -> Long
    Splits a frame into exactly TWO pieces at Dist from the I-end
    (IEnd=True) or J-end (IEnd=False). NewName() is ByRef -> call
    returns [NewName, ret].

    The user enters segment lengths in order from the I-end (e.g.
    "1.5, 2.0, 1.0" cuts the frame into a 1.5-long piece, then a
    2.0-long piece, then a 1.0-long piece, with whatever remains
    becoming the final piece automatically - so lengths do not need to
    sum exactly to the total frame length).
    """
    raw = input(
        f"Enter the segment lengths for frame '{frame_id}', in order from "
        f"the I-end, comma separated (e.g. '1.5, 2.0, 1.0'; the remainder "
        f"becomes the last piece automatically): "
    )
    try:
        segment_lengths = [float(x.strip()) for x in raw.split(",") if x.strip()]
    except ValueError:
        raise ValueError(f"Could not parse segment lengths from '{raw}'.")

    if len(segment_lengths) < 1:
        raise ValueError("At least one segment length is required.")

    all_pieces = []
    remaining_name = frame_id

    # Every length except the last one requires an explicit cut; the
    # last piece is simply whatever remains after the final cut.
    for length in segment_lengths[:-1]:
        new_name = []
        [new_name, ret] = sap_model.EditFrame.DivideAtDistance(remaining_name, length, True, new_name)
        if ret != 0:
            raise RuntimeError(
                f"Failed to divide frame '{remaining_name}' at distance {length} "
                f"(SAP2000 return code {ret})."
            )
        new_name = list(new_name)
        if len(new_name) != 2:
            raise RuntimeError(
                f"Unexpected DivideAtDistance result for '{remaining_name}': {new_name}"
            )
        piece, remaining_name = new_name[0], new_name[1]
        all_pieces.append(piece)

    all_pieces.append(remaining_name)  # final leftover piece
    print(f"Frame '{frame_id}' divided into {len(all_pieces)} custom-length parts: {all_pieces}")
    return all_pieces


def _divide_selected_frames(sap_model, frame_ids):
    """
    For each frame ID in frame_ids, ask the user whether to divide it
    into equal parts or into custom-length segments, then perform the
    chosen division.

    Returns the flat list of all new frame object names resulting from
    every division (the original IDs no longer exist after division).
    """
    all_new_ids = []
    for frame_id in frame_ids:
        print(f"\nDivision method for frame '{frame_id}':")
        print("  1) Equal parts")
        print("  2) Custom segment lengths")
        while True:
            choice = input("Enter 1 or 2: ").strip()
            if choice in ("1", "2"):
                break
            print("Please enter 1 or 2.")

        if choice == "1":
            new_ids = _divide_by_equal_parts(sap_model, frame_id)
        else:
            new_ids = _divide_by_custom_lengths(sap_model, frame_id)

        all_new_ids.extend(new_ids)

    return all_new_ids


def interactive_setup(sap_model, base_material_name: str = None):
    """
    Run the full Phase A interactive workflow for the frame elastic
    modulus parameter category.

    Returns:
        dict with keys:
            "mat_type_label": str
            "poisson_ratio": float
            "thermal_coeff": float
            "elements": list of {"frame_id": str, "material_name": str}
    """
    print("\n=== Frame elastic modulus - setup ===")
    print(
        "TIP: in SAP2000, go to View > Set Display Options and enable "
        "'Labels' for Frames (and disable it for Points/Areas if it "
        "helps) so the frame IDs are visible when you're asked to select them."
    )

    wants_category = selection_helper.ask_yes_no(
        "Do you want to configure Frame elastic modulus for this run?"
    )
    if not wants_category:
        print("Skipping Frame elastic modulus.")
        return None

    # Step 1: show all frame IDs, let the user pick target elements.
    selected_ids = selection_helper.interactive_select(
        sap_model, "frame", prompt_label="frame elements"
    )

    # Step 2: ask whether the user wants to act on a portion of these
    # elements (i.e. divide them into smaller pieces first).
    wants_division = selection_helper.ask_yes_no(
        "\nDo you want to act on a portion of these elements "
        "(divide them into smaller parts before assigning materials)?"
    )

    if wants_division:
        candidate_ids = _divide_selected_frames(sap_model, selected_ids)
        print(
            "\nDivision complete. Frame IDs have changed - "
            "please re-select the elements to act on from the updated list."
        )
        final_ids = selection_helper.interactive_select(
            sap_model, "frame", candidate_ids=candidate_ids,
            prompt_label="frame elements resulting from division"
        )
    else:
        final_ids = selected_ids

    # Step 3 (MOVED HERE per feedback): ask once whether E should be
    # shared or independent per element, BEFORE naming/creating
    # materials - so the whole setup decision is front-loaded and
    # doesn't feel disconnected from the later per-element material
    # creation.
    if len(final_ids) > 1:
        same_for_all_e = selection_helper.ask_yes_no(
            "\nUse the same E for all selected frame elements? "
            "(a single value in single-run mode, or a shared candidate "
            "range in dataset mode - see the next question about "
            "independent vs synchronized variation)"
        )
    else:
        same_for_all_e = True  # moot with a single element

    # Step 4: try to read material type / Poisson ratio / thermal
    # coefficient automatically from the original material (works only
    # if the frame's section is of type "General" - falls back to
    # manual questions otherwise).
    auto_read = _read_original_material(sap_model, final_ids[0])

    if auto_read is not None:
        mat_type_label, mat_type_value, poisson_ratio, thermal_coeff = auto_read
        print(
            f"\nRead from the original material (frame '{final_ids[0]}'): "
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
            "(section type is not 'General', or reading failed) - "
            "please enter them manually."
        )
        mat_type_label, mat_type_value = _ask_mat_type()
        poisson_ratio = _ask_float("Poisson's ratio for the generated materials: ")
        thermal_coeff = _ask_float("Thermal coefficient [1/T] for the generated materials: ")

    base_name = base_material_name or input(
        "\nBase name for generated materials (e.g. 'MU' -> 'MU_12', 'MU_13', ...): "
    ).strip()

    # Step 5: create one material per final frame element and assign it.
    elements = []
    for frame_id in final_ids:
        material_name = f"{base_name}_{frame_id}"

        # Verified: SetMaterial(Name, MatType, Color=-1, Notes="", GUID="") -> Long
        ret = sap_model.PropMaterial.SetMaterial(material_name, mat_type_value)
        if ret != 0:
            raise RuntimeError(f"Failed to create material '{material_name}' (return code {ret}).")

        # Verified: FrameObj.SetMaterialOverwrite(Name, PropName, ItemType=Object) -> Long
        ret = sap_model.FrameObj.SetMaterialOverwrite(frame_id, material_name)
        if ret != 0:
            raise RuntimeError(
                f"Failed to assign material '{material_name}' to frame '{frame_id}' "
                f"(return code {ret})."
            )

        elements.append({"frame_id": frame_id, "material_name": material_name})
        print(f"Created and assigned material '{material_name}' to frame '{frame_id}'.")

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
            (ready to be combined with other parameter categories via
            itertools.product in the dataset generator)
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
                    f"E value for frame '{el['frame_id']}' (material '{el['material_name']}') [F/L^2]: "
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
            vary_in_sync = False  # moot with a single element

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
            print(f"\nDefine E range for frame '{el['frame_id']}' (material '{el['material_name']}'):")
            values = _ask_range()
            dimensions.append({"material_name": el["material_name"], "values": values})

    return dimensions


def apply(sap_model, material_name: str, e_value: float,
          poisson_ratio: float, thermal_coeff: float):
    """
    Push a specific E value into the model for the given material, right
    before running an analysis.

    Verified: PropMaterial.SetMPIsotropic(Name, e, u, a, Temp=0) -> Long
    Poisson ratio and thermal coefficient are kept constant (as defined
    during interactive_setup) - only E varies between runs.
    """
    ret = sap_model.PropMaterial.SetMPIsotropic(material_name, e_value, poisson_ratio, thermal_coeff)
    if ret != 0:
        raise RuntimeError(
            f"Failed to set E={e_value} for material '{material_name}' (return code {ret})."
        )
