#!/usr/bin/env bash
# urdf2vla のコンテナを作成・起動する（既存の別コンテナには触れない）。image は先に bash docker/build.sh で作る。
#   bash docker/run.sh              シミュレーション用 urdf2vla-sim（Isaac Lab Arena / Isaac Lab 3.0 / Isaac Sim 6.2）
#   bash docker/run.sh gr00t        GR00T（変換・学習・推論サーバ）用 urdf2vla-gr00t
#   bash docker/run.sh [gr00t] --rm 停止して削除（named volume のキャッシュは残す）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXTRA=()
if [[ "${1:-}" == "gr00t" ]]; then
  shift
  NAME="${URDF2VLA_GR00T_CONTAINER:-urdf2vla-gr00t}"
  IMAGE="${URDF2VLA_GR00T_IMAGE:-urdf2vla/gr00t:23ace64}"
  EXTRA=(--shm-size=16g)   # 学習の DataLoader が共有メモリを使う
  VOL="urdf2vla-gr00t"
else
  NAME="${URDF2VLA_CONTAINER:-urdf2vla-sim}"
  IMAGE="${URDF2VLA_IMAGE:-urdf2vla/isaaclab_arena:b9e37c5}"
  VOL="urdf2vla-sim"   # Isaac Sim 6.2 のキャッシュ（シェーダ・拡張）
fi

if [[ "${1:-}" == "--rm" ]]; then
  docker rm -f "$NAME"
  exit 0
fi

if docker container inspect "$NAME" >/dev/null 2>&1; then
  if [[ "$(docker inspect -f '{{.State.Running}}' "$NAME")" != "true" ]]; then
    docker start "$NAME" >/dev/null
  fi
  echo "[urdf2vla] $NAME is running"
  exit 0
fi

if [[ -z "$(docker images -q "$IMAGE" 2>/dev/null)" ]]; then
  echo "[urdf2vla] image $IMAGE がありません。先に bash docker/build.sh を実行してください。" >&2
  exit 1
fi

# entrypoint はホストと同じ uid/gid のユーザを作り、引数なしならシェルで待機する（-dit が必須）。
# Arena の image の HEALTHCHECK は常駐する Kit アプリを前提にしている（urdf2vla は Skill ごとに起動する）ので無効にする
docker run -dit --name "$NAME" --no-healthcheck \
  --gpus all --network host "${EXTRA[@]}" \
  -e ACCEPT_EULA=Y -e PRIVACY_CONSENT=Y \
  -e DOCKER_RUN_USER_ID="$(id -u)" -e DOCKER_RUN_USER_NAME="$(id -un)" \
  -e DOCKER_RUN_GROUP_ID="$(id -g)" -e DOCKER_RUN_GROUP_NAME="$(id -gn)" \
  -v "$ROOT":/workspace/urdf2vla \
  -v "$HOME/.cache":"/home/$(id -un)/.cache" \
  -v "${VOL}-ov-data":"/home/$(id -un)/.local/share/ov" \
  -v "${VOL}-nv-cache":"/home/$(id -un)/.nv" \
  "$IMAGE" >/dev/null

# entrypoint がユーザを作り終えるまで待つ
for _ in $(seq 1 30); do
  if docker exec "$NAME" id -u "$(id -un)" >/dev/null 2>&1; then break; fi
  sleep 1
done
# named volume はコンテナ内で root 所有になるので、実行ユーザに渡す
docker exec "$NAME" bash -c "mkdir -p /home/$(id -un)/.local/share && chown -R $(id -u):$(id -g) /home/$(id -un)/.local /home/$(id -un)/.nv"
echo "[urdf2vla] created $NAME from $IMAGE"
