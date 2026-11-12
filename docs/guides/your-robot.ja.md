# 自分のロボットを使う

ロボットの URDF から、シミュレーションが動くところまでの手順です。例として **FANUC CRX-3iA** を使います。FANUC は
URDF を公開していて、多くの産業用ロボットと同じく CRX には**グリッパが付いていません**。そこで、グリッパを取り付ける方法と、
別のグリッパに付け替える方法も合わせて説明します。手順はどの URDF でも同じです。まだなら先に Franka の
[チュートリアル](../tutorial.md) を通してください。うまくいったときの様子が分かります。

<figure markdown="span">
  ![CRX-3iA の手先・左・右のカメラ（エピソードの開始時）](../assets/crx3ia_cameras.png){ width="900" }
  <figcaption>NVIDIA のアセットライブラリの Robotiq 2F-85 を付けた FANUC CRX-3iA を、手先・左・右のカメラで見たところ。
  腕は FANUC の URDF から、グリッパは urdf2vla が取り付けたものです。</figcaption>
</figure>

## 1. URDF を用意する

=== "FANUC CRX（この例）"

    FANUC はロボットの記述を [FANUC-CORPORATION/fanuc_description](https://github.com/FANUC-CORPORATION/fanuc_description)
    （Apache-2.0）で xacro 形式で公開しています。例のスクリプトが、版を固定してダウンロードし、CRX-3iA を URDF に展開します。
    `xacro` が必要です（ROS 2 のもの、または `pip install xacro`）。

    ```bash
    source /opt/ros/jazzy/setup.bash      # xacro
    bash examples/crx3ia/fetch_urdf.sh    # → examples/crx3ia/fanuc_description/crx3ia.urdf
    ```

    ほかの CRX も同じように展開できます: `bash examples/crx3ia/fetch_urdf.sh crx10ia_l`（ほかに `crx5ia`、`crx10ia`、
    `crx10ia_lp`、`crx20ia_l`、`crx30ia`）。urdf2vla で確かめているのは CRX-3iA です。

=== "自分の URDF"

    urdf2vla は URDF から読めることはすべて読みます。始める前に次を確かめてください。

    - **メッシュが見つかること。** `package://my_robot_description/meshes/...` は、URDF の親フォルダと
      `robot.package_paths` に書いたフォルダから探します。別のマシンの絶対パス（`file:///root/ws/install/...`）は、
      同じフォルダの下からパスの末尾が一致するものを探します。リポジトリの外にあるメッシュは、コンテナから読めるように
      プロジェクトに自動でコピーします。腕と、残すグリッパのメッシュは見つかる必要があります。手首のカメラやその
      取付金具などの部品は、見つからなければ警告を出して形なしで取り込みます。
    - **関節の可動範囲とトルクが実際の値であること。** すべての関節のトルクが同じ値だったり、可動範囲のない関節が
      あったりすると警告します（CAD から書き出した URDF には仮の値がよく入っています）。トルクは URDF を編集せずに
      上書きできます（下記）。
    - **グリッパがあること。** URDF に入っているか、[NVIDIA のアセットライブラリから取り付ける](#simready) ものです。
      いま対応しているのは、2 本指のグリッパの付いた単腕・双腕のロボットです（[双腕ロボット](#双腕ロボット)）。

    xacro は先に展開します: `xacro my_robot.urdf.xacro -o my_robot.urdf`

    FANUC CRX-3iA と Franka のほかに、次の 7 軸アームも、公開の URDF からこのページの手順でデモの記録まで進めることを
    確かめています: **KUKA LBR iiwa 14**（ROS-Industrial の `kuka_experimental`、腕だけ。`gripper: {simready: robotiq_2f_85}`
    の 1 行）と **Kinova Gen3**（Kinova の `ros2_kortex` の `gen3_2f85.urdf`。付いている Robotiq を同じ 1 行で付け替え）。

## 2. プロジェクトを作る

```bash
urdf2vla init projects/crx3ia --from examples/crx3ia/project.yaml
```

`projects/crx3ia/project.yaml` ができます。編集するのはこのファイルだけです。urdf2vla が作るものは、同じフォルダの
`generated/`、`build/`、`snapshots/`、`logs/` に入ります。これらは手で編集しないでください。

```yaml title="projects/crx3ia/project.yaml"
name: crx3ia

robot:
  urdf: ../../examples/crx3ia/fanuc_description/crx3ia.urdf
  gripper: {simready: robotiq_2f_85, mount_link: fanuc_flange}   # グリッパを取り付ける（3 章）

cameras:          # DROID の配置: 手先 + 左右
  - preset: wrist
  - preset: left
  - preset: right

overrides:        # generated/ を編集せずに、生成された値を変える
  robot: {}
  task: {}
  cameras: {}
```

ほかのロボットは URDF から始めます（`urdf2vla init projects/myrobot --urdf /path/to/my_robot.urdf`）。分かっていることは
`robot:` の下に書いておくと、質問されません。

```yaml
robot:
  urdf: /path/to/my_robot.urdf
  # end_effector: tool0
  # gripper_joints: [finger_left, finger_right]
  # gripper_open_position: upper        # 指が開くのは関節の lower / upper どちらの限界か
  # default_pose: {joint1: 0.0, joint2: -0.6}
  # effort_limits: {"joint[1-6]": 50.0} # N·m（関節名の正規表現 → 値）
  # base_pose: {pos: [0, 0, 0], rot_xyzw: [0, 0, 0, 1]}
  # package_paths: [../my_ws/src]
```

## 3. グリッパを取り付ける・付け替える {#simready}

工程を実行する前に決めます。グリッパのない腕には取り付けが必要で、最初の工程がグリッパについて質問するからです。
何を掴めるかはグリッパで決まるので、いちばんよく変える部分でもあります。urdf2vla は、**NVIDIA のアセットライブラリ**
（Isaac Sim に付属の、物理が設定済みのロボットとグリッパのモデル）のグリッパを、`project.yaml` の 1 行でどの腕にも取り付けられます。
CRX-3iA の例にはこの 1 行がすでに入っているので、先に[工程の実行](#run)へ進み、付け替えるときにここへ戻ってきても構いません。

<figure markdown="span">
  ![Isaac Sim のアセットライブラリのグリッパ](../assets/simready_grippers.png){ width="900" }
  <figcaption>NVIDIA の Isaac Sim のアセットライブラリにあるグリッパの一部（NVIDIA のサムネイル）。</figcaption>
</figure>

<small>NVIDIA Isaac Sim 6.2 のアセットライブラリのサムネイル。Robotiq 2F-85、SCHUNK EGK 25・EGU 50 のアセットは
CC BY 4.0、Robotiq 2F-140・Hand-E は BSD-3-Clause（© 2013 ROS-Industrial）。詳細は
[THIRD_PARTY_NOTICES.txt](../assets/THIRD_PARTY_NOTICES.txt)。</small>

### 使うとき

- **腕にグリッパがない**とき（FANUC の URDF など）。
- **URDF のグリッパがシミュレーションで物体を保持できない**とき。閉リンクが簡略化されている、mimic 関節がうまく変換されない、
  可動範囲が効かない、などです。症状は「開閉はできるが物体を保持できない」グリッパで、urdf2vla の `joint_limits_hold` と
  `grasp_hold` の検査が失敗します。
- **別のグリッパを試したい**とき。大きな物体を掴むために開きの広いもの、実機に付けているものなど。

### `project.yaml` の 1 行

```yaml
robot:
  gripper: {simready: robotiq_2f_85, mount_link: fanuc_flange}
```

| `simready:` | グリッパ | 開き | 長さ（フランジから指先） | 使える物体（CRX-3iA） | アセットのライセンス |
|---|---|---|---|---|---|
| `robotiq_2f_85` | Robotiq 2F-85 | 85 mm | 14.8 cm | りんご・ライム・洋梨・キューブ（スクリプトの記録は 12 回中 10 回成功） | CC BY 4.0 |
| `ufactory_xarm` | UFactory xArm Gripper | 88 mm | 16.2 cm | ライム（記録は 5 回中 5 回成功）。りんごと洋梨は抜ける。キューブは保持できるが、長い指が台に当たって掴む高さまで下ろせない | 記載なし（アセットのフォルダにライセンスのファイルがない） |

値は各アセットで測ったもので、`src/urdf2vla/grippers.py` にあります。上の図のほかのグリッパは、まだ対応していません。
Robotiq 2F-140 は閉リンクがこの設定ではばらけ、Hand-E は 2 つの顎を別々に駆動し、SCHUNK のグリッパは物体の幅より
開きが小さいためです。

### 付け替えの手順

キーを変えて、`configure_robot` からやり直します。グリッパによって変わるものは、すべて計算し直されます。

```bash
# robot.gripper.simready: ufactory_xarm
urdf2vla configure_robot  --project projects/crx3ia   # 把持点（TCP）の位置、接近方向と指の向き
urdf2vla configure_task   --project projects/crx3ia   # 指の間に入る物体、台の位置
urdf2vla configure_camera --project projects/crx3ia   # 手先カメラは新しいグリッパに合わせて付く
urdf2vla import_robot     --project projects/crx3ia   # Isaac Sim の Robot Assembler でグリッパを取り付ける
urdf2vla launch_sim       --project projects/crx3ia   # 開閉と、各物体を保持できるかを検査する
```

| 変わるもの | 行う工程 | 2F-85 を xArm Gripper にしたとき |
|---|---|---|
| 把持点（TCP）、接近方向と指の向き | `configure_robot` | 把持点が指先から 1.2 cm ではなく 2 cm になる（xArm は指の腹が長い） |
| グリッパに入らない幅の物体を、警告を出して除く | `configure_task` | なし（4 つの物体はすべて指の間に入る） |
| 手先を真下に向けて作業域に届かなければ、台を手前に寄せる | `configure_task` | 台を 10 cm ではなく 12 cm 手前に寄せる |
| 腕の USD にグリッパを取り付ける（アセットは初回にダウンロード） | `import_robot` | xArm Gripper のアセットをダウンロードして取り付ける |
| 把持力・ソルバーの反復回数と、開閉・各物体の保持の検査 | `import_robot`、`launch_sim` | りんごと洋梨の `grasp_hold` が失敗する |

`grasp_hold` が失敗した物体はそのグリッパで保持できない物体、`build/collect_report.yaml` の試行で失敗が続く物体は
スクリプトの記録で掴めない物体です。それを除いて、`configure_task` と `launch_sim` をもう一度実行します。

```yaml
task:
  objects: [lime]
```

<figure markdown="span">
  ![2 種類のグリッパを付けた CRX-3iA](../assets/crx3ia_grippers.png){ width="900" }
  <figcaption>同じ CRX-3iA に Robotiq 2F-85 と UFactory xArm Gripper を付けたところ（<code>launch_sim</code> の開始時）。
  2 つのプロジェクトで違うのは <code>robot.gripper.simready</code>（と、xArm では物体のリスト）だけです。</figcaption>
</figure>

### グリッパを付ける場所（`mount_link`）

グリッパのない腕では、`mount_link` は書かなくてかまいません。urdf2vla は腕の先のいちばん外側のフレーム（`tool0` や FANUC の
`fanuc_flange` などのフランジ・ツールのフレーム）に、指がフランジ面から外へ伸びる向き（最後の関節の軸のフランジ側）で付けます。
どのフレームに付けたかと、ほかの候補は `configure_robot` が表示します。別のフレームにするときは `mount_link` に書きます。
どのフレームを選んでも指はフランジ面から外へ伸びます（+z ではなく +x が外向きのフレーム、たとえば FANUC の `flange` でも）。

取り付けた様子がおかしければ、`launch_sim` の snapshot ですぐ分かります。

### URDF にあるグリッパを置き換える

URDF にグリッパが入っているなら、`gripper: {simready: robotiq_2f_85}` だけを書きます。`configure_robot` が URDF のグリッパの
link を外し、同じ場所に、接近方向と指の向きを元のグリッパに合わせて新しいグリッパを付けます。別の場所に付けるときだけ
`mount_link` を書きます。外すグリッパのメッシュは見つからなくても構いません。

### グリッパを追加する

アセットライブラリのグリッパは、`src/urdf2vla/grippers.py` に 1 項目を足すと使えます。アセットのパス、駆動する指の関節と
その開閉の位置、それに追従する関節、接近方向と指の向き、指先までの距離、開きです。値はシミュレーションのコンテナで
プローブを使って測ります（`docker exec` を含む全体のコマンドは `src/urdf2vla/sim/gripper_probe.py` の冒頭にあります）。

```bash
python -m urdf2vla.sim.gripper_probe Isaac/Robots/Robotiq/Hand-E/Robotiq_Hand_E_edit.usd --out hand_e.yaml
```

アセットの関節（種類、可動範囲、駆動、どの関節がどれに追従するか）と、グリッパを開いた状態での各指の衝突形状の位置を
出力します。そのグリッパを使うプロジェクトで `import_robot` と `launch_sim` を実行すれば、値が正しいかを検査が教えてくれます。
丸い物体が指先から抜けるなら、把持点（`grasp_depth`）を奥にします。ただし、低い物体まで下ろしたときに指先が台に
当たらない範囲にします。

## 4. 設定の工程を実行し、質問に答える {#run}

```bash
urdf2vla inspect_robot    --project projects/crx3ia
urdf2vla configure_robot  --project projects/crx3ia
urdf2vla configure_task   --project projects/crx3ia
urdf2vla configure_camera --project projects/crx3ia
urdf2vla import_robot     --project projects/crx3ia
```

各コマンドは結果を JSON で出力します。`"status"` が次にすることを示します。

| `status` | 終了コード | 意味 |
|---|---|---|
| `ok` | 0 | 完了。`next` が次の工程。`warnings` があれば読んでください。 |
| `needs_input` | 2 | ロボットについての事実が必要です。`questions` に答えてから、`next` の工程を実行します。 |
| `error` | 1 | 工程か検査が失敗しました。`errors` がどれかを示します。`urdf2vla diagnose --project ...` を実行し、[トラブルシューティング](../troubleshooting.md) を見てください。 |

CRX-3iA では次のようになります。

- **`configure_robot`** — 「Robotiq 2F-85 を `fanuc_flange` に取り付けます」。手先の link や指の開く向きの質問は
  出ません。グリッパの形はアセットから分かるからです。
- **`configure_task`** — 「台を 10 cm 手前に寄せました」。CRX-3iA は小さく、グリッパを真下に向けるとその長さの分だけ
  手首が高くなります。urdf2vla は、手先を真下に向けたまま作業域の全体に届くかを逆運動学（IK）で確かめ、届くまで台を手前に寄せます。
- **`import_robot`** — `needs_input`。FANUC の URDF のゼロ姿勢は手先が台の方を向かないので、IK で求めた初期姿勢を提案し、
  `robot.default_pose` を質問します。

コマンドで答え、`next` の工程を実行して続けます。

```bash
urdf2vla answer --project projects/crx3ia robot.default_pose=suggested
urdf2vla configure_robot --project projects/crx3ia
urdf2vla import_robot    --project projects/crx3ia
urdf2vla launch_sim      --project projects/crx3ia
```

または **ブラウザでロボットを見ながら答えます**。[`urdf2vla view`](view.md) は候補を 3D で表示し、答えを同じファイル
（`answers.yaml`）に保存します。

<figure markdown="span">
  ![CRX-3iA の urdf2vla view の質問の欄](../assets/view_questions_crx3ia.png){ width="900" }
  <figcaption><code>urdf2vla view</code> での CRX-3iA の初期姿勢の質問。<b>提案の姿勢を見る</b> で、<code>suggested</code>
  と答える前にその姿勢を確かめられます。灰色の箱は取り付けたグリッパの代わりの表示です（view はグリッパのアセットを読みません）。</figcaption>
</figure>

### 質問される可能性のあること

| キー | 答えるもの | 質問されるとき |
|---|---|---|
| `arms.<i>.ee_link` | ロボットの手先の link | 手先になりうる link が複数あるとき（双腕は腕ごとに質問） |
| `grippers.<i>.open_position` | `lower` / `upper` | 回転する指の関節で、開く向きが分からないとき |
| `grippers.<i>.open_close` | `[開, 閉]` の角度 [rad] | 指の関節に可動範囲がないとき |
| `robot.gripper_joints` | 指の関節名のリスト | グリッパが自動で見つからないとき |
| `robot.front_axis` | `+x` / `+y` / `-x` / `-y` | ロボットの正面が +x でないとき（+x を向くように回して置きます） |
| `joints.<name>.role` | `fixed_at_reset` / `passive` | 腕でもグリッパでもない可動関節があるとき |
| `robot.default_pose` | `suggested` / `keep` / `{関節: 値, …}` | 取り込み後、手先が台の方（下）を向いていないとき。`suggested` は `configure_task` が決めた作業の構えの位置で手先が真下を向く IK の解 |

## 5. 結果を確かめ、記録する

`launch_sim` は短い試運転の最初と最後に各カメラが見たものを `snapshots/` に保存し、記録に時間を使う前に物理の検査
（物体を掴むことを含む）をします。CRX-3iA では 21 の検査がすべて通り、りんご・ライム・洋梨・キューブの `grasp_hold`
も通ります。あとはチュートリアルと同じように記録します。

```bash
urdf2vla configure_vla_interface --project projects/crx3ia
urdf2vla setup_teleoperation     --project projects/crx3ia
urdf2vla collect_dataset         --project projects/crx3ia --device scripted --num-demos 10
```

手元では、スクリプトが 12 回の試行で 10 個のデモを記録し、変換したデータセットからの再生は 4 回すべて成功しました
（`convert_dataset` の後に `evaluate_vla --replay --episodes 4`）。

<figure markdown="span">
  ![記録した CRX-3iA のエピソードの再生](../assets/crx3ia_replay.gif){ width="900" }
  <figcaption>スクリプトで記録した CRX-3iA のデモを、変換したデータセットから再生したところ（<code>evaluate_vla --replay</code>）。</figcaption>
</figure>

## 6. 物体を選び、場面とカメラを調整する {#objects}

既定では、エピソードごとにりんご・ライム・洋梨・色付きの立方体のどれか 1 つを台に置き、ロボットはそれを箱に
入れます。ほかの物体は `project.yaml` で選び、`configure_task` と **`launch_sim`** をもう一度実行します（`launch_sim` が各物体の
落ち着く向きを測り、エピソードはそれを使います）。

```yaml
task:
  objects: [lime, color_cube, rubiks_cube]   # エピソードごとに 1 つ。指示文でその名前を言う
  container: klt_bin                          # トレイなら plastic_tray、平らな緑の置き場所なら "disc"
```

| キー | 物体 | 大きさ (cm) | 出所 |
|---|---|---|---|
| `rubiks_cube` | ルービックキューブ（元のデータセットの利用条件を確かめていないので既定には入れない） | 5.8 × 5.8 × 5.8 | Isaac Lab Arena の物体ライブラリ（HOT3D 由来） |
| `apple` | りんご（70 % に縮小） | 5.4 × 5.7 × 6.7 | NVIDIA SimReady |
| `lime` | ライム | 6.4 × 7.5 × 6.1 | NVIDIA SimReady |
| `pear` | 洋梨 | 6.1 × 13.4 × 6.2 | NVIDIA SimReady |
| `color_cube` | 色付きの立方体 | 5.0 × 5.0 × 5.0 | Isaac Sim の props |
| `cube` | 赤い立方体（プリミティブ、ダウンロード不要） | 4.5 × 4.5 × 4.5 | — |
| `klt_bin`（容器、既定） | 箱 | 19.8 × 29.7 × 14.6 | Isaac Sim の props（工場の KLT の部品箱） |
| `plastic_tray`（容器） | トレイ | 25.6 × 27.0 × 2.1 | NVIDIA SimReady |

別の物体のリストで記録したデータセットは、新しいリストでは再生できません（エピソードはリストの何番目を使ったかを記録します）。
物体のリストを導入する前の記録を再生するには `task: {objects: [cube], container: disc}` にします。

グリッパの開きより幅の広い物体は、警告を出して除きます（Franka は 8 cm。ほかは上のグリッパの表）。
アセットは初回に NVIDIA のアセットサーバからダウンロードします。測った大きさのカタログは `src/urdf2vla/objects.py` です。

台・物体を置く範囲・箱は腕の届く範囲から配置し、単腕のロボットでは手先を真下に向けて届くかを IK で確かめます。
動かすには、生成されたキーを `overrides.task`（`generated/task.yaml` と同じ構造）に書き、`configure_task` をもう一度実行します。

```yaml
overrides:
  task:
    target: {pos: [0.45, -0.18]}
    instruction: "place the {object} into the {target}"
    table: {material: walnut}        # 台の見た目: oak（既定）、walnut、plain（単色）
```

カメラの既定は [DROID](https://droid-dataset.github.io/) の配置（手先カメラと、台を見下ろす左右のカメラ、256×256）です。
双腕では腕ごとに手先カメラが付くので、既定は 4 台になります。ほかに `front`（正面）と `side`（横）のプリセットがあります。
実機のカメラに合わせるなら、明示的に書きます。

```yaml
cameras:
  - preset: wrist
  - {name: head_cam, mount: link, parent_link: head_link, pos: [0, 0, 0], rot_xyzw: [0, 0, 0, 1],
     convention: ros, width: 640, height: 480, intrinsic_matrix: [600, 0, 320, 0, 600, 240, 0, 0, 1]}
```

四元数は常に `[x, y, z, w]` です。

## 双腕ロボット

2 本の arm の連鎖を別々に検出し、設定・シミュレーション・学習まで別々に保ちます。ロボット固有の Python コードは要りません。
腕ごとに関節・IK の剛体・TCP・接近方向・指の開閉方向と、任意の手先カメラを作ります。VLA の state / action は
「右腕の関節、左腕の関節、右グリッパ、左グリッパ」の順です（検出順。上の例では right が先）。

台は左右の肩の中央に置き、物体は台の左半分、箱は右半分に置きます。内蔵の scripted 操作は、物体に近い方の腕で把持し、
もう一方の腕は初期姿勢のままグリッパを開いて保持します。この配置では毎回同じ腕になるので、scripted のデモで動くのは
片方の腕だけです（もう一方の腕の関節とグリッパも毎フレーム記録します）。`launch_sim` の把持検査はグリッパごとに実行します。

Franka を 2 本並べたロボットで、取り込み・検査・scripted の収集・変換・replay の評価まで確かめています。2 本の腕が
共通の root link から出ているときは、その link に `<inertial>` を書いてください。質量がないと importer が腕ごとに別の
articulation にし、`import_robot` が `Expected 1 prims at '/World/Robot', found 2` で止まります。

同梱の Dual Scorpion の例では、各腕の 8 番目の関節（joint7）をグリッパにし、joint0〜6 を腕にします。

```yaml
robot:
  urdf: /path/to/dual_scorpion_Full_urdf/urdf/scorpion.urdf
  front_axis: +y
  gripper_joints: [joint7_R, joint7_L]
  gripper_open_close: [[-0.8, 0.0], [-0.8, 0.0]]  # 仮値。開閉方向は snapshot で確認する
  effort_limits: {"joint[0-6]_[RL]": 60.0}         # 仮値（下記）
task:
  objects: [color_cube]
```

Dual Scorpion の URDF は全関節が `effort: 1.0` で、元プロジェクトも仮値としています。シミュレーションで把持を判断する前に、
実機のモータ値で `robot.effort_limits` を上書きしてください。10 N·m では止まっている側の腕が下がり、`setup_teleoperation` に
失敗します。例では腕を 60 N·m にし、グリッパは URDF の値のままです。回転する顎では果物を保持できないので、例は立方体だけです。
またグリッパの開き幅は連続回転の 1 関節からは決まらないので、物体が開きに入るかの自動スクリーニングは行われません。

この設定で Dual Scorpion は `import_robot`・`launch_sim`・`setup_teleoperation` を通りますが、scripted の収集はまだ
成功しません（8 回中 0 回。物体の真上の点の手前で手先が止まります）。物体と箱を片方の腕の前に寄せると真上までは
行きますが、下降の途中で止まります（4 回中 0 回）。原因はまだ特定できていません。

同梱の OpenArm の例（`examples/openarm`、Enactic OpenArm v2.0）は最後まで通りますが、配置は手で決めています。OpenArm の
グリッパは前腕の延長上にあり、手首の曲げは最大 90° です。手先が真下を向くのは前腕が水平か下向きのときだけなので、上から
掴めるのは肩からおよそ 0.3 m 以上下に限られます。自動配置の台はそれより高く、そこに真下向きで届く姿勢は上腕を肩より上に
上げるものしかありません。そこで例では次のようにしています。

- 台を 0.17 m に下げ、縁 14.6 cm の箱の代わりに低い `plastic_tray` を使う
- 物体とトレイを左腕の側に置く（腕は体の中心線をほとんど越えられない。joint2 の内側への回転は最大 10°）
- scripted 操作の近づく高さを 8 cm にする（`overrides.teleop.approach_height`）
- 肘を下げた初期姿勢を `robot.default_pose` に書く（ホストの IK で求めた）
- 指のトルクを 2 N·m に下げる。URDF の 7 N·m では、回転する指が立方体を片方の指に押し付け、`launch_sim` の把持検査が
  立方体を支えるのをやめると滑り落ちる
- 物理ソルバの位置の反復回数を 64 にする（`overrides.robot.asset.solver_position_iterations`）。既定の 32 では左手の
  把持検査にわずかに失敗する（立方体が TCP から 2.2 cm ずれる。許容は 2 cm）

この設定で OpenArm はすべての検査を通り、scripted の収集は 10 回中 8 回成功（毎回左腕）、replay の評価は 8 回中 8 回成功です。
失敗した 2 回はどちらも物体が置く範囲のトレイ側の端にあり、手先が把持の高さの 6 mm 上で止まりました。開いた指がトレイの縁に
当たっているとみられます。

## いまの制限

- 腕は 1 本か 2 本です。3 本以上の arm 連鎖はシミュレーションの Embodiment では対応していません。
- 双腕: scripted 操作はエピソードごとに 1 本の腕を使います（既定の配置では毎回同じ腕）。`generate_mimic` と
  `evaluate_vla --droid` は単腕だけです。
- タスクは 1 つ: 物体を掴んで容器に入れる（または平らな置き場所に置く）Pick & Place。物体は `src/urdf2vla/objects.py` のカタログから選びます。
- グリッパ: 2 本指の平行グリッパと、片側の顎だけが動くグリッパ。吸着や多指ハンドには対応していません。
- SimReady のグリッパへの付け替え（`robot.gripper.simready`）は、いまは単腕のロボットだけです。
