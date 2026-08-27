"""
main.py

Top-level orchestrator for the SAP2000 model updating / dataset
generation tool.

Instead of cascading through every parameter category in sequence
(frame material -> shell material -> internal constraints -> external
constraints), the user is asked UPFRONT which categories to activate.
Only the chosen categories' interactive_setup() functions are called.

STATUS: this demonstrates the pattern for single_apply mode using the
categories that exist so far (material_frame, material_shell,
internal_constraints). external_constraints.py and dataset_generator.py
are not yet written - dataset_creation mode and the "external
constraints" category are placeholders here, to be wired in once those
modules exist.
"""

import os
import re

from core import selection_helper
from core.sap_interface import SapInterface
from parameters import material_frame, material_shell, internal_constraints, external_constraints
# from core import dataset_generator            # imported lazily inside run_dataset_creation


CATEGORY_OPTIONS = {
    "frame_material": "Frame elastic modulus",
    "shell_material": "Shell/Area elastic modulus",
    "internal_constraints": "Internal constraints (diaphragm, equal-displacement)",
    "external_constraints": "External constraints (base restraints/springs)",
}

# Default CSI installation directory - SAP2000 versions are installed as
# subfolders here, e.g. "SAP2000 24", "SAP2000 26", each containing its
# own SAP2000.exe.
_DEFAULT_CSI_INSTALL_DIR = r"C:\Program Files\Computers and Structures"


def _find_sap2000_installations(base_dir: str = _DEFAULT_CSI_INSTALL_DIR):
    """
    Scan the default CSI installation directory for available SAP2000
    versions.

    Returns a list of (version_label, exe_path) tuples, sorted by
    version number descending (newest first). Empty list if the base
    directory doesn't exist or no matching installation is found.
    """
    installations = []
    if not os.path.isdir(base_dir):
        return installations

    for entry in os.listdir(base_dir):
        match = re.match(r"SAP2000\s+(\d+)", entry, re.IGNORECASE)
        if not match:
            continue
        exe_path = os.path.join(base_dir, entry, "SAP2000.exe")
        if os.path.isfile(exe_path):
            version_number = int(match.group(1))
            installations.append((version_number, entry, exe_path))

    installations.sort(key=lambda x: x[0], reverse=True)
    return [(label, path) for _, label, path in installations]


def _choose_program_path():
    """
    Auto-detect installed SAP2000 versions under the default CSI install
    directory. If exactly one is found, use it automatically. If
    multiple are found, ask the user which one to use. If none are
    found, ask the user to enter the path manually.
    """
    installations = _find_sap2000_installations()

    if len(installations) == 1:
        label, path = installations[0]
        print(f"Found SAP2000 installation: {label} ({path})")
        return path

    if len(installations) > 1:
        print("Multiple SAP2000 installations found:")
        for i, (label, path) in enumerate(installations, start=1):
            print(f"  {i}) {label} ({path})")
        while True:
            choice = input(f"Choose which one to use (1-{len(installations)}): ").strip()
            try:
                idx = int(choice)
                if 1 <= idx <= len(installations):
                    return installations[idx - 1][1]
            except ValueError:
                pass
            print("Invalid choice, please try again.")

    # No installations auto-detected under the default path - fall back
    # to manual entry (e.g. non-default install location).
    print(
        f"No SAP2000 installation auto-detected under "
        f"'{_DEFAULT_CSI_INSTALL_DIR}'."
    )
    return input("Enter the full path to SAP2000.exe manually: ").strip()


def run_single_apply(sap_model):
    """
    Run the single-scenario update workflow: only ask about, set up, and
    apply the parameter categories the user selects upfront.
    """
    chosen = selection_helper.ask_multi_choice(
        "Which parameter categories do you want to configure for this run?",
        CATEGORY_OPTIONS,
    )

    if not chosen:
        print("No categories selected - nothing to do.")
        return

    setups = {}
    values = {}

    if "frame_material" in chosen:
        frame_setup = material_frame.interactive_setup(sap_model)
        if frame_setup is not None:
            setups["frame_material"] = frame_setup
            values["frame_material"] = material_frame.interactive_define_values(
                frame_setup, "single_apply"
            )

    if "shell_material" in chosen:
        shell_setup = material_shell.interactive_setup(sap_model)
        if shell_setup is not None:
            setups["shell_material"] = shell_setup
            values["shell_material"] = material_shell.interactive_define_values(
                shell_setup, "single_apply"
            )

    if "internal_constraints" in chosen:
        internal_setup = internal_constraints.interactive_setup(sap_model)
        if internal_setup is not None:
            setups["internal_constraints"] = internal_setup
            values["internal_constraints"] = internal_constraints.interactive_define_values(
                internal_setup, "single_apply"
            )
        # else: user opted out of both diaphragm and equal - category skipped.

    if "external_constraints" in chosen:
        # external_constraints.py has a different interface from the other
        # modules: interactive_setup() takes `mode` and returns
        # (setup_config, values) together in one call, since the UX asks
        # for stiffness values immediately after each node group instead
        # of in a separate later step.
        external_setup, external_values = external_constraints.interactive_setup(
            sap_model, "single_apply"
        )
        if external_setup is not None:
            setups["external_constraints"] = external_setup
            values["external_constraints"] = external_values

    # Apply everything that was configured.
    if "frame_material" in values:
        cfg = setups["frame_material"]
        for material_name, e_value in values["frame_material"].items():
            material_frame.apply(
                sap_model, material_name, e_value,
                cfg["poisson_ratio"], cfg["thermal_coeff"]
            )

    if "shell_material" in values:
        cfg = setups["shell_material"]
        for material_name, e_value in values["shell_material"].items():
            material_shell.apply(
                sap_model, material_name, e_value,
                cfg["poisson_ratio"], cfg["thermal_coeff"]
            )

    if "internal_constraints" in values:
        v = values["internal_constraints"]
        internal_constraints.apply(
            sap_model, setups["internal_constraints"],
            diaphragm_assignment=v["diaphragm"],
            equal_states=v["equal_states"],
        )

    if "external_constraints" in values:
        external_constraints.apply(
            sap_model, setups["external_constraints"], values["external_constraints"]
        )

    print("\nAll selected parameter categories have been applied to the model.")
    print("Run the analysis (sap.run_analysis()) and extract results as needed.")


def run_dataset_creation(sap):
    """
    Dataset generation workflow - delegates to core.dataset_generator,
    which collects dimensions from every chosen category, builds the
    combination grid, and writes the resulting CSV.
    """
    from core import dataset_generator

    while True:
        output_csv_path = input(
            "\nFull path for the output CSV dataset "
            "(e.g. C:\\python\\datasets\\dataset_01.csv): "
        ).strip()
        if output_csv_path:
            break
        print("Please enter a non-empty path.")

    dataset_generator.run_dataset_creation(sap, output_csv_path)


_BANNER = """
========================================================================
                       SAP.py2.0
========================================================================

A CLI tool for SAP2000 (via the OAPI/comtypes interface) that updates
structural model parameters - frame/shell material stiffness, internal
constraints (diaphragms, equal-displacement), and external constraints
(base restraints/springs) - either as a single scenario update, or as
a combinatorial batch that runs a modal analysis per scenario and
exports the results to a CSV dataset.

The dataset generation mode is intended for structural health
monitoring (SHM) and damage detection research: by systematically
varying parameters across a defined range, it produces the kind of
labeled scenario data that is often missing from real-world monitoring
campaigns, for use in training ML/statistical models.

Reference: [paper reference to be added]

Created by Gianluca Bruno.
========================================================================
"""


def main():
    print(_BANNER)

    # Step 1: model file (the tool opens an existing, user-created model).
    program_path = _choose_program_path()
    model_path = input("\nFull path to the SAP2000 model (.sdb) to open: ").strip()

    sap = SapInterface(program_path=program_path, model_path=model_path)
    sap.connect()
    sap.open_model()
    sap.unlock_model()
    print(f"Model opened: {model_path}")

    # Step 2: mode (single scenario vs dataset generation).
    mode = None
    while mode not in ("a", "b"):
        mode = input(
            "\nChoose mode:\n"
            "  a) Single scenario update (single_apply)\n"
            "  b) Dataset generation (dataset_creation)\n"
            "Enter a or b: "
        ).strip().lower()

    # Step 3: which parameter categories to configure (asked inside
    # run_single_apply / run_dataset_creation, upfront before cascading
    # into any of them).
    try:
        if mode == "a":
            run_single_apply(sap.sap_model)
        else:
            run_dataset_creation(sap)
    finally:
        # save=False by default - the user reviews the result in SAP2000
        # before deciding whether to save.
        print("\nDone. SAP2000 instance left open for review - "
              "save manually in SAP2000 when ready.")


if __name__ == "__main__":
    main()
