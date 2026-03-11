python - <<'PY'
import warnings; warnings.filterwarnings("ignore", module="ase.io.cif")
from ase.io import read, write
from ase import Atoms
import spglib

cif = "Al_cyclo6phosph.cif"
symprec = 1e-3; angtol = 0.5

a = read(cif)

# Check SG from CIF read
sg1 = spglib.get_spacegroup((a.cell, a.get_scaled_positions(), a.get_atomic_numbers()),
                            symprec=symprec, angle_tolerance=angtol)
print("SG from CIF read:", sg1)

# Standardize to conventional cell (unique setting)
lat,pos,nums = spglib.standardize_cell((a.cell, a.get_scaled_positions(), a.get_atomic_numbers()),
                                       to_primitive=False, no_idealize=False,
                                       symprec=symprec, angle_tolerance=angtol)
conv = Atoms(numbers=nums, cell=lat, scaled_positions=pos, pbc=True)
print("Conventional nat:", len(conv))

# Primitive cell
lat,pos,nums = spglib.find_primitive((conv.cell, conv.get_scaled_positions(), conv.numbers),
                                     symprec=symprec, angle_tolerance=angtol)
prim = Atoms(numbers=nums, cell=lat, scaled_positions=pos, pbc=True)
print("Primitive nat:", len(prim))

# Verify SG again
sg2 = spglib.get_spacegroup((prim.cell, prim.get_scaled_positions(), prim.numbers),
                            symprec=symprec, angle_tolerance=angtol)
print("SG after standardize+primitive:", sg2)

# Write QE input with only species and geometry
pseudos = {"Al":"Al.pbe-n-kjpaw_psl.1.0.0.UPF","P":"P.pbe-n-kjpaw_psl.1.0.0.UPF","O":"O.pbe-n-kjpaw_psl.1.0.0.UPF"}
write("Al_cyclo6phosph_prim.in", prim, format="espresso-in",
      pseudopotentials=pseudos, pseudo_dir="../01_pseudos_library")
PY
