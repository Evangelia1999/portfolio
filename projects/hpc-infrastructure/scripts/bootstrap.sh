#!/usr/bin/env bash
set -euo pipefail

# ========= Config =========
GRAFANA_ADMIN_USER="${GRAFANA_ADMIN_USER:-admin}"
GRAFANA_ADMIN_PASSWORD="${GRAFANA_ADMIN_PASSWORD:-admin}"
STACK_DIR="$HOME/Computational-Chemistry/infra/monitoring"
PROM_TARGETS="${PROM_TARGETS:-localhost:9100}"
FORCE="${FORCE:-0}"   # 1 = overwrite monitoring configs

log(){ printf '[*] %s\n' "$*"; }
need(){ command -v "$1" >/dev/null 2>&1; }
pkg_installed(){ dpkg -s "$1" >/dev/null 2>&1; }

# ===== Detect tarballs =====
find_tar() {
  local pattern="$1"
  find "$HOME" -maxdepth 1 -type f -name "$pattern" | head -n1 || true
}

ORCA_TARBALL="$(find_tar 'orca_*.tar.xz')"
QE_TARBALL="$(find_tar 'qe*.tar.gz')"

# ===== Helper =====
ensure_path_exports() {
  # persist for future shells
  sudo tee /etc/profile.d/orca_qe.sh >/dev/null <<'EOF'
[ -x /opt/orca/orca ] && export PATH=/opt/orca:$PATH
[ -d /opt/qe/bin ]    && export PATH=/opt/qe/bin:$PATH
[ -d /opt/orca ] && export LD_LIBRARY_PATH=/opt/orca:${LD_LIBRARY_PATH:-}
EOF
  sudo chmod 644 /etc/profile.d/orca_qe.sh

  # make binaries available NOW via system symlinks
  sudo install -d /usr/local/bin
  [ -x /opt/orca/orca ] && sudo ln -sf /opt/orca/orca /usr/local/bin/orca
  for b in pw.x ph.x cp.x; do
    [ -x "/opt/qe/bin/$b" ] && sudo ln -sf "/opt/qe/bin/$b" "/usr/local/bin/$b"
  done
}

# ========= Steps =========
step_01_prereqs() {
  log "Install Docker and node_exporter if missing"
  if ! need docker; then
    sudo apt-get update
    sudo apt-get install -y ca-certificates curl gnupg lsb-release
    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release; echo $VERSION_CODENAME) stable" \
      | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
    sudo apt-get update
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
    sudo usermod -aG docker "$USER" || true
  fi
  sudo systemctl enable --now docker

  if ! pkg_installed prometheus-node-exporter; then
    sudo apt-get install -y prometheus-node-exporter
  fi
  sudo systemctl enable --now prometheus-node-exporter
}

step_03_stack_up() {
  log "Starting Prometheus + Grafana..."
  (
    cd "$STACK_DIR"
    export GRAFANA_ADMIN_USER GRAFANA_ADMIN_PASSWORD
    if docker info >/dev/null 2>&1; then
      docker compose up -d
    elif sudo -n docker info >/dev/null 2>&1; then
      sudo docker compose up -d
    else
      # last fallback: run in docker group without relogin
      sudo usermod -aG docker "$USER" || true
      sg docker -c "docker compose up -d" || sudo docker compose up -d
    fi
  )
}

step_04_orca() {
  if [[ -z "$ORCA_TARBALL" ]]; then
    log "ORCA tarball not found — skipping ORCA installation."
    return
  fi
  local ORCA_VER="$(basename "$ORCA_TARBALL" .tar.xz)"
  local ORCA_DIR="/opt/${ORCA_VER}"
  if [[ -x "$ORCA_DIR/orca" ]]; then
    log "ORCA already installed at $ORCA_DIR"
  else
    log "Installing ORCA from $ORCA_TARBALL"
    sudo apt-get install -y libgomp1 libquadmath0 libatomic1 libfftw3-3 libgcc-s1 libstdc++6 libopenmpi3 openmpi-bin
    sudo mkdir -p "$ORCA_DIR"
    sudo tar -xJf "$ORCA_TARBALL" -C "$ORCA_DIR" --strip-components=1
  fi
  sudo ln -sfn "$ORCA_DIR" /opt/orca
  ensure_path_exports
  log "ORCA ready."
}

step_05_qe() {
  if [[ -z "$QE_TARBALL" ]]; then
    log "QE tarball not found — skipping QE installation."
    return
  fi
  local QE_VER="$(basename "$QE_TARBALL" .tar.gz)"
  local QE_DIR="/opt/${QE_VER}"

  if [[ -x "$QE_DIR/bin/pw.x" ]]; then
    log "QE already installed at $QE_DIR"
  else
    log "Installing QE from $QE_TARBALL"
    sudo apt-get install -y build-essential gfortran mpich libfftw3-dev liblapack-dev libblas-dev
    sudo mkdir -p "$QE_DIR"
    sudo tar -xzf "$QE_TARBALL" -C "$QE_DIR" --strip-components=1
    # ensure write perms for build
    sudo chown -R "$USER":"$USER" "$QE_DIR"
    chmod -R u+rwX "$QE_DIR"

    if [[ ! -x "$QE_DIR/bin/pw.x" ]]; then
      log "Building QE..."
      (cd "$QE_DIR" && ./configure MPIF90=mpif90 F90=gfortran && make pw -j"$(nproc)")
    fi
  fi

  sudo ln -sfn "$QE_DIR" /opt/qe
  ensure_path_exports
  log "QE ready."
}

main() {
  step_01_prereqs
  step_03_stack_up
  step_04_orca
  step_05_qe
  log "Setup complete. Grafana :3000 (admin/${GRAFANA_ADMIN_PASSWORD}), Prometheus :9090."
}
main "$@"
