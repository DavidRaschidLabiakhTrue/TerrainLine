"""
TerrainLine Studio - unified GUI for crumple + bands.

Run: python terrainline_studio.py

Pick a mode from the dropdown:
  - Crumple: terrainify shapes (output/<stem>/<stem>_N.svg)
  - Bands:   generate band rings around closed shapes
             (output/<stem>_bands/<stem>_N.svg)

Each mode has live preview as you drag sliders.
"""

import json
import random
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path

from svgpathtools import svg2paths2

from terrainlineEngine import (
    load_config,
    process_paths_with_config,
    split_continuous_subpaths,
    path_to_polyline,
    generate_bands,
    apply_crumple,
    smooth_pass,
    bbox_diagonal,
    subdivide,
    crumple_param,
    write_svg,
)


ROOT = Path(__file__).parent
IN_DIR = ROOT / "input"
OUT_DIR = ROOT / "output"
CONFIG_PATH = ROOT / "config.json"

MODES = ["Crumple", "Bands"]
BANDS_OUTPUT_SUFFIX = "_bands"

# (name, from, to, resolution, is_int)
CRUMPLE_SPECS = [
    ("amplitude",   0.0,  0.20, 0.001, False),
    ("frequency",   0.1,  10.0, 0.1,   False),
    ("octaves",     1,    8,    1,     True),
    ("persistence", 0.0,  1.0,  0.01,  False),
    ("subdivision", 1,    20,   1,     True),
    ("bias",        -1.0, 1.0,  0.01,  False),
    ("smoothness",  0,    5,    1,     True),
]


class TerrainLineStudio:
    def __init__(self, root):
        self.root = root
        self.root.title("TerrainLine Studio")
        self.root.geometry("1250x880")

        IN_DIR.mkdir(exist_ok=True)
        OUT_DIR.mkdir(exist_ok=True)

        self.config = self._load_config_safe()
        self.paths = []
        self.originals = []       # all sampled subpaths (for gray underlay)
        self.closed_sources = []  # closed-only (band source candidates)
        self.current_stem = None
        self._preview_job = None

        # Shared crumple controls (both modes use them)
        self._crumple_vars = {}
        self._crumple_labels = {}

        self._build_ui()
        self._refresh_file_list()
        self.root.bind("<Configure>", self._on_resize)

    # ---------- config ----------

    def _load_config_safe(self):
        if not CONFIG_PATH.exists():
            messagebox.showerror("Missing config", f"{CONFIG_PATH} not found")
            self.root.destroy()
            return None
        try:
            return load_config(CONFIG_PATH)
        except Exception as e:
            messagebox.showerror("Config error", f"Failed to load config:\n{e}")
            return self._default_config()

    def _default_config(self):
        return {
            "crumple": {
                "amplitude": {"value": 0.03, "spectrum": None},
                "frequency": {"value": 1.5, "spectrum": None},
                "octaves": {"value": 4, "spectrum": None},
                "persistence": {"value": 0.5, "spectrum": None},
                "subdivision": {"value": 8, "spectrum": None},
                "bias": {"value": 0.0, "spectrum": None},
                "smoothness": {"value": 1, "spectrum": None},
                "seed": 42,
            },
            "bands": {
                "enabled": False, "count": 3, "spacing": 10,
                "direction": "outward", "crumple_variance": 0.3,
            },
            "output": {"format": "polyline", "precision": 2},
        }

    def _config_val(self, name):
        entry = self.config["crumple"][name]
        return entry["value"] if isinstance(entry, dict) else entry

    # ---------- UI ----------

    def _build_ui(self):
        main = ttk.Frame(self.root)
        main.pack(fill=tk.BOTH, expand=True)

        # Left pane
        left = ttk.Frame(main, width=360)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=8, pady=8)
        left.pack_propagate(False)

        # --- Mode dropdown (top, prominent) ---
        mode_frame = ttk.LabelFrame(left, text="Mode")
        mode_frame.pack(fill=tk.X, pady=(0, 6))
        self.mode_var = tk.StringVar(value=MODES[0])
        mode_combo = ttk.Combobox(mode_frame, textvariable=self.mode_var, state="readonly",
                                  values=MODES, font=("TkDefaultFont", 11))
        mode_combo.pack(fill=tk.X, padx=6, pady=6)
        mode_combo.bind("<<ComboboxSelected>>", lambda e: self._on_mode_change())

        # --- Input ---
        file_frame = ttk.LabelFrame(left, text="Input")
        file_frame.pack(fill=tk.X, pady=6)
        row = ttk.Frame(file_frame)
        row.pack(fill=tk.X, padx=6, pady=6)
        self.file_var = tk.StringVar()
        self.file_combo = ttk.Combobox(row, textvariable=self.file_var, state="readonly")
        self.file_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.file_combo.bind("<<ComboboxSelected>>", lambda e: self._on_file_selected())
        ttk.Button(row, text="↻", width=3, command=self._refresh_file_list).pack(
            side=tk.LEFT, padx=(4, 0))

        # --- Crumple (shared) ---
        crumple_frame = ttk.LabelFrame(left, text="Crumple")
        crumple_frame.pack(fill=tk.X, pady=6)
        for name, lo, hi, res, is_int in CRUMPLE_SPECS:
            self._make_slider(crumple_frame, name, lo, hi, res, is_int)

        seed_row = ttk.Frame(crumple_frame)
        seed_row.pack(fill=tk.X, padx=6, pady=(2, 6))
        ttk.Label(seed_row, text="seed", width=12).pack(side=tk.LEFT)
        self.seed_var = tk.StringVar(value=str(self.config["crumple"]["seed"]))
        seed_entry = ttk.Entry(seed_row, textvariable=self.seed_var, width=10)
        seed_entry.pack(side=tk.LEFT, padx=(0, 4))
        seed_entry.bind("<KeyRelease>", lambda e: self.schedule_preview())
        ttk.Button(seed_row, text="Reseed", command=self.on_reseed).pack(side=tk.LEFT)

        # --- Bands (visible only in Bands mode) ---
        self.bands_frame = ttk.LabelFrame(left, text="Bands")
        self._build_bands_section(self.bands_frame)
        # packed/unpacked by _on_mode_change

        # --- Output ---
        out_frame = ttk.LabelFrame(left, text="Output")
        out_frame.pack(fill=tk.X, pady=6)
        self.fmt_var = tk.StringVar(value=self.config["output"]["format"])
        fmt_row = ttk.Frame(out_frame)
        fmt_row.pack(fill=tk.X, padx=6, pady=4)
        ttk.Label(fmt_row, text="format:").pack(side=tk.LEFT, padx=(0, 6))
        for v in ("polyline", "bezier", "both"):
            ttk.Radiobutton(fmt_row, text=v, variable=self.fmt_var, value=v).pack(side=tk.LEFT)

        prec_row = ttk.Frame(out_frame)
        prec_row.pack(fill=tk.X, padx=6, pady=(0, 6))
        ttk.Label(prec_row, text="precision", width=10).pack(side=tk.LEFT)
        self.precision_var = tk.IntVar(value=self.config["output"]["precision"])
        self.prec_label = ttk.Label(prec_row, text=str(self.precision_var.get()), width=4)
        self.prec_label.pack(side=tk.RIGHT)
        ttk.Scale(prec_row, from_=0, to=6, variable=self.precision_var, orient=tk.HORIZONTAL,
                  command=lambda v: self.prec_label.config(text=str(int(float(v))))).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=4)

        # --- Buttons ---
        btn_frame = ttk.Frame(left)
        btn_frame.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(btn_frame, text="Save to Disk", command=self.on_save_disk).pack(fill=tk.X, pady=2)
        ttk.Button(btn_frame, text="Save Config", command=self.on_save_config).pack(fill=tk.X, pady=2)
        ttk.Button(btn_frame, text="Reload Config", command=self.on_reload_config).pack(fill=tk.X, pady=2)

        # --- Right pane: canvas + status ---
        right = ttk.Frame(main)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8), pady=8)
        self.canvas = tk.Canvas(right, bg="white", highlightthickness=1,
                                highlightbackground="#999")
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.status_var = tk.StringVar(value="Ready. Pick a file to start.")
        ttk.Label(right, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W).pack(
            fill=tk.X, pady=(4, 0))

        # Initialize mode visibility
        self._on_mode_change(initial=True)

    def _build_bands_section(self, parent):
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, padx=6, pady=4)
        ttk.Label(row, text="count", width=12).pack(side=tk.LEFT)
        self.bands_count = tk.IntVar(value=self.config["bands"]["count"])
        self.count_label = ttk.Label(row, text=str(self.bands_count.get()), width=6)
        self.count_label.pack(side=tk.RIGHT)
        ttk.Scale(row, from_=1, to=20, variable=self.bands_count, orient=tk.HORIZONTAL,
                  command=lambda v: (self.count_label.config(text=str(int(float(v)))),
                                     self.schedule_preview())).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=4)

        row = ttk.Frame(parent)
        row.pack(fill=tk.X, padx=6, pady=4)
        ttk.Label(row, text="spacing", width=12).pack(side=tk.LEFT)
        self.bands_spacing = tk.DoubleVar(value=float(self.config["bands"]["spacing"]))
        self.spacing_label = ttk.Label(row, text=f"{self.bands_spacing.get():.1f}", width=6)
        self.spacing_label.pack(side=tk.RIGHT)
        ttk.Scale(row, from_=0.5, to=100, variable=self.bands_spacing, orient=tk.HORIZONTAL,
                  command=lambda v: (self.spacing_label.config(text=f"{float(v):.1f}"),
                                     self.schedule_preview())).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=4)

        row = ttk.Frame(parent)
        row.pack(fill=tk.X, padx=6, pady=4)
        ttk.Label(row, text="direction", width=12).pack(side=tk.LEFT)
        self.bands_direction = tk.StringVar(value=self.config["bands"]["direction"])
        ttk.Combobox(row, textvariable=self.bands_direction, state="readonly",
                     values=["outward", "inward", "both"], width=12).pack(side=tk.LEFT)
        self.bands_direction.trace_add("write", lambda *a: self.schedule_preview())

        # Bands-mode source options
        self.crumple_source = tk.BooleanVar(value=True)
        ttk.Checkbutton(parent, text="crumple source before banding",
                        variable=self.crumple_source,
                        command=self.schedule_preview).pack(anchor=tk.W, padx=6, pady=2)
        self.include_source = tk.BooleanVar(value=False)
        ttk.Checkbutton(parent, text="include source in output",
                        variable=self.include_source,
                        command=self.schedule_preview).pack(anchor=tk.W, padx=6, pady=(0, 6))

    def _make_slider(self, parent, name, lo, hi, res, is_int):
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, padx=6, pady=2)
        ttk.Label(row, text=name, width=12).pack(side=tk.LEFT)

        initial = self._config_val(name)
        var = tk.IntVar(value=int(initial)) if is_int else tk.DoubleVar(value=float(initial))
        self._crumple_vars[name] = var

        value_lbl = ttk.Label(row, text=self._fmt_val(initial, is_int), width=6)
        value_lbl.pack(side=tk.RIGHT)
        self._crumple_labels[name] = (value_lbl, is_int)

        def on_change(v):
            try:
                f = float(v)
            except ValueError:
                return
            if is_int:
                value_lbl.config(text=str(int(round(f))))
            else:
                value_lbl.config(text=f"{f:.3f}" if res < 0.01 else f"{f:.2f}")
            self.schedule_preview()

        ttk.Scale(row, from_=lo, to=hi, variable=var, orient=tk.HORIZONTAL,
                  command=on_change).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)

    @staticmethod
    def _fmt_val(v, is_int):
        if is_int:
            return str(int(v))
        return f"{float(v):.3f}" if abs(v) < 1 else f"{float(v):.2f}"

    # ---------- mode switching ----------

    def _on_mode_change(self, initial=False):
        mode = self.mode_var.get()
        if mode == "Bands":
            # Insert bands section after crumple (re-pack to enforce position)
            self.bands_frame.pack(fill=tk.X, pady=6, after=self._find_crumple_frame())
        else:
            self.bands_frame.pack_forget()
        if not initial:
            self.schedule_preview()

    def _find_crumple_frame(self):
        # Find the Crumple LabelFrame among left pane children
        left = self.bands_frame.master
        for child in left.winfo_children():
            if isinstance(child, ttk.LabelFrame) and child.cget("text") == "Crumple":
                return child
        return None

    # ---------- file handling ----------

    def _refresh_file_list(self):
        svgs = sorted(IN_DIR.glob("*.svg"))
        names = [s.name for s in svgs]
        self.file_combo["values"] = names
        if names:
            if self.file_var.get() not in names:
                self.file_var.set(names[0])
                self._on_file_selected()
        else:
            self.file_var.set("")
            self.paths = []
            self.originals = []
            self.closed_sources = []
            self.canvas.delete("all")
            self.status_var.set("No SVGs in input/")

    def _on_file_selected(self):
        name = self.file_var.get()
        if not name:
            return
        path = IN_DIR / name
        try:
            paths, _, _ = svg2paths2(str(path))
        except Exception as e:
            self.status_var.set(f"ERROR parsing {name}: {e}")
            self.paths = []
            self.originals = []
            self.closed_sources = []
            self.canvas.delete("all")
            return
        if not paths:
            self.status_var.set(f"No paths in {name}")
            return

        self.paths = paths
        self.current_stem = path.stem
        self.originals = []
        self.closed_sources = []
        for p in paths:
            for sp in split_continuous_subpaths(p):
                pts, closed = path_to_polyline(sp)
                if len(pts) >= 2:
                    self.originals.append((pts, closed))
                    if closed and len(pts) >= 3:
                        self.closed_sources.append(pts)
        self.status_var.set(
            f"Loaded {name}: {len(self.originals)} subpath(s), {len(self.closed_sources)} closed"
        )
        self.schedule_preview()

    # ---------- config build ----------

    def _build_config_from_ui(self):
        try:
            seed = int(self.seed_var.get())
        except ValueError:
            seed = 42

        crumple = {"seed": seed}
        for name, _, _, _, is_int in CRUMPLE_SPECS:
            v = self._crumple_vars[name].get()
            v = int(round(v)) if is_int else float(v)
            crumple[name] = {"value": v, "spectrum": None}

        mode = self.mode_var.get()
        return {
            "crumple": crumple,
            "paths": self.config.get("paths", {
                "closed_shape_mode": "preserve_closure",
                "open_path_mode": "preserve_endpoints",
            }),
            "bands": {
                # Bands enabled flag in the engine config only matters when we use the
                # engine's main entry point. Our Bands-mode pipeline calls the lower-
                # level generate_bands() directly, so this is mostly informational.
                "enabled": mode == "Bands",
                "count": int(self.bands_count.get()),
                "spacing": float(self.bands_spacing.get()),
                "direction": self.bands_direction.get(),
                "crumple_variance": self.config["bands"].get("crumple_variance", 0.3),
            },
            "output": {
                "format": self.fmt_var.get(),
                "precision": int(self.precision_var.get()),
            },
        }

    # ---------- pipelines ----------

    def _generate_crumple_shapes(self, cfg):
        return process_paths_with_config(self.paths, cfg)

    def _generate_bands_shapes(self, cfg):
        out = []
        sub = int(crumple_param(cfg, "subdivision"))
        smoothness = int(crumple_param(cfg, "smoothness"))
        crumple_src = self.crumple_source.get()
        include_src = self.include_source.get()

        for idx, src_pts in enumerate(self.closed_sources):
            bdiag = bbox_diagonal(src_pts)
            pts = subdivide(src_pts, sub, True)
            if crumple_src:
                pts = apply_crumple(pts, True, cfg, bdiag, seed_offset=idx)
                if smoothness > 0:
                    pts = smooth_pass(pts, True, smoothness)
            if include_src:
                out.append((pts, True))
            out.extend(generate_bands(pts, True, cfg, bdiag))
        return out

    # ---------- preview ----------

    def schedule_preview(self):
        if self._preview_job:
            self.root.after_cancel(self._preview_job)
        self._preview_job = self.root.after(250, self.update_preview)

    def update_preview(self):
        self._preview_job = None
        if not self.paths:
            return

        mode = self.mode_var.get()
        try:
            cfg = self._build_config_from_ui()
            if mode == "Bands":
                if not self.closed_sources:
                    self.canvas.delete("all")
                    self._draw(self.originals, [])
                    self.status_var.set("No closed shapes - Bands mode needs closed paths")
                    return
                shapes = self._generate_bands_shapes(cfg)
            else:
                shapes = self._generate_crumple_shapes(cfg)
        except Exception as e:
            self.status_var.set(f"Preview error: {e}")
            return

        if not shapes:
            self.canvas.delete("all")
            self._draw(self.originals, [])
            self.status_var.set("No shapes produced (try different params)")
            return

        self._draw(self.originals, shapes)
        if mode == "Bands":
            self.status_var.set(
                f"Bands preview: {len(shapes)} ring(s) from {len(self.closed_sources)} source(s)"
            )
        else:
            self.status_var.set(f"Crumple preview: {len(shapes)} shape(s)")

    def _on_resize(self, event):
        if event.widget is self.root:
            self.schedule_preview()

    def _draw(self, originals, generated):
        self.canvas.delete("all")
        self.canvas.update_idletasks()
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        if cw < 4 or ch < 4:
            return

        all_pts = []
        for pts, _ in originals:
            all_pts.extend(pts)
        for pts, _ in generated:
            all_pts.extend(pts)
        if not all_pts:
            return
        xs = [p[0] for p in all_pts]
        ys = [p[1] for p in all_pts]
        minx, maxx = min(xs), max(xs)
        miny, maxy = min(ys), max(ys)
        w = max(maxx - minx, 1e-6)
        h = max(maxy - miny, 1e-6)

        pad = 0.05
        scale = min(cw * (1 - 2 * pad) / w, ch * (1 - 2 * pad) / h)
        ox = (cw - w * scale) / 2 - minx * scale
        oy = (ch - h * scale) / 2 - miny * scale

        def transform(pts):
            return [(p[0] * scale + ox, p[1] * scale + oy) for p in pts]

        for pts, closed in originals:
            tp = transform(pts)
            if closed:
                tp = tp + [tp[0]]
            flat = [c for p in tp for c in p]
            if len(flat) >= 4:
                self.canvas.create_line(*flat, fill="#cccccc", width=1)

        for pts, closed in generated:
            tp = transform(pts)
            if closed:
                tp = tp + [tp[0]]
            flat = [c for p in tp for c in p]
            if len(flat) >= 4:
                self.canvas.create_line(*flat, fill="black", width=1)

    # ---------- actions ----------

    def on_reseed(self):
        self.seed_var.set(str(random.randint(0, 999999)))
        self.schedule_preview()

    def _output_dir_for_mode(self):
        stem = self.current_stem
        mode = self.mode_var.get()
        if mode == "Bands":
            return OUT_DIR / f"{stem}{BANDS_OUTPUT_SUFFIX}"
        return OUT_DIR / stem

    def on_save_disk(self):
        if not self.paths or not self.current_stem:
            self.status_var.set("Nothing to save - load a file first")
            return

        mode = self.mode_var.get()
        try:
            cfg = self._build_config_from_ui()
            if mode == "Bands":
                if not self.closed_sources:
                    self.status_var.set("No closed shapes - Bands mode needs closed paths")
                    return
                shapes = self._generate_bands_shapes(cfg)
            else:
                shapes = self._generate_crumple_shapes(cfg)
        except Exception as e:
            self.status_var.set(f"Save failed: {e}")
            return

        if not shapes:
            self.status_var.set("No shapes to save")
            return

        stem = self.current_stem
        file_dir = self._output_dir_for_mode()
        file_dir.mkdir(parents=True, exist_ok=True)

        n = 0
        existing = {p.stem for p in file_dir.glob(f"{stem}_*.svg")}
        while f"{stem}_{n}" in existing:
            n += 1

        out_path = file_dir / f"{stem}_{n}.svg"
        write_svg(shapes, out_path, cfg["output"]["precision"], cfg["output"]["format"])
        self.status_var.set(f"Saved [{mode}]: {out_path.relative_to(ROOT)}  ({len(shapes)} shapes)")

    def on_save_config(self):
        if not messagebox.askyesno("Overwrite config?",
                                   f"Overwrite {CONFIG_PATH.name} with current settings?"):
            return
        try:
            seed = int(self.seed_var.get())
        except ValueError:
            seed = 42

        crumple = {}
        for name, _, _, _, is_int in CRUMPLE_SPECS:
            v = self._crumple_vars[name].get()
            v = int(round(v)) if is_int else float(v)
            crumple[name] = {"value": v, "spectrum": None}
        crumple["seed"] = seed

        out = {
            "crumple": crumple,
            "paths": self.config.get("paths", {
                "closed_shape_mode": "preserve_closure",
                "open_path_mode": "preserve_endpoints",
            }),
            "bands": {
                "enabled": self.mode_var.get() == "Bands",
                "count": int(self.bands_count.get()),
                "spacing": float(self.bands_spacing.get()),
                "direction": self.bands_direction.get(),
                "crumple_variance": self.config["bands"].get("crumple_variance", 0.3),
            },
            "output": {
                "format": self.fmt_var.get(),
                "precision": int(self.precision_var.get()),
            },
        }
        try:
            with open(CONFIG_PATH, "w") as f:
                json.dump(out, f, indent=2)
            self.config = out
            self.status_var.set(f"Saved config to {CONFIG_PATH.name}")
        except Exception as e:
            self.status_var.set(f"Config save failed: {e}")

    def on_reload_config(self):
        try:
            self.config = load_config(CONFIG_PATH)
        except Exception as e:
            self.status_var.set(f"Reload failed: {e}")
            return

        for name, _, _, _, is_int in CRUMPLE_SPECS:
            v = self._config_val(name)
            var = self._crumple_vars[name]
            var.set(int(round(v)) if is_int else float(v))
            lbl, ii = self._crumple_labels[name]
            lbl.config(text=self._fmt_val(v, ii))

        self.seed_var.set(str(self.config["crumple"]["seed"]))
        self.bands_count.set(self.config["bands"]["count"])
        self.count_label.config(text=str(self.bands_count.get()))
        self.bands_spacing.set(self.config["bands"]["spacing"])
        self.spacing_label.config(text=f"{self.bands_spacing.get():.1f}")
        self.bands_direction.set(self.config["bands"]["direction"])
        self.fmt_var.set(self.config["output"]["format"])
        self.precision_var.set(self.config["output"]["precision"])
        self.prec_label.config(text=str(self.precision_var.get()))

        self.status_var.set("Config reloaded")
        self.schedule_preview()


def main():
    root = tk.Tk()
    try:
        style = ttk.Style()
        for theme in ("clam", "alt", "default"):
            if theme in style.theme_names():
                style.theme_use(theme)
                break
    except Exception:
        pass
    TerrainLineStudio(root)
    root.mainloop()


if __name__ == "__main__":
    main()