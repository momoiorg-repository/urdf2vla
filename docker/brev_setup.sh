#!/usr/bin/env bash
# NVIDIA Brev のインスタンスを urdf2vla が動く状態にする（Launchable の setup script として、または手で 1 回）。
#
#   Launchable: setup script に docker/brev_launchable.sh の内容を貼る（このファイルを取得して実行する）
#   手で:       curl -fsSL https://raw.githubusercontent.com/momoiorg-repository/urdf2vla/main/docker/brev_setup.sh -o /tmp/urdf2vla_brev_setup.sh
#               bash /tmp/urdf2vla_brev_setup.sh     （空きの大きいディスクに clone する。clone 済みなら bash docker/brev_setup.sh）
#   進み具合:   bash docker/brev_setup.sh --status      （ログ: /var/log/urdf2vla-setup.log）
#
# すること（各段階は終わると印を残す。もう一度実行すると、終わっていない段階からやり直す）:
#   1. NVIDIA ドライバが 580 より古ければ 580 に上げて再起動する（再起動の後は systemd が続きを実行する）
#   2. システムのディスクが足りなければ、Docker の保存先を空きの大きいディスクに移す
#   3. GPU の CDI の定義を作り、コンテナから GPU が見えることを確かめる
#   4. urdf2vla を使うユーザを docker グループに入れる
#   5. そのユーザで: uv と Python 環境（.venv）、docker/build.sh、docker/run.sh（2 つのコンテナ）、urdf2vla doctor
#   6. ログインしたときに進み具合と次の手順を表示し、終わっていればリポジトリに移って .venv を有効にする
#
# 環境変数（どれも省略可）:
#   HF_TOKEN           Hugging Face のトークン（read）。~/.cache/huggingface/token に保存する（ログには出さない）
#   URDF2VLA_USER      urdf2vla を使うユーザ（既定: 実行したユーザ。root で実行したら ubuntu、なければ uid 1000）
#   URDF2VLA_REF       リポジトリを clone するときの branch / tag（既定: main）
#   URDF2VLA_DATA_DIR  リポジトリと Docker の保存先を置くディレクトリ（既定: 空きの大きいディスクを自動で選ぶ）
set -euo pipefail

REPO_URL="${URDF2VLA_REPO_URL:-https://github.com/momoiorg-repository/urdf2vla.git}"
REF="${URDF2VLA_REF:-main}"
STATE="${URDF2VLA_SETUP_STATE:-/var/lib/urdf2vla-setup}"
LOG="${URDF2VLA_SETUP_LOG:-/var/log/urdf2vla-setup.log}"
UNIT=urdf2vla-setup.service
DRIVER_MIN=580        # Isaac Sim 6.2 と GR00T の image（PyTorch は CUDA 13）が要る版
DOCKER_NEED_GB=130    # image 約 64 GB + build のキャッシュ最大 50 GB
REPO_NEED_GB=200      # checkpoint 1 つ 24 GB（既定で 2 つ）、データセット、Hugging Face のモデル
DOCS=https://momoiorg-repository.github.io/urdf2vla/ja/guides/brev/
SELF="$(realpath "${BASH_SOURCE[0]}")"
HINT_BEGIN="# >>> urdf2vla (docker/brev_setup.sh) >>>"
HINT_END="# <<< urdf2vla (docker/brev_setup.sh) <<<"

log() { printf '[urdf2vla-setup %s] %s\n' "$(date +%H:%M:%S)" "$*"; }
set_status() { printf '%s\n' "$*" > "$STATE/status"; chmod 644 "$STATE/status"; }
free_gb() { df -P -B1G "$1" 2>/dev/null | awk 'NR==2 {print $4}'; }
same_fs() { [[ "$(stat -c %d "$1")" == "$(stat -c %d "$2")" ]]; }
biggest_mount() {  # 空きがいちばん大きいディスク:「<空き GB> <マウント先>」
  df -P -B1G -x tmpfs -x devtmpfs -x overlay -x squashfs -x efivarfs 2>/dev/null \
    | awk 'NR > 1 && $6 !~ /^\/(boot|snap)/ {print $4, $6}' | sort -n | tail -1
}
driver_major() { nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1 | cut -d. -f1; }
have_systemd() { [[ -d /run/systemd/system ]] && command -v systemctl >/dev/null; }
as_user() {  # urdf2vla を使うユーザで実行する（sudo は /etc/group を読み直すので、足したばかりの docker グループも効く）
  sudo -u "$TARGET_USER" -H env "PATH=$USER_HOME/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" "$@"
}

resolve_user() {
  local u="${URDF2VLA_USER:-${SUDO_USER:-}}"
  if [[ -z "$u" || "$u" == root ]]; then
    if id ubuntu >/dev/null 2>&1; then u=ubuntu; else u="$(getent passwd 1000 | cut -d: -f1 || true)"; fi
  fi
  if [[ -z "$u" ]] || ! id "$u" >/dev/null 2>&1; then
    echo "urdf2vla を使うユーザが分かりません。URDF2VLA_USER=<ユーザ名> を付けて実行してください。" >&2
    exit 1
  fi
  echo "$u"
}

# ---- 段階（root で実行する） ------------------------------------------------------------------------------

stage() {  # stage <名前> <コマンド...>: 印がなければ実行し、成功したら印を残す。終了コード 75 は「再起動を待つ」
  local name=$1 rc
  shift
  if [[ -f "$STATE/$name.done" ]]; then
    log "[$name] 済み"
    return 0
  fi
  set_status "running: $name"
  log "[$name] 開始"
  set +e
  (set -e; "$@")
  rc=$?
  set -e
  if [[ $rc -eq 75 ]]; then exit 0; fi
  if [[ $rc -ne 0 ]]; then
    set_status "failed: $name"
    log "[$name] 失敗（終了コード $rc）。直してから、もう一度 bash $SELF を実行すると、ここからやり直します。"
    exit 1
  fi
  touch "$STATE/$name.done"
  log "[$name] 完了"
}

step_driver() {
  local cur prev held old
  cur="$(driver_major || true)"
  if [[ -n "$cur" && "$cur" -ge $DRIVER_MIN ]]; then
    log "NVIDIA ドライバ $cur（$DRIVER_MIN 以降）"
    return 0
  fi
  # 新しいドライバを入れた後、再起動するまでは nvidia-smi が動かない（新しい userspace と古い kernel module）
  if [[ -f "$STATE/driver.installed" ]]; then
    log "ドライバ $DRIVER_MIN を入れましたが、動いているのは ${cur:-（nvidia-smi が動かない）}です。再起動していなければ sudo reboot、していれば: ${DOCS}#driver"
    return 1
  fi
  if [[ -f "$STATE/driver.started" ]]; then
    prev="$(cat "$STATE/driver.started")"
    log "前回のドライバの更新（$prev から）が途中で止まりました。もう一度入れます"
  elif [[ -z "$cur" ]]; then
    log "nvidia-smi が動きません。ドライバの状態は urdf2vla doctor が詳しく調べます: ${DOCS}#driver"
    return 1
  else
    prev="$cur"
    echo "$prev" > "$STATE/driver.started"
  fi
  log "NVIDIA ドライバが $prev です。$DRIVER_MIN に上げます（Brev が hold している版を外し、$DRIVER_MIN 系に固定する）"
  export DEBIAN_FRONTEND=noninteractive
  local apt=(apt-get -y -o DPkg::Lock::Timeout=900)
  held="$(apt-mark showhold | grep -E 'nvidia|cuda-drivers' || true)"
  # shellcheck disable=SC2086  # パッケージ名の並び
  [[ -z "$held" ]] || apt-mark unhold $held
  "${apt[@]}" update
  # CUDA のリポジトリから新しい系統（620 など）が混ざらないよう、ドライバの版を 580 系に固定する
  if (cd /tmp && apt-get download "nvidia-driver-pinning-$DRIVER_MIN" && dpkg -i "nvidia-driver-pinning-${DRIVER_MIN}"_*.deb); then
    log "nvidia-driver-pinning-$DRIVER_MIN を入れました"
  else
    log "nvidia-driver-pinning-$DRIVER_MIN がありません（CUDA のリポジトリがない）。固定せずに進めます"
  fi
  old="$(dpkg-query -W -f='${db:Status-Abbrev} ${Package}\n' | awk -v v="$prev" '$1 ~ /^[ih]i/ && $2 ~ ("-" v "(-|$)") {print $2}')"
  # shellcheck disable=SC2086
  [[ -z "$old" ]] || "${apt[@]}" purge $old
  "${apt[@]}" install "nvidia-driver-$DRIVER_MIN-open" "nvidia-utils-$DRIVER_MIN" "nvidia-compute-utils-$DRIVER_MIN"
  touch "$STATE/driver.installed"
  if [[ "${URDF2VLA_UNDER_SYSTEMD:-0}" == 1 ]]; then
    set_status "rebooting: driver"
    log "ドライバ $DRIVER_MIN を入れました。再起動します（再起動の後、続きを自動で実行します）"
    systemctl reboot
    sleep 600
    log "再起動しませんでした。sudo reboot を実行してください"
    return 1
  fi
  set_status "reboot needed: driver"
  log "ドライバ $DRIVER_MIN を入れました。sudo reboot で再起動し、もう一度 bash $SELF を実行してください"
  return 75
}

step_docker_root() {
  local root free bigfree bigpath new
  root="$(docker info -f '{{.DockerRootDir}}')"
  free="$(free_gb "$root")"
  if [[ -n "${URDF2VLA_DATA_DIR:-}" ]]; then
    bigpath="$URDF2VLA_DATA_DIR"
    bigfree="$(free_gb "$bigpath")"
  else
    read -r bigfree bigpath < <(biggest_mount) || true
  fi
  log "Docker の保存先: $root（空き ${free} GB。image に約 $DOCKER_NEED_GB GB 要ります）"
  if [[ "$free" -ge $DOCKER_NEED_GB || -z "${bigpath:-}" ]] || [[ "${bigfree:-0}" -le "$free" ]] || same_fs "$root" "$bigpath"; then
    [[ "$free" -ge $DOCKER_NEED_GB ]] || log "空きの大きいディスクが見つかりません。このまま進めます（足りなければ docker/build.sh が止まります）"
    return 0
  fi
  new="${bigpath%/}/docker"
  log "Docker の保存先を $new（空き ${bigfree} GB）に移します"
  mkdir -p /etc/docker "$new"
  if [[ -f /etc/docker/daemon.json && ! -f /etc/docker/daemon.json.urdf2vla-bak ]]; then
    cp /etc/docker/daemon.json /etc/docker/daemon.json.urdf2vla-bak
  fi
  # 既存の設定（nvidia の runtime など）は残し、data-root だけを足す
  python3 - "$new" <<'EOF'
import json, os, sys
path = "/etc/docker/daemon.json"
cfg = {}
if os.path.exists(path):
    with open(path) as f:
        text = f.read().strip()
    cfg = json.loads(text) if text else {}
cfg["data-root"] = sys.argv[1]
with open(path + ".tmp", "w") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
os.replace(path + ".tmp", path)
EOF
  systemctl restart docker
  [[ "$(docker info -f '{{.DockerRootDir}}')" == "$new" ]]
}

step_cdi() {
  if command -v nvidia-ctk >/dev/null; then
    mkdir -p /etc/cdi
    # ドライバ 580 より前の nvidia-ctk は nvSandboxUtilsShutdown で落ちることがある（そのときは機能を切って作り直す）
    nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml \
      || nvidia-ctk cdi generate --feature-flag=disable-nvsandboxutils --output=/etc/cdi/nvidia.yaml
  else
    log "nvidia-ctk がありません（CDI の定義は作りません）"
  fi
  log "コンテナから GPU が見えるか確かめます"
  docker run --rm --gpus all ubuntu:22.04 nvidia-smi -L
}

step_docker_group() {
  if id -nG "$TARGET_USER" | tr ' ' '\n' | grep -qx docker; then
    log "$TARGET_USER は docker グループに入っています"
  else
    usermod -aG docker "$TARGET_USER"
    log "$TARGET_USER を docker グループに入れました（今開いている端末では、ログインし直すと効きます）"
  fi
}

step_doctor() {
  set_status "running: doctor"
  log "[doctor] urdf2vla doctor"
  as_user bash -c "cd $(printf %q "$REPO") && .venv/bin/urdf2vla doctor" > "$STATE/doctor.json" 2>&1 || true
  chmod 644 "$STATE/doctor.json"
  cat "$STATE/doctor.json"
}

run_stages() {
  [[ $EUID -eq 0 ]] || { echo "--run は root で実行します" >&2; exit 1; }
  # shellcheck source=/dev/null
  source "$STATE/env"
  log "==== urdf2vla setup: ユーザ $TARGET_USER、リポジトリ $REPO ===="
  stage driver step_driver
  stage docker_root step_docker_root
  stage cdi step_cdi
  stage docker_group step_docker_group
  stage python as_user bash "$SELF" --user-step python
  stage images as_user bash "$SELF" --user-step build
  stage containers as_user bash "$SELF" --user-step containers
  step_doctor
  touch "$STATE/finished"
  set_status "done"
  if [[ "${URDF2VLA_UNDER_SYSTEMD:-0}" == 1 ]]; then systemctl disable "$UNIT" >/dev/null 2>&1 || true; fi
  log "==== urdf2vla setup: 完了 ===="
}

# ---- urdf2vla を使うユーザで実行する段階 --------------------------------------------------------------------

user_step() {
  cd "$(dirname "$SELF")/.."
  export PATH="$HOME/.local/bin:$PATH"
  case "$1" in
    python)
      mkdir -p "$HOME/.cache"  # コンテナがマウントする。ないと Docker が root の持ち物として作ってしまう
      command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
      [[ -x .venv/bin/python ]] || uv venv .venv
      uv pip install --python .venv/bin/python -e '.[view]' xacro
      ;;
    build) bash docker/build.sh ;;
    containers) bash docker/run.sh && bash docker/run.sh gr00t ;;
    *) echo "不明な step: $1" >&2; exit 2 ;;
  esac
}

# ---- 入口 ------------------------------------------------------------------------------------------------

show_status() {
  local s repo=""
  s="$(cat "$STATE/status" 2>/dev/null || echo "not started")"
  # shellcheck source=/dev/null
  [[ -r "$STATE/env" ]] && repo="$(source "$STATE/env" && echo "$REPO")"
  case "$s" in
    done*)
      echo "urdf2vla: セットアップ済み（${repo}）。次に:"
      echo "  1. https://huggingface.co/nvidia/Cosmos-Reason2-2B で利用規約に同意し、hf auth login（学習・評価に必要。済んでいれば不要）"
      echo "  2. urdf2vla doctor"
      echo "  3. チュートリアル: https://momoiorg-repository.github.io/urdf2vla/ja/tutorial/"
      python3 - "$STATE/doctor.json" 2>/dev/null <<'EOF' || true
import json, sys
for e in json.load(open(sys.argv[1])).get("errors", []):
    print("  セットアップの最後の urdf2vla doctor のエラー:", e)
EOF
      ;;
    running*) echo "urdf2vla: セットアップ中（${s#running: }）。進み具合: tail -f $LOG" ;;
    rebooting*) echo "urdf2vla: ドライバを入れて再起動しています。再起動の後、続きを自動で実行します（tail -f $LOG）" ;;
    "reboot needed"*) echo "urdf2vla: ドライバを入れました。sudo reboot の後、もう一度 bash ${repo:-.}/docker/brev_setup.sh を実行してください" ;;
    failed*) echo "urdf2vla: セットアップが「${s#failed: }」で止まりました。ログ: $LOG。直してから bash ${repo:-.}/docker/brev_setup.sh で続きから（${DOCS}）" ;;
    *) echo "urdf2vla: セットアップはまだ始まっていません（bash docker/brev_setup.sh）" ;;
  esac
}

find_or_clone_repo() {
  local here base free homefree bigfree bigpath
  here="$(dirname "$SELF")/.."
  if [[ -f "$here/pyproject.toml" && -d "$here/src/urdf2vla" ]]; then
    REPO="$(cd "$here" && pwd -P)"
    log "リポジトリ: $REPO（この場所のまま使います）"
    free="$(free_gb "$REPO")"
    read -r bigfree bigpath < <(biggest_mount) || true
    if [[ "${free:-0}" -lt $REPO_NEED_GB && -n "${bigpath:-}" && "${bigfree:-0}" -gt "${free:-0}" ]] && ! same_fs "$REPO" "$bigpath"; then
      log "注意: リポジトリのディスクの空きは ${free} GB です（checkpoint は 1 つ 24 GB）。空きの大きい $bigpath（${bigfree} GB）に置くには、"
      log "      ドキュメントの curl の手順で実行し直すか、git clone $REPO_URL ${bigpath%/}/urdf2vla で clone し直してください"
    fi
    return 0
  fi
  if [[ -n "${URDF2VLA_DATA_DIR:-}" ]]; then
    base="$URDF2VLA_DATA_DIR"
  else
    homefree="$(free_gb "$USER_HOME")"
    read -r bigfree bigpath < <(biggest_mount) || true
    if [[ "$homefree" -ge $REPO_NEED_GB || -z "${bigpath:-}" ]] || same_fs "$USER_HOME" "$bigpath"; then
      base="$USER_HOME"
    else
      base="$bigpath"
      log "ホーム（空き ${homefree} GB）より空きの大きい $bigpath（空き ${bigfree} GB）にリポジトリを置きます"
    fi
  fi
  REPO="${base%/}/urdf2vla"
  if [[ -d "$REPO/.git" ]]; then
    log "既存のリポジトリを使います: $REPO"
  else
    mkdir -p "$REPO"
    chown "$TARGET_USER:" "$REPO"
    log "clone: $REPO_URL（$REF）→ $REPO"
    as_user git clone --branch "$REF" "$REPO_URL" "$REPO"
  fi
  if [[ "$base" != "$USER_HOME" && ! -e "$USER_HOME/urdf2vla" ]]; then
    as_user ln -s "$REPO" "$USER_HOME/urdf2vla"
  fi
  REPO="$(cd "$REPO" && pwd -P)"
}

save_token() {  # HF_TOKEN → ~/.cache/huggingface/token（ユーザの持ち物、600）。値はどこにも表示しない
  [[ -n "${HF_TOKEN:-}" ]] || return 0
  # shellcheck disable=SC2016  # $HOME はユーザのシェルで展開する
  printf '%s' "$HF_TOKEN" | as_user bash -c 'umask 077 && mkdir -p "$HOME/.cache/huggingface" && cat > "$HOME/.cache/huggingface/token"'
  unset HF_TOKEN
  log "Hugging Face のトークンを $USER_HOME/.cache/huggingface/token に保存しました"
}

install_login_hint() {
  local rc="$USER_HOME/.bashrc" repo_q state_q
  repo_q="$(printf %q "$REPO")"
  state_q="$(printf %q "$STATE")"
  if [[ -f "$rc" ]] && grep -qF "$HINT_BEGIN" "$rc"; then return 0; fi
  as_user tee -a "$rc" >/dev/null <<EOF

$HINT_BEGIN
if [[ \$- == *i* && -f $repo_q/docker/brev_setup.sh ]]; then
  bash $repo_q/docker/brev_setup.sh --status
  if [[ "\$(cat $state_q/status 2>/dev/null)" == done* ]]; then cd $repo_q && [[ -f .venv/bin/activate ]] && source .venv/bin/activate; fi
fi
$HINT_END
EOF
  log "ログインしたときの案内を $rc に足しました"
}

install_unit() {
  cat > "/etc/systemd/system/$UNIT" <<EOF
[Unit]
Description=urdf2vla setup (docker/brev_setup.sh)
Wants=network-online.target
After=network-online.target docker.service
ConditionPathExists=!$STATE/finished

[Service]
Type=oneshot
ExecStart=/bin/bash $REPO/docker/brev_setup.sh --run
Environment=URDF2VLA_UNDER_SYSTEMD=1 URDF2VLA_SETUP_STATE=$STATE URDF2VLA_SETUP_LOG=$LOG
StandardOutput=append:$LOG
StandardError=inherit
TimeoutStartSec=infinity

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable "$UNIT" >/dev/null 2>&1
}

main() {
  case "${1:-}" in
    --status) show_status; return 0 ;;
    --run) run_stages; return 0 ;;
    --user-step) user_step "${2:?}"; return 0 ;;
    -h | --help) sed -n '2,/^set -euo/p' "$SELF" | sed '$d; s/^# \{0,1\}//'; return 0 ;;
    "") ;;
    *) echo "不明な引数: $1（--help で使い方）" >&2; exit 2 ;;
  esac
  if [[ $EUID -ne 0 ]]; then
    exec sudo --preserve-env=HF_TOKEN,URDF2VLA_REF,URDF2VLA_REPO_URL,URDF2VLA_DATA_DIR,URDF2VLA_SETUP_STATE,URDF2VLA_SETUP_LOG \
      env URDF2VLA_USER="${URDF2VLA_USER:-$(id -un)}" bash "$SELF"
  fi
  mkdir -p "$STATE" "$(dirname "$LOG")"
  chmod 755 "$STATE"
  touch "$LOG"
  chmod 644 "$LOG"
  TARGET_USER="$(resolve_user)"
  USER_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
  # 準備（トークン・clone・案内）。失敗したら止める（|| の中では set -e が効かないので、終了コードを PIPESTATUS で見る）
  local prep_rc
  set +e
  (
    set -e
    log "==== urdf2vla setup の開始（$(date -Is)）===="
    log "実行: $(id -un)、urdf2vla を使うユーザ: $TARGET_USER（$USER_HOME）、cwd: $(pwd)"
    log "OS: $(. /etc/os-release && echo "$PRETTY_NAME")、kernel $(uname -r)"
    log "GPU: $(nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>&1 | head -2 | tr '\n' ' ' || true)"
    log "Docker: $(docker info -f '{{.ServerVersion}}, root {{.DockerRootDir}}' 2>&1 | head -1 || true)"
    log "NVIDIA Container Toolkit: $(nvidia-ctk --version 2>&1 | head -1 || true)、$(grep -hE '^\s*mode\s*=' /etc/nvidia-container-runtime/config.toml 2>/dev/null | head -1 || true)"
    df -h -x tmpfs -x devtmpfs -x overlay -x squashfs 2>&1 || true
    save_token
    find_or_clone_repo
    printf 'TARGET_USER=%q\nUSER_HOME=%q\nREPO=%q\n' "$TARGET_USER" "$USER_HOME" "$REPO" > "$STATE/env"
    chmod 644 "$STATE/env"
    install_login_hint
  ) 2>&1 | tee -a "$LOG"
  prep_rc=${PIPESTATUS[0]}
  set -e
  if [[ $prep_rc -ne 0 ]]; then
    set_status "failed: prepare"
    log "準備（clone など）で止まりました（終了コード $prep_rc）。ログ: $LOG" | tee -a "$LOG"
    exit 1
  fi
  unset HF_TOKEN
  # shellcheck source=/dev/null
  source "$STATE/env"
  SELF="$REPO/docker/brev_setup.sh"
  local rc=0
  if have_systemd; then
    install_unit
    log "続きは systemd（$UNIT）で実行します。この端末や接続が切れても止まりません。進み具合: tail -f $LOG" | tee -a "$LOG"
    tail -n 0 -F "$LOG" 2>/dev/null &
    local tail_pid=$!
    systemctl start "$UNIT" || rc=$?
    sleep 1
    kill "$tail_pid" 2>/dev/null || true
  else
    bash "$SELF" --run 2>&1 | tee -a "$LOG" || rc=$?
  fi
  show_status
  return "$rc"
}

main "$@"
