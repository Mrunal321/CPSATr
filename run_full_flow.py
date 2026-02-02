"""One-shot pipeline: BLIF -> cut enumeration -> CP-SAT -> rebuild -> optional mapper."""

import argparse
import csv
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from main_cpsat import solve_circuit


def _resolve_binary(explicit, candidates, description, flag_hint=None):
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return str(p)
        raise FileNotFoundError(f"{description} '{explicit}' not found")

    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(candidate)

    flag = flag_hint or description.replace(" ", "-")
    raise FileNotFoundError(f"Unable to locate {description}; pass the --{flag} flag")


def _run(cmd, cwd=None):
    print("[run]", " ".join(str(c) for c in cmd))
    subprocess.run(cmd, check=True, cwd=cwd)


def _append_stats_row(csv_path, headers, row):
    csv_path = Path(csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not csv_path.exists() or csv_path.stat().st_size == 0

    with csv_path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def _run_single_pipeline(args):
    input_blif = Path(args.input_blif).resolve()
    if not input_blif.is_file():
        raise FileNotFoundError(f"Input BLIF '{input_blif}' not found")
    if input_blif.suffix.lower() != ".blif":
        raise ValueError("run_full_flow expects a BLIF file as input")

    out_dir = Path(args.output_dir).resolve() if args.output_dir else input_blif.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    stem = args.output_stem or input_blif.stem
    cuts_json = Path(args.cuts_json) if args.cuts_json else out_dir / f"{stem}_cuts.json"
    chosen_json = Path(args.chosen_json) if args.chosen_json else out_dir / f"{stem}_chosen_cuts.json"
    if args.rebuilt_blif:
        rebuilt_blif = Path(args.rebuilt_blif)
    elif args.rebuilt_dir:
        rebuilt_dir = Path(args.rebuilt_dir).resolve()
        rebuilt_dir.mkdir(parents=True, exist_ok=True)
        rebuilt_blif = rebuilt_dir / f"{stem}_rebuilt.blif"
    else:
        rebuilt_blif = out_dir / f"{stem}_rebuilt.blif"

    tools_dir = Path(args.tools_dir).resolve() if args.tools_dir else input_blif.parent
    script_dir = Path(__file__).resolve().parent
    cut_enum_bin = _resolve_binary(
        args.cut_enum_bin,
        [tools_dir / "cut_enumeration", script_dir / "cut_enumeration", shutil.which("cut_enumeration")],
        "cut_enumeration binary",
        flag_hint="cut-enum-bin",
    )

    rebuild_candidates = [
        tools_dir / "rebuild_from_cpsat",
        script_dir / "rebuild_from_cpsat",
        shutil.which("rebuild_from_cpsat"),
    ]
    if args.cut_enum_bin:
        rebuild_candidates.insert(0, Path(args.cut_enum_bin).resolve().parent / "rebuild_from_cpsat")

    rebuild_bin = _resolve_binary(
        args.rebuild_bin,
        rebuild_candidates,
        "rebuild_from_cpsat binary",
        flag_hint="rebuild-bin",
    )

    stage_times = {}

    def _record(label, func):
        start = time.perf_counter()
        result = func()
        stage_times[label] = time.perf_counter() - start
        return result

    # 1) cut enumeration
    ce_cmd = [cut_enum_bin, str(input_blif), str(cuts_json)]
    if args.cut_size:
        ce_cmd.append(str(args.cut_size))
    _record("cut_enumeration", lambda: _run(ce_cmd))

    # 2) CP-SAT cut selection
    cp_sat_result = _record(
        "cp_sat",
        lambda: solve_circuit(
            str(cuts_json),
            str(chosen_json),
            objective_mode=args.objective,
        ),
    ) or {}

    cp_status = cp_sat_result.get("status", "")
    cp_good = cp_status in ("FEASIBLE", "OPTIMAL")
    if not cp_good:
        print(f"CP-SAT returned status {cp_status}; skipping rebuild and final steps.")
        stage_times["rebuild"] = 0.0
        stage_times["final"] = 0.0
        t_pre = sum(stage_times.get(key, 0.0) for key in ("cut_enumeration", "cp_sat", "rebuild"))
        final_time = 0.0
        t_opt = 0.0
        t_total = t_pre + t_opt
        print(
            f"T_pre   = {t_pre:.2f}s "
            f"(cut_enum {stage_times.get('cut_enumeration', 0.0):.2f}s + "
            f"cp_sat {stage_times.get('cp_sat', 0.0):.2f}s + "
            f"rebuild {stage_times.get('rebuild', 0.0):.2f}s)"
        )
        print("T_opt   = 0.00s (skipped)")
        print(f"T_total = {t_total:.2f}s")
        print("Pipeline halted after CP-SAT.")
        stats_path = Path(args.stats_csv).resolve() if args.stats_csv else out_dir / f"{stem}_stats.csv"
        stats_headers = [
            "timestamp",
            "input_blif",
            "output_dir",
            "objective",
            "cut_size",
            "final_tool",
            "cuts_json",
            "chosen_json",
            "rebuilt_blif",
            "cp_sat_status",
            "cp_sat_objective",
            "cut_enum_time_s",
            "cp_sat_time_s",
            "rebuild_time_s",
            "final_time_s",
            "t_pre_s",
            "t_total_s",
        ]
        stats_row = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "input_blif": str(input_blif),
            "output_dir": str(out_dir),
            "objective": args.objective,
            "cut_size": args.cut_size if args.cut_size is not None else "",
            "final_tool": args.final_tool,
            "cuts_json": str(cuts_json),
            "chosen_json": str(chosen_json),
            "rebuilt_blif": str(rebuilt_blif),
            "cp_sat_status": cp_sat_result.get("status", ""),
            "cp_sat_objective": cp_sat_result.get("objective_value", ""),
            "cut_enum_time_s": f"{stage_times.get('cut_enumeration', 0.0):.4f}",
            "cp_sat_time_s": f"{stage_times.get('cp_sat', 0.0):.4f}",
            "rebuild_time_s": f"{stage_times.get('rebuild', 0.0):.4f}",
            "final_time_s": f"{final_time:.4f}",
            "t_pre_s": f"{t_pre:.4f}",
            "t_total_s": f"{t_total:.4f}",
        }
        _append_stats_row(stats_path, stats_headers, stats_row)
        print(f"Stats appended to {stats_path}")
        summary_path = Path(args.summary_csv).resolve() if args.summary_csv else out_dir / "summary_stats.csv"
        _append_stats_row(summary_path, stats_headers, stats_row)
        print(f"Summary appended to {summary_path}")
        return

    # 3) rebuild netlist
    rebuild_cmd = [rebuild_bin, str(input_blif), str(cuts_json), str(chosen_json), str(rebuilt_blif)]
    _record("rebuild", lambda: _run(rebuild_cmd))

    final_time = 0.0
    # No final mapping step; pipeline ends after rebuild
    stage_times["final"] = 0.0
    t_pre = sum(stage_times.get(key, 0.0) for key in ("cut_enumeration", "cp_sat", "rebuild"))
    t_opt = final_time
    t_total = t_pre + t_opt

    print(
        f"T_pre   = {t_pre:.2f}s "
        f"(cut_enum {stage_times.get('cut_enumeration', 0.0):.2f}s + "
        f"cp_sat {stage_times.get('cp_sat', 0.0):.2f}s + "
        f"rebuild {stage_times.get('rebuild', 0.0):.2f}s)"
    )
    if args.final_tool != "none":
        print(f"T_opt   = {t_opt:.2f}s ({args.final_tool})")
    else:
        print("T_opt   = 0.00s (no final tool)")
    print(f"T_total = {t_total:.2f}s")
    print("Pipeline finished successfully.")

    stats_path = Path(args.stats_csv).resolve() if args.stats_csv else out_dir / f"{stem}_stats.csv"
    stats_headers = [
        "timestamp",
        "input_blif",
        "output_dir",
        "objective",
        "cut_size",
            "final_tool",
            "cuts_json",
            "chosen_json",
            "rebuilt_blif",
            "cp_sat_status",
            "cp_sat_objective",
            "cut_enum_time_s",
            "cp_sat_time_s",
            "rebuild_time_s",
            "final_time_s",
            "t_pre_s",
        "t_total_s",
    ]
    stats_row = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "input_blif": str(input_blif),
        "output_dir": str(out_dir),
        "objective": args.objective,
        "cut_size": args.cut_size if args.cut_size is not None else "",
        "final_tool": args.final_tool,
        "cuts_json": str(cuts_json),
        "chosen_json": str(chosen_json),
        "rebuilt_blif": str(rebuilt_blif),
        "cp_sat_status": cp_sat_result.get("status", ""),
        "cp_sat_objective": cp_sat_result.get("objective_value", ""),
        "cut_enum_time_s": f"{stage_times.get('cut_enumeration', 0.0):.4f}",
        "cp_sat_time_s": f"{stage_times.get('cp_sat', 0.0):.4f}",
        "rebuild_time_s": f"{stage_times.get('rebuild', 0.0):.4f}",
        "final_time_s": f"{final_time:.4f}",
        "t_pre_s": f"{t_pre:.4f}",
        "t_total_s": f"{t_total:.4f}",
    }
    _append_stats_row(stats_path, stats_headers, stats_row)
    print(f"Stats appended to {stats_path}")
    summary_path = Path(args.summary_csv).resolve() if args.summary_csv else out_dir / "summary_stats.csv"
    _append_stats_row(summary_path, stats_headers, stats_row)
    print(f"Summary appended to {summary_path}")


def run_pipeline(args):
    input_path = Path(args.input_blif).resolve()
    if input_path.is_dir():
        blif_files = sorted(p for p in input_path.glob("*.blif") if p.is_file())
        if not blif_files:
            raise FileNotFoundError(f"No BLIF files found in directory '{input_path}'")
        print(f"Found {len(blif_files)} BLIF files in {input_path}")
        for blif in blif_files:
            print(f"\n=== Processing {blif.name} ===")
            file_args = argparse.Namespace(**vars(args))
            file_args.input_blif = str(blif)
            _run_single_pipeline(file_args)
        return

    _run_single_pipeline(args)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Full BLIF->CP-SAT->rebuild pipeline")
    parser.add_argument("input_blif", help="Original BLIF file to process")
    parser.add_argument("--objective", default="og", choices=["og", "inv", "area", "depth", "overall"], help="CP-SAT objective")
    parser.add_argument("--cut-size", type=int, default=None, help="Optional K passed to cut_enumeration")
    parser.add_argument("--output-dir", default=None, help="Directory for generated artifacts (defaults to BLIF dir)")
    parser.add_argument("--output-stem", default=None, help="Base name for generated files")
    parser.add_argument("--cuts-json", default=None, help="Override path for the cut enumeration JSON")
    parser.add_argument("--chosen-json", default=None, help="Override path for the chosen cuts JSON")
    parser.add_argument("--rebuilt-blif", default=None, help="Override path for the rebuilt BLIF")
    parser.add_argument("--rebuilt-dir", default=None, help="Directory to place rebuilt BLIFs (default: output dir)")
    parser.add_argument("--tools-dir", default=None, help="Directory containing cut_enumeration/rebuild binaries")
    parser.add_argument("--cut-enum-bin", default=None, help="Explicit cut_enumeration binary path")
    parser.add_argument("--rebuild-bin", default=None, help="Explicit rebuild_from_cpsat binary path")
    parser.add_argument("--final-tool", choices=["none"], default="none", help="No downstream tool (mock2abc removed)")
    parser.add_argument("--stop-after-rebuild", action="store_true", help="Skip any final mapping tool and stop after writing rebuilt BLIF")
    parser.add_argument("--final-base", default=None, help="(unused) kept for backward compat")
    parser.add_argument("--stats-csv", default=None, help="CSV file to append pipeline stats (default: <output_dir>/<stem>_stats.csv)")
    parser.add_argument("--summary-csv", default=None, help="CSV file to append combined stats for all runs (default: <output_dir>/summary_stats.csv)")
    args = parser.parse_args(argv)

    run_pipeline(args)


if __name__ == "__main__":
    main()
