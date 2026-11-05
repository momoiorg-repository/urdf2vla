"""四元数の順序。urdf2vla は Isaac Lab 3.0 と同じ (x, y, z, w)。設定ファイルのキーは rot_xyzw。"""

from __future__ import annotations

import warnings

IDENTITY_XYZW = [0.0, 0.0, 0.0, 1.0]


def accept_legacy_wxyz(data):
    """旧キー rot_wxyz（Isaac Lab 2.x 時代の (w, x, y, z) 順）を rot_xyzw に読み替える。

    rot_wxyz を書くのはユーザだけ（生成ファイルは rot_xyzw）なので、両方あれば rot_wxyz を優先する
    （overrides で旧キーを書いた場合に、生成された値で上書きされないように）。
    """
    if not isinstance(data, dict) or "rot_wxyz" not in data:
        return data
    data = dict(data)
    w, x, y, z = data.pop("rot_wxyz")
    data["rot_xyzw"] = [x, y, z, w]
    warnings.warn(
        f"rot_wxyz は廃止されました。rot_xyzw: {[x, y, z, w]}（[x, y, z, w] の順）に書き換えてください（今回は自動で変換しました）。",
        UserWarning,
        stacklevel=2,
    )
    return data
