#!/usr/bin/env bash
# urdf2vla の実行環境を用意する（初回に 1 回。30〜60 分、数十 GB）。
#   - シミュレーション: Isaac Lab Arena（Isaac Lab 3.0 / Isaac Sim 6.2）を固定コミットで取得し、image を作る
#   - GR00T: Isaac-GR00T（N1.7）を固定コミットで取得し、その Dockerfile で image を作る（変換・学習・推論サーバ用）
#
#   bash docker/build.sh            取得（なければ）+ image のビルド（同じ image があればスキップ）
#   bash docker/build.sh --force    image を作り直す
#   bash docker/build.sh sim|gr00t シミュレーション用 / GR00T 用の image だけ作る
#
# 既存の image（isaaclab_arena:latest など）には触れない。作る image は urdf2vla/isaaclab_arena:<Arena のコミット>。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Isaac Lab Arena main（2026-11-04）。submodule の Isaac Lab は v3.0.0、base image は isaac-sim:6.2.0
ARENA_COMMIT="${URDF2VLA_ARENA_COMMIT:-b9e37c549df8985701c49b24ea454d403496bc9f}"
# Isaac-GR00T の N1.7 リリース
GR00T_COMMIT="${URDF2VLA_GR00T_COMMIT:-23ace64f17aa5015259b8609d371eb61a357c776}"
ARENA_DIR="$ROOT/external/IsaacLab-Arena"
GR00T_DIR="$ROOT/external/Isaac-GR00T"
IMAGE="${URDF2VLA_IMAGE:-urdf2vla/isaaclab_arena:${ARENA_COMMIT:0:7}}"
GR00T_BASE_IMAGE="urdf2vla/gr00t-base:${GR00T_COMMIT:0:7}"
GR00T_IMAGE="${URDF2VLA_GR00T_IMAGE:-urdf2vla/gr00t:${GR00T_COMMIT:0:7}}"
FORCE=false
WHAT=all
for a in "$@"; do
  case "$a" in
    --force) FORCE=true ;;
    sim|gr00t) WHAT=$a ;;
  esac
done
has_image() { [[ "$FORCE" == false ]] && [[ -n "$(docker images -q "$1" 2>/dev/null)" ]]; }

# .gitmodules は ssh の URL なので、鍵がなくても取れるよう https に読み替える
GIT_HTTPS=(-c "url.https://github.com/.insteadOf=git@github.com:")

checkout() {  # checkout <url> <dir> <commit>
  local url=$1 dir=$2 commit=$3
  if [[ ! -d "$dir/.git" ]]; then
    git clone --filter=blob:none "$url" "$dir"
  fi
  if ! git -C "$dir" cat-file -e "$commit^{commit}" 2>/dev/null; then
    git -C "$dir" fetch --filter=blob:none origin "$commit"
  fi
  git -C "$dir" checkout --quiet --detach "$commit"
  echo "[urdf2vla] $(basename "$dir") @ ${commit:0:7}"
}

mkdir -p "$ROOT/external"
checkout https://github.com/isaac-sim/IsaacLab-Arena.git "$ARENA_DIR" "$ARENA_COMMIT"
git "${GIT_HTTPS[@]}" -C "$ARENA_DIR" submodule update --init --filter=blob:none
git -C "$ARENA_DIR" submodule status
checkout https://github.com/NVIDIA/Isaac-GR00T.git "$GR00T_DIR" "$GR00T_COMMIT"

if [[ "$WHAT" != gr00t ]]; then
  if has_image "$IMAGE"; then
    echo "[urdf2vla] image $IMAGE は作成済み（作り直すには --force）"
  else
    echo "[urdf2vla] building $IMAGE（Isaac Sim の base image の取得を含め 30〜60 分）"
    "$ARENA_DIR/docker/build_docker.sh" -n "$IMAGE"
    echo "[urdf2vla] built $IMAGE"
  fi
fi
if [[ "$WHAT" != sim ]]; then
  if has_image "$GR00T_IMAGE"; then
    echo "[urdf2vla] image $GR00T_IMAGE は作成済み（作り直すには --force）"
  else
    echo "[urdf2vla] building $GR00T_IMAGE（Isaac-GR00T の Dockerfile。CUDA と依存の取得を含め 20〜40 分）"
    DOCKER_BUILDKIT=1 docker build --network host -f "$GR00T_DIR/docker/Dockerfile" -t "$GR00T_BASE_IMAGE" "$GR00T_DIR"
    DOCKER_BUILDKIT=1 docker build --build-arg BASE_IMAGE="$GR00T_BASE_IMAGE" -f "$ROOT/docker/Dockerfile.gr00t" -t "$GR00T_IMAGE" "$ROOT/docker"
    echo "[urdf2vla] built $GR00T_IMAGE"
  fi
fi
echo "[urdf2vla] 次に bash docker/run.sh（シミュレーション）と bash docker/run.sh gr00t（GR00T）でコンテナを作成します"
