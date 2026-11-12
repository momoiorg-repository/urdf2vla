# デモを記録する

VLA はデモ（毎 step のカメラ画像・ロボットの状態・そのときの行動）から学びます。urdf2vla は `collect_dataset` で
Isaac Sim の中でデモを記録し、**成功した**エピソードだけを残します。

記録の前に、準備の工程を一度実行します。

```bash
urdf2vla configure_vla_interface --project projects/myrobot   # VLA が見るもの・出すもの
urdf2vla setup_teleoperation     --project projects/myrobot   # 手先が指令どおりに動くかの検査
```

## 記録されるもの

| | 中身 |
|---|---|
| 画像 | 全カメラ（既定: 腕ごとの手先 + 左 + 右）、256×256 の RGB、制御周期（既定 20 Hz）ごと |
| 状態 | 全腕の関節角 + グリッパごとの値（0 = 閉、1 = 開） |
| 行動 | 全腕の関節の目標 + 全グリッパの指令（制御器に出した指令） |
| タスクの文 | そのエピソードの物体の名前を言う指示文。例: "put the apple in the bin" |

エピソードごとに、タスクの物体の 1 つを台のランダムな位置に置きます。物体が箱の中にあり、グリッパが離した状態が
数 step 続くと成功です。失敗したエピソードは捨てるので、
データセットには良いデモだけが入ります。記録は `datasets/<名前>.hdf5` に保存します（既定は `demos`。`--dataset` で変更）。

## 3 つの記録のしかた

=== "スクリプト（人は不要）"

    スクリプトの制御器が物体を掴んで箱に入れます。物体とその位置はエピソードごとにランダムです。
    双腕では物体に近い方の腕を使い、もう一方は初期姿勢のままグリッパを開いて保持します。データセットを作り、
    全工程を確かめる一番早い方法です。

    ```bash
    urdf2vla collect_dataset --project projects/myrobot --device scripted --num-demos 50
    ```

    試行は最大で `--num-demos` の 3 倍です（`--max-attempts` で変更）。失敗した試行は `build/collect_report.yaml` に
    まとめ、`snapshots/collect_fail_*.png` に画像を残すので、掴むのと置くのどちらで失敗したかが分かります。

=== "Isaac Sim の画面でキー操作"

    **WebRTC のライブ配信**で Isaac Sim を見ながら、自分でロボットを動かします。シミュレータはコンテナの中で動き、
    NVIDIA の配信クライアントで画面を見ます。

    ```bash
    urdf2vla collect_dataset --project projects/myrobot --device keyboard --livestream
    ```

    1. *Isaac Sim WebRTC Streaming Client* を入れます（[Isaac Sim のドキュメント](https://docs.isaacsim.omniverse.nvidia.com/)
       の "Livestream Clients"）。
    2. urdf2vla を動かしているマシンにつなぎます（同じマシンなら `127.0.0.1`、そうでなければその IP アドレス）。
    3. 配信画面をクリックしてキー入力を受け取れるようにし、下のキーで操作します。

    記録は実時間に合わせて進むので、ロボットは見たままの速さで動きます。

=== "端末でキー操作"

    GUI は使いません。シミュレーションは画面なしで動き、2 つ目の端末がキー入力をローカルのソケットで送ります。

    ```bash
    urdf2vla collect_dataset --project projects/myrobot --device socket --num-demos 10
    # 1 つ目が待ち状態になったら、2 つ目の端末で:
    urdf2vla teleop-keys
    ```

    SSH 越しでも使えます。場面は見えないので、主に簡単な試験用です。保存された画像で確かめます。

## キー

| キー | 手先の動き |
|---|---|
| ++w++ / ++s++ | 前 / 後（ロボットの正面 +x の方向） |
| ++a++ / ++d++ | 左 / 右（±y） |
| ++q++ / ++e++ | 上 / 下（±z） |
| ++z++ / ++x++, ++t++ / ++g++, ++c++ / ++v++ | roll, pitch, yaw |
| ++k++ | 操作中の腕のグリッパを開く / 閉じる |
| ++n++ | 操作する腕を切り替える（双腕） |
| ++r++ | このエピソードを捨ててやり直す |
| ++esc++ | 記録を終える |

手先は少しずつ動きます（1 制御 step あたり 8 mm と 0.04 rad）。遅すぎる・速すぎるときは `project.yaml` の
`overrides.teleop` に `pos_step` と `rot_step` を書きます。物体を箱に置いた時点で、エピソードは自動で保存されます。エピソードの始めにその指示文を表示します。

## Isaac Lab Mimic でデモを増やす

[Isaac Lab Mimic](https://isaac-sim.github.io/IsaacLab/main/source/overview/imitation-learning/teleop_imitation.html)
は、少数のデモから多くのデモを作ります。元のデモを「物体を掴む」と「箱に入れる」に分け、新しいエピソードの物体と箱の
位置に合わせてそれぞれを動かして実行し、成功したものを残します。`generate_mimic` は、**物体 1 つと容器**のタスクで
これを一通り行います。

```yaml
# project.yaml
task:
  objects: [color_cube]
  container: klt_bin
```

```bash
# 元のデモを自分で記録する: 別の端末で `urdf2vla teleop-keys` を実行する
urdf2vla generate_mimic --project projects/myrobot --source-device socket --source-demos 5 --trials 100
# スクリプトに記録させる（最初に流れを試すとき）
urdf2vla generate_mimic --project projects/myrobot --source-demos 10 --trials 100
```

1. **元のデモ** — `--source-demos` 個の成功したエピソードを、Mimic が使う姿勢と一緒に記録します
   （`datasets/demos_mimic_src.hdf5`）。既定ではスクリプトが記録し、`--source-device socket`（`urdf2vla teleop-keys` からの
   キー入力）か `keyboard` では自分で操作して記録します。物体を容器に置いて離したエピソードだけを使います。R でエピソードを
   やり直し、Esc で終わります。
2. **生成** — Mimic が、物体を毎回新しい位置に置いて `--trials` 回試し、成功したものを残します
   （`datasets/demos_mimic_gen.hdf5`。行動だけ）。
3. **記録** — 生成したエピソードを初期状態から再生し、カメラ画像付きでふつうの記録と同じく保存します
   （`datasets/demos.hdf5`。`collect_dataset` と同じ検査付き）。

結果には 2 つの数が出ます。`--trials` 回のうち Mimic が生成に成功した数と、そのうちデータセットに記録できた数です。
`--trials` は試す回数で、成功数の目標ではありません。

Franka の例（`task: {objects: [color_cube], container: klt_bin}`）では、元のデモ 10 個から **100 回中 75 回の生成に成功**し、
75 個すべてを画像付きで記録できました。キューブは置く範囲の全体（向きは ±27°）に散らばります。RTX 5080 1 枚で約 45 分です（元のデモ 3 分、生成 23 分、記録 18 分）。

### 双腕のロボット

Mimic では腕ごとに手先を扱います。元のデモで物体を掴んだ腕が、生成するデモでも掴んで置きます。もう一方の腕は元のデモの
姿勢のままで、物体に合わせて動かしません（動かすと止まっている手が容器に当たります）。元のデモはすべて同じ腕で記録して
ください。両方の腕のデモが混ざると `generate_mimic` は止まり、腕ごとに分けて記録するよう伝えます。

OpenArm や Dual Scorpion のように、スクリプトでうまく作業できないロボットのデータセットは、この方法で作ります。ロボットと
場面を整え（`launch_sim` と `setup_teleoperation` が通る）、自分でデモをいくつか記録し、Mimic で増やします。元のデモ 5 個
（人の代わりにスクリプトで記録）と 20 回の試行で、止まっている腕の手はどのエピソードでも動きませんでした（0 mm）。

| ロボット | 作業する腕 | 生成に成功 | 画像付きで記録 |
|---|---|---|---|
| Franka 2 本（`examples/dual_franka`） | 左 | 20 回中 15 回 | 15 |
| OpenArm（`examples/openarm`） | 左 | 20 回中 20 回 | 20 |

操作中は N で腕を切り替えます（`urdf2vla teleop-keys`）。

## デモはいくつ必要か

まず **スクリプトのデモ 50 個**で全工程を通します。いろいろな物体と位置から動ける方策には、もっと多く（ふつうは数百）を
見込んでください。人の操作（キー操作）のデモは、スクリプトにはないばらつきを加えます。

## 記録中の検査

`collect_dataset` は記録そのものを検査します。次の問題は数値には表れず、学習を台無しにするからです。

| 検査 | 失敗するとき |
|---|---|
| `camera_frames_updated` | カメラの画像が 2 つの step の間で変わらない（止まったカメラ） |
| `first_frame_fresh` | エピソードの最初の画像に、前のエピソードの場面が残っている |
| `label_replay` | 記録した行動だけで再生しても、記録した動きにならない |

失敗したときの対処は [トラブルシューティング](../troubleshooting.md) を見てください。
