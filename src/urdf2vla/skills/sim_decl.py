"""Isaac Sim 内で動く Skill の宣言（実装は urdf2vla.sim 側）。"""

from urdf2vla.registry import Arg, declare_sim_skill

declare_sim_skill(
    "import_robot",
    phase=1,
    summary="正規化 URDF を USD に変換して Isaac Sim に読み込み、関節名・剛体・発散・初期姿勢追従・グリッパ開閉を検査する",
    inputs=["generated/robot_config.yaml", "build/robot.urdf"],
    outputs=["build/usd/robot.usd", "build/import_report.yaml"],
    requires=["configure_robot"],
    args=[
        Arg(("--force",), {"action": "store_true", "help": "USD を作り直す"}),
        Arg(("--timeout",), {"type": float, "default": 900.0, "help": "秒"}),
    ],
)

declare_sim_skill(
    "launch_sim",
    phase=1,
    summary="ロボット・カメラ・台・物体・置き場所で Pick & Place 環境を組み立てて起動し、カメラ画像を保存する",
    inputs=["generated/robot_config.yaml", "generated/cameras.yaml", "generated/task.yaml", "build/usd/robot.usd"],
    outputs=["snapshots/*.png", "build/launch_report.yaml"],
    requires=["import_robot", "configure_camera"],
    args=[
        Arg(("--steps",), {"type": int, "default": 60, "help": "動作確認で進める制御ステップ数"}),
        Arg(("--livestream",), {"action": "store_true", "help": "WebRTC で GUI を配信し、Ctrl-C まで動かし続ける"}),
        Arg(("--timeout",), {"type": float, "default": 900.0, "help": "秒（--livestream では無制限）"}),
    ],
)
