"""Phase 3〜4 の Skill の宣言（実装は urdf2vla.gr00t / urdf2vla.sim 側）。"""

from urdf2vla.registry import Arg, declare_sim_skill

declare_sim_skill(
    "convert_dataset",
    runs_in="gr00t",
    phase=3,
    summary="HDF5 のデモを GR00T（LeRobot v2.1）形式に変換し、modality 定義・統計を作って GR00T のローダで読めるか検査する",
    inputs=["datasets/<name>.hdf5"],
    outputs=["datasets/<name>_gr00t/ | datasets/<name>_droid_gr00t/", "build/convert_report.yaml"],
    requires=["collect_dataset"],
    args=[
        Arg(("--dataset",), {"default": "demos", "help": "datasets/<名前>.hdf5"}),
        Arg(("--force",), {"action": "store_true", "help": "出力先があれば作り直す"}),
        Arg(("--layout",), {"choices": ["native", "droid"], "default": "native",
                            "help": "native: urdf2vla の並び（全カメラ・関節目標）→ <名前>_gr00t。droid: GR00T の DROID の embodiment"
                                    "（単腕。eef_9d + グリッパ + 関節 7、外部カメラ 1 台 + 手先）→ <名前>_droid_gr00t（train_vla --dataset <名前>_droid）"}),
        Arg(("--exterior",), {"default": None, "help": "--layout droid: 外部カメラ（カンマ区切り。既定は記録した外部カメラ全部で、各デモをカメラごとに 1 エピソード書く）"}),
        Arg(("--timeout",), {"type": float, "default": 3600.0, "help": "秒"}),
    ],
)

declare_sim_skill(
    "train_vla",
    runs_in="gr00t",
    phase=3,
    summary="GR00T N1.7 の fine-tune ジョブを作る。GPU メモリが足りればそのまま学習し、足りなければ他のマシンで実行できる一式（train.sh + データ）を作る",
    inputs=["datasets/<name>_gr00t/"],
    outputs=["training/<job>/train.sh", "training/<job>.tar.gz | training/<job>/checkpoints/"],
    requires=["convert_dataset"],
    args=[
        Arg(("--dataset",), {"default": "demos", "help": "変換済みの datasets/<名前>_gr00t"}),
        Arg(("--job",), {"default": None, "help": "ジョブ名（既定: <プロジェクト>_<dataset>）"}),
        Arg(("--max-steps",), {"type": int, "default": 10000}),
        Arg(("--batch-size",), {"type": int, "default": 32, "help": "global batch size"}),
        Arg(("--save-steps",), {"type": int, "default": 1000}),
        Arg(("--save-total-limit",), {"type": int, "default": 2, "help": "残す checkpoint の数（1 つ約 24 GB: 重み 11 GB + optimizer 13 GB）"}),
        Arg(("--base-model",), {"default": None, "help": "ベースモデル（既定: 単腕・DROID の並びは nvidia/GR00T-N1.7-DROID、双腕は nvidia/GR00T-N1.7-3B）"}),
        Arg(("--check-only",), {"default": None, "help": "学習せず、この checkpoint がグリッパを開く場面で開くかだけ確かめる（シミュレーションなし、1〜2 分）"}),
        Arg(("--force-local",), {"action": "store_true", "help": "GPU メモリが足りなくてもこのマシンで学習を試す"}),
        Arg(("--timeout",), {"type": float, "default": 7 * 24 * 3600.0, "help": "秒"}),
    ],
)
