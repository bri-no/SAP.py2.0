"""
modal_extractor.py

Extracts modal analysis results (periods, frequencies, mass participation
ratios) from SAP2000 after running the MODAL load case.

Also provides:
    - ask_num_modes(): interactive prompt for how many modes to save
      per scenario (used by dataset_generator.py).
    - modal_results_as_row(): formats one scenario's modal results into
      a flat CSV row with a full [frequency, UX,UY,UZ,RX,RY,RZ] block
      per mode (per-mode mass participation ratios, not the cumulative
      "Sum" variants).

This module is a direct, lightly-hardened port of the block already used
and proven working in the user's original SAP.py script - it is NOT a
newly-derived API sequence, so it does not carry the same "not yet
verified" caveats as the other parameter modules. The only additions
here are: explicit return-code checking, guarding against a zero period
(division by zero when computing frequency), and a couple of convenience
wrappers for CSV row building (used later by dataset_generator.py).

---------------------------------------------------------------------------
API verification status:

VERIFIED BY REUSE (already used successfully in the user's original
working script, not re-derived from the CHM in this project):
    - Results.Setup.DeselectAllCasesAndCombosForOutput() -> Long
    - Results.Setup.SetCaseSelectedForOutput(CaseName) -> Long
    - Results.Setup.SetOptionModeShape(...) -> Long
    - Results.ModalParticipatingMassRatios(...) -> Long, with the same
      ByRef output array pattern as the original script:
        [NumberResults, LoadCase, StepType, StepNum, Period, UX, UY, UZ,
         SumUX, SumUY, SumUZ, RX, RY, RZ, SumRX, SumRY, SumRZ, ret] = ...
---------------------------------------------------------------------------
"""


def extract_modal_results(sap_model, num_modes: int = 10):
    """
    Set up the MODAL case for output, request up to num_modes mode
    shapes, and extract the modal participating mass ratios - including
    periods and the corresponding frequencies (1/period).

    Args:
        sap_model: the active SapModel COM object (analysis must have
            already been run - call sap.run_analysis() first).
        num_modes: number of mode shapes to request in the output
            (matches the user's original script's SetOptionModeShape(1, 10)
            pattern, generalized to a configurable count).

    Returns:
        dict with keys:
            "number_results": int
            "load_case": list of str
            "period": list of float [s]
            "frequency": list of float [Hz] (or None where period == 0,
                to avoid a ZeroDivisionError - a zero period usually
                signals an invalid/rigid-body mode and should be flagged
                rather than silently producing inf)
            "ux","uy","uz","sum_ux","sum_uy","sum_uz",
            "rx","ry","rz","sum_rx","sum_ry","sum_rz": lists of float
                (modal participating mass ratios, as returned by SAP2000)
    """
    ret = sap_model.Results.Setup.DeselectAllCasesAndCombosForOutput()
    if ret != 0:
        raise RuntimeError(f"DeselectAllCasesAndCombosForOutput failed (return code {ret}).")

    ret = sap_model.Results.Setup.SetCaseSelectedForOutput("MODAL")
    if ret != 0:
        raise RuntimeError(f"SetCaseSelectedForOutput('MODAL') failed (return code {ret}). "
                            f"Does this model have a load case named 'MODAL'?")

    ret = sap_model.Results.Setup.SetOptionModeShape(1, num_modes)
    if ret != 0:
        raise RuntimeError(f"SetOptionModeShape failed (return code {ret}).")

    number_results = 0
    load_case = []
    step_type = []
    step_num = []
    period = []
    ux, uy, uz = [], [], []
    sum_ux, sum_uy, sum_uz = [], [], []
    rx, ry, rz = [], [], []
    sum_rx, sum_ry, sum_rz = [], [], []

    [number_results, load_case, step_type, step_num, period, ux, uy, uz,
     sum_ux, sum_uy, sum_uz, rx, ry, rz, sum_rx, sum_ry, sum_rz, ret] = \
        sap_model.Results.ModalParticipatingMassRatios(
            number_results, load_case, step_type, step_num, period, ux, uy, uz,
            sum_ux, sum_uy, sum_uz, rx, ry, rz, sum_rx, sum_ry, sum_rz
        )

    if ret != 0:
        raise RuntimeError(
            f"ModalParticipatingMassRatios failed (return code {ret}). "
            f"Make sure the analysis has been run before extracting results."
        )

    frequency = []
    for p in period:
        if p == 0:
            frequency.append(None)  # flag invalid/rigid-body mode instead of raising
        else:
            frequency.append(1.0 / p)

    return {
        "number_results": number_results,
        "load_case": list(load_case),
        "period": list(period),
        "frequency": frequency,
        "ux": list(ux), "uy": list(uy), "uz": list(uz),
        "sum_ux": list(sum_ux), "sum_uy": list(sum_uy), "sum_uz": list(sum_uz),
        "rx": list(rx), "ry": list(ry), "rz": list(rz),
        "sum_rx": list(sum_rx), "sum_ry": list(sum_ry), "sum_rz": list(sum_rz),
    }


def get_frequencies(sap_model, num_modes: int = 10):
    """
    Convenience wrapper: returns just the list of frequencies [Hz],
    ordered by mode number, for the cases where only the frequencies
    (not the full mass-participation detail) are needed.
    """
    results = extract_modal_results(sap_model, num_modes)
    return results["frequency"]


def ask_num_modes(default: int = 10) -> int:
    """
    Ask the user how many modes to save for the dataset - each row of
    the resulting CSV will have one [frequency, UX,UY,UZ,RX,RY,RZ] block
    per requested mode. Uses core.selection_helper.ask_int for robust,
    typo-resilient input with a sensible default.
    """
    from core import selection_helper
    return selection_helper.ask_int(
        f"\nHow many vibration modes do you want to save per scenario? "
        f"(press Enter for default {default}): ",
        minimum=1, default=default
    )


def modal_results_as_row(results: dict, num_modes: int, prefix: str = "mode"):
    """
    Format modal results into a flat dict suitable for one CSV row,
    with one full [frequency, UX, UY, UZ, RX, RY, RZ] block per mode -
    the per-mode mass participation ratios (NOT the cumulative "Sum"
    variants), as requested for the dataset output.

    Produces columns like:
        mode1_f, mode1_UX, mode1_UY, mode1_UZ, mode1_RX, mode1_RY, mode1_RZ,
        mode2_f, mode2_UX, ...

    Pads with None if fewer modes were actually returned by SAP2000 than
    requested, so every row in the dataset CSV has the same fixed set
    of columns regardless of scenario.

    Args:
        results: the dict returned by extract_modal_results().
        num_modes: total number of mode blocks to produce (should match
            the num_modes requested via ask_num_modes()/extract_modal_results()).
        prefix: column name prefix per mode (default "mode" -> "mode1_f", ...).
    """
    row = {}
    n_available = len(results["frequency"])

    for i in range(num_modes):
        col = f"{prefix}{i + 1}"
        if i < n_available:
            row[f"{col}_f"] = results["frequency"][i]
            row[f"{col}_UX"] = results["ux"][i]
            row[f"{col}_UY"] = results["uy"][i]
            row[f"{col}_UZ"] = results["uz"][i]
            row[f"{col}_RX"] = results["rx"][i]
            row[f"{col}_RY"] = results["ry"][i]
            row[f"{col}_RZ"] = results["rz"][i]
        else:
            row[f"{col}_f"] = None
            row[f"{col}_UX"] = None
            row[f"{col}_UY"] = None
            row[f"{col}_UZ"] = None
            row[f"{col}_RX"] = None
            row[f"{col}_RY"] = None
            row[f"{col}_RZ"] = None

    return row
