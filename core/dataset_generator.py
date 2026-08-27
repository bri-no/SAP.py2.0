"""
dataset_generator.py

Orchestrates the "dataset_creation" workflow: collects variable
dimensions from every chosen parameter category, builds the full
Cartesian product grid of all combinations, and for each combination:
applies the parameter values, runs the MODAL analysis, extracts modal
results, and writes one row to a CSV dataset.

CSV SCHEMA (wide format - one row per scenario/combination, one column
per parameter and per modal output, ready for ML/statistical use):

    scenario_id, <param columns...>, mode1_f, mode1_UX, mode1_UY,
    mode1_UZ, mode1_RX, mode1_RY, mode1_RZ, mode2_f, ..., analysis_status

Param columns are named after the natural, already-unique identifier of
each variable element:
    - frame/shell material (independent):  the generated material name
      (e.g. "MU_12")
    - frame/shell material (synchronized group): the joined material
      names (e.g. "MU_12+MU_13+MU_14") - these share ONE column and ONE
      value per row, since they always move together (see
      material_frame.py / material_shell.py "vary IN SYNC" option)
    - diaphragm membership:  "diaph_node_<node_id>"
    - equal constraint:      "equal_<name>" (or "equal_<name1>_<name2>_..."
                              for a simultaneous group)
    - external spring:       "K_<direction>_node_<node_id>"

Each combination represents one simulated "damage-like" scenario -
varying material stiffness, constraint configuration, and support
conditions to populate the space of plausible structural states for
downstream ML/statistical use (the original motivation for this tool).
"""

import csv
import itertools
import os
import time

from core import selection_helper, modal_extractor
from parameters import material_frame, material_shell, internal_constraints, external_constraints


CATEGORY_OPTIONS = {
    "frame_material": "Frame elastic modulus",
    "shell_material": "Shell/Area elastic modulus",
    "internal_constraints": "Internal constraints (diaphragm, equal-displacement)",
    "external_constraints": "External constraints (base restraints/springs)",
}

# Above this many combinations, ask for explicit confirmation before
# running (each combination costs a full SAP2000 analysis - a large grid
# can take a very long time).
_LARGE_GRID_WARNING_THRESHOLD = 500


def _normalize_frame_shell_dimensions(raw_dimensions, category):
    """
    Normalize material_frame.py / material_shell.py dataset_creation
    dimensions into the common internal shape used by this module.

    Each raw dimension is either:
        {"material_name": str, "values": [...]}            (independent)
    or
        {"material_names": [str, ...], "values": [...]}     (synchronized group -
                                                               all share one value)
    """
    normalized = []
    for dim in raw_dimensions:
        if "material_names" in dim:
            names = dim["material_names"]
            normalized.append({
                "category": category,
                "column_name": "+".join(names),
                "key": list(names),  # list key -> _build_apply_args applies to all of them
                "values": dim["values"],
            })
        else:
            normalized.append({
                "category": category,
                "column_name": dim["material_name"],
                "key": dim["material_name"],  # string key -> single material
                "values": dim["values"],
            })
    return normalized


def _normalize_internal_constraints_dimensions(raw_dimensions):
    """
    Normalize internal_constraints.py dataset_creation dimensions
    (mix of "diaphragm" and "equal_group" kinds) into the common shape.
    """
    normalized = []
    for dim in raw_dimensions:
        if dim["kind"] == "diaphragm":
            normalized.append({
                "category": "diaphragm",
                "column_name": f"diaph_node_{dim['node_id']}",
                "key": dim["node_id"],
                "values": dim["values"],
            })
        elif dim["kind"] == "equal_group":
            names = dim["names"]
            normalized.append({
                "category": "equal_group",
                "column_name": "equal_" + "_".join(names),
                "key": tuple(names),
                "values": dim["values"],
            })
    return normalized


def _normalize_external_constraints_dimensions(raw_dimensions):
    """
    Normalize external_constraints.py dataset_creation dimensions into
    the common shape.

    Each raw dimension is either:
        {"node_id": str, "direction": str, "values": [...]}              (independent)
    or
        {"node_directions": [(node_id, direction), ...], "values": [...]} (synchronized
                                                                            group - all
                                                                            share one value)
    """
    normalized = []
    for dim in raw_dimensions:
        if "node_directions" in dim:
            items = dim["node_directions"]
            column_name = "+".join(f"K_{direction}_node_{node_id}" for node_id, direction in items)
            normalized.append({
                "category": "external_spring",
                "column_name": column_name,
                "key": list(items),  # list of (node_id, direction) tuples -> apply to all
                "values": dim["values"],
            })
        else:
            normalized.append({
                "category": "external_spring",
                "column_name": f"K_{dim['direction']}_node_{dim['node_id']}",
                "key": (dim["node_id"], dim["direction"]),
                "values": dim["values"],
            })
    return normalized


def _collect_dimensions(sap_model, chosen_categories):
    """
    Run interactive_setup() (+ interactive_define_values() where
    applicable) for every chosen category, collect the setup configs
    needed later by apply(), and return the full list of normalized
    dimensions across all categories combined.

    Any category the user opts out of during its own setup (returns
    None) is simply skipped - not added to setups or all_dimensions.

    Returns:
        (setups, all_dimensions)
            setups: dict {category: setup_config}
            all_dimensions: list of normalized dimension dicts (see
                _normalize_* functions above)
    """
    setups = {}
    all_dimensions = []

    if "frame_material" in chosen_categories:
        frame_setup = material_frame.interactive_setup(sap_model)
        if frame_setup is not None:
            setups["frame_material"] = frame_setup
            raw = material_frame.interactive_define_values(frame_setup, "dataset_creation")
            all_dimensions.extend(_normalize_frame_shell_dimensions(raw, "frame_material"))

    if "shell_material" in chosen_categories:
        shell_setup = material_shell.interactive_setup(sap_model)
        if shell_setup is not None:
            setups["shell_material"] = shell_setup
            raw = material_shell.interactive_define_values(shell_setup, "dataset_creation")
            all_dimensions.extend(_normalize_frame_shell_dimensions(raw, "shell_material"))

    if "internal_constraints" in chosen_categories:
        internal_setup = internal_constraints.interactive_setup(sap_model)
        if internal_setup is not None:
            setups["internal_constraints"] = internal_setup
            raw = internal_constraints.interactive_define_values(
                internal_setup, "dataset_creation"
            )
            all_dimensions.extend(_normalize_internal_constraints_dimensions(raw))

    if "external_constraints" in chosen_categories:
        # external_constraints has a merged setup+values interface (see
        # its module docstring) - mode is passed directly to interactive_setup.
        external_setup, raw = external_constraints.interactive_setup(
            sap_model, "dataset_creation"
        )
        if external_setup is not None:
            setups["external_constraints"] = external_setup
            all_dimensions.extend(_normalize_external_constraints_dimensions(raw))

    return setups, all_dimensions


def _build_apply_args(combo, all_dimensions):
    """
    Given one specific combination (a tuple of chosen values, one per
    dimension, in the same order as all_dimensions), reconstruct the
    per-category argument structures expected by each module's apply().

    A dimension's "key" can be:
        - a single string (frame_material/shell_material independent,
          diaphragm node_id) -> applies the value to that one key
        - a list of strings (frame_material/shell_material synchronized
          group) -> applies the SAME value to every key in the list
        - a tuple of strings (equal_group) -> applies the SAME value to
          every name in the tuple
        - a tuple (node_id, direction) (external_spring, independent) ->
          applies the value to that one (node_id, direction) pair
        - a list of (node_id, direction) tuples (external_spring,
          synchronized group) -> applies the SAME value to every pair

    Returns a dict:
        {
            "frame_material": {material_name: value, ...},
            "shell_material": {material_name: value, ...},
            "diaphragm_assignment": {node_id: diaphragm_name, ...},
            "equal_states": {constraint_name: "ON"/"OFF", ...},
            "spring_values": {(node_id, direction): value, ...},
        }
    """
    args = {
        "frame_material": {},
        "shell_material": {},
        "diaphragm_assignment": {},
        "equal_states": {},
        "spring_values": {},
    }

    for dim, value in zip(all_dimensions, combo):
        category = dim["category"]
        key = dim["key"]

        if category == "frame_material":
            if isinstance(key, list):
                for name in key:
                    args["frame_material"][name] = value
            else:
                args["frame_material"][key] = value
        elif category == "shell_material":
            if isinstance(key, list):
                for name in key:
                    args["shell_material"][name] = value
            else:
                args["shell_material"][key] = value
        elif category == "diaphragm":
            args["diaphragm_assignment"][key] = value
        elif category == "equal_group":
            for name in key:  # key is a tuple of constraint names in this group
                args["equal_states"][name] = value
        elif category == "external_spring":
            if isinstance(key, list):
                for node_id, direction in key:
                    args["spring_values"][(node_id, direction)] = value
            else:
                args["spring_values"][key] = value

    return args


def _apply_combination(sap, setups, apply_args):
    """
    Apply one full combination to the model, right before running the
    analysis. `sap` is the SapInterface instance (needed for unlock_model()).
    """
    sap.unlock_model()

    if apply_args["frame_material"]:
        cfg = setups["frame_material"]
        for material_name, e_value in apply_args["frame_material"].items():
            material_frame.apply(sap.sap_model, material_name, e_value,
                                  cfg["poisson_ratio"], cfg["thermal_coeff"])

    if apply_args["shell_material"]:
        cfg = setups["shell_material"]
        for material_name, e_value in apply_args["shell_material"].items():
            material_shell.apply(sap.sap_model, material_name, e_value,
                                  cfg["poisson_ratio"], cfg["thermal_coeff"])

    if "internal_constraints" in setups and (apply_args["diaphragm_assignment"] or apply_args["equal_states"]):
        internal_constraints.apply(
            sap.sap_model, setups["internal_constraints"],
            diaphragm_assignment=apply_args["diaphragm_assignment"],
            equal_states=apply_args["equal_states"],
        )

    if apply_args["spring_values"]:
        external_constraints.apply(
            sap.sap_model, setups["external_constraints"], apply_args["spring_values"]
        )


def run_dataset_creation(sap, output_csv_path: str):
    """
    Full dataset generation workflow.

    Args:
        sap: a connected, model-open SapInterface instance (see
            core/sap_interface.py). Its sap_model is used for all
            parameter setup/apply calls; sap itself is used for
            unlock_model(), save(), and run_analysis().
        output_csv_path: full path where the resulting CSV dataset will
            be written.
    """
    if not output_csv_path or not output_csv_path.strip():
        raise ValueError("output_csv_path cannot be empty.")

    sap_model = sap.sap_model

    chosen = selection_helper.ask_multi_choice(
        "Which parameter categories do you want to include in the dataset?",
        CATEGORY_OPTIONS,
    )
    if not chosen:
        print("No categories selected - nothing to generate.")
        return

    setups, all_dimensions = _collect_dimensions(sap_model, chosen)

    if not all_dimensions:
        print("No variable dimensions were defined - nothing to generate.")
        return

    num_modes = modal_extractor.ask_num_modes()

    # Build the full grid.
    value_lists = [dim["values"] for dim in all_dimensions]
    total_combinations = 1
    for values in value_lists:
        total_combinations *= len(values)

    print(f"\nGrid summary: {len(all_dimensions)} variable dimension(s), "
          f"{total_combinations} total combination(s).")
    for dim in all_dimensions:
        print(f"  {dim['column_name']}: {len(dim['values'])} value(s)")

    if total_combinations > _LARGE_GRID_WARNING_THRESHOLD:
        proceed = selection_helper.ask_yes_no(
            f"\nThis grid has {total_combinations} combinations - each requires a full "
            f"SAP2000 analysis and could take a long time. Proceed anyway?"
        )
        if not proceed:
            print("Dataset generation cancelled.")
            return

    combinations = list(itertools.product(*value_lists))

    # Prepare CSV.
    param_columns = [dim["column_name"] for dim in all_dimensions]
    mode_columns = []
    for i in range(num_modes):
        mode_columns.extend([
            f"mode{i+1}_f", f"mode{i+1}_UX", f"mode{i+1}_UY", f"mode{i+1}_UZ",
            f"mode{i+1}_RX", f"mode{i+1}_RY", f"mode{i+1}_RZ",
        ])
    fieldnames = ["scenario_id"] + param_columns + mode_columns + ["analysis_status"]

    output_dir = os.path.dirname(output_csv_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    success_count = 0
    failure_count = 0
    start_time = time.time()

    with open(output_csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for i, combo in enumerate(combinations):
            scenario_id = i + 1
            row = {"scenario_id": scenario_id}
            for dim, value in zip(all_dimensions, combo):
                row[dim["column_name"]] = value

            print(f"\n[{scenario_id}/{total_combinations}] Applying combination...")

            try:
                apply_args = _build_apply_args(combo, all_dimensions)
                _apply_combination(sap, setups, apply_args)

                sap.save()
                sap.set_active_case("MODAL", exclusive=True)
                ret = sap.run_analysis()
                if ret != 0:
                    raise RuntimeError(f"RunAnalysis failed (return code {ret}).")

                # NOT USED: sap.reopen_for_postprocessing() was originally
                # called here defensively (see its docstring in
                # sap_interface.py) to guard against stale-state results
                # after RunAnalysis(). Left commented rather than deleted:
                # the user has consistently gotten correct modal results
                # from other automated scripts that never reopen the file
                # between analyses, so it's being skipped here too - this
                # also removes the SAP2000 window flicker (taskbar icon
                # disappearing/reappearing) seen during dataset batches,
                # and should measurably speed up large grids since
                # File.OpenFile() is not free per combination.
                # If stale/incorrect modal results are ever observed in a
                # dataset run, uncomment the line below first before
                # investigating further:
                # sap.reopen_for_postprocessing()
                results = modal_extractor.extract_modal_results(sap_model, num_modes)
                mode_row = modal_extractor.modal_results_as_row(results, num_modes)
                row.update(mode_row)
                row["analysis_status"] = "success"
                success_count += 1

            except Exception as e:
                print(f"  FAILED: {e}")
                for col in mode_columns:
                    row[col] = None
                row["analysis_status"] = f"failed: {e}"
                failure_count += 1

            writer.writerow(row)
            f.flush()  # keep partial progress safe if the run is interrupted

    elapsed = time.time() - start_time
    print(f"\n=== Dataset generation complete ===")
    print(f"Total combinations: {total_combinations}")
    print(f"Successful: {success_count}, Failed: {failure_count}")
    print(f"Elapsed time: {elapsed/60:.1f} minutes")
    print(f"CSV written to: {output_csv_path}")
