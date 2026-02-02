import csv
import json
import inspect
import cirkit
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent

### Global settings
verbose = True
print_progress = True

### Misc
class color:
   PURPLE = '\033[95m'
   CYAN = '\033[96m'
   DARKCYAN = '\033[36m'
   BLUE = '\033[94m'
   GREEN = '\033[92m'
   YELLOW = '\033[93m'
   RED = '\033[91m'
   BOLD = '\033[1m'
   UNDERLINE = '\033[4m'
   ENDC = '\033[0m'

### Benchmarks
benchmarks = {
  "adder":      { 'verify': True },
  "arbiter":    { 'verify': True },
  "bar":        { 'verify': True },
  "cavlc":      { 'verify': True },
  "ctrl":       { 'verify': True },
  "dec":        { 'verify': True },
  "div":        { 'verify': False },
  "i2c":        { 'verify': True },
  "int2float":  { 'verify': True },
  "log2":       { 'verify': False },
  "max":        { 'verify': True },
  "mem_ctrl":   { 'verify': True },
  "multiplier": { 'verify': True },
  "priority":   { 'verify': True },
  "router":     { 'verify': True },
  "sin":        { 'verify': True },
  "sqrt":       { 'verify': True },
  "square":     { 'verify': True },
  "voter":      { 'verify': True },
  "hyp":        { 'verify': False },
}

### Configurations
configurations = {
   #  "aig" : {},
    "mig" : {}
   #  "xag" : {},
   #  "xmg" : {}
}

# Some cirkit builds may not provide all network types. Detect which stores are
# available to avoid passing unsupported flags (which leads to
# "argument was not expected" errors).
unsupported_configurations = set()
try:
   _store_params = inspect.signature(cirkit.store).parameters
except (TypeError, ValueError):
   _store_params = None

supported_stores = set()
if _store_params is None or "lut" in _store_params:
   supported_stores.add("lut")

for _conf_name in list(configurations.keys()):
   if _store_params is not None and _conf_name not in _store_params:
      unsupported_configurations.add(_conf_name)
      print(f"[w] configuration '{_conf_name}' not supported by cirkit (missing --{_conf_name} flag)")
      continue
   try:
      cirkit.store(clear=True, **{_conf_name: True})
      supported_stores.add(_conf_name)
   except Exception as exc:  # noqa: BLE001
      unsupported_configurations.add(_conf_name)
      print(f"[w] configuration '{_conf_name}' not supported by cirkit: {exc}")

### Cirkit wrapper calls
def aigerfile(name):
   path = ROOT / "benchmarks" / f"{name}.aig"
   if not path.exists():
      raise FileNotFoundError(f"Benchmark not found: {path}")
   return path

def resultfile(name, suffix, ext):
   result_path = ROOT / "results" / f"{name}_{suffix}.{ext}"
   result_path.parent.mkdir(parents=True, exist_ok=True)
   return result_path

def read(name,filename):
   return cirkit.read_aiger(filename=str(filename), **{name : True})

def write(name,filename):
   cirkit.write_verilog(filename=str(filename), **{name : True})

def clear_store():
   store_kwargs = {'clear': True}
   for store in supported_stores:
      store_kwargs[store] = True
   cirkit.store(**store_kwargs)

def ps(name):
   return cirkit.ps(silent=True, **{name : True})

def lut_mapping(name):
   return cirkit.lut_mapping(**{name : True})

def collapse_mapping(name):
   return cirkit.collapse_mapping(**{name : True})

def compute_stats(name):
   ntk_stats = ps(name).dict()
   cost_stats = cirkit.migcost(**{name : True}).dict()
   lut_mapping(name)
   collapse_mapping(name)
   lut_stats = ps('lut').dict()

   statistics = {
      'pis': ntk_stats['pis'],
      'pos': ntk_stats['pos'],
      'gates' : ntk_stats['gates'],
      'depth': ntk_stats['depth'],
      'inverters': cost_stats.get('num_inverters'),
      'luts' : lut_stats['gates'],
      'lut_depth' : lut_stats.get('depth'),
      'qca_area': cost_stats.get('qca_area'),
      'qca_delay': cost_stats.get('qca_delay'),
      'qca_energy': cost_stats.get('qca_energy'),
      'stmg_area': cost_stats.get('stmg_area'),
      'stmg_delay': cost_stats.get('stmg_delay'),
      'stmg_energy': cost_stats.get('stmg_energy'),
   }

   return statistics

def rfz(name):
   if ( name == 'mig' ):
      return cirkit.refactor(strategy=1, progress=True, zero_gain=True)
   else:
      return {'time_total': 0.0}

def rf(name):
   if ( name == 'mig' ):
      return cirkit.refactor(strategy=1, progress=True)
   else:
      return {'time_total': 0.0}

def rwz(name):
  if ( name == 'mig' ):
    return cirkit.cut_rewrite(strategy=0, progress=True, lutsize=4, zero_gain=True, mig=True)
  elif ( name == 'aig' ):
    return cirkit.cut_rewrite(strategy=0, progress=True, lutsize=4, zero_gain=True, aig=True)
  elif ( name == "xmg" ):
    return cirkit.cut_rewrite(strategy=0, progress=True, lutsize=4, zero_gain=True, xmg=True)
  elif ( name == "xag" ):
    return cirkit.cut_rewrite(strategy=0, progress=True, lutsize=4, zero_gain=True, xag=True)
  else:
    print("[i] rwz: graph type not supported")

def rw(name):
  if ( name == 'mig' ):
    return cirkit.cut_rewrite(strategy=0, progress=True, lutsize=4, mig=True)
  elif ( name == 'aig' ):
    return cirkit.cut_rewrite(strategy=0, progress=True, lutsize=4, aig=True)
  elif ( name == "xmg" ):
    return cirkit.cut_rewrite(strategy=0, progress=True, lutsize=4, xmg=True)
  elif ( name == "xag" ):
    return cirkit.cut_rewrite(strategy=0, progress=True, lutsize=4, xag=True)
  else:
    print("[i] rw: graph type not supported")

def rsz(name, cut_size, depth=1):
   return cirkit.resub(progress=True, max_pis=cut_size, depth=depth, zero_gain=True)

def rs(name, cut_size, depth=1):
   if name == "mig" and cut_size > 8 and depth > 1:
      depth = 1
   return cirkit.resub(progress=True, max_pis=cut_size, depth=depth)

def bz(name):
   if ( name == 'mig' ):
      return cirkit.mighty(area_aware=True)
   else:
      return {'time_total': 0.0}

def abc_cec(in_filename, out_filename, log_filename="abc.log"):
   cmd = ["abc", "-c", f"cec -n {in_filename} {out_filename}"]
   res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
   Path(log_filename).write_text(res.stdout)
   return res.stdout

### Optimization script
compress2rs = [
    [bz, {}],
    [rs, {'cut_size': 6}],
    [rw, {}],
    [rs, {'cut_size': 6, 'depth':2}],
    [rf, {},],
    [rs, {'cut_size': 8},8],
    [bz, {},],
    [rs, {'cut_size': 8, 'depth':2}],
    [rw, {},],
    [rs, {'cut_size': 10}],
    [rwz, {},],
    [rs, {'cut_size': 10, 'depth':2}],
    [bz, {}],
    [rs, {'cut_size': 12}],
    [rfz, {}],
    [rs, {'cut_size': 12, 'depth':2}],
    [rwz, {}],
    [bz, {}],
]

def run_flow(flow_script, verbose = False):
    time_total = 0.0
    for transformation in flow_script:
        functor = transformation[0]
        args = transformation[1]

        stats = functor(name, **args)
        time = stats['time_total']

        if (verbose):
            print("[i] ", functor.__name__, args, 'time: %8.2fs' % time)
        time_total = time_total + time

    statistics = {
        'time_total': time_total
    }
    return statistics

table = {}

# table = {
#     'adder' :
#     {
#         'baseline': { 'gates': gates, 'depth': depth, 'luts': luts, 'time': time },
#         'aig': { ... } ,
#         'mig': { ... },
#         'xag': { ... },
#         'xmg': { ... },
#     }
#     ...
# }

for benchmark, benchmark_params in benchmarks.items():
   for name, configuration in configurations.items():
      if name in unsupported_configurations:
         print(f"[w] skipping {benchmark} with {name}: configuration not supported")
         continue
      try:
         print(f"[i] run {benchmark} with {name}")

         # read benchmark
         in_filename = aigerfile(benchmark)
         read(name, in_filename)

         # compute statistics for initial benchmark
         stats_before = compute_stats(name)

         # clear storage and re-read benchmark
         clear_store()
         read(name, in_filename)

         # run flow script
         stats_opt = run_flow(compress2rs, verbose)

         # compute statistics for optimized benchmark
         stats_after = compute_stats(name)

         # write result file
         out_filename = resultfile(benchmark, name, 'v')
         write(name, out_filename)

         # verify final result using ABC CEC
         verified = color.BLUE + '[not checked]' + color.ENDC
         if benchmark_params['verify']:
            output = abc_cec(in_filename, out_filename, log_filename="abc.log")
            if "Networks are equivalent" in output:
               verified = color.GREEN + '[verified]' + color.ENDC
            else:
               print('[e] verification after optimization failed')
               verified = color.RED + '[failed]' + color.ENDC

         # update table
         if (not benchmark in table):
            table[benchmark] = {
               'baseline': {
                  'pis': stats_before['pis'],
                  'pos': stats_before['pos'],
                  'gates': stats_before['gates'],
                  'depth': stats_before['depth'],
                  'inverters': stats_before['inverters'],
                  'luts': stats_before['luts'],
                  'lut_depth': stats_before['lut_depth'],
                  'qca_area': stats_before['qca_area'],
                  'qca_delay': stats_before['qca_delay'],
                  'qca_energy': stats_before['qca_energy'],
                  'stmg_area': stats_before['stmg_area'],
                  'stmg_delay': stats_before['stmg_delay'],
                  'stmg_energy': stats_before['stmg_energy'],
                  'time': 0.0,
               },
               name: {
                  'pis': stats_before['pis'],
                  'pos': stats_before['pos'],
                  'gates': stats_after['gates'],
                  'depth': stats_after['depth'],
                  'inverters': stats_after['inverters'],
                  'luts': stats_after['luts'],
                  'lut_depth': stats_after['lut_depth'],
                  'qca_area': stats_after['qca_area'],
                  'qca_delay': stats_after['qca_delay'],
                  'qca_energy': stats_after['qca_energy'],
                  'stmg_area': stats_after['stmg_area'],
                  'stmg_delay': stats_after['stmg_delay'],
                  'stmg_energy': stats_after['stmg_energy'],
                  'time': stats_opt['time_total'],
               }
            }
         else:
            table[benchmark][name] = {
               'pis': stats_before['pis'],
               'pos': stats_before['pos'],
               'gates': stats_after['gates'],
               'depth': stats_after['depth'],
               'inverters': stats_after['inverters'],
               'luts': stats_after['luts'],
               'lut_depth': stats_after['lut_depth'],
               'qca_area': stats_after['qca_area'],
               'qca_delay': stats_after['qca_delay'],
               'qca_energy': stats_after['qca_energy'],
               'stmg_area': stats_after['stmg_area'],
               'stmg_delay': stats_after['stmg_delay'],
               'stmg_energy': stats_after['stmg_energy'],
               'time': stats_opt['time_total'],
            }

         # print progress for each benchmark
         if print_progress:
            print(table[benchmark][name], verified)
      except Exception as exc:  # noqa: BLE001
         if f"--{name}" in str(exc) and "not expected" in str(exc):
            unsupported_configurations.add(name)
            supported_stores.discard(name)
            print(f"[w] marking configuration '{name}' as unsupported after failure: {exc}")
            continue
         print(f"[e] flow failed for {benchmark} with {name}: {exc}")
      finally:
         clear_store()

# Generate final table in LATEX format
network_order = list(configurations.keys())
header = [
   "Benchmark",
   "PIs/POs",
   "Gates$_0$",
   "Depth$_0$",
   "Inv$_0$",
   "LUTs$_0$",
   "LUTd$_0$",
   "QCA$_0$ (area)",
   "QCA$_0$ (delay)",
   "QCA$_0$ (energy)",
   "STMG$_0$ (area)",
   "STMG$_0$ (delay)",
   "STMG$_0$ (energy)",
]
for net in network_order:
   header.extend([
      f"Gates$_{{{net}}}$",
      f"Depth$_{{{net}}}$",
      f"Inv$_{{{net}}}$",
      f"LUTs$_{{{net}}}$",
      f"LUTd$_{{{net}}}$",
      f"QCA$_{{{net}}}$ (area)",
      f"QCA$_{{{net}}}$ (delay)",
      f"QCA$_{{{net}}}$ (energy)",
      f"STMG$_{{{net}}}$ (area)",
      f"STMG$_{{{net}}}$ (delay)",
      f"STMG$_{{{net}}}$ (energy)",
      f"Time$_{{{net}}}$",
   ])

col_spec = "l" + "r" * (len(header) - 1)
print("\\begin{tabular}{" + col_spec + "}")
print(" & ".join(header) + " \\\\ \\hline")

for benchmark in benchmarks.keys():
   benchmark_data = table.get(benchmark)
   if not benchmark_data or 'baseline' not in benchmark_data:
      print(f"[w] skipping {benchmark}: baseline missing")
      continue

   base = benchmark_data['baseline']
   row = [
      benchmark,
      f"{base['pis']}/{base['pos']}",
      f"{base['gates']}",
      f"{base['depth']}",
      f"{base['inverters']}",
      f"{base['luts']}",
      f"{base['lut_depth']}",
      f"{base['qca_area']}",
      f"{base['qca_delay']}",
      f"{base['qca_energy']}",
      f"{base['stmg_area']}",
      f"{base['stmg_delay']}",
      f"{base['stmg_energy']}",
   ]

   for net in network_order:
      data = benchmark_data.get(net) if benchmark_data else None
      if data is None:
         missing = ["-"] * 11 + ["-"]  # gates, depth, inv, luts, lut_depth, qca_area, qca_delay, qca_energy, stmg_area, stmg_delay, stmg_energy, time
         row.extend(missing)
         continue
      row.extend([
         f"{data['gates']}",
         f"{data['depth']}",
         f"{data['inverters']}",
         f"{data['luts']}",
         f"{data['lut_depth']}",
         f"{data['qca_area']}",
         f"{data['qca_delay']}",
         f"{data['qca_energy']}",
         f"{data['stmg_area']}",
         f"{data['stmg_delay']}",
         f"{data['stmg_energy']}",
         f"{data['time']:.2f}",
      ])

   print(" & ".join(row) + " \\\\")

print("\\end{tabular}")

# Emit CSV with the same data (plus QCA/STMG) for easy copying.
csv_header = [
   "benchmark",
   "pis",
   "pos",
   "gates_0",
   "depth_0",
   "inv_0",
   "luts_0",
   "lut_depth_0",
   "qca_area_0",
   "qca_delay_0",
   "qca_energy_0",
   "stmg_area_0",
   "stmg_delay_0",
   "stmg_energy_0",
]
for net in network_order:
   csv_header.extend([
      f"gates_{net}",
      f"depth_{net}",
      f"inv_{net}",
      f"luts_{net}",
      f"lut_depth_{net}",
      f"qca_area_{net}",
      f"qca_delay_{net}",
      f"qca_energy_{net}",
      f"stmg_area_{net}",
      f"stmg_delay_{net}",
      f"stmg_energy_{net}",
      f"time_{net}",
   ])

csv_path = ROOT / "New_OG_30_50_blif_results" / "summary_on_New_OG_30_50_blifs.csv"
csv_path.parent.mkdir(parents=True, exist_ok=True)
with csv_path.open("w", newline="") as csvfile:
   writer = csv.writer(csvfile)
   writer.writerow(csv_header)

   for benchmark in benchmarks.keys():
      if benchmark not in table or 'baseline' not in table[benchmark]:
         print(f"[w] skipping {benchmark} in CSV: baseline missing")
         continue

      base = table[benchmark]['baseline']
      row = [
         benchmark,
         base['pis'],
         base['pos'],
         base['gates'],
         base['depth'],
         base['inverters'],
         base['luts'],
         base['lut_depth'],
         base['qca_area'],
         base['qca_delay'],
         base['qca_energy'],
         base['stmg_area'],
         base['stmg_delay'],
         base['stmg_energy'],
      ]

      for net in network_order:
         data = table[benchmark].get(net)
         if data is None:
            row.extend([None] * 11 + [None])
            continue
         row.extend([
            data['gates'],
            data['depth'],
            data['inverters'],
            data['luts'],
            data['lut_depth'],
            data['qca_area'],
            data['qca_delay'],
            data['qca_energy'],
            data['stmg_area'],
            data['stmg_delay'],
            data['stmg_energy'],
            f"{data['time']:.2f}",
         ])

      writer.writerow(row)

print(f"[i] CSV written to {csv_path}")
