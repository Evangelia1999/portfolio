#!/usr/bin/env bash
# qe_restarter.sh — restart QE pw.x on crash, auto-clean corrupted WFs
# ../qe_restarter.sh ~/Computational-Chemistry/DFT/QEspresso/A-AlPO3-3_qe/A-AlPO3-3_qe.in &
# ../qe_restarter.sh ~/Computational-Chemistry/DFT/QEspresso/B-AlPO3-3_qe/B-AlPO3-3_qe.in &
set -euo pipefail

# Usage: ./qe_restarter.sh /abs/path/to/job.in [max_restarts]
if [ $# -lt 1 ]; then echo "usage: $0 /path/to/job.in [max_restarts]"; exit 1; fi
INFILE="$(realpath "$1")"
MAX_RESTARTS="${2:-50}"

IN_DIR="$(dirname "$INFILE")"
IN_BASE="$(basename "$INFILE")"
STEM="${IN_BASE%.in}"
OUTDIR="${IN_DIR}/tmp"
OUTFILE="${IN_DIR}/${STEM}.relax.out"
PID_FILE="${OUTFILE}.pid"
LOCK="${OUTFILE}.watch.lock"

# Parse prefix from &CONTROL; fall back to STEM
PREFIX="$(grep -iA50 '^[[:space:]]*&control' "$INFILE" \
  | grep -im1 'prefix[[:space:]]*=' \
  | sed -E 's/.*prefix[[:space:]]*=[[:space:]]*["'\'' ]?([^"'\'' ,/}]+).*/\1/' || true)"
[ -z "${PREFIX:-}" ] && PREFIX="$STEM"

# Runtime config (override via env)
MPIRUN="${MPIRUN:-mpirun -np 32}"
PW_CMD="${PW_CMD:-pw.x -npool 4 -ndiag 8 -ntg 2}"
EXTRA_ENV="${EXTRA_ENV:-OMP_NUM_THREADS=1}"
SLEEP_SECS="${SLEEP_SECS:-30}"
STALL_SECS="${STALL_SECS:-1800}"   # window for stall detection (seconds)

# Clock ticks per second for /proc/*/stat accounting
CLK_TCK="$(getconf CLK_TCK 2>/dev/null || echo 100)"

log_has()    { grep -qE "$1" "$OUTFILE" 2>/dev/null || return 1; }
should_exit_success() { log_has '\bJOB DONE\b'; }
recent_errors() { tail -n 200 "$OUTFILE" 2>/dev/null | grep -E 'davcio|MPI_ABORT|PMIX|error reading file|Terminated' || true; }

# Clean up wavefunctions on davcio(10) or wfc read errors (multi-line safe)
cleanup_wfc_if_needed () {
  if log_has 'davcio \(10\)' || log_has 'from[[:space:]]+davcio[[:space:]]*:[[:space:]]*error[[:space:]]*#?[[:space:]]*10' \
     || log_has 'error reading file .*\.wfc[0-9]+'; then
    echo "[watchdog] removing wavefunctions (davcio/*.wfc)"
    rm -f "${OUTDIR}/${PREFIX}.wfc"* 2>/dev/null || true
    rm -f "${OUTDIR}/${PREFIX}.save/"*wfc* 2>/dev/null || true
  fi
}

pgid_of_pid () { ps -o pgid= -p "$1" 2>/dev/null | awk '{print $1}'; }

# Sum CPU jiffies (utime+stime) for the whole process group
cpu_jiffies_group () {
  local pg="$1" sum=0
  # pgrep -g lists all PIDs in the process group; absence is fine
  while read -r p; do
    # fields: ... 14=utime, 15=stime
    if read -r _ _ _ _ _ _ _ _ _ _ _ _ _ ut st _ <"/proc/$p/stat" 2>/dev/null; then
      sum=$(( sum + ut + st ))
    fi
  done < <(pgrep -g "$pg" 2>/dev/null || true)
  echo "$sum"
}

kill_job () {
  local pid pg
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  [ -n "${pid:-}" ] || return 0
  pg="$(pgid_of_pid "$pid" || true)"
  if [ -n "${pg:-}" ]; then
    echo "[watchdog] killing process group PGID=$pg"
    kill -TERM "-${pg}" 2>/dev/null || true
    sleep 10
    kill -KILL "-${pg}" 2>/dev/null || true
  else
    echo "[watchdog] killing PID=$pid"
    kill -TERM "$pid" 2>/dev/null || true
    sleep 10
    kill -KILL "$pid" 2>/dev/null || true
  fi
}

start_job () {
  echo "[watchdog] start ${STEM} (prefix=${PREFIX})"
  for i in 3 2 1; do [ -f "${OUTFILE}.$i" ] && mv -f "${OUTFILE}.$i" "${OUTFILE}.$((i+1))" || true; done
  [ -f "${OUTFILE}" ] && mv -f "${OUTFILE}" "${OUTFILE}.1" || true
  mkdir -p "${OUTDIR}"
  : > "${OUTFILE}"  # ensure file exists with fresh mtime
  # run from input dir so relative pseudo_dir/outdir in input work
  # start new session so we can manage the whole PGID; record mpirun PID
  bash -lc "cd '${IN_DIR}' && ${EXTRA_ENV} setsid nohup ${MPIRUN} ${PW_CMD} -in '${IN_BASE}' > '${OUTFILE}' 2>&1 & echo \$! > '${PID_FILE}'"
  # reset CPU baseline
  PREV_CPU_J=0
  PREV_CPU_TS="$(date +%s)"
}

is_running () {
  if [ ! -f "${PID_FILE}" ]; then return 1; fi
  local pid pg
  pid="$(cat "${PID_FILE}" 2>/dev/null || true)"
  [ -n "${pid:-}" ] || return 1
  if kill -0 "$pid" 2>/dev/null; then return 0; fi
  pg="$(pgid_of_pid "$pid" || true)"
  if [ -n "${pg:-}" ] && pgrep -g "$pg" >/dev/null 2>&1; then return 0; fi
  return 1
}

log_stalled () {
  [ -f "$OUTFILE" ] || return 1
  local now mtime age
  now="$(date +%s)"
  mtime="$(stat -c %Y "$OUTFILE" 2>/dev/null || echo 0)"
  age=$(( now - mtime ))
  [ "$age" -ge "$STALL_SECS" ]
}

cpu_stalled () {
  local pid pg now cpu_j dts dcpu
  pid="$(cat "$PID_FILE" 2>/dev/null || echo)"; [ -n "$pid" ] || return 0
  pg="$(pgid_of_pid "$pid" 2>/dev/null || echo)"; [ -n "$pg" ] || return 0
  now="$(date +%s)"
  cpu_j="$(cpu_jiffies_group "$pg")"
  dts=$(( now - PREV_CPU_TS ))
  dcpu=$(( cpu_j - PREV_CPU_J ))
  # update baseline for next call
  PREV_CPU_J="$cpu_j"; PREV_CPU_TS="$now"
  # need a decent window; require at least STALL_SECS/2 elapsed
  [ "$dts" -lt $(( STALL_SECS / 2 )) ] && return 1
  # treat as stalled if CPU advanced < 2 seconds in the window
  [ $(( dcpu / CLK_TCK )) -lt 2 ]
}

exec 9>"${LOCK}" || exit 1
flock -n 9 || { echo "[watchdog] lock held for ${STEM}"; exit 1; }

nrestart=0
PREV_CPU_J=0
PREV_CPU_TS="$(date +%s)"
start_job

while :; do
  sleep "${SLEEP_SECS}"

  if should_exit_success; then
    echo "[watchdog] ${STEM} finished (JOB DONE)"
    exit 0
  fi

  # Hard stall only if no log growth AND no CPU progress
  if is_running && log_stalled && cpu_stalled; then
    echo "[watchdog] ${STEM} hard stall (no log and no CPU progress); killing and restarting"
    kill_job
  fi

  # Process gone (silent exit or killed)
  if ! is_running; then
    echo "[watchdog] ${STEM} not running; inspecting…"
    recent_errors
    cleanup_wfc_if_needed
    nrestart=$((nrestart+1))
    if [ "$nrestart" -gt "$MAX_RESTARTS" ]; then
      echo "[watchdog] max restarts (${MAX_RESTARTS}) reached; stopping."
      exit 2
    fi
    echo "[watchdog] restart #${nrestart}"
    start_job
  fi
done
