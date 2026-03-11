#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# GaussView-style Raman/IR spectrum from ORCA output (blue sticks + black curve)

"""
Plot ORCA IR/Raman spectrum with blue sticks + black broadened curve.

Usage:
  python plot_orca_freq_with_blue_lines.py <orca_output.out> [--kind raman|ir] [--fwhm 15]
                           [--xmin 0] [--xmax 4000] [--points 20000]
                           [--broaden gaussian|lorentzian]
                           [--output spectrum.png] [--show]

e.g. python plot_orca_freq_with_blue_lines.py iPP_1mer.out --kind raman --fwhm 15 --output spectrum.png

Notes:
- For Raman, it reads the "RAMAN SPECTRUM" table (Activity column).
- For IR, it reads the "IR SPECTRUM" table (Int column, km/mol).
- Units: x-axis in cm^-1. y-axis in Activity (Å^4) for Raman, Int (km/mol) for IR.
"""

import argparse, re, sys
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path

def parse_orca_table(text, kind="raman"):
    if kind == "raman":
        m = re.search(r'\n-+\s*RAMAN SPECTRUM\s*-+\n(.*?)(?:\n-+\s*[A-Z ]+\s*-+|\Z)', text, flags=re.S|re.I)
        line = re.compile(r'^\s*\d+:\s*([\d\.]+)\s+([0-9eE\.\-\+]+)\s+[0-9eE\.\-\+]+', re.M)
    elif kind == "ir":
        m = re.search(r'\n-+\s*IR SPECTRUM\s*-+\n(.*?)(?:\n-+\s*[A-Z ]+\s*-+|\Z)', text, flags=re.S|re.I)
        line = re.compile(r'^\s*\d+:\s*([\d\.]+)\s+[0-9eE\.\-\+]+\s+([0-9eE\.\-\+]+)', re.M)
    else:
        return np.array([]), np.array([])

    if not m:
        return np.array([]), np.array([])
    f, v = [], []
    for a, b in line.findall(m.group(1)):
        f.append(float(a)); v.append(float(b))
    return np.array(f), np.array(v)

def broaden(x, centers, heights, fwhm=15.0):
    if len(centers) == 0:
        return np.zeros_like(x)
    sigma = fwhm / (2.0 * np.sqrt(2.0 * np.log(2.0)))
    y = np.zeros_like(x)
    for c, h in zip(centers, heights):
        y += h * np.exp(-0.5 * ((x - c) / sigma) ** 2)
    return y

def plot_gaussview_style(freqs, vals, kind="raman", xmin=None, xmax=None, fwhm=15, out_png=None, points=20000, shape="gaussian"):
    # X range
    if xmin is None: xmin = max(0, freqs.min() - 50)
    if xmax is None: xmax = freqs.max() + 50
    x = np.linspace(xmin, xmax, points)

    # Broadening
    if shape == "gaussian":
        y = broaden(x, freqs, vals, fwhm)
    else:
        gamma = fwhm/2.0
        y = np.zeros_like(x)
        for c, h in zip(freqs, vals):
            y += h * (gamma**2) / ((x - c)**2 + gamma**2)

    # Normalize broadened curve to max stick height
    if y.max() > 0 and vals.max() > 0:
        y *= (vals.max() / y.max())

    # Figure style
    fig, ax = plt.subplots(figsize=(9.5, 4.2), dpi=150)
    fig.patch.set_facecolor('#ece9da')     # beige outer panel
    ax.set_facecolor('#efefef')            # grey plot area
    for s in ax.spines.values():
        s.set_linewidth(2.0); s.set_edgecolor('#404040')
    ax.grid(True, color='#c9c9c9', linestyle=':', linewidth=0.8)

    # Black broadened curve
    ax.plot(x, y, color='k', linewidth=1.6, zorder=2)

    # Blue sticks stop just under the curve at their x
    ytops = np.interp(freqs, x, y, left=0, right=0) * 0.96
    for f, t in zip(freqs, ytops):
        if xmin <= f <= xmax:
            ax.vlines(f, 0, t, color='#0b3d91', linewidth=1.0, zorder=1)

    # Titles/labels
    ax.set_title("Raman Scattering Spectrum" if kind == "raman" else "IR Spectrum",
                 fontsize=18, fontweight='bold', pad=10)
    ax.set_xlabel("Frequency (cm$^{-1}$)", fontsize=12)
    ax.set_ylabel("Intensity (a.u.)" if kind == "raman" else "IR Intensity (km/mol)", fontsize=12)
    ax.set_xlim(xmin, xmax)

    plt.tight_layout()
    if out_png is None:
        out_png = str(Path("spectrum") .with_suffix(f".{kind}.spectra.png"))
    plt.savefig(out_png, dpi=300, facecolor=fig.get_facecolor(), bbox_inches='tight')
    return out_png

def main():
    ap = argparse.ArgumentParser(description="GaussView-style Raman/IR plot from ORCA output")
    ap.add_argument("file", help="ORCA output file (.out/.log)")
    ap.add_argument("--kind", choices=["raman","ir"], default="raman", help="Spectrum kind")
    ap.add_argument("--fwhm", type=float, default=15.0, help="Broadening FWHM (cm^-1)")
    ap.add_argument("--xmin", type=float, default=0.0, help="x-axis min (cm^-1)")
    ap.add_argument("--xmax", type=float, default=None, help="x-axis max (cm^-1), default auto")
    ap.add_argument("--points", type=int, default=20000, help="Number of points for broadened curve")
    ap.add_argument("--broaden", choices=["gaussian","lorentzian"], default="gaussian", help="Line shape")
    ap.add_argument("--output", default=None, help="Output image file (e.g., spectrum.png)")
    ap.add_argument("--show", action="store_true", help="Show the plot window after saving")
    args = ap.parse_args()

    path = Path(args.file)
    if not path.exists():
        sys.exit(f"Error: file not found: {path}")

    text = path.read_text(errors="ignore")
    freqs, vals = parse_orca_table(text, args.kind)
    if len(freqs) == 0:
        sys.exit("No spectral lines found in ORCA output (check --kind and your calculation).")

    # Negative value sanity check (rare, but helps catch parse issues)
    if np.any(vals < 0):
        sys.exit(f"Error: Negative {args.kind.upper()} values detected (min = {vals.min():.3f}). Check your ORCA output.")

    # Auto xmax if not provided
    xmax = args.xmax if args.xmax is not None else max(4000.0, freqs.max() + 50.0)

    out = args.output or str(path.with_suffix(f".{args.kind}.spectra.png"))
    out_path = plot_gaussview_style(freqs, vals, kind=args.kind,
                                    xmin=args.xmin, xmax=xmax,
                                    fwhm=args.fwhm, out_png=out,
                                    points=args.points, shape=args.broaden)
    if args.show:
        plt.show()
    print(f"Saved: {out_path}")

if __name__ == "__main__":
    main()
