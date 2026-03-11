#!/bin/bash
#SBATCH --job-name=melamine_1
#SBATCH --partition=short
#SBATCH --nodes=1
#SBATCH --ntasks=16
#SBATCH --cpus-per-task=1
#SBATCH --chdir=/home/ubuntu/Computational-Chemistry/DFT/Phosphorus_FRs_project/melamine_1
#SBATCH --output=slurm-%j.out
#SBATCH --error=slurm-%j.err

set -euo pipefail

# Avoid oversubscription from threaded libs (ORCA is using MPI ranks here)
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1

# Sanity check (optional)
echo "NTASKS=$SLURM_NTASKS  CPUS_PER_TASK=$SLURM_CPUS_PER_TASK"

# Run
/opt/orca/orca melamine_1.inp > melamine_1.out
