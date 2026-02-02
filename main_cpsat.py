import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from ortools.sat.python import cp_model


def _status_to_str(status):
    if status == cp_model.OPTIMAL:
        return "OPTIMAL"
    if status == cp_model.FEASIBLE:
        return "FEASIBLE"
    if status == cp_model.UNKNOWN:
        return "UNKNOWN"
    if status == cp_model.INFEASIBLE:
        return "INFEASIBLE"
    return str(status)


def _find_cut_enumeration_binary(binary_hint, blif_path):
    """Return an executable path for cut_enumeration."""
    candidates = []
    if binary_hint:
        hint_path = Path(binary_hint)
        if hint_path.is_file():
            return str(hint_path)
        resolved = shutil.which(binary_hint)
        if resolved:
            return resolved
        raise FileNotFoundError(f"cut_enumeration binary '{binary_hint}' not found")

    parent_candidate = Path(blif_path).parent / "cut_enumeration"
    candidates.append(parent_candidate)
    script_dir_candidate = Path(__file__).resolve().parent / "cut_enumeration"
    candidates.append(script_dir_candidate)

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)

    resolved = shutil.which("cut_enumeration")
    if resolved:
        return resolved

    raise FileNotFoundError(
        "Unable to locate cut_enumeration. Pass --cut-enum-bin with the binary path."
    )


def _generate_cuts_json_from_blif(blif_path, binary_hint=None, cut_size=None):
    """Run cut_enumeration on a BLIF file and return the generated JSON path."""
    cut_enum_bin = _find_cut_enumeration_binary(binary_hint, blif_path)
    tmp = tempfile.NamedTemporaryFile(
        prefix=f"{Path(blif_path).stem}_cuts_",
        suffix=".json",
        delete=False,
    )
    tmp_path = Path(tmp.name)
    tmp.close()

    cmd = [cut_enum_bin, str(blif_path), str(tmp_path)]
    if cut_size is not None:
        cmd.append(str(cut_size))
    print(
        f"Converting BLIF to cuts JSON via '{cut_enum_bin}' (output: {tmp_path})"
    )
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"cut_enumeration failed for '{blif_path}' with exit code {exc.returncode}"
        ) from exc

    return tmp_path


def _load_cuts_data(cuts_path, binary_hint=None, cut_size=None):
    """Load cut data either directly from JSON or via BLIF conversion."""
    cuts_path = Path(cuts_path)
    if not cuts_path.exists():
        raise FileNotFoundError(f"Cuts file '{cuts_path}' does not exist")

    temp_json = None
    if cuts_path.suffix.lower() != ".json":
        temp_json = _generate_cuts_json_from_blif(
            cuts_path, binary_hint=binary_hint, cut_size=cut_size
        )
        json_path = temp_json
    else:
        json_path = cuts_path

    try:
        with open(json_path, "r") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        if temp_json is None:
            raise ValueError(
                f"Cuts file '{cuts_path}' is not valid JSON"
            ) from exc
        raise ValueError(
            f"Generated cuts JSON '{json_path}' is invalid"
        ) from exc
    finally:
        if temp_json is not None:
            os.unlink(temp_json)

    return data


def _normalize_cuts_data(data):
    """Ensure cuts JSON uses dict-form cuts with default costs."""
    nodes = data.get("nodes", [])
    normalized_nodes = []
    for nd in nodes:
        cuts = []
        for cut in nd.get("cuts", []):
            if isinstance(cut, dict):
                cuts.append(cut)
            else:
                # Interpret a bare list as leaves-only; fill in default costs.
                cuts.append({
                    "leaves": list(cut),
                    "inv_cost": 0,
                    "area_cost": len(cut),
                    "depth_cost": 1,
                })
        nd_copy = dict(nd)
        nd_copy["cuts"] = cuts
        normalized_nodes.append(nd_copy)
    data["nodes"] = normalized_nodes
    if "inputs" not in data:
        data["inputs"] = []
    return data


def _compute_depth_upper_bound(data):
    """Compute a heuristic depth upper bound using a greedy depth calculation."""
    nodes = data.get("nodes", [])
    outputs = data.get("outputs") or []
    node_map = {nd["name"]: nd for nd in nodes}
    if not outputs:
        if "Nout" in node_map:
            outputs = ["Nout"]
        elif nodes:
            outputs = [nodes[-1]["name"]]

    memo = {}
    visiting = set()

    def depth(name):
        if name in memo:
            return memo[name]
        if name in visiting:
            # Cycle detected; return a conservative bound.
            return len(nodes) or 1
        nd = node_map.get(name)
        if nd is None:
            memo[name] = 0
            return 0
        visiting.add(name)
        best = None
        for cut in nd.get("cuts", []):
            leaves_raw = cut.get("leaves", [])
            if len(leaves_raw) == 1 and leaves_raw[0] == name:
                # Skip self-cuts to stay consistent with model construction.
                continue
            leaves = [l for l in leaves_raw if l != name]
            leaf_depth = max((depth(l) for l in leaves), default=0)
            cut_depth = leaf_depth + int(cut.get("depth_cost", 1) or 1)
            if best is None or cut_depth < best:
                best = cut_depth
        visiting.remove(name)
        best = best if best is not None else 0
        memo[name] = best
        return best

    ub_candidates = [depth(out) for out in outputs] if outputs else []
    base = max(ub_candidates) if ub_candidates else len(nodes) or 1
    # Add slack so the "upper bound" is not accidentally too tight.
    ub_with_slack = max(base + 10, int(base * 1.5))
    # Force UB to be at least the number of nodes to avoid infeasibility from an undershoot.
    ub = max(ub_with_slack, len(nodes) or 1)
    return max(1, ub)


def _extract_chosen_cuts(node_dicts, var_node_used, var_cut, solver):
    chosen_cuts = {}
    for nd in node_dicts:
        nname = nd["name"]
        if solver.Value(var_node_used[nname]) == 1:
            cuts_for_node = var_cut[nname]
            for ci in cuts_for_node:
                if solver.Value(ci["var"]) == 1:
                    chosen_cuts[nname] = ci["cut_index"]
                    break
    return chosen_cuts


def solve_circuit(
    cuts_path,
    chosen_json_path,
    objective_mode="original",
    cut_enum_bin=None,
    cut_size=None,
):
    data = _load_cuts_data(cuts_path, binary_hint=cut_enum_bin, cut_size=cut_size)
    data = _normalize_cuts_data(data)
    node_dicts = data["nodes"]
    outputs = data.get("outputs", [])
    inputs = data.get("inputs") or []

    def build_model(depth_bound=None, fix_depth=None):
        include_depth = depth_bound is not None
        model = cp_model.CpModel()

        var_node_used = {
            nd["name"]: model.NewBoolVar("used_" + nd["name"])
            for nd in node_dicts
        }

        node_names = {nd["name"] for nd in node_dicts}
        var_cut = {}
        cut_counter = 0
        for nd_idx, nd in enumerate(node_dicts):
            nname = nd["name"]
            var_cut[nname] = []
            for i, cut_obj in enumerate(nd["cuts"]):
                leaves = cut_obj["leaves"]
                if len(leaves) == 1 and leaves[0] == nname:
                    continue

                inv_cost = cut_obj.get("inv_cost", 0)
                area_cost = cut_obj.get("area_cost", len(leaves))
                depth_cost = cut_obj.get("depth_cost", 1)
                cvar = model.NewBoolVar(f"cut_{nname}_{i}")
                lex_weight = cut_counter
                cut_counter += 1
                var_cut[nname].append({
                    "var": cvar,
                    "leaves": leaves,
                    "inv_cost": inv_cost,
                    "area_cost": area_cost,
                    "depth_cost": depth_cost,
                    "lex_weight": lex_weight,
                    "cut_index": i,
                })

        # (A) exactly 1 cut if node used, 0 otherwise
        for nd in node_dicts:
            nname = nd["name"]
            cuts_for_node = var_cut[nname]
            if cuts_for_node:
                model.Add(sum(ci["var"] for ci in cuts_for_node) == var_node_used[nname])
            else:
                model.Add(var_node_used[nname] == 0)

        # (B) cut -> leaves used (for internal leaves)
        forced_1 = model.NewConstant(1)
        for nd in node_dicts:
            nname = nd["name"]
            for ci in var_cut[nname]:
                for leaf in ci["leaves"]:
                    if leaf in node_names:
                        model.AddImplication(ci["var"], var_node_used[leaf])
                    else:
                        model.AddImplication(ci["var"], forced_1)

        level_vars = {}
        D = None
        if include_depth:
            max_depth_bound = max(1, depth_bound or len(node_dicts) or 1)
            level_vars = {
                nd["name"]: model.NewIntVar(0, max_depth_bound, f"L_{nd['name']}")
                for nd in node_dicts
            }
            D = model.NewIntVar(0, max_depth_bound, "D")

            for inp in inputs:
                if inp in level_vars:
                    model.Add(level_vars[inp] == 0)

            big_m = max_depth_bound
            for nd in node_dicts:
                nname = nd["name"]
                if nname not in level_vars:
                    continue
                node_level = level_vars[nname]
                # Link levels to usage to avoid floating levels.
                model.Add(node_level <= big_m * var_node_used[nname])
                if nname not in inputs:
                    model.Add(node_level >= var_node_used[nname])
                for ci in var_cut[nname]:
                    cvar = ci["var"]
                    step = ci.get("depth_cost", 1) or 1
                    for leaf in ci["leaves"]:
                        if leaf in level_vars:
                            model.Add(node_level >= level_vars[leaf] + step - big_m * (1 - cvar))

            for nname, lvl in level_vars.items():
                model.Add(D >= lvl)
            if fix_depth is not None:
                model.Add(D == fix_depth)

        # force outputs as roots if present
        if outputs:
            for out in outputs:
                if out in var_node_used:
                    model.Add(var_node_used[out] == 1)
        else:
            if "Nout" in var_node_used:
                model.Add(var_node_used["Nout"] == 1)
            elif node_dicts:
                root = node_dicts[-1]["name"]
                model.Add(var_node_used[root] == 1)
            # used for og objective
        lambda_inv = 10
        lambda_area = 1
            # used for overall objective
        alpha_depth = 100   
        beta_area = 10
        gamma_inv = 1

        inv_terms = [
            ci["inv_cost"] * ci["var"]
            for nd in node_dicts
            for ci in var_cut[nd["name"]]
        ]
        area_terms = [
            ci["area_cost"] * ci["var"]
            for nd in node_dicts
            for ci in var_cut[nd["name"]]
        ]
        original_terms = [
            (lambda_inv * ci["inv_cost"] + lambda_area * ci["area_cost"]) * ci["var"]
            for nd in node_dicts
            for ci in var_cut[nd["name"]]
        ]

        def apply_objective(mode):
            if mode in ("og", "original"):
                primary_cost = cp_model.LinearExpr.Sum(original_terms)
                model.Minimize(primary_cost)
            elif mode == "inv":
                primary_cost = cp_model.LinearExpr.Sum(inv_terms)
                model.Minimize(primary_cost)
            elif mode == "area":
                primary_cost = cp_model.LinearExpr.Sum(area_terms)
                model.Minimize(primary_cost)
            elif mode == "depth":
                if D is None:
                    raise RuntimeError("Depth objective requested but depth model not built.")
                model.Minimize(D)
            elif mode == "overall":
                if D is None:
                    raise RuntimeError("Overall objective requested but depth model not built.")
                total_inv = cp_model.LinearExpr.Sum(inv_terms)
                total_area = cp_model.LinearExpr.Sum(area_terms)
                primary_cost = alpha_depth * D + beta_area * total_area + gamma_inv * total_inv
                model.Minimize(primary_cost)
            elif mode == "overall_tiebreak":
                total_inv = cp_model.LinearExpr.Sum(inv_terms)
                total_area = cp_model.LinearExpr.Sum(area_terms)
                model.Minimize(beta_area * total_area + gamma_inv * total_inv)
            elif mode == "depth_tiebreak_area":
                model.Minimize(cp_model.LinearExpr.Sum(area_terms))
            else:
                raise ValueError(f"Unknown objective mode: {mode}")

        return {
            "model": model,
            "apply_objective": apply_objective,
            "var_node_used": var_node_used,
            "var_cut": var_cut,
            "D": D,
            "area_terms": area_terms,
            "inv_terms": inv_terms,
            "level_vars": level_vars,
            "depth_bound": depth_bound,
        }

    def solve_model(
        model,
        time_limit=10,
        absolute_gap=None,
        relative_gap=None,
        num_workers=50,
        seed=1,
        stop_after_first=False,
    ):
        solver = cp_model.CpSolver()
        solver.parameters.random_seed = seed
        solver.parameters.num_search_workers = num_workers
        solver.parameters.max_time_in_seconds = time_limit
        solver.parameters.log_search_progress = False
        if absolute_gap is not None:
            solver.parameters.absolute_gap_limit = absolute_gap
        if relative_gap is not None:
            solver.parameters.relative_gap_limit = relative_gap
        if stop_after_first:
            solver.parameters.stop_after_first_solution = True
        status = solver.Solve(model)
        return solver, status

    final_solver = None
    final_status = None
    final_D = None
    final_objective = None
    chosen_cuts = {}

    if objective_mode in ("depth", "overall"):
        depth_bound = _compute_depth_upper_bound(data)
        depth_bound = max(depth_bound, len(node_dicts) or 1)
        print(f"Using depth upper bound UB = {depth_bound}")

        # Phase A: depth-only minimize D with relaxed gap and short cap.
        phase_a = build_model(depth_bound=depth_bound)
        phase_a["apply_objective"]("depth")
        solver_a, status_a = solve_model(
            phase_a["model"],
            time_limit=120,
            absolute_gap=1,
            relative_gap=None,
            num_workers=16,
            seed=1,
            stop_after_first=True,
        )
        status_a_str = _status_to_str(status_a)
        print(f"Phase A status: {status_a_str}")
        if status_a not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            print("No feasible solution in Phase A.")
            return {"status": status_a_str, "objective_value": None}

        best_depth = solver_a.Value(phase_a["D"])
        print(f"Phase A best depth D = {best_depth}")
        phase_a_cuts = _extract_chosen_cuts(
            node_dicts, phase_a["var_node_used"], phase_a["var_cut"], solver_a
        )
        final_solver = solver_a
        final_status = status_a_str
        final_D = best_depth
        final_objective = best_depth
        tie_objective = None

        # Phase B: fix depth and minimize tie-breaker.
        tie_mode = "depth_tiebreak_area" if objective_mode == "depth" else "overall_tiebreak"
        phase_b = build_model(depth_bound=depth_bound, fix_depth=best_depth)
        phase_b["apply_objective"](tie_mode)
        solver_b, status_b = solve_model(
            phase_b["model"],
            time_limit=60,
            num_workers=16,
            seed=1,
        )
        status_b_str = _status_to_str(status_b)
        print(f"Phase B status: {status_b_str}")
        if status_b in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            final_solver = solver_b
            final_status = status_b_str
            final_D = solver_b.Value(phase_b["D"])
            tie_objective = solver_b.ObjectiveValue()
            final_objective = best_depth
            chosen_cuts = _extract_chosen_cuts(
                node_dicts, phase_b["var_node_used"], phase_b["var_cut"], solver_b
            )
        else:
            print("No feasible solution in Phase B; returning Phase A solution.")
            chosen_cuts = phase_a_cuts
    else:
        single = build_model(depth_bound=None)
        single["apply_objective"](objective_mode)
        solver, status = solve_model(
            single["model"],                        #this timing is for the others area/og/inv
            time_limit=15,
            relative_gap=0.05,
            num_workers=50,
            seed=0,
        )
        status_str = _status_to_str(status)
        print(f"Status: {status_str}")
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            print("No feasible solution.")
            return {"status": status_str, "objective_value": None}
        final_solver = solver
        final_status = status_str
        final_D = solver.Value(single["D"]) if single["D"] else None
        final_objective = solver.ObjectiveValue()
        chosen_cuts = _extract_chosen_cuts(node_dicts, single["var_node_used"], single["var_cut"], solver)

    print(f"Status: {final_status}")
    if final_objective is not None:
        print(f"Objective value ({objective_mode}) = {final_objective}")
    if objective_mode in ("depth", "overall"):
        print("Global depth D =", final_D)
        if objective_mode in ("depth", "overall"):
            print("Phase B tie-break objective =", tie_objective)

    out = {"chosen_cuts": chosen_cuts}
    with open(chosen_json_path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"Written chosen cuts to {chosen_json_path}")

    return {"status": final_status, "objective_value": final_objective}

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="CP-SAT cut selection driver")
    parser.add_argument(
        "--cuts",
        default="/home/mrunal/Mockturtle-mMIG-main/build/examples/ctrl.json",
        help="Path to cuts JSON (from cut_enumeration) or a BLIF file that will be converted."
    )
    parser.add_argument(
        "--out",
        default="/home/mrunal/Mockturtle-mMIG-main/build/examples/ctrl_chosen_cuts.json",
        help="Destination JSON for chosen cuts."
    )
    parser.add_argument(
        "--objective",
        choices=["og", "inv", "area", "depth", "overall"],
        default="og",
        help="Optimization objective."
    )
    parser.add_argument(
        "--cut-enum-bin",
        default=None,
        help="Path to the cut_enumeration binary (required if --cuts is BLIF and the binary is not on PATH).",
    )
    parser.add_argument(
        "--cut-size",
        type=int,
        default=None,
        help="Optional K value to pass to cut_enumeration when converting BLIF inputs.",
    )
    args = parser.parse_args()

    solve_circuit(
        args.cuts,
        args.out,
        objective_mode=args.objective,
        cut_enum_bin=args.cut_enum_bin,
        cut_size=args.cut_size,
    )
