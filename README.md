# SAP.py2.0

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)
![Platform: Windows](https://img.shields.io/badge/platform-Windows-lightgrey)

A CLI tool for **SAP2000** (via the OAPI/comtypes interface) that updates
structural model parameters — frame/shell material stiffness, internal
constraints (diaphragms, equal-displacement), and external constraints
(base restraints/springs) — either as a **single scenario update**, or as
a **combinatorial batch** that runs a modal analysis per scenario and
exports the results to a labeled CSV dataset.

The dataset-generation mode is built for **structural health monitoring
(SHM) and damage-detection research**: by systematically varying model
parameters across a defined range, it produces the kind of labeled
scenario data that is often missing from real-world monitoring
campaigns, for use in training ML/statistical models.

> 📄 Reference paper: *[to be added upon publication]*

---

## 🔬 Development status

SAP.py2.0 is an active research tool, not a finished commercial product.
Dataset generation is **intentionally decoupled** from any optimization
or model-updating loop — that is a deliberate design choice, not a
missing feature, distinguishing this version from the prior SAP.py v1.0
(presented at WCEE2024). Interfaces and CSV schemas may still change
between versions. Treat it as a research prototype.

---

## 🚀 Two ways to use it

### Option A — Run from source (Python)

Recommended if you want to read, modify, or extend the tool.

```bash
git clone https://github.com/<your-github-username>/SAP.py2.0.git
cd SAP.py2.0
pip install -r requirements.txt
python main.py
```

**Requirements:**
- Windows (the OAPI/COM interface is Windows-only)
- Python 3.9+
- A licensed local installation of **SAP2000** (any version from the
  supported range; the tool auto-detects installations under
  `C:\Program Files\Computers and Structures\`)

### Option B — Standalone executable (no Python required)

Precompiled Windows binaries are provided in [`dist/`](dist/):

| File | Description |
|---|---|
| [`SAP.py2.0.exe`](dist/SAP.py2.0.exe) | Full CLI tool — same functionality as running `main.py` from source. |
| [`SAP.py2.0_GUI.exe`](dist/SAP.py2.0_GUI.exe) | Graphical wrapper around the same logic. **Bonus binary**: distributed compiled-only, source not yet published in this repository. |

Just download and double-click — no Python installation needed. SAP2000
must still be installed and licensed on the machine.

---

## 🧠 What it does

The tool asks you upfront which parameter categories to activate for the
run, instead of cascading through all of them in sequence:

| Category | What it varies |
|---|---|
| Frame elastic modulus | Creates one material per selected frame element (optionally subdivided), lets you sweep its E value |
| Shell/Area elastic modulus | Same idea, for meshed area (shell) elements |
| Internal constraints | Diaphragm membership per node group, equal-displacement constraints |
| External constraints | Per-DOF choice between rigid restraint and variable-stiffness spring at supports |

Two modes:

- **`single_apply`** — configure and push one specific scenario into the
  open model, for manual inspection in SAP2000.
- **`dataset_creation`** — build the full Cartesian product of every
  varied dimension, run a MODAL analysis per combination, and append one
  row per scenario to a CSV dataset:

  ```
  scenario_id, <param columns...>, mode1_f, mode1_UX, mode1_UY, mode1_UZ,
  mode1_RX, mode1_RY, mode1_RZ, mode2_f, ..., analysis_status
  ```

  Parameters that are set to vary **in sync** (e.g. a group of frame
  elements meant to always share the same stiffness) collapse into a
  single shared column instead of one column each — you choose this
  coupling explicitly per parameter group, rather than it being inferred.

---

## 📁 Project structure

```
SAP.py2.0/
├── main.py                       # CLI entry point / orchestrator
├── core/
│   ├── sap_interface.py          # Persistent SAP2000 OAPI/comtypes session wrapper
│   ├── dataset_generator.py      # Combinatorial grid + CSV dataset writer
│   ├── modal_extractor.py        # Modal analysis results extraction
│   └── selection_helper.py       # Shared "list -> select -> highlight -> confirm" workflow
├── parameters/
│   ├── material_frame.py         # Frame elastic modulus category
│   ├── material_shell.py         # Shell/area elastic modulus category
│   ├── internal_constraints.py   # Diaphragms + equal-displacement constraints
│   └── external_constraints.py   # Base restraints / springs per DOF
├── dist/
│   ├── SAP.py2.0.exe              # Compiled CLI (source included above)
│   └── SAP.py2.0_GUI.exe          # Compiled GUI (bonus, compiled-only)
├── requirements.txt
├── CITATION.cff
└── LICENSE
```

---

## ⚠️ Known limitations

- Windows-only (COM/OAPI dependency).
- Requires a valid, separately licensed installation of SAP2000 — this
  repository does not include or redistribute any CSI software.
- The GUI is currently distributed as a compiled executable only; its
  source is not yet part of this public repository.
- CSV schema and parameter category set are expected to evolve as the
  tool is extended (see development status above).

---

## 📚 Cite this work

If you use SAP.py2.0 in your research, please cite it — see
[`CITATION.cff`](CITATION.cff) for machine-readable metadata (also
usable directly via GitHub's "Cite this repository" button).

```bibtex
@software{Bruno_SAP_py2_0,
  author  = {Bruno, Gianluca and Parisi, Fabio and Ruggieri, Sergio},
  title   = {{SAP.py2.0}},
  year    = {2026},
  license = {MIT},
  url     = {https://github.com/<your-github-username>/SAP.py2.0}
}
```

A companion paper describing the tool and an illustrative case study
(a mixed masonry/RC building in the port of Bari, Italy) is in
preparation — citation details will be added here once published.

---

## 🏆 Authors & Affiliations

| Author | Affiliation | Contact | ORCID / Profiles |
|---|---|---|---|
| Gianluca Bruno | Rutgers University – CAIT; Politecnico di Bari | gianluca.bruno@rutgers.edu | [ORCID](https://orcid.org/0009-0009-6965-3126) · [ResearchGate](https://www.researchgate.net/profile/Gianluca-Bruno-2) · Scopus 59692398300 |
| Fabio Parisi | Politecnico di Bari | fabio.parisi@poliba.it | [ResearchGate](https://www.researchgate.net/profile/Fabio-Parisi-2) · Scopus 57212473555 |
| Sergio Ruggieri | Politecnico di Bari | sergio.ruggieri@poliba.it | [ORCID](https://orcid.org/0000-0001-5119-8967) · [ResearchGate](https://www.researchgate.net/profile/Sergio-Ruggieri-2) · Scopus 57200721168 |

---

## 📄 License

Released under the [MIT License](LICENSE). See the LICENSE file for the
full text and an important note on SAP2000's separate proprietary
licensing.
