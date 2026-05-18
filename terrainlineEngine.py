"""
TerrainLine - terrainify SVG shapes with organic crumple.

Drop SVGs in input/, run, get terrainified versions in output/<name>/<name>_N/.
Each run is non-destructive: a new _N folder is created each time.
Variations sweep numeric crumple params combinatorically.
"""

import itertools
import json
import math
import re
import sys
from copy import deepcopy
from pathlib import Path

import numpy as np
from opensimplex import OpenSimplex
from svgpathtools import svg2paths2, Path as SvgPath
from shapely.geometry import Polygon


# Params that are conceptually integers - spectrum samples get rounded
INT_PARAMS = {"octaves", "subdivision", "smoothness"}
# All numeric params that can have a spectrum
SWEEP_PARAMS = ["amplitude", "frequency", "octaves",
                "persistence", "subdivision", "bias", "smoothness"]


# ---------- config ----------

def load_config(path="config.json"):
    with open(path, "r") as f:
        return json.load(f)


def get_base_values(config):
    """Extract the base 'value' for each crumple param into a flat dict."""
    out = {}
    for k in SWEEP_PARAMS:
        entry = config["crumple"][k]
        if isinstance(entry, dict):
            out[k] = entry["value"]
        else:
            out[k] = entry
    out["seed"] = config["crumple"]["seed"]
    return out


def build_variation_combos(config):
    """
    Build all combinatoric parameter combinations from spectrums.
    Each combo is a dict mapping param_name -> value.
    Excludes the all-base combo (that's the base output, not a variation).
    """
    base = get_base_values(config)
    axes = {}
    for k in SWEEP_PARAMS:
        entry = config["crumple"][k]
        if isinstance(entry, dict) and entry.get("spectrum"):
            spec = entry["spectrum"]
            steps = spec["steps"]
            if steps < 2:
                values = [spec["min"]]
            else:
                values = list(np.linspace(spec["min"], spec["max"], steps))
            if k in INT_PARAMS:
                values = sorted({int(round(v)) for v in values})
            axes[k] = values

    if not axes:
        return []

    keys = list(axes.keys())
    value_lists = [axes[k] for k in keys]
    combos = []
    for prod in itertools.product(*value_lists):
        combo = dict(base)  # start with base values for non-swept params
        for k, v in zip(keys, prod):
            combo[k] = v
        # Skip the combo that exactly matches base (that's the _N base file)
        if all(combo[k] == base[k] for k in keys):
            continue
        combos.append(combo)
    return combos


def apply_combo_to_config(config, combo):
    """Return a deep copy of config with crumple values set from combo."""
    c = deepcopy(config)
    for k in SWEEP_PARAMS:
        if k in combo:
            entry = c["crumple"][k]
            if isinstance(entry, dict):
                entry["value"] = combo[k]
            else:
                c["crumple"][k] = combo[k]
    c["crumple"]["seed"] = combo.get("seed", c["crumple"]["seed"])
    return c


def crumple_param(config, name):
    """Get the active value of a crumple param (handles both dict and scalar forms)."""
    entry = config["crumple"][name]
    if isinstance(entry, dict):
        return entry["value"]
    return entry


# ---------- SVG -> polylines ----------

def split_continuous_subpaths(svg_path):
    """Split a Path into continuous subpaths (handles M jumps within one path)."""
    if len(svg_path) == 0:
        return []
    subpaths = []
    current = [svg_path[0]]
    for i in range(1, len(svg_path)):
        prev_end = current[-1].end
        curr_start = svg_path[i].start
        if abs(prev_end - curr_start) > 1e-6:
            subpaths.append(SvgPath(*current))
            current = [svg_path[i]]
        else:
            current.append(svg_path[i])
    if current:
        subpaths.append(SvgPath(*current))
    return subpaths


def path_to_polyline(svg_path, samples_per_segment=20):
    try:
        total_len = svg_path.length()
    except Exception:
        return [], False
    if total_len == 0:
        return [], False

    num_samples = max(samples_per_segment * len(svg_path), 50)
    points = []
    for i in range(num_samples + 1):
        t = i / num_samples
        try:
            pt = svg_path.point(svg_path.ilength(t * total_len))
        except Exception:
            pt = svg_path.point(t)
        points.append((pt.real, pt.imag))

    if len(points) < 2:
        return points, False

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    diag = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
    tol = max(diag * 0.01, 1e-3)
    dx = points[0][0] - points[-1][0]
    dy = points[0][1] - points[-1][1]
    is_closed = math.hypot(dx, dy) < tol
    if is_closed:
        points = points[:-1]
    return points, is_closed


# ---------- subdivision ----------

def subdivide(points, factor, is_closed):
    if factor <= 1:
        return points
    out = []
    n = len(points)
    last = n if is_closed else n - 1
    for i in range(last):
        a = points[i]
        b = points[(i + 1) % n]
        for k in range(factor):
            t = k / factor
            out.append((a[0] + (b[0] - a[0]) * t,
                        a[1] + (b[1] - a[1]) * t))
    if not is_closed:
        out.append(points[-1])
    return out


# ---------- normals ----------

def compute_normals(points, is_closed):
    n = len(points)
    normals = []
    for i in range(n):
        if is_closed:
            prev_pt = points[(i - 1) % n]
            next_pt = points[(i + 1) % n]
        else:
            prev_pt = points[i - 1] if i > 0 else points[i]
            next_pt = points[i + 1] if i < n - 1 else points[i]
        tx = next_pt[0] - prev_pt[0]
        ty = next_pt[1] - prev_pt[1]
        length = math.hypot(tx, ty)
        if length == 0:
            normals.append((0.0, 0.0))
            continue
        normals.append((-ty / length, tx / length))
    return normals


# ---------- noise ----------

class CrumpleNoise:
    def __init__(self, seed, octaves, persistence, frequency):
        self.simplex = OpenSimplex(seed=int(seed))
        self.octaves = max(1, int(octaves))
        self.persistence = persistence
        self.frequency = frequency

    def sample_open(self, t):
        total = 0.0
        amp = 1.0
        freq = self.frequency
        norm = 0.0
        for _ in range(self.octaves):
            total += self.simplex.noise2(t * freq * 10.0, 0.0) * amp
            norm += amp
            amp *= self.persistence
            freq *= 2.0
        return total / norm if norm > 0 else 0.0

    def sample_closed(self, t):
        total = 0.0
        amp = 1.0
        freq = self.frequency
        norm = 0.0
        angle = t * 2.0 * math.pi
        for _ in range(self.octaves):
            r = freq * 2.0
            x = math.cos(angle) * r
            y = math.sin(angle) * r
            total += self.simplex.noise2(x, y) * amp
            norm += amp
            amp *= self.persistence
            freq *= 2.0
        return total / norm if norm > 0 else 0.0


# ---------- displacement ----------

def apply_crumple(points, is_closed, config, bbox_diag, seed_offset=0):
    amplitude = crumple_param(config, "amplitude") * bbox_diag
    bias = crumple_param(config, "bias")

    noise = CrumpleNoise(
        seed=config["crumple"]["seed"] + seed_offset,
        octaves=crumple_param(config, "octaves"),
        persistence=crumple_param(config, "persistence"),
        frequency=crumple_param(config, "frequency"),
    )

    normals = compute_normals(points, is_closed)
    n = len(points)
    out = []

    for i in range(n):
        t = i / n if is_closed else (i / (n - 1) if n > 1 else 0.0)
        val = noise.sample_closed(t) if is_closed else noise.sample_open(t)
        if bias != 0.0:
            val = val * (1.0 + bias * (1.0 if val > 0 else -1.0))
        if not is_closed:
            edge = 0.1
            if t < edge:
                val *= 0.5 - 0.5 * math.cos(math.pi * t / edge)
            elif t > 1.0 - edge:
                val *= 0.5 - 0.5 * math.cos(math.pi * (1.0 - t) / edge)
        nx, ny = normals[i]
        out.append((points[i][0] + nx * val * amplitude,
                    points[i][1] + ny * val * amplitude))
    return out


def smooth_pass(points, is_closed, iterations):
    iterations = int(iterations)
    for _ in range(iterations):
        n = len(points)
        new = []
        for i in range(n):
            if is_closed:
                prev_pt = points[(i - 1) % n]
                next_pt = points[(i + 1) % n]
            else:
                if i == 0 or i == n - 1:
                    new.append(points[i])
                    continue
                prev_pt = points[i - 1]
                next_pt = points[i + 1]
            new.append((
                (prev_pt[0] + points[i][0] * 2 + next_pt[0]) / 4,
                (prev_pt[1] + points[i][1] * 2 + next_pt[1]) / 4,
            ))
        points = new
    return points


# ---------- bands ----------

def generate_bands(points, is_closed, config, bbox_diag):
    b = config["bands"]
    if not b["enabled"] or not is_closed or len(points) < 3:
        return []
    try:
        poly = Polygon(points)
        if not poly.is_valid:
            poly = poly.buffer(0)
    except Exception:
        return []

    out = []
    direction = b["direction"]
    spacing = b["spacing"]
    count = b["count"]
    smoothness = int(crumple_param(config, "smoothness"))

    for k in range(1, count + 1):
        offsets = []
        if direction in ("outward", "both"):
            offsets.append(spacing * k)
        if direction in ("inward", "both"):
            offsets.append(-spacing * k)
        for offset in offsets:
            try:
                ring = poly.buffer(offset, join_style=2)
            except Exception:
                continue
            if ring.is_empty:
                continue
            geoms = list(ring.geoms) if hasattr(ring, "geoms") else [ring]
            for g in geoms:
                if not isinstance(g, Polygon):
                    continue
                coords = list(g.exterior.coords)[:-1]
                if len(coords) < 3:
                    continue
                variance_seed = int(k * 1000 + (1 if offset > 0 else 2) * 100)
                band_pts = apply_crumple(coords, True, config, bbox_diag,
                                          seed_offset=variance_seed)
                if smoothness > 0:
                    band_pts = smooth_pass(band_pts, True, smoothness)
                out.append((band_pts, True))
    return out


# ---------- output formatting ----------

def points_to_svg_path_d(points, is_closed, precision):
    if not points:
        return ""
    fmt = f"{{:.{precision}f}}"
    parts = [f"M {fmt.format(points[0][0])} {fmt.format(points[0][1])}"]
    for p in points[1:]:
        parts.append(f"L {fmt.format(p[0])} {fmt.format(p[1])}")
    if is_closed:
        parts.append("Z")
    return " ".join(parts)


def points_to_bezier_d(points, is_closed, precision):
    if len(points) < 2:
        return ""
    fmt = f"{{:.{precision}f}}"
    n = len(points)

    def get(i):
        if is_closed:
            return points[i % n]
        return points[max(0, min(n - 1, i))]

    parts = [f"M {fmt.format(points[0][0])} {fmt.format(points[0][1])}"]
    last = n if is_closed else n - 1
    for i in range(last):
        p0 = get(i - 1)
        p1 = get(i)
        p2 = get(i + 1)
        p3 = get(i + 2)
        c1 = (p1[0] + (p2[0] - p0[0]) / 6.0, p1[1] + (p2[1] - p0[1]) / 6.0)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6.0, p2[1] - (p3[1] - p1[1]) / 6.0)
        parts.append(
            f"C {fmt.format(c1[0])} {fmt.format(c1[1])} "
            f"{fmt.format(c2[0])} {fmt.format(c2[1])} "
            f"{fmt.format(p2[0])} {fmt.format(p2[1])}"
        )
    if is_closed:
        parts.append("Z")
    return " ".join(parts)


def write_svg(shapes, out_path, precision, fmt):
    all_pts = [p for s, _ in shapes for p in s]
    if not all_pts:
        return
    xs = [p[0] for p in all_pts]
    ys = [p[1] for p in all_pts]
    minx, miny, maxx, maxy = min(xs), min(ys), max(xs), max(ys)
    pad = max((maxx - minx), (maxy - miny)) * 0.05
    vb = f"{minx - pad} {miny - pad} {(maxx - minx) + 2 * pad} {(maxy - miny) + 2 * pad}"

    def write_one(suffix, builder):
        path = out_path if not suffix else out_path.with_name(
            out_path.stem + suffix + out_path.suffix
        )
        d_strings = [builder(pts, closed, precision) for pts, closed in shapes]
        with open(path, "w") as f:
            f.write(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}">\n')
            for d in d_strings:
                f.write(f'  <path d="{d}" fill="none" stroke="black" stroke-width="1"/>\n')
            f.write('</svg>\n')

    if fmt == "polyline":
        write_one("", points_to_svg_path_d)
    elif fmt == "bezier":
        write_one("", points_to_bezier_d)
    elif fmt == "both":
        write_one("_poly", points_to_svg_path_d)
        write_one("_bezier", points_to_bezier_d)


# ---------- core processing ----------

def bbox_diagonal(points):
    if not points:
        return 1.0
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return math.hypot(max(xs) - min(xs), max(ys) - min(ys)) or 1.0


def process_paths_with_config(paths, config):
    """Run the full crumple pipeline on a list of svgpathtools Paths."""
    output_shapes = []
    sub = int(crumple_param(config, "subdivision"))
    smoothness = int(crumple_param(config, "smoothness"))

    shape_idx = 0
    for p in paths:
        for sp in split_continuous_subpaths(p):
            polyline, is_closed = path_to_polyline(sp)
            if len(polyline) < 2:
                continue
            bdiag = bbox_diagonal(polyline)
            polyline = subdivide(polyline, sub, is_closed)
            crumpled = apply_crumple(polyline, is_closed, config, bdiag,
                                      seed_offset=shape_idx)
            if smoothness > 0:
                crumpled = smooth_pass(crumpled, is_closed, smoothness)
            output_shapes.append((crumpled, is_closed))
            output_shapes.extend(generate_bands(crumpled, is_closed, config, bdiag))
            shape_idx += 1
    return output_shapes


# ---------- run folder management ----------

def next_run_number(parent_dir, stem):
    """Find the next available _N for this input file in parent_dir."""
    if not parent_dir.exists():
        return 0
    pattern = re.compile(rf"^{re.escape(stem)}_(\d+)$")
    used = set()
    for entry in parent_dir.iterdir():
        if entry.is_dir():
            m = pattern.match(entry.name)
            if m:
                used.add(int(m.group(1)))
    n = 0
    while n in used:
        n += 1
    return n


def process_file(in_path, output_root, config):
    print(f"\n  reading: {in_path.name}")
    try:
        paths, _, _ = svg2paths2(str(in_path))
    except Exception as e:
        print(f"  ERROR parsing {in_path.name}: {e}")
        return
    if not paths:
        print(f"  no paths found, skipping")
        return

    stem = in_path.stem  # original filename, no sanitization
    file_dir = output_root / stem
    file_dir.mkdir(parents=True, exist_ok=True)

    run_n = next_run_number(file_dir, stem)
    run_dir = file_dir / f"{stem}_{run_n}"
    run_dir.mkdir(parents=True, exist_ok=True)
    variations_dir = run_dir / "variations"

    fmt = config["output"]["format"]
    precision = config["output"]["precision"]

    # --- BASE OUTPUT ---
    print(f"  run #{run_n} -> {run_dir}")
    base_shapes = process_paths_with_config(paths, config)
    if not base_shapes:
        print(f"  no usable shapes")
        return
    base_out = run_dir / f"{stem}_{run_n}.svg"
    write_svg(base_shapes, base_out, precision, fmt)
    print(f"    base: {base_out.name}  ({len(base_shapes)} shapes)")

    # --- VARIATIONS ---
    combos = build_variation_combos(config)
    if not combos:
        print(f"    no variations defined (no spectrums in config)")
        return

    variations_dir.mkdir(parents=True, exist_ok=True)
    print(f"    generating {len(combos)} variation(s)...")
    manifest = {
        "input_file": in_path.name,
        "run": run_n,
        "base_values": get_base_values(config),
        "variations": []
    }

    for v_idx, combo in enumerate(combos, start=1):
        v_config = apply_combo_to_config(config, combo)
        v_shapes = process_paths_with_config(paths, v_config)
        if not v_shapes:
            print(f"    V{v_idx}: SKIPPED (no shapes)")
            continue
        v_name = f"{stem}_{run_n}_V{v_idx}.svg"
        v_out = variations_dir / v_name
        write_svg(v_shapes, v_out, precision, fmt)
        combo_str = ", ".join(f"{k}={combo[k]}" for k in SWEEP_PARAMS if combo[k] != get_base_values(config)[k])
        print(f"    V{v_idx}: {v_name}  [{combo_str}]")
        manifest["variations"].append({
            "id": f"V{v_idx}",
            "file": v_name,
            "params": {k: combo[k] for k in SWEEP_PARAMS}
        })

    manifest_path = run_dir / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"    manifest: {manifest_path.name}")


def main():
    root = Path(__file__).parent
    in_dir = root / "input"
    out_dir = root / "output"
    in_dir.mkdir(exist_ok=True)
    out_dir.mkdir(exist_ok=True)

    config_path = root / "config.json"
    if not config_path.exists():
        print(f"ERROR: {config_path} not found")
        sys.exit(1)

    config = load_config(config_path)
    print(f"TerrainLine starting")
    print(f"  input:  {in_dir}")
    print(f"  output: {out_dir}")

    svgs = sorted(in_dir.glob("*.svg"))
    if not svgs:
        print("no SVG files in input/")
        return

    print(f"  found {len(svgs)} SVG file(s)")
    for svg in svgs:
        process_file(svg, out_dir, config)
    print("\ndone.")


if __name__ == "__main__":
    main()