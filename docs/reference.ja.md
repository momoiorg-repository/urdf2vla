# リファレンス

## コマンド

| コマンド | すること |
|---|---|
| `urdf2vla init DIR --urdf URDF` | URDF のプロジェクトを作る。`--from examples/<robot>/project.yaml` で例から作る |
| `urdf2vla <工程> --project DIR [オプション]` | 工程を 1 つ実行する（下記）。`urdf2vla <工程> --help` でオプション一覧 |
| `urdf2vla status --project DIR` | 各工程の最後の結果と、次に実行するもの |
| `urdf2vla answer --project DIR key=value …` | 質問への回答を `answers.yaml` に記録する。値は YAML: `'robot.default_pose={j1: 0.0, j2: 0.3}'` |
| `urdf2vla show --project DIR <名前>` | 生成されたファイルを表示する（例: `robot_metadata`, `robot_config`） |
| `urdf2vla diagnose --project DIR` | 最後に失敗した工程のログを既知の問題と照らし合わせる |
| `urdf2vla view --project DIR [--port 8080]` | [ブラウザの画面](guides/view.md)。`.[view]` が必要 |
| `urdf2vla teleop-keys [--port 50555]` | 端末のキー入力を `collect_dataset --device socket` に送る |
| `urdf2vla skills [--json]` | 工程の入出力と前提（`--json` で機械可読） |

## 工程（Skill）

| 工程 | すること |
|---|---|
| `inspect_robot` | URDF を読む: link と関節、腕、グリッパ（mimic を含む）、手先の link の候補、メッシュ。曖昧なことは質問する |
| `configure_robot` | URDF と回答からロボットの設定を作り、正規化した URDF を書く（`package://` を解決、空の root link を除去） |
| `configure_task` | 腕の届く範囲と肩の高さから、台・立方体・置き場所を配置する |
| `configure_camera` | カメラを置く: 既定は DROID の配置（`wrist`, `left`, `right`）。ほかに `front` / `side` のプリセットか明示指定 |
| `import_robot` | URDF を USD に変換し（設定があれば SimReady のグリッパを取り付け）、ロボットの検査をする。手先が下を向いていなければ IK で初期姿勢を提案する |
| `launch_sim` | Isaac Lab Arena で Pick & Place の環境を組み立て、カメラの画像を保存し、カメラ・場面・試しの把持を検査する。`--livestream` で WebRTC のクライアント用に動かし続ける |
| `configure_vla_interface` | VLA の入力（カメラ画像、腕の関節 + グリッパの値）と出力（関節の目標 + グリッパの指令）を決める |
| `setup_teleoperation` | 逆運動学による手先の操作を設定し、指令どおりに動くかを検査する |
| `collect_dataset` | 成功したデモを記録し（`--device scripted` / `keyboard` / `socket`）、HDF5 に保存して検査する |
| `convert_dataset` | 記録を GR00T N1.7 の modality のファイル付きの LeRobot v2.1 に変換し、GR00T のローダで検査する |
| `train_vla` | GR00T N1.7 の fine-tune ジョブを書いて検査する。GPU が 44 GB 以上なら学習し、そうでなければ一式にまとめる |
| `evaluate_vla` | GR00T の推論サーバ（`--model`）かデータセットの再生（`--replay`）でエピソードを実行し、成功率を出す |

各工程の実行場所、前提の工程、読むもの・書くもの、オプション
（*host* = 手元のマシン、*sim* = シミュレーション用コンテナ、*gr00t* = GR00T のコンテナ）:

--8<-- "skills-table.md"

## 各工程の結果

各工程は JSON を 1 つ出力し、`logs/latest_<工程>.json` に保存します。

| フィールド | 意味 |
|---|---|
| `status` | `ok` / `needs_input` / `error`（終了コード 0 / 2 / 1） |
| `next` | 次に実行する工程 |
| `questions` | `needs_input` のとき: `key`, `prompt`, `candidates`, `suggestion`, `reason` |
| `inferred` | URDF から導いた値（確認したほうがよいもの） |
| `warnings` | 工程は止めなかったが知っておくべきこと |
| `errors` | 工程が失敗した理由 |
| `outputs` | 書いたファイル（プロジェクトからの相対パス） |
| `data` | 工程の主な結果（`checks` を含む） |
| `log` | ログのファイル |

## `project.yaml`

```yaml
name: myrobot

robot:
  urdf: /path/to/robot.urdf            # 必須
  package_paths: [../my_ws/src]        # package:// を探す追加のフォルダ
  end_effector: tool0                  # 手先の link（なければ、曖昧なときに質問）
  control_joints: [joint1, joint2]     # 腕の関節（なければ自動検出）
  gripper_joints: [finger_l, finger_r] # 指の関節（なければ自動検出）
  gripper_open_position: upper         # 指が開くのは関節の lower / upper どちらの限界か
  default_pose: {joint1: 0.0}          # 初期姿勢 [rad または m]
  effort_limits: {"joint[1-6]": 50.0}  # N·m。関節名の正規表現 → 値（URDF の値より優先）
  base_pose: {pos: [0, 0, 0], rot_xyzw: [0, 0, 0, 1]}
  gripper: {simready: robotiq_2f_85}   # URDF のグリッパを付け替える（URDF にグリッパがなければ + mount_link）

cameras:                               # プリセット: wrist, left, right, front, side。または明示指定
  - preset: wrist
  - preset: left
  - preset: right

overrides:                             # generated/ のファイルと同じ構造
  robot: {}                            #   generated/robot_config.yaml
  task: {}                             #   generated/task.yaml
  cameras: {}                          #   generated/cameras.yaml
  teleop: {}                           #   generated/teleop.yaml
```

よく使う上書き:

| キー | 既定 | 効果 |
|---|---|---|
| `overrides.robot.asset.solver_position_iterations` | 32（SimReady のグリッパでは 64） | 接触の精度。指が物体にめり込むときに上げる |
| `overrides.task.object.size` / `.mass` | `[0.045, 0.045, 0.045]` m / 0.1 kg | 立方体 |
| `overrides.task.target.radius` | 0.06 m | 置き場所（成功の半径は `task.success.radius`） |
| `overrides.task.language_instruction` | "pick up the red cube and place it on the green target" | VLA に渡すタスクの文 |
| `overrides.task.episode_length_s` | 30 秒 | エピソードの時間切れ |
| `overrides.task.render_fix` | false | カメラが止まるとき、step ごとに描画を 1 回増やす |
| `overrides.teleop.pos_step` / `.rot_step` | 0.008 m / 0.04 rad | キー操作の 1 制御 step あたりの移動量 |
| `overrides.teleop.grasp_height_offset` | 0 m | スクリプトで掴む高さ（立方体の中心から） |

## プロジェクトのフォルダ

```text
projects/<名前>/
  project.yaml      あなたの設定              answers.yaml   あなたの回答
  generated/        robot_metadata, robot_config, task, cameras, vla_interface, teleop（編集しない）
  build/            robot.urdf, usd/, meshes/, *_report.yaml, eval_videos/
  snapshots/        launch_sim・collect_dataset・evaluate_vla のカメラ画像
  datasets/         <名前>.hdf5 の記録、<名前>_gr00t/ の変換済みデータ
  training/         <job>/train.sh, <job>.tar.gz, <job>/checkpoints/
  logs/             実行ごとのログと JSON の結果、latest_<工程>.json
  state.json        各工程の最後の状態
```

## `runtime.yaml`

工程をどこで実行するか。既定値は `docker/build.sh` と `docker/run.sh` に合わせてあります。

| キー | 既定 | 意味 |
|---|---|---|
| `mode` | `docker` | `docker`、または今の Python 環境（Isaac Lab を入れた環境）で動かす `local` |
| `container` / `image` | `urdf2vla-sim` / `urdf2vla/isaaclab_arena:b9e37c5` | シミュレーション用コンテナ |
| `python` | `…/isaaclab.sh -p` | そのコンテナでの Python の起動のしかた |
| `mounts` | リポジトリを `/workspace/urdf2vla` に | コンテナから見えるホストのフォルダ |
| `env` | — | 追加の環境変数 |
| `gr00t.container` / `gr00t.image` | `urdf2vla-gr00t` / `urdf2vla/gr00t:23ace64` | GR00T のコンテナ |

## 検査

| 工程 | 検査 |
|---|---|
| `import_robot` | `articulation_root`, `all_joints_driven`, `mimic_constraints_off`, `joint_name_match`, `bodies_exist`, `no_nan`, `default_pose_tracking`, `within_limits`, `gripper_motion:<gripper>`, `joint_limits_hold:<gripper>` |
| `launch_sim` | `camera_alive:<camera>`, `camera_not_black:<camera>`, `camera_mount:<camera>`, `no_nan`, `object_on_table`, `grasp_hold:<gripper>`, `grasp_opening:<gripper>` |
| `setup_teleoperation` | `ik_direction:<arm>:+x`, `ik_direction:<arm>:+z`, `ik_direction:<arm>:-y`, `action_label:<arm>` |
| `collect_dataset` | `camera_frames_updated`, `first_frame_fresh`, `label_replay` |
| `convert_dataset` | `gr00t_loader`, `video_matches:<camera>` |
| `train_vla` | `finetune_args_valid` |
| `evaluate_vla` | `camera_frames_updated` |

失敗の意味と、何を変えればよいか: [トラブルシューティング](troubleshooting.md)
