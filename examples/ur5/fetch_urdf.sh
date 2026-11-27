#!/usr/bin/env bash
# Universal Robots の公式の URDF（UniversalRobots/Universal_Robots_ROS2_Description）を取得し、xacro で URDF に展開する。
#
#   uv pip install xacro                   # xacro（PyPI。ROS 2 の xacro と同じ URDF になる）。ROS 2 があれば source /opt/ros/jazzy/setup.bash
#   bash examples/ur5/fetch_urdf.sh        # → examples/ur5/ur_description/ur5.urdf
#   bash examples/ur5/fetch_urdf.sh ur5e   # 他の機種: ur3 ur3e ur5 ur5e ur7e ur10 ur10e ur12e ur16e ur15 ur18 ur20 ur30 など
#                                          # （config/ にある名前。urdf2vla で確かめたのは ur5）
#
# ライセンス: BSD-3-Clause。ただし UR8LONG・UR15・UR18・UR20・UR30・UR*g のメッシュと Smart Panel のメッシュは、UR の
# 「Terms and Conditions for use of Graphical Documentation」に従う（ur_description/README.md の License）。
#
# UR の xacro は $(find ur_description) でファイルを探す（ROS のパッケージの場所を ament_index_python で調べる）。ROS 2 がなくても
# 展開できるよう、ur_description の場所だけを返す小さな代わりを一時ディレクトリに作って xacro に渡す。
# URDF は ur_description/ の直下に書く。package://ur_description/... のメッシュは、urdf2vla が URDF の場所から探して見つける。
set -euo pipefail

TYPE=${1:-ur5}
HERE=$(cd "$(dirname "$0")" && pwd)
DIR=$HERE/ur_description
REPO=https://github.com/UniversalRobots/Universal_Robots_ROS2_Description
COMMIT=0eb1a861f7772fceb38510d90fa2f98ec668070c  # 2026-11-03。urdf2vla はこの版の ur5 で確かめている

if [ ! -d "$DIR/.git" ]; then
  git clone --quiet --filter=blob:none --sparse --no-checkout "$REPO" "$DIR"
  git -C "$DIR" sparse-checkout set urdf config meshes/common
  git -C "$DIR" checkout --quiet "$COMMIT"
fi
if [ ! -d "$DIR/config/$TYPE" ]; then
  echo "未対応の機種です: $TYPE（$DIR/config/ にあるディレクトリの名前を指定してください）" >&2
  exit 1
fi
git -C "$DIR" sparse-checkout add "meshes/$TYPE"
if ! command -v xacro >/dev/null; then
  echo "xacro が見つかりません。uv pip install xacro（urdf2vla の環境に入れる）か、ROS 2 の環境を読み込んで（source /opt/ros/<distro>/setup.bash）ください。" >&2
  exit 1
fi

# $(find ur_description) の代わり（ROS 2 の ament_index_python と同じ名前の小さなモジュール）
SHIM=$(mktemp -d)
trap 'rm -rf -- "$SHIM"' EXIT
mkdir -p "$SHIM/ament_index_python"
: > "$SHIM/ament_index_python/__init__.py"
cat > "$SHIM/ament_index_python/packages.py" <<EOF
def get_package_share_directory(name):
    if name == "ur_description":
        return "$DIR"
    raise LookupError(f"package not found: {name}")
EOF
(cd "$DIR" && PYTHONPATH="$SHIM${PYTHONPATH:+:$PYTHONPATH}" xacro urdf/ur.urdf.xacro "ur_type:=$TYPE" "name:=$TYPE" -o "$TYPE.urdf")
# 機種によってはほかの機種のメッシュを使う（ur10g-1750 は ur17g-1300 のもの）: URDF が参照するメッシュのフォルダをすべて取得する
for d in $(grep -oE 'package://ur_description/meshes/[^/]+' "$DIR/$TYPE.urdf" | sort -u | sed 's#package://ur_description/##'); do
  git -C "$DIR" sparse-checkout add "$d"
done
echo "$DIR/$TYPE.urdf"
