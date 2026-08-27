"""
sap_interface.py

Manages a single, persistent connection to SAP2000 via the OAPI (comtypes).
The same SAP2000 instance and the same open model are reused across all
analyses in a batch (single-apply or dataset generation), instead of
opening/closing the program on every run.

All other modules (param_manager, modal_extractor, dataset_generator, ...)
receive the `SapModel` COM object from this module and operate on it.
"""

import os
import sys
import comtypes.client
import comtypes.gen.SAP2000v1 as SAP2000v1


class SapInterface:
    """
    Wrapper around a single SAP2000 session.

    Typical usage:
        sap = SapInterface(program_path=..., model_path=...)
        sap.connect()
        sap.open_model()
        sap.set_active_case("MODAL")
        ... run many analyses on sap.sap_model ...
        sap.close()
    """

    def __init__(self, program_path: str, model_path: str,
                 attach_to_instance: bool = False, units: int = 6):
        """
        Args:
            program_path: full path to SAP2000.exe. Ignored if attach_to_instance=True.
            model_path: full path to the .sdb model file to open.
            attach_to_instance: if True, attach to an already-running SAP2000
                instance instead of starting a new one.
            units: SAP2000 unit system code (default 6 = kN-m-C).
        """
        self.program_path = program_path
        self.model_path = model_path
        self.attach_to_instance = attach_to_instance
        self.units = units

        self.helper = None
        self.sap_object = None
        self.sap_model = None

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self):
        """
        Create (or attach to) a SAP2000 instance and start the application.
        Must be called before open_model().
        """
        self.helper = comtypes.client.CreateObject("SAP2000v1.Helper")
        self.helper = self.helper.QueryInterface(SAP2000v1.cHelper)

        if self.attach_to_instance:
            try:
                self.sap_object = self.helper.GetObject("CSI.SAP2000.API.SapObject")
            except (OSError, comtypes.COMError):
                raise RuntimeError(
                    "No running instance of SAP2000 found, or failed to attach to it."
                )
        else:
            if not os.path.exists(self.program_path):
                raise FileNotFoundError(
                    f"SAP2000 executable not found at: {self.program_path}"
                )
            try:
                self.sap_object = self.helper.CreateObject(self.program_path)
            except (OSError, comtypes.COMError):
                raise RuntimeError(
                    f"Cannot start a new SAP2000 instance from: {self.program_path}"
                )

        self.sap_object.ApplicationStart()
        self.sap_model = self.sap_object.SapModel

    def open_model(self):
        """
        Open the model file specified in self.model_path.
        Must be called after connect().
        """
        if self.sap_model is None:
            raise RuntimeError("SAP2000 is not connected. Call connect() first.")

        if not os.path.exists(self.model_path):
            raise FileNotFoundError(f"Model file not found at: {self.model_path}")

        ret = self.sap_model.File.OpenFile(self.model_path)
        if ret != 0:
            raise RuntimeError(f"Failed to open model file: {self.model_path}")

        self.sap_model.SetPresentUnits(self.units)

    def close(self, save: bool = True):
        """
        Optionally save the model, then close the SAP2000 application.

        Args:
            save: if True, saves the model before closing.
        """
        if self.sap_model is not None and save:
            self.sap_model.File.Save(self.model_path)

        if self.sap_object is not None:
            self.sap_object.ApplicationExit(False)

        self.sap_object = None
        self.sap_model = None

    # ------------------------------------------------------------------
    # Model / analysis helpers used across all batch runs
    # ------------------------------------------------------------------

    def unlock_model(self):
        """
        Unlock the model so that parameters (materials, constraints, sections)
        can be modified. Must be called before any parameter change, and
        after every RunAnalysis() call, since SAP2000 locks the model once
        results exist.
        """
        self.sap_model.SetModelIsLocked(False)

    def set_active_case(self, case_name: str, exclusive: bool = True):
        """
        Enable a given load/analysis case for the next run, optionally
        disabling all other cases first.

        Args:
            case_name: name of the case to run (e.g. "MODAL").
            exclusive: if True, disables every other case before enabling
                this one, so only `case_name` is run.
        """
        if exclusive:
            self.sap_model.Analyze.SetRunCaseFlag("", False, True)
        self.sap_model.Analyze.SetRunCaseFlag(case_name, True)

    def save(self):
        """Save the model to self.model_path."""
        self.sap_model.File.Save(self.model_path)

    def run_analysis(self):
        """
        Run the currently enabled analysis case(s).
        Returns the SAP2000 API return code (0 = success).
        """
        ret = self.sap_model.Analyze.RunAnalysis()
        return ret

    def reopen_for_postprocessing(self):
        """
        SAP2000 sometimes requires the model to be reopened after analysis
        to avoid stale-state issues in results post-processing. Call this
        if results extraction behaves inconsistently after RunAnalysis().
        """
        self.sap_model.File.OpenFile(self.model_path)


# ----------------------------------------------------------------------
# Quick manual test / example usage (not executed on import)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    # Example configuration - replace with real paths or load from config JSON
    PROGRAM_PATH = r"C:\Program Files\Computers and Structures\SAP2000 24\SAP2000.exe"
    MODEL_PATH = r"C:\python\test_sap_py\modello_sap\example_model.sdb"

    sap = SapInterface(program_path=PROGRAM_PATH, model_path=MODEL_PATH)
    try:
        sap.connect()
        sap.open_model()
        sap.set_active_case("MODAL")
        sap.save()
        print("SAP2000 connected and model opened successfully.")
    finally:
        sap.close(save=False)
