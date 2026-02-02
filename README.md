# CP-SAT Cut Selection Demo

Minimal, runnable pipeline for the paper experiments.

## Contents
- `main_cpsat.py` – CP-SAT formulation for cut selection
- `run_full_flow.py` – BLIF -> cut enumeration -> CP-SAT -> rebuild
- `cut_enumeration.cpp` (source) + `tools/cut_enumeration` (built binary)
- `rebuild_from_cpsat.cpp` (source) + `tools/rebuild_from_cpsat` (built binary)
- `blif_to_aig.py` – helper to convert BLIF to AIG (from Mockturtle tools)
- `experiments-dac19-flow/run.py` – downstream DAC'19 evaluation flow
- `full_adder/` sample BLIF + cuts (add if not present)

## Quickstart
```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Build the two helper binaries from your Mockturtle checkout (paths may vary):
```bash
cmake --build build --target cut_enumeration rebuild_from_cpsat
cp build/examples/cut_enumeration build/examples/rebuild_from_cpsat tools/
```

Run CP-SAT end-to-end on the sample:
```bash
python run_full_flow.py full_adder/full_adder.blif \
  --output-dir out \
  --objective overall \
  --cut-enum-bin tools/cut_enumeration \
  --rebuild-bin tools/rebuild_from_cpsat \
  --stats-csv out/full_adder_stats.csv \
  --summary-csv out/summary_stats.csv
```

Convert rebuilt BLIF to AIG (needed for DAC flow):
```bash
python tools/blif_to_aig.py out/full_adder_rebuilt.blif out/full_adder_rebuilt.aig
```

Run DAC'19 flow on that AIG:
```bash
python experiments-dac19-flow/run.py --input out/full_adder_rebuilt.aig --out-dir out/dac19_results
```

Process a whole directory of BLIFs in one shot:
```bash
python run_full_flow.py path/to/blif_dir --output-dir out_runs \
  --cut-enum-bin tools/cut_enumeration --rebuild-bin tools/rebuild_from_cpsat
# each BLIF gets cuts / chosen_cuts / rebuilt files + stats in out_runs/
```

Run DAC'19 flow on all rebuilt AIGs from that batch:
```bash
for f in out_runs/*_rebuilt.blif; do
  base=${f%.blif}
  python tools/blif_to_aig.py "$f" "${base}.aig"
  python experiments-dac19-flow/run.py --input "${base}.aig" --out-dir out_runs/dac19_results
done
```

### Tuning knobs
- Cut size K (controls maximum cut fan-in for enumeration): add `--cut-size 3` (or 4, 5, etc.) to `run_full_flow.py`. This value is passed directly to the `cut_enumeration` binary.
- Objective choice (`--objective`):
  - `og`        = lambda_inv*inv + lambda_area*area (default weights below)
  - `inv`       = minimize inverter count
  - `area`      = minimize area
  - `depth`     = minimize depth (requires depth modeling)
  - `overall`   = alpha_depth*depth + beta_area*area + gamma_inv*inv
- Objective weights (for `og` and `overall` modes) live in `main_cpsat.py` near the bottom of `solve_circuit` (Can start experimentinmg by changing the weights):
  ```python
  lambda_inv = 10
  lambda_area = 1
  alpha_depth = 100
  beta_area = 10
  gamma_inv = 1
  ```
  Adjust and rerun to change the trade-off between depth/area/inverter counts.
- Depth fixing: pass `--fix-depth N` to `main_cpsat.py` CLI if you call it directly (or extend `run_full_flow.py` to expose it) to enforce a depth target.
- Output placement: use `--output-dir` (and optionally `--stats-csv` / `--summary-csv`) to keep all generated artifacts inside this repo, e.g., `--output-dir out --stats-csv out/<name>_stats.csv --summary-csv out/summary_stats.csv`. Otherwise defaults are near the input BLIF and may include absolute paths in CSVs.
- Benchmarks: CP-SAT consumes BLIFs; DAC'19 flow consumes AIGs. Use `tools/blif_to_aig.py` to convert rebuilt BLIFs before running `experiments-dac19-flow/run.py`. The repo includes the DAC'19 `benchmarks/` AIG set for reference; you can drop your own rebuilt AIGs there or point `--input` to their paths.

### Key flags in `run_full_flow.py`
- `--objective {og,inv,area,depth,overall}` pick cost function (see above).
- `--cut-size K` maximum cut size passed to `cut_enumeration`.
- `--output-dir DIR` base directory for all generated artifacts.
- `--output-stem NAME` override base filename (defaults to BLIF stem).
- `--cuts-json / --chosen-json / --rebuilt-blif / --rebuilt-dir` override specific artifact paths.
- `--tools-dir DIR` search directory for binaries; or use `--cut-enum-bin` and `--rebuild-bin` to point explicitly.
- `--stats-csv PATH` and `--summary-csv PATH` control where timing/metric rows are appended.
- `--final-tool` is fixed to `none` (no mapper step in this trimmed setup).

## Notes
- `tools/` should contain the binaries `cut_enumeration` and `rebuild_from_cpsat`. Build them from your Mockturtle checkout (e.g., `cmake --build build --target cut_enumeration rebuild_from_cpsat`) and copy the resulting executables from `build/examples/` into `tools/`. Avoid absolute-path symlinks so a fresh clone works anywhere.
- If you prefer to rebuild locally, both source files (`cut_enumeration.cpp`, `rebuild_from_cpsat.cpp`) are included; compile against the mockturtle headers, e.g.:
  ```bash
  g++ -std=c++17 -O3 -I../Mockturtle-mMIG-main/include \
      -o tools/cut_enumeration cut_enumeration.cpp
  g++ -std=c++17 -O3 -I../Mockturtle-mMIG-main/include \
      -o tools/rebuild_from_cpsat rebuild_from_cpsat.cpp
  ```
- DAC'19 flow prerequisites (in `experiments-dac19-flow/`): install `cirkit==3.0a2.dev5` (`pip install cirkit==3.0a2.dev5`) and ensure the `abc` binary is on your `PATH` (build from https://github.com/berkeley-abc/abc). Benchmarks (`benchmarks/*.aig`) are already included here; the result folders are historical.
- Benchmarks: only `full_adder` is provided for a smoke test. Add your EPFL/other BLIFs to run broader sweeps.
- Results directories under `experiments-dac19-flow` are historical; they aren’t needed for the smoke test.
- If you move this repo, update the symlink targets or pass `--cut-enum-bin/--rebuild-bin` explicitly.
