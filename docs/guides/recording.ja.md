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
| 画像 | 全カメラ（既定: 手先・左・右）、256×256 の RGB、制御周期（既定 20 Hz）ごと |
| 状態 | 腕の関節角 + グリッパの値 1 つ（0 = 閉、1 = 開） |
| 行動 | 腕の関節の目標 + グリッパの指令（制御器に出した指令） |
| タスクの文 | そのエピソードの物体の名前を言う指示文。例: "put the apple in the bin" |

エピソードごとに、タスクの物体の 1 つを台のランダムな位置に置きます。物体が箱の中にあり、グリッパが離した状態が
数 step 続くと成功です。失敗したエピソードは捨てるので、
データセットには良いデモだけが入ります。記録は `datasets/<名前>.hdf5` に保存します（既定は `demos`。`--dataset` で変更）。

## 3 つの記録のしかた

=== "スクリプト（人は不要）"

    スクリプトの制御器が物体を掴んで箱に入れます。物体とその位置はエピソードごとにランダムです。
    データセットを作り、全工程を確かめる一番早い方法です。

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
| ++k++ | グリッパを開く / 閉じる |
| ++r++ | このエピソードを捨ててやり直す |
| ++esc++ | 記録を終える |

手先は少しずつ動きます（1 制御 step あたり 8 mm と 0.04 rad）。遅すぎる・速すぎるときは `project.yaml` の
`overrides.teleop` に `pos_step` と `rot_step` を書きます。物体を箱に置いた時点で、エピソードは自動で保存されます。エピソードの始めにその指示文を表示します。

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
