#!/usr/bin/env bash
# FANUC の公式の URDF（FANUC-CORPORATION/fanuc_description, Apache-2.0）を取得し、xacro で URDF に展開する。
#
#   source /opt/ros/jazzy/setup.bash          # xacro（ROS 2）。ROS がなければ `pip install xacro`
#   bash examples/crx3ia/fetch_urdf.sh        # → examples/crx3ia/fanuc_description/crx3ia.urdf
#   bash examples/crx3ia/fetch_urdf.sh crx10ia_l   # 他の CRX: crx5ia crx10ia crx10ia_l crx10ia_lp crx20ia_l crx30ia
#
# URDF は fanuc_description/ の直下に書く。package://fanuc_crx_description/... のメッシュは、urdf2vla が URDF の
# 場所から探して見つける（robot.package_paths は不要）。
set -euo pipefail

MODEL=${1:-crx3ia}
HERE=$(cd "$(dirname "$0")" && pwd)
DIR=$HERE/fanuc_description
REPO=https://github.com/FANUC-CORPORATION/fanuc_description
COMMIT=fb40c9803a826ba68c7c8e28ba904a25efa7fcd2  # 2026-08-05。urdf2vla はこの版で確かめている

if [ ! -d "$DIR/.git" ]; then
  git clone --quiet --filter=blob:none --sparse --no-checkout "$REPO" "$DIR"
  git -C "$DIR" sparse-checkout set fanuc_crx_description LICENSES
  git -C "$DIR" checkout --quiet "$COMMIT"
fi

MACRO=fanuc_crx_description/urdf/${MODEL}_urdf_macro.xacro
if [ ! -f "$DIR/$MACRO" ]; then
  echo "未対応の機種です: $MODEL（$DIR/fanuc_crx_description/urdf/ にある *_urdf_macro.xacro の名前を指定してください）" >&2
  exit 1
fi
if ! command -v xacro >/dev/null; then
  echo "xacro が見つかりません。ROS 2 の環境を読み込む（source /opt/ros/<distro>/setup.bash）か、pip install xacro を実行してください。" >&2
  exit 1
fi

# FANUC の robot/<model>.urdf.xacro と同じ内容。マクロを相対パスで読むので、ROS のワークスペースを作らずに展開できる
cat > "$DIR/$MODEL.urdf.xacro" <<EOF
<?xml version="1.0" ?>
<robot name="$MODEL" xmlns:xacro="http://wiki.ros.org/xacro">
  <xacro:include filename="$MACRO" />
  <link name="world" />
  <link name="ee_link" />
  <xacro:$MODEL parent="world" child="ee_link">
    <origin xyz="0 0 0" rpy="0 0 0" />
  </xacro:$MODEL>
</robot>
EOF
(cd "$DIR" && xacro "$MODEL.urdf.xacro" -o "$MODEL.urdf")
echo "$DIR/$MODEL.urdf"
