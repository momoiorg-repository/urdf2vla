#!/bin/bash
# ホストと同じ uid / gid のユーザを作る（urdf2vla は docker exec -u <ユーザ> で実行し、生成ファイルの所有者をホストと揃える）。
# 引数なしならシェルで待機する（docker run -dit で使う）。
set -euo pipefail
if [[ -n "${DOCKER_RUN_USER_NAME:-}" ]] && ! id -u "$DOCKER_RUN_USER_NAME" >/dev/null 2>&1; then
  groupadd --force --gid "${DOCKER_RUN_GROUP_ID}" "${DOCKER_RUN_GROUP_NAME}"
  useradd --no-log-init --uid "${DOCKER_RUN_USER_ID}" --gid "${DOCKER_RUN_GROUP_ID}" --shell /bin/bash "${DOCKER_RUN_USER_NAME}"
  mkdir -p "/home/${DOCKER_RUN_USER_NAME}"
  chown "${DOCKER_RUN_USER_ID}:${DOCKER_RUN_GROUP_ID}" "/home/${DOCKER_RUN_USER_NAME}"
fi
exec "$@"
