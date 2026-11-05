# トラブルシューティング

工程が失敗すると、JSON の結果は `"status": "error"` になり、`errors` に失敗した検査の名前が入ります。役に立つコマンドは 2 つです。

```bash
urdf2vla diagnose --project projects/myrobot     # 最後のログを既知の問題と照らし合わせ、対処を提案する
urdf2vla status   --project projects/myrobot     # どの工程が済んだか、次に何を実行するか
```

各工程は、検査の元になった測定値を報告ファイルにも書きます: `build/import_report.yaml`、`build/launch_report.yaml`、
`build/teleop_report.yaml`、`build/collect_report.yaml`、`build/convert_report.yaml`、`build/eval_report.yaml`。
ログの全文は `logs/` にあります。

!!! tip "設定の変え方"
    `generated/` のファイルは編集しないでください（次に実行すると書き直されます）。変更は `project.yaml` の `overrides:`
    に（生成されたファイルと同じ構造で）書き、そのファイルを作る工程と、その後の工程を実行し直します。
    `urdf2vla status` で、もう一度実行が必要な工程が分かります。

## 症状から探す

### グリッパは閉じるが、立方体が滑り落ちる・落ちる

| 失敗した検査 | 工程 | 意味 | 対処 |
|---|---|---|---|
| `joint_limits_hold` | `import_robot` | 指の関節が押されて可動範囲を越えた。シミュレータで可動範囲が効いていない。簡略化された URDF のグリッパでよく起きる | グリッパを SimReady のアセットに付け替える: `robot.gripper.simready`（[ガイド](guides/your-robot.md#simready)）。`configure_robot` からやり直す |
| `grasp_hold` | `launch_sim` | 指の間に置いた立方体が、離した後に離れていった | `overrides.robot.asset.solver_position_iterations` を増やして（既定 32、SimReady のグリッパは 64）`import_robot` からやり直す。グリッパの力が不自然なら、トルク上限を確かめる |
| `grasp_opening` | `launch_sim` | 直動の指が立方体の幅より狭く閉じた（めり込んだ） | `overrides.robot.asset.solver_position_iterations` を増やして `import_robot` からやり直す |

### ロボットが姿勢を保てない・震える・吹き飛ぶ

| 失敗した検査 | 意味 | 対処 |
|---|---|---|
| `no_nan` | シミュレーションが発散した | 関節のゲインを下げる（下記）、URDF に質量や慣性のない link がないか確かめる、自己衝突を有効にしていたら無効にする（`overrides.robot.asset.self_collision`） |
| `default_pose_tracking` | 初期姿勢に届かない関節がある | その関節のトルク上限（URDF が仮の値なら `robot.effort_limits`）と初期姿勢を確かめ、剛性を上げる（下記） |
| `within_limits` | 初期姿勢が関節の可動範囲の外 | `robot.default_pose` を直す |
| `gripper_motion` | グリッパが開・閉の位置に届かない | 開く向き（`grippers.0.open_position`）と、指の関節のトルク・速度の上限を確かめる |

**関節ゲインの変え方:** `actuators` はリストで、`overrides` に書いたリストは生成されたものを丸ごと置き換えます。
`generated/robot_config.yaml` の `actuators:` のリスト全体を `project.yaml` の `overrides.robot.actuators` に写し、
`stiffness` / `damping` を変えて、`configure_robot` からやり直します。

### 手先が違う向きに動く

| 失敗した検査 | 工程 | 対処 |
|---|---|---|
| `ik_direction:<arm>:+x` / `+z` / `-y` | `setup_teleoperation` | ある軸の方向に動かす指令で、別の方向に動いた。ロボットの正面（`robot.front_axis`）、手先の link（`arms.0.ee_link`）、初期姿勢が特異姿勢（腕が伸び切った姿勢）の近くでないかを確かめる |
| `action_label` | `setup_teleoperation` | 制御器が出す関節の目標がロボットと合わない。たいていは上の問題のどれかが原因 |

### カメラに何も写らない・違うものが写る

| 失敗した検査 | 意味 | 対処 |
|---|---|---|
| `camera_not_black` | 画像が（ほぼ）真っ黒。たいていはカメラがロボットの形状の中にある | 動かす: `project.yaml` の `cameras:`。手先カメラなら `offset`（`- {preset: wrist, offset: 0.12}`）。`configure_camera` からやり直す |
| `camera_alive`, `camera_frames_updated` | 2 つの step の間で画像が変わらなかった。止まったカメラは学習データを台無しにする | `overrides.task.render_fix: true` を書き、`configure_task` からやり直す（制御 step ごとに描画が 1 回増えます） |
| `camera_mount` | 手先カメラが設定した位置にない | `build/launch_report.yaml` を付けて不具合として報告してください |
| `first_frame_fresh` | エピソードの最初の画像に前のエピソードが写っている | そのデータセットは使わず、新しい `--dataset` の名前で記録し直す |

画像を見てください: `launch_sim` の `snapshots/<カメラ>_start.png` と `_end.png`、または全カメラの視野を描く
[`urdf2vla view`](guides/view.md)。

### 記録が失敗する・成功が少ない

`build/collect_report.yaml` に全試行が載っています（`object_lift_m`: 立方体が持ち上がったか、`object_to_target_m`:
置き場所からどれだけ離れて終わったか）。`snapshots/collect_fail_*.png` は失敗した試行の最後の画像です。

- **立方体が持ち上がらない** — 掴む問題: 上のグリッパの項を見るか、掴む高さを調整する（`overrides.teleop.grasp_height_offset`）。
- **持ち上がるが置けない** — 置き場所に届きにくい: `overrides.task.target.pos` で近づける。
- **`label_replay` が失敗する** — 記録した行動を再生しても同じ動きにならない（ラベルがロボットの動きを表していない）。
  先に `ik_direction` とグリッパの検査を確かめる。

### 変換・学習が失敗する

| 失敗した検査 | 対処 |
|---|---|
| `gr00t_loader` | GR00T が変換したデータセットを読めなかった。`convert_dataset --force` をやり直し、続くなら `build/convert_report.yaml` を付けて報告してください |
| `video_matches:<カメラ>` | エンコードした動画が記録した画像と違う。`convert_dataset --force` をやり直す |
| `finetune_args_valid` | GR00T が学習の引数を受け付けなかった。`train_vla` に渡した値を確かめる |
| 「この GPU では GR00T N1.7 を学習できません」の警告 | エラーではありません。44 GB 以上のマシンで学習します（[学習と評価](guides/training.md#another-machine)） |

## 環境の問題

| メッセージ・症状 | 原因 | 対処 |
|---|---|---|
| 「コンテナ … が起動していません」/ `No such container` | コンテナが動いていない | `bash docker/run.sh`（変換・学習・評価は `bash docker/run.sh gr00t` も） |
| 「…秒以内に終わりませんでした」 | Isaac Sim の最初の起動はシェーダのコンパイルで数分かかることがある | `--timeout` を大きくしてもう一度実行する |
| `GLXBadFBConfig` | OpenGL の設定が GPU と合わない（GPU が 2 つあるノート PC で起きやすい） | `runtime.yaml` の `env:` に `__NV_PRIME_RENDER_OFFLOAD: "1"` と `__GLX_VENDOR_LIBRARY_NAME: nvidia` を足す |
| メッシュのファイルを開けない | URDF が参照するメッシュがコンテナから見えない | `urdf2vla show --project … robot_metadata` の meshes を確かめ、フォルダを `robot.package_paths` に足す |
| `CUDA out of memory` | 他のプロセスが GPU を使っている | `nvidia-smi` で確かめ、他の学習ジョブやコンテナを止める |
| `urdf2vla view` が「追加のパッケージが必要」と言う | 画面用のパッケージが入っていない | `uv pip install -e '.[view]'` |

## 助けを求める

実行したコマンド、その JSON の出力、その工程の報告ファイル（`build/*_report.yaml`）、`logs/` のログの末尾を付けて
[issue](https://github.com/momoiorg-repository/urdf2vla/issues) を作ってください。`urdf2vla diagnose` がログの関係する部分を表示します。
