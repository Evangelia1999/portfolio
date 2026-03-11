Data extraction summary (GROMACS 2022.2, container: c83d877cce7c5defb1d4cf6071d0f0089282f327e2f55978c6641cb19aed1c3a)

Working directory inside container:
  /root/simulations/MD/GROMACS/pp_melt/05_prod
Host directory:
  /mnt/c/Users/evang/simulations/Computational-Chemistry/MD/GROMACS/pp_melt/05_prod

Files used:
  md.edr        (source for energy terms)
  energy.xvg    (extracted energy terms)
  box_xyz.xvg   (extracted box X/Y/Z)
  04_npt/npt.gro (start box for production)
  md.gro        (end box for production)

Extracted statistics (from energy.xvg, first vs last):
  Potential      756685.0625  ->  754036.0000  kJ/mol
  TotalEnergy   1256421.7500  -> 1251721.2500  kJ/mol
  ConservedEn   1256489.3750  -> 1734094.3750  kJ/mol
  Temperature       474.4836  ->     472.5359  K
  Pressure           72.2928  ->     -60.9485  bar
  Volume           1123.6460  ->    1134.9796  nm^3
  Density            749.5765 ->     742.0914  kg/m^3
  Enthalpy       1256489.3750 -> 1251789.6250  kJ/mol

Box size (from .gro last line):
  Start (04_npt/npt.gro): 10.39625 10.39625 10.39625  nm
  End   (05_prod/md.gro): 10.43108 10.43108 10.43108  nm
  Delta L per axis: +0.03483 nm
  Delta V: +11.33137 nm^3 (~+1.01%)

GROMACS commands used (inside Docker):
  # GROMACS version
  docker exec <container> /gromacs/bin.AVX2_256/gmx_mpi --version

  # Extract energy terms to energy.xvg
  docker exec -w /root/simulations/MD/GROMACS/pp_melt/05_prod <container> \
    bash -lc "printf '7\n9\n10\n11\n12\n17\n18\n20\n0\n' | /gromacs/bin.AVX2_256/gmx_mpi energy -f md.edr"

  # Extract density only
  docker exec -w /root/simulations/MD/GROMACS/pp_melt/05_prod <container> \
    bash -lc "printf '18\n0\n' | /gromacs/bin.AVX2_256/gmx_mpi energy -f md.edr"

  # Extract box X/Y/Z to box_xyz.xvg
  docker exec -w /root/simulations/MD/GROMACS/pp_melt/05_prod <container> \
    bash -lc "printf '14 15 16\n0\n' | /gromacs/bin.AVX2_256/gmx_mpi energy -f md.edr -o box_xyz.xvg"

Replace <container> with:
  c83d877cce7c5defb1d4cf6071d0f0089282f327e2f55978c6641cb19aed1c3a
