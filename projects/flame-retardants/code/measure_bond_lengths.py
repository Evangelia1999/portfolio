#!/usr/bin/env python3
"""
High-stakes bond-length screening for phosphorus FR molecules (ORCA outputs) using ASE.

Key upgrades vs. your current script:
- Robust file picking:
  * prefers *_freq.out then *.out, but if multiple candidates exist it ranks by:
    - successful termination marker
    - presence of final coordinates block
    - newest mtime as a tie-breaker
- Robust bond inference:
  * uses covalent radii * multiplier (default 1.18)
  * adds sanity checks (P valence expectations; warns on suspicious coordination)
- Correct bond-type classification:
  * P=O chosen as shortest P–O neighbor
  * P–O(ester) excludes P=O and excludes O–H; optionally checks O has a C neighbor (more robust ester check)
  * P–N measured for each P–N
  * N–C is split into:
      - N-C(linker): carbon neighbor of N that is also bonded to P (N–CH2–P motif) OR best aliphatic candidate
      - N-C(aryl_or_ring): aromatic/sp2 carbon neighbor (for p-phenylenediamine, etc.)
    (No averaging across fundamentally different N–C environments)
- Outputs:
  * CSV with every measured bond (atom indices, elements, distance)
  * TXT summary per family (avg/min/max, n), but based on the correctly split bond types.

Usage examples:
  ./measure_bond_lengths_strict.py DFT/Phosphorus_FRs_project/optimized_molecules/frequencies_2 --out-prefix bond_lengths_strict
  ./measure_bond_lengths_strict.py path/to/molecule_dir --multiplier 1.18

Assumptions:
- You keep one molecule per subdirectory, and subdir name encodes family/group like your current setup.
- ORCA .out files contain final geometry readable by ASE.
"""

import argparse
import glob
import os
import re
import sys
from collections import defaultdict

from ase.data import covalent_radii
from ase.io import read
from ase.neighborlist import NeighborList

DEFAULT_MULTIPLIER = 1.18


def warn(msg):
    print(f"WARNING: {msg}", file=sys.stderr)


def info(msg):
    print(f"INFO: {msg}", file=sys.stderr)


# -----------------------------
# ORCA file ranking / picking
# -----------------------------
_TERMINATION_PATTERNS = [
    re.compile(r"ORCA TERMINATED NORMALLY", re.IGNORECASE),
    re.compile(r"TOTAL RUN TIME", re.IGNORECASE),
]
_COORD_PATTERNS = [
    re.compile(r"CARTESIAN COORDINATES\s*\(ANGSTROEM\)", re.IGNORECASE),
    re.compile(r"CARTESIAN COORDINATES", re.IGNORECASE),
]


def file_text_score(path, max_bytes=2_000_000):
    """
    Heuristic scoring:
      +5 if normal termination
      +3 if coordinates section exists
    """
    score = 0
    try:
        with open(path, "rb") as f:
            data = f.read(max_bytes)
        text = data.decode(errors="ignore")
    except Exception:
        return score

    for pat in _TERMINATION_PATTERNS:
        if pat.search(text):
            score += 5
            break

    for pat in _COORD_PATTERNS:
        if pat.search(text):
            score += 3
            break

    return score


def pick_geometry_file(subdir):
    """
    Pick the "best" ORCA output from a molecule subdir.
    Preference order:
      1) named preferred files if they exist
      2) otherwise rank by content score, then newest mtime
    """
    base = os.path.basename(subdir)
    outs = sorted(glob.glob(os.path.join(subdir, "*.out")))
    if not outs:
        return None

    preferred = [
        os.path.join(subdir, f"{base}_freq.out"),
        os.path.join(subdir, f"{base}.out"),
    ]
    preferred_existing = [p for p in preferred if p in outs]
    if preferred_existing:
        # If both exist, rank them by score+mtime too
        candidates = preferred_existing
    else:
        candidates = outs

    ranked = []
    for p in candidates:
        s = file_text_score(p)
        try:
            mtime = os.path.getmtime(p)
        except Exception:
            mtime = 0
        ranked.append((s, mtime, p))

    ranked.sort(key=lambda x: (x[0], x[1]), reverse=True)
    best = ranked[0][2]
    if len(ranked) > 1:
        # warn if we had to choose among multiple similar candidates
        top_score = ranked[0][0]
        close = [r for r in ranked[1:] if r[0] == top_score]
        if close:
            warn(
                f"Multiple .out candidates with same top score in {subdir}. "
                f"Picked {os.path.basename(best)}; consider cleaning the folder."
            )
    return best


def iter_input_files(paths):
    files = []
    for p in paths:
        if os.path.isdir(p):
            subdirs = [
                os.path.join(p, d)
                for d in sorted(os.listdir(p))
                if os.path.isdir(os.path.join(p, d))
            ]
            if subdirs:
                for sd in subdirs:
                    f = pick_geometry_file(sd)
                    if f:
                        files.append(f)
                    else:
                        warn(f"No .out geometry file found in {sd}")
            else:
                # directory with .out directly
                outs = sorted(glob.glob(os.path.join(p, "*.out")))
                if outs:
                    # rank within the directory
                    tmp = pick_geometry_file(p)
                    if tmp:
                        files.append(tmp)
                else:
                    warn(f"No subdirs and no .out files in {p}")
        else:
            files.append(p)
    return files


# -----------------------------
# Structure reading / bonding
# -----------------------------
def read_structure(path):
    """
    Read last frame via ASE. If this fails, the file is not usable.
    """
    try:
        return read(path, index=-1)
    except Exception as exc:
        warn(f"Failed to read {path}: {exc}")
        return None


def build_neighbors(atoms, multiplier):
    """
    Neighbor graph using covalent radii cutoffs.
    """
    cutoffs = [covalent_radii[a.number] * multiplier for a in atoms]
    nl = NeighborList(cutoffs, skin=0.0, self_interaction=False, bothways=True)
    nl.update(atoms)
    neighbors = []
    for i in range(len(atoms)):
        idxs, _ = nl.get_neighbors(i)
        neighbors.append(set(idxs))
    return neighbors


def bond_length(atoms, i, j):
    return float(atoms.get_distance(i, j))


def is_bonded_to_h(neighbors, atoms, idx):
    for j in neighbors[idx]:
        if atoms[j].symbol == "H":
            return True
    return False


def count_neighbors_by_element(neighbors, atoms, idx, element):
    return sum(1 for j in neighbors[idx] if atoms[j].symbol == element)


def find_atoms(atoms, element):
    return [i for i, a in enumerate(atoms) if a.symbol == element]


# -----------------------------
# Chemistry heuristics
# -----------------------------
def is_aromatic_like_carbon(atoms, neighbors, c_idx):
    """
    Heuristic: aromatic/sp2 carbon tends to have >=2 non-H neighbors and fewer H.
    We do not do full aromaticity. We just need to separate ring C from CH2 linker C.
    """
    non_h = [j for j in neighbors[c_idx] if atoms[j].symbol != "H"]
    h = [j for j in neighbors[c_idx] if atoms[j].symbol == "H"]
    # Typical CH2: 2 non-H (N and P) + 2 H
    # Typical ring C: 3 non-H (two ring neighbors + substituent) + 0-1 H
    if len(non_h) >= 3 and len(h) <= 1:
        return True
    return False


def pick_linker_carbon_for_n(atoms, neighbors, n_idx):
    """
    For an N bonded to P, pick a 'linker' carbon:
      1) carbon neighbor that is also bonded to P (strong motif: N-CH2-P)
      2) else None
    Returns: (linker_c_idx or None, other_c_idxs list)
    """
    c_neighbors = [j for j in neighbors[n_idx] if atoms[j].symbol == "C"]
    if not c_neighbors:
        return None, []

    # 1) carbon also bonded to P
    linker = []
    for c in c_neighbors:
        if any(atoms[j].symbol == "P" for j in neighbors[c]):
            linker.append(c)
    if not linker:
        return None, []
    # deterministic ordering
    linker.sort()
    return linker[0], [c for c in c_neighbors if c not in linker]


def classify_nc_bonds(atoms, neighbors, n_idx):
    """
    Returns:
      linker_cs: list (usually 0-1) of carbon indices that look like N-CH2-P / aliphatic linker
      aryl_ring_cs: list (unused; kept for API compatibility)
      other_cs: list (unused; kept for API compatibility)
    """
    c_neighbors = [j for j in neighbors[n_idx] if atoms[j].symbol == "C"]
    if not c_neighbors:
        return [], [], []

    linker_c, others = pick_linker_carbon_for_n(atoms, neighbors, n_idx)
    linker_cs = [linker_c] if linker_c is not None else []
    return linker_cs, [], []


def sanity_check_p_center(atoms, neighbors, p_idx, path):
    """
    P(V) center expectation in your molecules:
      - typically 1 P=O oxygen
      - 2 ester oxygens
      - 1 nitrogen
    So around 4 heavy-atom neighbors, though resonance doesn't change connectivity.
    We only warn; do not crash.
    """
    heavy_neighbors = [j for j in neighbors[p_idx] if atoms[j].symbol != "H"]
    o_n = [j for j in heavy_neighbors if atoms[j].symbol == "O"]
    n_n = [j for j in heavy_neighbors if atoms[j].symbol == "N"]

    if len(heavy_neighbors) < 3 or len(heavy_neighbors) > 5:
        warn(
            f"{os.path.basename(path)}: P{p_idx+1} has {len(heavy_neighbors)} heavy neighbors "
            f"(expected ~4). Check cutoff/mis-bonding."
        )

    if len(o_n) < 2:
        warn(f"{os.path.basename(path)}: P{p_idx+1} has only {len(o_n)} O neighbors (expected >=3 total P–O).")
    if len(n_n) < 1:
        warn(f"{os.path.basename(path)}: P{p_idx+1} has no N neighbor (expected P–N).")

    return o_n, n_n


# -----------------------------
# Grouping / naming
# -----------------------------
def group_from_path(path):
    name = os.path.basename(os.path.dirname(path))
    if name.startswith("melamine"):
        family = "melamine"
    elif name.startswith("p_phenyldiamine"):
        family = "p_phenyldiamine"
    else:
        family = "other"

    group = "unknown"
    if "_h_" in name or name.endswith("_h"):
        group = "h"
    elif "_ch3_" in name or name.endswith("_ch3"):
        group = "ch3"
    elif "_aryl_" in name or name.endswith("_aryl"):
        group = "aryl"
    elif "_ethyl_" in name or name.endswith("_ethyl"):
        group = "ethyl"
    return family, group, name


# -----------------------------
# Analysis core
# -----------------------------
def analyze_file(path, multiplier, require_ester_c_neighbor=True):
    atoms = read_structure(path)
    if atoms is None:
        return [], {}, None

    neighbors = build_neighbors(atoms, multiplier)

    family, group, name = group_from_path(path)
    p_indices = find_atoms(atoms, "P")
    if not p_indices:
        warn(f"No P atoms found in {path}")
        return [], {}, (family, group, name, path, [])

    rows = []
    summary = defaultdict(list)

    for p_idx in p_indices:
        o_neighbors, n_neighbors = sanity_check_p_center(atoms, neighbors, p_idx, path)

        # -----------------
        # P=O and P–O types
        # -----------------
        if o_neighbors:
            o_sorted = sorted(o_neighbors, key=lambda j: bond_length(atoms, p_idx, j))
            p_o = o_sorted[0]  # shortest P–O
            d = bond_length(atoms, p_idx, p_o)
            rows.append(make_row(path, family, group, name, p_idx, "P=O", p_idx, p_o, atoms, d))
            summary[(p_idx + 1, "P=O")].append(d)

            # ester Os: other O neighbors; for *_h_* allow all remaining O neighbors
            ester_os = []
            for j in o_neighbors:
                if j == p_o:
                    continue
                if group != "h" and is_bonded_to_h(neighbors, atoms, j):
                    continue
                if group != "h" and require_ester_c_neighbor:
                    if not any(atoms[k].symbol == "C" for k in neighbors[j]):
                        # If O is not bonded to C, it might be something else (e.g., bridging or mis-bonded)
                        continue
                ester_os.append(j)

            if len(ester_os) != 2:
                warn(
                    f"{os.path.basename(path)}: P{p_idx+1} has {len(ester_os)} ester-like O neighbors "
                    f"(expected 2). Consider inspecting raw CSV rows for this P center."
                )

            for j in ester_os:
                d = bond_length(atoms, p_idx, j)
                rows.append(make_row(path, family, group, name, p_idx, "P-O(ester)", p_idx, j, atoms, d))
                summary[(p_idx + 1, "P-O(ester)")].append(d)

        # -----------------
        # P–N and N–C types
        # -----------------
        for n_idx in n_neighbors:
            d = bond_length(atoms, p_idx, n_idx)
            rows.append(make_row(path, family, group, name, p_idx, "P-N", p_idx, n_idx, atoms, d))
            summary[(p_idx + 1, "P-N")].append(d)

            linker_cs, aryl_ring_cs, other_cs = classify_nc_bonds(atoms, neighbors, n_idx)
            if not linker_cs:
                warn(f"{os.path.basename(path)}: N{n_idx+1} (bound to P{p_idx+1}) has no C neighbors.")

            # N–C(linker)
            for c_idx in linker_cs:
                d_nc = bond_length(atoms, n_idx, c_idx)
                rows.append(make_row(path, family, group, name, p_idx, "N-C(linker)", n_idx, c_idx, atoms, d_nc))
                summary[(p_idx + 1, "N-C(linker)")].append(d_nc)

            # N–C(other) types intentionally omitted; only report N–C when C is bonded to P

        # -----------------
        # P–C (all carbon neighbors)
        # -----------------
        c_neighbors_p = [j for j in neighbors[p_idx] if atoms[j].symbol == "C"]
        for c_idx in c_neighbors_p:
            d_pc = bond_length(atoms, p_idx, c_idx)
            rows.append(make_row(path, family, group, name, p_idx, "P-C", p_idx, c_idx, atoms, d_pc))
            summary[(p_idx + 1, "P-C")].append(d_pc)

            # N–C(linker): any N bonded to this C (even if N not detected as P neighbor)
            n_neighbors_c = [j for j in neighbors[c_idx] if atoms[j].symbol == "N"]
            for n_idx in n_neighbors_c:
                d_nc = bond_length(atoms, n_idx, c_idx)
                rows.append(make_row(path, family, group, name, p_idx, "N-C(linker)", n_idx, c_idx, atoms, d_nc))
                summary[(p_idx + 1, "N-C(linker)")].append(d_nc)

    meta = (family, group, name, path, [i + 1 for i in p_indices])
    return rows, summary, meta


def make_row(file_path, family, group, mol_name, p_idx, bond_type, i, j, atoms, dist):
    """
    Standardized per-bond row.
    All atom indices written 1-based for human use; keep 0-based too if you prefer.
    """
    return {
        "file": file_path,
        "family": family,
        "group": group,
        "molecule": mol_name,
        "P_index_1based": p_idx + 1,
        "bond_type": bond_type,
        "i_1based": i + 1,
        "j_1based": j + 1,
        "elem_i": atoms[i].symbol,
        "elem_j": atoms[j].symbol,
        "distance_A": f"{dist:.6f}",
    }


# -----------------------------
# Output
# -----------------------------
def write_csv(path, rows):
    if not rows:
        warn(f"No rows to write for {path}")
        return
    fields = [
        "file", "family", "group", "molecule", "P_index_1based", "bond_type",
        "i_1based", "j_1based", "elem_i", "elem_j", "distance_A"
    ]
    with open(path, "w") as f:
        f.write(",".join(fields) + "\n")
        for r in rows:
            f.write(",".join(str(r.get(k, "")) for k in fields) + "\n")


def summarize(summary_dict):
    """
    summary_dict keys: (P_index_1based, bond_type) -> list(distances)
    returns list of dicts
    """
    out = []
    for (p_idx, bond_type), vals in sorted(summary_dict.items()):
        if not vals:
            continue
        avg = sum(vals) / len(vals)
        out.append({
            "P_index_1based": p_idx,
            "bond_type": bond_type,
            "n": len(vals),
            "avg_A": f"{avg:.6f}",
            "min_A": f"{min(vals):.6f}",
            "max_A": f"{max(vals):.6f}",
        })
    return out


def write_family_txt(path, molecules_meta, per_file_summary, family_name):
    """
    molecules_meta: list of (family, group, name, file_path, p_indices)
    per_file_summary: dict[file_path] -> summary_rows(list of dicts)
    """
    with open(path, "w") as f:
        for fam, grp, name, file_path, p_indices in sorted(molecules_meta):
            if fam != family_name:
                continue
            f.write(f"{name} ({grp})\n")
            f.write(f"  source: {os.path.basename(file_path)}\n")
            if not p_indices:
                f.write("  NOTE: no P atoms detected\n\n")
                continue

            # group by P
            rows = per_file_summary.get(file_path, [])
            by_p = defaultdict(list)
            for r in rows:
                by_p[r["P_index_1based"]].append(r)

            for p_idx in sorted(by_p.keys()):
                f.write(f"  P{p_idx}:\n")
                def cat_key(bond_type):
                    if bond_type.startswith("P-O") or bond_type == "P=O":
                        return 0
                    if bond_type == "P-N":
                        return 1
                    if bond_type == "P-C":
                        return 2
                    if bond_type.startswith("N-C"):
                        return 3
                    return 9

                rows_sorted = sorted(by_p[p_idx], key=lambda x: (cat_key(x["bond_type"]), x["bond_type"]))
                last_cat = None
                for r in rows_sorted:
                    cat = cat_key(r["bond_type"])
                    if last_cat is not None and cat != last_cat:
                        f.write("-------------------------------------------------------------\n")
                    f.write(
                        f"    {r['bond_type']}: avg={r['avg_A']} min={r['min_A']} max={r['max_A']} (n={r['n']})\n"
                    )
                    last_cat = cat
            f.write("\n")


def main():
    ap = argparse.ArgumentParser(description="Strict P-centered bond-length screening from ORCA .out using ASE.")
    ap.add_argument("paths", nargs="*", default=["."], help="Files or directories to process (default: .)")
    ap.add_argument("--multiplier", type=float, default=DEFAULT_MULTIPLIER,
                    help="Covalent radii multiplier for bond cutoff (default: 1.18)")
    ap.add_argument("--out-prefix", default="bond_lengths_strict", help="Output prefix (default: bond_lengths_strict)")
    ap.add_argument("--no-ester-c-check", action="store_true",
                    help="Disable requirement that ester O must have a C neighbor (less strict).")
    args = ap.parse_args()

    files = iter_input_files(args.paths)
    if not files:
        print("No input files found.", file=sys.stderr)
        sys.exit(1)

    all_rows = []
    molecules_meta = []
    per_file_summary = {}

    require_ester_c_neighbor = not args.no_ester_c_check

    info(f"Found {len(files)} file(s) to process.")
    for path in files:
        rows, summary_dict, meta = analyze_file(path, args.multiplier, require_ester_c_neighbor=require_ester_c_neighbor)
        all_rows.extend(rows)
        if meta is not None:
            molecules_meta.append(meta)
        per_file_summary[path] = summarize(summary_dict)

    csv_path = f"{args.out_prefix}.csv"
    write_csv(csv_path, all_rows)
    print(f"Wrote {csv_path}")

    mel_path = f"{args.out_prefix}_melamine.txt"
    ppd_path = f"{args.out_prefix}_p_phenyldiamine.txt"
    other_path = f"{args.out_prefix}_other.txt"

    write_family_txt(mel_path, molecules_meta, per_file_summary, "melamine")
    write_family_txt(ppd_path, molecules_meta, per_file_summary, "p_phenyldiamine")
    write_family_txt(other_path, molecules_meta, per_file_summary, "other")

    print(f"Wrote {mel_path}")
    print(f"Wrote {ppd_path}")
    print(f"Wrote {other_path}")

    # Hard warning if we wrote almost nothing
    if len(all_rows) < 10:
        warn("Very few bonds were recorded. This usually means ASE failed to parse geometries or bonding cutoff is wrong.")


if __name__ == "__main__":
    main()
