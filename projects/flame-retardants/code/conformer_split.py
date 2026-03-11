#!/usr/bin/env python3
"""
Split a multi-conformer XYZ (multiple XYZ blocks) into separate files.

Default behavior:
- Keep only conformers within 4.0 kcal/mol of the minimum.

Optional "cluster mode":
- Export ALL conformers found in the input file (ignore the 4.0 kcal/mol cutoff),
  while still computing and printing ΔE relative to the lowest-energy conformer.

Adds a metadata comment line to each output file with:
- conformer number (output index)
- energy in Hartree (Eh)
- ΔE from the lowest-energy conformer in kcal/mol and cal/mol

Output modes:
- XYZ files:        <out_dir>/<name>_1.xyz, <name>_2.xyz, ...
- ORCA input files: each conformer goes into its own folder:
      <out_dir>/<name>_1/<name>_1.inp
      <out_dir>/<name>_2/<name>_2.inp
  (writes user-provided ORCA header at top, then: * xyz <charge> <multiplicity> ... * )
"""

import os
import re
import sys
from dataclasses import dataclass
from typing import List, Optional, Tuple


THRESHOLD_KCAL = 4.0

# Energy conversions
HARTREE_TO_KCAL = 627.509474
EV_PER_HARTREE = 27.211386245988  # eV per Eh
KJ_PER_KCAL = 4.184
KJ_PER_HARTREE = HARTREE_TO_KCAL * KJ_PER_KCAL  # kJ/mol per Eh

_FLOAT_RE = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"


@dataclass
class Conf:
    natoms: int
    coords: List[str]
    src_comment: str
    energy_raw: float
    unit: str
    energy_kcal: float
    energy_hartree: float


def prompt_path(prompt: str) -> str:
    val = input(prompt).strip().strip('"').strip("'")
    val = os.path.expandvars(os.path.expanduser(val))
    return val


def unit_from_comment(comment: str) -> str:
    c = comment.lower()
    if "kcal" in c:
        return "kcal"
    if "kj" in c or "kj/mol" in c:
        return "kj"
    if "ev" in c:
        return "ev"
    if "eh" in c or "au" in c or "hartree" in c:
        return "hartree"
    return "hartree"  # default


def extract_energy(comment: str) -> Optional[float]:
    s = comment.strip()

    # If comment is exactly one number
    if re.fullmatch(rf"\s*{_FLOAT_RE}\s*", s):
        try:
            return float(s)
        except ValueError:
            return None

    # Explicit "energy" / "E="
    m = re.search(rf"(?i)\b(?:energy|e)\s*[:=]\s*({_FLOAT_RE})\b", s)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None

    # If exactly one float anywhere in line
    floats = re.findall(_FLOAT_RE, s)
    if len(floats) == 1:
        try:
            return float(floats[0])
        except ValueError:
            return None

    # If "energy" present, take last float
    if re.search(r"(?i)\benergy\b", s) and floats:
        try:
            return float(floats[-1])
        except ValueError:
            return None

    return None


def to_kcal(value: float, unit: str) -> float:
    if unit == "kcal":
        return value
    if unit == "kj":
        return value / KJ_PER_KCAL
    if unit == "ev":
        return (value / EV_PER_HARTREE) * HARTREE_TO_KCAL
    if unit == "hartree":
        return value * HARTREE_TO_KCAL
    return value * HARTREE_TO_KCAL


def to_hartree(value: float, unit: str) -> float:
    if unit == "hartree":
        return value
    if unit == "ev":
        return value / EV_PER_HARTREE
    if unit == "kcal":
        return value / HARTREE_TO_KCAL
    if unit == "kj":
        return value / KJ_PER_HARTREE
    return value


def parse_xyz_blocks(path: str) -> List[Tuple[int, str, List[str]]]:
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    blocks: List[Tuple[int, str, List[str]]] = []
    i = 0
    n = len(lines)

    def skip_empty(idx: int) -> int:
        while idx < n and lines[idx].strip() == "":
            idx += 1
        return idx

    while True:
        i = skip_empty(i)
        if i >= n:
            break

        natoms_line = lines[i].strip()
        if not re.fullmatch(r"\d+", natoms_line):
            raise ValueError(
                f"Expected atom count (integer) at line {i+1}, got: {natoms_line!r}"
            )
        natoms = int(natoms_line)

        if i + 1 >= n:
            raise ValueError(f"Missing comment line after atom count at line {i+1}.")

        comment = lines[i + 1].rstrip("\n")

        start = i + 2
        end = start + natoms
        if end > n:
            raise ValueError(
                f"Not enough coordinate lines for a block starting at line {i+1}. "
                f"Expected {natoms} coordinate lines, file ended early."
            )

        coords = [ln.rstrip("\n") for ln in lines[start:end]]
        blocks.append((natoms, comment, coords))
        i = end

    return blocks


def make_meta_comment(conf_idx: int, e_h: float, d_kcal: float) -> str:
    d_cal = d_kcal * 1000.0
    return (
        f"conformer={conf_idx} | E={e_h:.10f} Eh | dE={d_kcal:.6f} kcal/mol ({d_cal:.1f} cal/mol)"
    )


def write_xyz(path: str, natoms: int, comment: str, coords: List[str]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"{natoms}\n")
        f.write(f"{comment}\n")
        for ln in coords:
            f.write(f"{ln.rstrip()}\n")


def read_orca_header() -> str:
    tpl = prompt_path("ORCA header: enter template file path, or press Enter to paste: ")
    if tpl:
        if not os.path.isfile(tpl):
            print(f"ERROR: template file not found: {tpl}", file=sys.stderr)
            raise SystemExit(1)
        with open(tpl, "r", encoding="utf-8") as f:
            text = f.read()
        return text.rstrip() + "\n"

    print("Paste ORCA header now. End with a line containing only: END")
    lines: List[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip() == "END":
            break
        lines.append(line)
    text = "\n".join(lines).rstrip()
    if text:
        text += "\n"
    return text


def write_orca_inp(
    path: str,
    header: str,
    meta_comment: str,
    charge: int,
    multiplicity: int,
    coords: List[str],
) -> None:
    with open(path, "w", encoding="utf-8") as f:
        if header:
            f.write(header)
            if not header.endswith("\n"):
                f.write("\n")

        f.write(f"# {meta_comment}\n")
        f.write(f"* xyz {charge} {multiplicity}\n")
        for ln in coords:
            f.write(f"{ln.rstrip()}\n")
        f.write("*\n")


def prompt_int(prompt: str, default: Optional[int] = None) -> int:
    while True:
        s = input(prompt).strip()
        if s == "" and default is not None:
            return default
        try:
            return int(s)
        except ValueError:
            print("Please enter an integer.")


def prompt_yes_no(prompt: str, default_no: bool = True) -> bool:
    s = input(prompt).strip().lower()
    if not s:
        return False if default_no else True
    return s in ("y", "yes", "true", "1")


def main() -> int:
    in_path = prompt_path("Path to conformer XYZ file: ")
    if not os.path.isfile(in_path):
        print(f"ERROR: input file does not exist: {in_path}", file=sys.stderr)
        return 1

    out_dir = prompt_path("Output directory for split files: ")
    if not out_dir:
        print("ERROR: output directory is empty.", file=sys.stderr)
        return 1
    os.makedirs(out_dir, exist_ok=True)

    prefix = input("Output file prefix name (e.g., conf): ").strip()
    if not prefix:
        print("ERROR: prefix name is empty.", file=sys.stderr)
        return 1

    # NEW: cluster mode toggle -> export ALL conformers regardless of energy cutoff
    cluster_mode = prompt_yes_no(
        "Cluster mode (export ALL conformers, ignore 4.0 kcal/mol cutoff)? [y/N]: ",
        default_no=True,
    )

    orca_mode = prompt_yes_no("Morph output into ORCA input files (.inp)? [y/N]: ", default_no=True)

    orca_header = ""
    orca_charge = 0
    orca_mult = 1
    out_ext = "xyz"

    if orca_mode:
        orca_header = read_orca_header()
        orca_charge = prompt_int("Charge (default 0): ", default=0)
        orca_mult = prompt_int("Multiplicity (e.g., 1 singlet, 2 doublet, 3 triplet): ", default=None)
        out_ext = "inp"

    raw_blocks = parse_xyz_blocks(in_path)
    if not raw_blocks:
        print("ERROR: no XYZ blocks found in file.", file=sys.stderr)
        return 1

    confs: List[Conf] = []
    skipped = 0

    for (natoms, comment, coords) in raw_blocks:
        e = extract_energy(comment)
        if e is None:
            skipped += 1
            continue
        unit = unit_from_comment(comment)
        confs.append(
            Conf(
                natoms=natoms,
                coords=coords,
                src_comment=comment,
                energy_raw=e,
                unit=unit,
                energy_kcal=to_kcal(e, unit),
                energy_hartree=to_hartree(e, unit),
            )
        )

    if not confs:
        print(
            "ERROR: could not extract energies from any XYZ block comment line.\n"
            "Make sure each block's 2nd line contains an energy, e.g.:\n"
            "  -59.81696391\n"
            "or\n"
            "  energy: -59.81696391 Eh\n"
            "or\n"
            "  E= -59.81696391\n",
            file=sys.stderr,
        )
        return 1

    # Determine reference minimum energy
    e0_kcal = min(c.energy_kcal for c in confs)

    # Decide which conformers to export
    if cluster_mode:
        # keep all; preserve file order
        kept: List[Tuple[Conf, float]] = [(c, c.energy_kcal - e0_kcal) for c in confs]
    else:
        # filter by threshold; sort by energy
        confs_sorted = sorted(confs, key=lambda c: c.energy_kcal)
        kept = []
        for c in confs_sorted:
            d_kcal = c.energy_kcal - e0_kcal
            if d_kcal <= THRESHOLD_KCAL + 1e-12:
                kept.append((c, d_kcal))

    print("")
    print(f"Input blocks found: {len(raw_blocks)}")
    print(f"Blocks with energy parsed: {len(confs)}")
    if skipped:
        print(f"Blocks skipped (no energy parsed): {skipped}")

    if cluster_mode:
        print("Cluster mode: ON (no energy cutoff applied)")
        print(f"Exporting conformers: {len(kept)}")
    else:
        print(f"Kept within {THRESHOLD_KCAL:.2f} kcal/mol of minimum: {len(kept)}")

    print("")
    print("Idx  E (Eh)                 ΔE (kcal/mol)   ΔE (cal/mol)     Output file")
    print("---- ---------------------  ------------    ------------     ------------------------------")

    for idx, (c, d_kcal) in enumerate(kept, start=1):
        meta = make_meta_comment(idx, c.energy_hartree, d_kcal)

        if orca_mode:
            conf_dir = os.path.join(out_dir, f"{prefix}_{idx}")
            os.makedirs(conf_dir, exist_ok=True)
            out_path = os.path.join(conf_dir, f"{prefix}_{idx}.{out_ext}")

            write_orca_inp(
                out_path,
                header=orca_header,
                meta_comment=meta,
                charge=orca_charge,
                multiplicity=orca_mult,
                coords=c.coords,
            )
        else:
            out_path = os.path.join(out_dir, f"{prefix}_{idx}.{out_ext}")
            write_xyz(out_path, c.natoms, meta, c.coords)

        d_cal = d_kcal * 1000.0
        print(f"{idx:>3}  {c.energy_hartree:>21.10f}  {d_kcal:>12.6f}  {d_cal:>12.1f}     {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
