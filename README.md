# TerrainLine

SVG Crumpling Program

Turn boring SVG shapes into hand-drawn, terrain-style line art. Good for fantasy maps, generative art, zines, or anything that should look a little less perfect.

## What is this?

You give it clean SVG shapes. It gives them back with crumpled, organic edges, like contour lines drawn by hand instead of by a computer. It can also draw concentric "bands" around your shapes for a topographic map look.

There are two ways to use it:

* **The script** (`terrainline.py`). Drop files in, get files out. Good for batch work.
* **The Studio** (`terrainline_studio.py`). A GUI with sliders and live preview. Good for playing around.

## Getting started

Clone it and install what it needs:

```bash
git clone https://github.com/yourname/terrainline.git
cd terrainline
pip install numpy opensimplex svgpathtools shapely
```

Tkinter ships with Python on most systems. If the GUI complains, install `python3-tk` for your OS.

## Using it

### The easy way (GUI)

```bash
python terrainline_studio.py
```

Drop some SVGs in the `input/` folder, pick one from the dropdown, and start dragging sliders. The preview updates as you go. When something looks good, hit **Save to Disk**.

### The batch way (script)

```bash
# put SVGs in input/, then:
python terrainline.py
```

Files land in `output/<your filename>/`. Each run gets its own numbered folder, so nothing ever gets overwritten.

## Where things go

```
terrainline/
├── input/           (drop SVGs here)
├── output/          (results show up here)
├── config.json      (default settings)
├── terrainline.py
├── terrainline_studio.py
└── terrainlineEngine.py
```

## The settings

What each knob does:

* **Amplitude.** How wobbly things get. Small is subtle, big is chaotic. 0.01 to 0.08 is a good range to start with.
* **Frequency.** How tight the wobble is. Low values give long, gentle waves. High values give jittery, crinkly edges.
* **Octaves.** How many layers of noise are stacked. More octaves means more fine detail. 3 to 5 is a sweet spot.
* **Persistence.** How much each extra octave contributes. Higher is rougher, lower is cleaner.
* **Subdivision.** How many extra points get inserted along your paths before crumpling. More points means smoother curves but slower processing.
* **Bias.** Pushes the wobble inward or outward. Useful when you want shapes to feel like they're bulging or pinching.
* **Smoothness.** Smoothing passes applied after the crumple. Bump it up to soften jagged bits.
* **Seed.** The random number that drives everything. Same seed means same result. Hit Reseed in the GUI to roll a new one.

## Bands mode

If you want the contour map look (rings around your shapes), switch the Studio dropdown to **Bands**, or set `bands.enabled` to `true` in `config.json`.

Settings:

* **Count.** How many rings to draw on each side.
* **Spacing.** Distance between rings, in SVG units.
* **Direction.** `outward`, `inward`, or `both`.

Bands only work on closed shapes. Open paths get skipped.

## Output options

In `config.json` under `output`:

* **Format.** `polyline` for straight segments, `bezier` for smooth curves, or `both` to write one of each.
* **Precision.** Decimal places in the saved SVG. 2 is usually fine.

## Spectrums (batch only)

Any numeric crumple setting can be turned into a spectrum, which makes the batch script sweep across a range and save a version for every combination. Useful when you want to compare a bunch of variations at once.

Example:

```json
"amplitude": {
  "value": 0.03,
  "spectrum": { "min": 0.01, "max": 0.08, "steps": 4 }
}
```

Each run folder gets a `variations/` subfolder with every combo, plus a `manifest.json` listing what's what.

## Tips

* For coastlines and landmasses, try low amplitude, high subdivision, 3 to 5 octaves, and 1 or 2 smoothness passes.
* For crinkled paper or crack patterns, try high frequency, low persistence, and zero smoothness.
* For elevation map vibes, use Bands mode with inward direction and 5 to 10 rings.
* Lock the seed when you want repeatable output. Reseed freely when you're still exploring.

## License

MIT.