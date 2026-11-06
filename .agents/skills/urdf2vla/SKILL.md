---
name: urdf2vla
description: ロボットの URDF から Isaac Sim 上に VLA 開発環境（Pick & Place）を作り、teleop・データ収集・GR00T 学習・評価へ進める。ユーザが「自分のロボットで VLA 開発を始めたい」「URDF を Isaac Sim に入れたい」「urdf2vla の次の工程を進めたい」「urdf2vla のエラーを直したい」と言ったときに使う。
---

# urdf2vla オーケストレーション

あなたの役割は **定義済みの urdf2vla Skill（CLI サブコマンド）を正しい順番で実行すること**。
Isaac Sim の設定ファイルや Isaac Lab のコードを自分で書いたり、生成された設定を推測で埋めたりしない。

## 絶対に守ること

1. **ロボットの事実を推測しない。** End Effector・制御関節・グリッパの関節と開く向き・初期姿勢・関節の役割は、
   Skill が `questions` を返したら **候補をそのままユーザに見せて選んでもらう**。自分で選ばない。
   `suggestion` は参考値で、ユーザの同意なしに回答として使わない（`suggestion: null` なら推奨はない）。
2. **`generated/` を手で編集しない。** 変更は `project.yaml` の `overrides:` か `urdf2vla answer` で行う。
3. **Skill の結果 JSON の `status`（と終了コード）に従って動く。**

| status | 終了コード | すること |
|---|---|---|
| `ok` | 0 | `next` の Skill へ進む。`warnings` があればユーザに伝える |
| `needs_input` | 2 | `questions` をユーザに提示 → 回答を `urdf2vla answer` で記録 → `next` の Skill を実行（回答はそこで取り込まれる） |
| `error` | 1 | `urdf2vla diagnose --project P` で原因と対処を確認（下の「自動で直してよい範囲」） |

終了コード 2 は失敗ではない。`set -e` のようにエラー扱いしないこと。`next` には次に実行する Skill 名だけが入る。

## 準備と実行方法

```bash
cd <urdf2vla リポジトリ>
source .venv/bin/activate          # urdf2vla コマンドが使えるようになる（または .venv/bin/urdf2vla を使う）
urdf2vla doctor                    # 最初に実行環境を検査する（GPU ドライバ、Docker、image・コンテナ、ディスク、アセットサーバ）
bash docker/build.sh               # 初回だけ（image urdf2vla/isaaclab_arena:b9e37c5 がなければ）。30〜60 分かかるので事前にユーザに伝える
bash docker/run.sh                 # シミュレーション用コンテナ urdf2vla-sim。何度実行しても安全（止まっていれば起動するだけ）
urdf2vla init projects/<name> --urdf <URDF の絶対パス>
urdf2vla status --project projects/<name>   # 各工程の状態と next
urdf2vla <skill> --project projects/<name> [オプション]   # Skill の実行（オプションは `urdf2vla <skill> --help`）
urdf2vla skills --json              # Skill 一覧（phase・入出力・前提）
```

## Phase 1 の流れ

1. `inspect_robot` — URDF 解析。ここで **End Effector の候補が複数ある・回転関節の指の開く向きが分からない・arm/gripper 以外の可動関節がある**
   と質問が返る。腕や残すグリッパのメッシュが見つからないときは `error` になるので、ROS パッケージの場所を聞いて `project.yaml` の
   `robot.package_paths` に書く（カメラや取付金具など、それ以外の部品のメッシュがないだけなら警告を出して形なしで進む）。
   **腕だけの URDF（グリッパがない）** では `robot.gripper_joints` の質問が出る。手首の関節を答えさせず、
   `project.yaml` の `robot:` に `gripper: {simready: robotiq_2f_85}` を書いてグリッパを付けるかをユーザに聞く
2. `configure_robot` — 未回答の質問が残っていれば同じ質問で `needs_input` になる
3. `configure_task` — 物体の候補と箱を選び（project.yaml の `task.objects` / `task.container`）、台・物体を置く範囲・箱を自動配置
4. `configure_camera` — 既定は DROID と同じ配置: 手先（wrist）+ ロボットの左右の外部カメラ（left / right）。正面（front）・横（side）も選べる。ユーザが実機カメラの情報（取付 link・位置姿勢・K 行列）を持っていれば `project.yaml` の `cameras:` に明示指定する
5. `import_robot` — USD 変換と検査（関節名・剛体・発散・初期姿勢追従・関節限界・グリッパ開閉）。
   初期姿勢で手先が下を向いていなければ IK で推奨姿勢を求め、`robot.default_pose` を質問する
6. `launch_sim` — 環境を起動し `snapshots/*.png` を保存。**`*_start.png` と `*_end.png` を開いて、ロボット・台・物体・置き場所が写っているか確認し、ユーザにも見せる**
   （画像を開けないときは、そのパスをユーザに伝えて見てもらう）。
   GUI で見たいと言われたら `urdf2vla launch_sim --project P --livestream`（WebRTC。Ctrl-C で終了）

## Phase 2 の流れ（データ収集）

7. `configure_vla_interface` — state / action / 画像の仕様（`generated/vla_interface.yaml`）
8. `setup_teleoperation` — teleop 設定（`generated/teleop.yaml`）と、手先が指令どおり動くかの検査
9. `collect_dataset --device scripted --num-demos N` — 自動操作で収集（人の操作は不要）。
   人が操作したいと言われたら `--device socket`（ユーザに別の端末で `urdf2vla teleop-keys` を実行してもらう）か
   `--device keyboard --livestream`（WebRTC の画面にフォーカスしてキー操作）。
   失敗したときは `build/collect_report.yaml` の `episodes`（`object_lift_m` = 物体が持ち上がったか、`object_to_target_m` など）と
   `snapshots/collect_fail_*.png` を見て、把持の失敗か置き場所の失敗かをユーザに説明する。
   把持の高さなどの調整は `project.yaml` の `overrides.teleop`（例: `grasp_height_offset: -0.01`）で行い、何を変えたか報告する。

## Phase 3〜4 の流れ（GR00T）

GR00T の Skill はコンテナ urdf2vla-gr00t で動く（`bash docker/run.sh gr00t`。何度実行しても安全）。

10. `convert_dataset --dataset <名前>` — GR00T 形式に変換。出力先があれば `--force` が必要（上書きしてよいかユーザに確認する）
11. `train_vla --dataset <名前>` — GPU が 44GB 未満なら学習せず `training/<job>.tar.gz` を作る（`status: ok` + warning）。
    ユーザに「大きな GPU のマシンに GR00T の image を用意し、train.sh の先頭に書いた `docker run` で学習する」と伝える。クラウドへのアップロードや HF への push は勝手にしない
12. `evaluate_vla --replay --dataset <名前>` — 学習前の検証（変換データの行動をサーバが返す）。
    学習済みなら `evaluate_vla --model <checkpoint のディレクトリ>`。結果は `build/eval_report.yaml` と `snapshots/eval_*.png`

## ユーザへの確認の仕方

- 日本語で、その時点の質問を **1 つのメッセージにまとめて** 出す。各質問に回答キー（例: `arms.0.ee_link`）と候補、`reason` を添える。
- **`inferred`（URDF から一意に決まった値）は、最初に質問を出すとき（質問がなければ `configure_robot` の後）に一緒に見せ、
  `import_robot` の前にユーザの了承を得る**（後の Skill が新しい `inferred` を出したら、それも次の確認で見せる）。
- 補足として URDF の該当箇所（関節の origin など）を読んで事実を添えるのはよい。ロボット製品の知識で答えを誘導しない。
- URDF に問題がありそうだと気づいたら（例: 実機では動く関節が fixed になっている）、指摘だけして修正はユーザに任せる。
- End Effector・指の開く向き・初期姿勢・カメラの位置のように「見れば分かる」質問では、`urdf2vla view --project P` を
  バックグラウンドで起動し（終了しないコマンドなので完了を待たない）、表示された URL をブラウザで開いて 3D で確かめるよう勧める。ブラウザの「回答を保存」は
  `answers.yaml` に書くので、ユーザが保存したと言ったら次の Skill を実行する。view は `.[view]` が入っていないと起動しない。

回答のキーと値:

| key | 値 |
|---|---|
| `robot.gripper_joints` | 指の関節名のリスト（双腕なら両手分。例: `[joint7_R, joint7_L]`）。自動で見つからないときだけ聞かれ、決まるまで End Effector は聞かれない |
| `robot.gripper.simready` | NVIDIA のアセットのグリッパを付ける・付け替える（`robotiq_2f_85`、`ufactory_xarm`）。腕だけの URDF のとき、または URDF のグリッパが Isaac Sim で掴めないとき（`joint_limits_hold` 失敗、指が押し戻される）にユーザに提案する。project.yaml の `robot:` に `gripper: {simready: robotiq_2f_85}` と書く（answer ではない）。取付先は腕の先のフレームから自動で選ぶ（`configure_robot` の警告に出る）。別のフレームにするなら `mount_link`。xArm Gripper はライム以外の物体で失敗しやすいので、まず `robotiq_2f_85` を勧める |
| `task.objects` / `task.container` | project.yaml の `task:` に書く（answer ではない）。物体の候補（`apple` `lime` `pear` `color_cube` `rubiks_cube`、プリミティブは `cube`。既定は rubiks_cube 以外の 4 つ）と容器（`klt_bin`（既定、KLT の部品箱）、`plastic_tray`、平らな円は `disc`）。既定は 4 つの物体と KLT の箱。台の見た目は `overrides.task.table.material`（oak / walnut / plain）。configure_task がグリッパの開きに入らない物体を警告付きで除くので、その警告はユーザに伝える |
| `robot.front_axis` | `+x` / `+y` / `-x` / `-y`（ロボットの正面。+x 以外なら正面が +x を向くように回して置く） |
| `arms.<i>.ee_link` | link 名。候補以外の link も指定できる（`urdf2vla show --project P robot_metadata` の `ee_candidates` が全候補）。存在しない名前は再質問される |
| `grippers.<i>.open_position` | `lower` / `upper` |
| `grippers.<i>.open_close` | `[開, 閉]` の関節角 [rad]（指の関節に可動範囲がない continuous のときだけ聞かれる） |
| `joints.<name>.role` | `fixed_at_reset`（reset 時の姿勢で保持）/ `passive`（駆動しない） |
| `robot.default_pose` | `suggested`（IK の推奨姿勢）/ `keep` / `{関節名: 値, ...}` |

`inspect_robot` の warnings に「トルク上限が仮の値かもしれない」「可動範囲がない」と出たら、ユーザに実機の値を聞く。
トルクは `project.yaml` の `robot.effort_limits`（関節名の正規表現 → N·m）で URDF の値を上書きできる。勝手に値を決めない。

```bash
urdf2vla answer --project P arms.0.ee_link=robotiq_85_base_link grippers.0.open_position=lower
urdf2vla answer --project P 'robot.default_pose={J1: 0.0, J2: 0.3, J3: 0.4, J4: 0.0, J5: -1.7, J6: 0.0}'
urdf2vla answer --project P 'robot.gripper_joints=[joint7_R, joint7_L]' 'grippers.0.open_close=[-0.8, 0.0]'
```

`project.yaml` の `robot:` に `end_effector` / `control_joints` / `gripper_joints` / `gripper_open_position` / `default_pose` を最初から書いておけば質問されない。

補足（ユーザに聞かれたら説明する）:
- 質量のない frame（例: `end_effector`, `tool0`）を End Effector にしても使える。Isaac Sim では剛体の祖先 link に固定オフセット付きで取り付け、IK とカメラはその frame を基準に動く。
- project.yaml の URDF パスはホスト側のパス。シミュレータには `build/robot.urdf`（メッシュをコンテナ側の絶対パスに書き換えたもの）を渡す。
  コンテナから見えない場所のメッシュは `build/meshes/` に自動でコピーされるので、マウントの追加は不要。
- 実行環境は Isaac Lab 3.0 + Isaac Lab Arena（Isaac Sim 6.2）。四元数は `(x, y, z, w)`、設定のキーは `rot_xyzw`
  （旧キー `rot_wxyz` は変換されるが警告が出るので、ユーザに書き換えを勧める）。
- 双腕のロボットは import_robot（USD 変換と検査）までは使えるが、Isaac Lab 3.0 版ではまだ環境を作れない（launch_sim 以降）。
- Isaac Lab 2.x 版で記録したデータ（HDF5 の urdf2vla_format 1）は convert_dataset が拒否する。記録し直す。

## 自動で直してよい範囲 / ユーザに聞く範囲

自動で直してよい（直したら何を変えたかユーザに報告する）:
- 環境の問題に見える（コンテナが起動しない、GPU が見えない、`nvidia-container-cli` のエラー）→ まず `urdf2vla doctor` を実行し、
  その `errors` の対処をユーザに伝える。sudo が要る対処（ドライバの更新・再起動、docker グループへの追加）は自分で実行せず、ユーザに頼む
- コンテナが止まっている → `bash docker/run.sh`
- タイムアウト（初回はシェーダのコンパイルで時間がかかる）→ `--timeout` を延ばして再実行
- GLXBadFBConfig → `runtime.yaml` の `env` に `__NV_PRIME_RENDER_OFFLOAD: "1"`, `__GLX_VENDOR_LIBRARY_NAME: nvidia`
- カメラの画角・台や物体の配置 → `project.yaml` の `overrides.cameras` / `overrides.task`
- scripted 収集の把持の高さ・速度 → `overrides.teleop`
- カメラ画像が step 間で更新されない（`camera_alive` 失敗）→ `overrides.task.render_fix: true`（制御周期が変わることを報告する）
- `grasp_opening` / `grasp_hold` 失敗（指が物体にめり込む / 挟んだ物体が落ちる）→ `overrides.robot.asset.solver_position_iterations`
  を 2 倍（32 → 64、上限 128）にして import_robot からやり直す（物理の計算が少し遅くなることを報告する）
- `first_frame_fresh` 失敗（エピソードの最初の画像が reset 前の場面）→ そのデータセットは使わず、collect_dataset を別の `--dataset` 名で記録し直し、
  それでも続くならユーザに報告する（環境を作った直後の描画の立ち上がりの問題）

必ずユーザに聞く:
- End Effector・制御関節・グリッパ関節・開閉の向き・初期姿勢・関節の役割
- 関節ゲイン（`overrides.robot.actuators`）を大きく変えること、URDF 自体の修正、マウントの追加
- `joint_limits_hold` 失敗（グリッパの関節が可動範囲の外まで回る）→ グリッパを SimReady のものに付け替えるか
  （`robot.gripper.simready`）。URDF を直すかどうかもユーザが決める
- 診断で原因が特定できないとき（`log_tail` を要約して見せる）

## 生成物の場所

- `generated/robot_metadata.yaml` URDF 解析結果（`urdf2vla show --project P robot_metadata`）
- `generated/robot_config.yaml` ロボット設定（各値の出所は `provenance`: urdf / user / inferred / default）
- `generated/task.yaml`, `generated/cameras.yaml`
- `build/import_report.yaml`, `build/launch_report.yaml` 検査結果
- `logs/` 各 Skill のログと結果 JSON
