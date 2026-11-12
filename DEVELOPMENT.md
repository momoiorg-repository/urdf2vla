# urdf2vla 開発者向けメモ

> 使い方のドキュメントは [docs/](docs/)（サイト: `mkdocs serve` または GitHub Pages）にあります。このファイルは
> 開発者向けに、設計の考え方・Isaac Sim でのハマりどころとその対処・検証状況・今後の予定をまとめたものです。


ロボットの **URDF + カメラ情報 + 少数の追加情報** から、Isaac Sim 上に VLA 開発環境（Pick & Place）を作るツールです。
Isaac Sim / Omniverse / VLA に詳しくないロボット開発者が、自分のロボットで VLA 開発を始められるようにすることを目的にしています。

```
URDF → ロボット解析 → 必要な情報だけ質問 → Isaac Sim へ導入 → カメラ配置 → シミュレーション起動   ← Phase 1（実装済み）
     → Observation / Action 定義 → Teleoperation → 軌道記録                                  ← Phase 2（実装済み）
     → GR00T 用データ変換 → GR00T 学習（ジョブ生成）                                          ← Phase 3（実装済み）
     → 推論サーバ → Isaac Sim 上で評価                                                        ← Phase 4（実装済み）
```

## 考え方

- **ロボットの事実は 1 か所に置く。** End Effector・制御関節・グリッパ（関節、mimic、開閉値、データセット上の値の定義）・初期姿勢・カメラは
  `generated/robot_config.yaml` / `cameras.yaml` に集約し、シミュレーション環境・teleop・データ変換・評価はすべてそこから派生させます。
  ロボット固有のコードは書きません（Franka と CRX-3iA + Robotiq を同じコードで動かしています）。
- **推測しない。** URDF から一意に決まらない重要な設定（例: End Effector の候補が複数ある、回転関節の指がどちらで開くか）は、候補を添えて質問します。
- **Skill を順番に実行する。** 各工程は独立した Skill（CLI サブコマンド）で、単体でも実行できます。
  Agent（`.agents/skills/urdf2vla/SKILL.md`。Codex はここを、Claude Code は同じファイルへのリンク `.claude/skills/urdf2vla/SKILL.md` を読む）は
  Skill を組み合わせる Orchestrator で、設定を自由生成しません。環境の問題は最初に `urdf2vla doctor` で調べます。
- **生成した設定はユーザが確認・編集できる。** 生成値には出所（`urdf` / `user` / `inferred` / `default`）が付きます。
  変更は `project.yaml` の `overrides:` に書くと、次回の生成で反映されます。
- **シミュレーション環境は Isaac Lab Arena で組み立てる。** URDF から作ったロボットを Arena の Embodiment に、
  台・物体・置き場所を Arena の Scene に、Pick & Place を Arena の Task にして `ArenaEnvBuilder` で環境を作ります
  （`src/urdf2vla/sim/arena/`）。Arena には URDF の読み込み・ロボットの自動解析・scripted 収集がないので、そこは urdf2vla が担います。

## 動作環境

| | バージョン |
|---|---|
| Isaac Lab | 3.0.0（Isaac Lab Arena `main` の b9e37c5 が固定している submodule） |
| Isaac Sim | 6.2.0（Python 3.12） |
| Isaac Lab Arena | `main` b9e37c5（2026-11-04）。リリース前のブランチなのでコミットで固定 |
| GR00T | N1.7（Isaac-GR00T 23ace64）。変換・学習・推論サーバは別コンテナ（Isaac Lab のバージョンとは独立、ZMQ で通信） |

Arena の GR00T 連携（変換・評価）は N1.6 で、変換が 1 カメラのみなので使っていません（urdf2vla は DROID と同じ手先 + 左右のカメラ、双腕は手先 2 台で計 4 台、N1.7）。

## 必要なもの

- NVIDIA GPU（RTX 5080 16GB で検証）とドライバ 580 以降、Docker（`--gpus` が使えること）、空きディスク 100GB 程度
- ホストの Python 3.10+ と [uv](https://docs.astral.sh/uv/)

## クイックスタート

```bash
cd urdf2vla
uv venv .venv && uv pip install -e '.[dev]'
source .venv/bin/activate
bash docker/build.sh                    # 初回だけ: Isaac Lab Arena（Isaac Lab 3.0 / Isaac Sim 6.2）と Isaac-GR00T を
                                        #   external/ に取得し、image urdf2vla/isaaclab_arena:b9e37c5（シミュレーション）と
                                        #   urdf2vla/gr00t:23ace64（変換・学習・推論サーバ）を作る（合わせて 1〜2 時間、100GB 程度）
bash docker/run.sh                      # シミュレーション用コンテナ urdf2vla-sim を作成・起動（既存コンテナには触れない）

# 例: Franka
urdf2vla init projects/franka --from examples/franka/project.yaml
urdf2vla inspect_robot   --project projects/franka
urdf2vla configure_robot --project projects/franka
urdf2vla configure_task  --project projects/franka
urdf2vla configure_camera --project projects/franka
urdf2vla import_robot    --project projects/franka   # USD 変換 + 検査（初回は 1〜2 分）
urdf2vla launch_sim      --project projects/franka   # 環境起動 + snapshots/*.png
urdf2vla launch_sim      --project projects/franka --livestream   # WebRTC で GUI 表示（Ctrl-C で終了。Isaac Lab 3.0 版では未検証）
```

Phase 2（データ収集）:

```bash
urdf2vla configure_vla_interface --project projects/franka   # state / action / 画像の仕様
urdf2vla setup_teleoperation     --project projects/franka   # teleop 設定 + 手先が指令どおり動くかの検査
urdf2vla collect_dataset --project projects/franka --device scripted --num-demos 50   # 自動操作で収集（人がいなくてよい）
urdf2vla collect_dataset --project projects/franka --device socket --num-demos 10     # 端末のキー入力で操作
urdf2vla teleop-keys                                                                 #   ↑ を起動したら別の端末で実行
urdf2vla collect_dataset --project projects/franka --device keyboard --livestream     # WebRTC の画面でキー操作
```

Phase 3〜4（GR00T）:

```bash
bash docker/run.sh gr00t                                                     # GR00T 用コンテナ urdf2vla-gr00t
urdf2vla convert_dataset --project projects/franka --dataset demos          # → datasets/demos_gr00t（LeRobot v2.1 + modality + 統計）
urdf2vla train_vla       --project projects/franka --dataset demos          # GPU が 44GB 未満なら training/<job>.tar.gz を作る
urdf2vla evaluate_vla    --project projects/franka --replay --dataset demos # 学習前に推論サーバ〜行動の適用までを検証
urdf2vla evaluate_vla    --project projects/franka --model training/franka_demos/checkpoints/checkpoint-10000 --episodes 20
```

GR00T N1.7 の学習は 44GB 以上の GPU が必要です（LLM・画像エンコーダを凍結しても約 42GB）。`train_vla` は GPU を確認し、
足りなければ `training/<job>.tar.gz`（train.sh + データセット + modality config）を作ります。大きな GPU のマシンに
GR00T の image（`bash docker/build.sh gr00t`、または `docker save urdf2vla/gr00t:23ace64` で持っていく）を用意し、
train.sh の先頭に書いた `docker run ... bash /job/train.sh` で学習して、できた checkpoint を `evaluate_vla --model` で評価します
（Isaac-GR00T を直接入れた環境なら `GR00T_ROOT=/path/to/Isaac-GR00T GR00T_PYTHON=python bash train.sh`）。

自分のロボットなら `urdf2vla init projects/myrobot --urdf /path/to/robot.urdf` から始めます。
各コマンドは結果を JSON で出力します（`status`: `ok` / `needs_input` / `error`）。

### 質問が返ってきたとき（例: URDF のグリッパをそのまま使うロボット）

```bash
urdf2vla inspect_robot --project projects/myrobot
#  "status": "needs_input"
#  arms.0.ee_link: 候補は robotiq_85_base_link, end_effector です。どれを使いますか？
#  grippers.0.open_position: robotiq_85_left_knuckle_joint はどちらの限界で指が開きますか？ lower=0.0 / upper=0.8
urdf2vla answer --project projects/myrobot arms.0.ee_link=robotiq_85_base_link grippers.0.open_position=lower
urdf2vla configure_robot --project projects/myrobot
...
urdf2vla import_robot --project projects/myrobot
#  初期姿勢で手先が下を向いていない → IK で「台の上 20cm で真下を向く姿勢」を求めて質問
urdf2vla answer --project projects/myrobot robot.default_pose=suggested
```

### ブラウザで確かめる（urdf2vla view）

Omniverse の画面を開かずに、ロボット・把持点（TCP）・台と物体・カメラの位置と画角を 3D で確かめ、質問に答えられます。
URDF と urdf2vla の設定ファイルだけを読む（Isaac Sim は不要）ので、`inspect_robot` の直後から使えます。

```bash
uv pip install -e '.[view]'                     # 初回だけ（viser, yourdfpy）
urdf2vla view --project projects/myrobot         # → http://localhost:8080（別の PC からは表示される LAN のアドレス）
```

- 関節のスライダで姿勢を動かす。「初期姿勢」「グリッパを開く / 閉じる」で設定どおりに動くかを見る。衝突形状も表示できる
- TCP（把持点）の座標軸と、接近方向（青）・指の開く向き（橙）の矢印。End Effector の候補の link（質問の候補を選ぶと強調）
- 台・物体・置き場所、正面（+x）の矢印、カメラの視野（`launch_sim` の後は、その画像を視野の中に表示）
- 工程の状態と検査の結果、まだ答えていない質問（選んで「回答を保存」すると `answers.yaml` に記録）
- カメラの欄: 取り付け先（link / world）・位置と向き（3D のハンドル）・視野角・解像度を変え、「このカメラから見る」で確かめて
  「カメラを保存」（`answers.yaml` の `cameras`。`project.yaml` の `cameras` より優先し、configure_camera が使う）

### グリッパを SimReady アセットに付け替える（例: FANUC CRX-3iA + Robotiq 2F-85）

腕にグリッパがない（FANUC の URDF など）、または URDF のグリッパが Isaac Sim でうまく動かない（閉リンクの簡略化・
mimic の変換の問題など）ときは、NVIDIA の Isaac Sim のアセットライブラリのグリッパを付けられます。`project.yaml` に 1 行:

```yaml
robot:
  urdf: fanuc_description/crx3ia.urdf   # examples/crx3ia/fetch_urdf.sh（FANUC の公式, Apache-2.0）
  gripper: {simready: robotiq_2f_85, mount_link: fanuc_flange}   # 対応: robotiq_2f_85 / ufactory_xarm
```

- `configure_robot` が取付の位置・向きと TCP を決める。URDF にグリッパがあればその link を外し、同じ取付位置・向き
  （接近方向と指の開く向きを合わせる）に付ける。なければ腕の先の外向き（最後の関節の軸のうち、子孫のフレームがある側）に
  指が伸びる向きで付ける。`mount_link` を省くと、いちばん外側のフレーム（同じ位置なら +z が外向きのもの: tool0、
  FANUC の fanuc_flange）を選ぶ（`_flange_outward`）。グリッパと End Effector の質問は出ない。
- `configure_task` は、手先を真下に向けて作業域に届くかをホストの IK（`urdf2vla/urdf/reach.py`）で確かめ、届かなければ
  台を手前に寄せる（腕が短くグリッパが長いと、真下に向けた手首が高くなる。CRX-3iA は 2F-85 で 10 cm、xArm で 12 cm）。
- `import_robot` が腕を USD に変換し、Isaac Sim の Robot Assembler でグリッパを取り付ける（ロボットの USD の
  variant `Gripper=<キー>` として保存）。グリッパのアセットはネットから読むので、初回はネット接続が必要。
- 開閉・把持点・mimic の追従関節・把持力・ソルバーの反復回数は、アセットで測った値を
  `urdf2vla/grippers.py` に持つ。測るのは `python -m urdf2vla.sim.gripper_probe <USD>`（シミュレーションのコンテナで実行）。

### Agent から使う

Claude Code をこのリポジトリで起動し、「`/path/to/robot.urdf` で VLA 開発を始めたい」のように頼むと、
`urdf2vla` Skill が工程を順に実行し、必要なときだけ質問します。

## コマンド

| コマンド | 内容 |
|---|---|
| `urdf2vla skills [--json]` | Skill 一覧（入出力・前提つき） |
| `urdf2vla init DIR --urdf URDF` / `--from examples/*/project.yaml` | プロジェクト作成 |
| `urdf2vla status --project P` | 工程の状態と次に実行する Skill |
| `urdf2vla answer --project P key=value ...` | 質問への回答を `answers.yaml` に記録 |
| `urdf2vla show --project P robot_config` | 生成された設定を表示 |
| `urdf2vla diagnose --project P` | 失敗した Skill のログから原因と対処を推定 |
| `urdf2vla view --project P [--port 8080]` | ブラウザで 3D 表示（ロボット・TCP・台・カメラ）、質問への回答（`.[view]` が必要） |
| `urdf2vla <skill> --project P` | Skill を実行 |

| Skill | 実行場所 | 内容 |
|---|---|---|
| `inspect_robot` | host | link/joint・root・arm・gripper（mimic 含む）・End Effector 候補・メッシュの解決 |
| `configure_robot` | host | Robot configuration と正規化 URDF（`package://` 解決、空の `world` link 除去） |
| `configure_task` | host | 台・物体・置き場所を腕の長さから配置（プリミティブのみ、ネット上のアセット不要） |
| `configure_camera` | host | 既定は URDF のカメラ（なければ DROID と同じ `wrist` + `left` + `right`）。ほかに `front` / `side` / `urdf` プリセット、取付 link・位置姿勢・内部パラメータの明示指定、`urdf2vla view` で保存したカメラ |
| `import_robot` | sim | URDF→USD 変換、関節名・剛体・発散・初期姿勢追従・関節限界・グリッパ開閉・グリッパの可動範囲（関節トルクで押す）の検査、初期姿勢の IK 提案 |
| `launch_sim` | sim | Pick & Place 環境（Isaac Lab Arena で組み立てる）の起動、カメラ画像の保存、カメラ更新・取付位置・物体・発散・把持（物体を掴んで保持できるか、指がめり込まないか）の検査 |
| `configure_vla_interface` | host | VLA の state（arm 関節角 + グリッパのスカラー）・action（関節目標 + グリッパ）・画像の仕様 |
| `setup_teleoperation` | sim | teleop 設定（目標手先姿勢を IK で追従）と、手先が指令方向に指令量だけ動くかの検査 |
| `collect_dataset` | sim | scripted / socket / keyboard でデモを記録し HDF5 に保存（成功デモのみ）、カメラ更新・最初の画像・ラベル再生の検査 |
| `convert_dataset` | gr00t | HDF5 → GR00T（LeRobot v2.1）、`modality.json` と N1.7 の modality config・統計を生成、GR00T のローダと動画の検査 |
| `train_vla` | gr00t | GR00T N1.7 fine-tune ジョブ（引数の検査）。GPU が足りれば学習、足りなければ持ち出し用の一式 |
| `evaluate_vla` | host → gr00t + sim | GR00T 推論サーバを起動し、Isaac Sim 側のクライアントで成功率を測る（`--replay` で学習前の配管検証、`--video` でエピソードごとの動画） |

## プロジェクトの中身

```
projects/<name>/
  project.yaml   入力と上書き設定（ユーザが編集）      answers.yaml  質問への回答
  generated/     robot_metadata / robot_config / task / cameras（自動生成）
  build/         robot.urdf（正規化）, meshes/（コンテナから見えないメッシュのコピー）, usd/, import_report.yaml,
                 launch_report.yaml, eval_report.yaml, eval_videos/
  snapshots/     カメラ画像                            logs/  Skill ごとのログと結果 JSON
```

## 設計上の決めごと（ハマりどころの自動対処）

- 四元数は Isaac Lab 3.0 と同じ `(x, y, z, w)`。設定のキーは `rot_xyzw`（2.x 時代の `rot_wxyz` を書くと変換して警告する）。
  記録データ（HDF5）は `urdf2vla_format: 2`。2.x 版で記録したデータ（format 1）は変換を拒否する（記録し直す）。
- URDF の mimic（グリッパの従動関節）: Isaac Lab 3.0 の URDF importer は mimic を `NewtonMimicAPI` の拘束として書く。
  PhysX では Franka の +1 の直動指では効くが、CRX の Robotiq の左右 knuckle（multiplier -1）では leader が可動範囲を越えて
  回り続けた。urdf2vla は import_robot で拘束を無効にし、gripper spec の open / close（follower は multiplier から計算）で
  全関節を駆動する（Isaac Lab の Franka の設定と同じやり方）。
- URDF から変換した簡略版 Robotiq 2F-85（CRX-3iA の URDF に含まれる、内側リンクが fixed のもの）は、Isaac Sim 6.2 では
  左 knuckle の可動範囲と速度上限が効かない（関節トルクで押すと 0〜0.8 rad を越えて回る。右は効く。mimic の有無・下限値を
  変えても同じで、原因は特定していない）。把持すると左指が押し戻されて物体が逃げる。CRX の例は SimReady の Robotiq 2F-85 に
  付け替える（上記）。
- SimReady の Robotiq 2F-85 は PhysX の mimic 拘束（`PhysxMimicJointAPI`）で 5 つの関節が `finger_joint` に追従する。
  - ソルバーの位置反復が 8 回だと、物体を挟んだときに拘束が保てず掴めない。64 回にする（Isaac Lab Arena の DROID と同じ）。
  - アセットの把持力（26 N·m）のままだと、物体を挟んだときに mimic の反力が手首を押し続け（J4・J5 に約 35 N·m）、手先が目標から
    4cm ずれたまま運べない。0.1kg の物体には 8 N·m で十分で、手首への反力も 1〜2 N·m になる。
  - 指の link の原点はすべて base_link の原点にある（アセットの作り）。把持点は指の腹の衝突形状から測った。
- 環境を作った直後は描画が立ち上がっておらず、reset の再描画だけでは最初のエピソードの最初の画像が reset 前の場面のまま残る
  （2.x 版では 2 回目以降のエピソードで起きていた）。環境を作ったら物理と描画を数 step 進めてから reset し直し、
  collect_dataset の `first_frame_fresh` 検査で最初の 2 フレームの差が続くフレームより極端に大きくないかを確かめる。
- 成功判定（置き場所の半径内・台の上・グリッパを開いた状態が N step 続く）は記録・評価のループ側で行う。Arena の成功条件は
  満たした瞬間に episode を終えて reset するので、N step の保持やデモの記録と合わない。Arena の Task は時間切れだけを終了条件にする。
- URDF のメッシュがコンテナから見えない場所にあれば、そのディレクトリごと `build/meshes/` にコピーして参照する。
  URDF に別マシン（コンテナ）の絶対パスが書かれていれば、末尾の部分パスで URDF の親ディレクトリと `package_paths` の下を探す。
- グリッパは「開閉できる」だけでは掴めるとは限らない（下の 2 つはどちらも開閉の検査を通った）。import_robot の
  `joint_limits_hold`（駆動を切って関節トルクで押し、可動範囲の中に留まるか）と、launch_sim の `grasp_hold`（指の間に
  物体を置いて閉じ、離しても落ちないか）・`grasp_opening`（直動の指の開きが物体の幅と合うか）で、データを集める前に見つける。
  URDF の簡略版 Robotiq は `joint_limits_hold` と `grasp_hold` に、反復 8 回の Franka は `grasp_opening` に失敗する。
- 物体はカタログ（`src/urdf2vla/objects.py`、SimReady と Arena のアセット。大きさは Isaac Sim で測った値）から選び、
  候補をすべて場面に生成してロボットの後ろの床に置いておき、エピソードの始めに 1 つを台に置く（`SimView.start_episode`）。
  指示文はエピソードごと（HDF5 の demo の attrs `language_instruction` / `object`、GR00T の `tasks.jsonl` に複数の task）。
  - 剛体の原点はアセットによって違う（りんごは中心、ルービックキューブは外接箱の中心から y に 2.9 cm ずれる、SimReady の
    多くは底）。カタログの値ではなく、生成した物体の USD から剛体の原点に対する外接箱の中心・底と生成時の向きを測って使う
    （測る前は、りんごが 4 cm 浮き、ルービックキューブは grasp_hold で指に食い込んで飛んだ）。
  - Isaac Sim 6.2 の SimReady は衝突形状と質量を variant `PhysX=Enabled` に持つ（Arena の SimReadyUsdObject の既定
    `Physics=physics` では衝突形状がない）。トレイは `kinematic_enabled` で台に固定する。
  - PhysX には転がり抵抗がないので、丸い果物（りんご・ライム）は浅いトレイ（縁 2.1 cm）の中で転がって外に出る。
    丸い物体だけ角速度の減衰 2.0 を付ける。箱の形の物体に付けると、掴んだときに指の面に沿って回り切れず指がめり込む
    （ルービックキューブで grasp_opening が 5.7 cm → 2.6 cm）。スクリプトの把持は、真上の位置合わせも 6 mm の精度にする
    （2 cm のずれのまま下降すると、指の開きに余裕のないりんごで指が物体の上に乗る）。
  - SimReady のりんごは直立では安定せず、腕を止めていても 2 秒ほどで横に倒れて 10 cm 転がる（角速度の減衰 0.05 でも 10 でも
    同じ軌跡。減衰では止まらない）。launch_sim で物体ごとにしばらく置いて落ち着いた向きと高さを測り（build/object_rest.json）、
    エピソードではその向き（＋鉛直軸まわりのランダムな回転）で置く。スクリプトの把持の高さは実際の中心の高さを使う。
  - 再生（replay の評価・label_replay）は、記録した「置いた姿勢（落ち着かせる前）」から記録と同じ手順で置き直す。落ち着いた
    後の姿勢から置き直すと、丸い物体は 2 回目の落ち着き方が違って 1〜2 cm 転がり、開ループの再生で指が外れた（洋梨・りんご）。
    これで tutorial の 10 エピソードの replay が 10/10。
  - Isaac Lab の rigid_props は instance（instanceable な prim の中身）を黙って飛ばす（SimReady のりんごの剛体は instance の中）。
    物体は instance を解いてから剛体に角速度の減衰を付ける（scene.py の _spawn_usd_damped）。
    ただし減衰が転がりを抑える効果は測っていない（りんごの倒れ方は減衰に関係しなかった。replay を直したのは落ち着いた向きと
    置いた姿勢からの再生）。減衰と instance を解く処理は残してあるが、効果がなければ外してよい。
  - 台の見た目は NVIDIA の木の MDL（Oak / Walnut）。UsdGeom.Cube には UV がなく、MDL を付けても色しか出ない（木目が出ない）
    ので、衝突用の箱は見えなくし、面ごとに UV を張ったメッシュを見た目として重ねる（scene.py の _spawn_wood_table）。
  - 既定の容器は KLT の部品箱（Isaac Sim の props）。剛体が instance の中にあるので、kinematic も instance を解いてから付ける
    （付かないと箱が動く）。容器の「底に置かれた」判定は底から 3 cm まで（深い箱の縁の上で持っている状態を成功にしない）。
  - 環境の時間切れ（Arena の Task）は記録・評価のループの上限より 10 秒長くする。同じだと最後の step で自動の reset が入り、
    物体が置き場に戻って失敗の記録（object_final）が意味を失う。
  - Isaac Lab の環境の作成が torch の乱数の種を設定し直すので、`--seed` は環境の作成の後で設定する（前は効いていなかった）。
- 推論サーバの後始末（`pkill -f "[u]rdf2vla.gr00t.server..."`）の括弧は、pkill を含む bash 自身にマッチしないため。
  名前を変えたときにこの括弧の形が置換から漏れ、サーバが残って次の評価がポートを取れなかった。
- articulation のソルバーの位置反復は既定 32 回。8 回（Isaac Lab の既定）だと、軽い物体を左右から挟んだときに接触を解き切れず、
  指が物体にめり込む（Franka で 45mm の立方体を持つと指の間が 23mm まで閉じた。衝突形状は正しく、動かない立方体では 22.5mm で
  止まる）。32 回で 44.8mm。SimReady の Robotiq 2F-85 は 64 回。
- カメラの既定は DROID（https://droid-dataset.github.io/）と同じ 3 台: 手先 + ロボットの左（+y）と右（-y）の高い位置から
  台の中央を見下ろす外部カメラ。DROID の外部カメラの配置（Franka の base から前 0.05 m・左右 0.57 m・上 0.66 m、焦点距離 2.1）を
  台の大きさで拡大縮小する（腕の大きさが違うロボットでも作業域が写る）。画像は正方形 256×256・画角 約 70°。
  双腕では手先カメラを腕ごとに付けるので 4 台（左右の手先 + 左右の外部）。
- URDF にカメラの frame があれば、そちらを既定にする（ロボット本来の観測。OpenArm は両手 + 頭の ZED Mini）。見つけ方は
  `urdf/cameras.py`: 名前（camera / cam / optical。mount・support など取付部品と、depth・infra・imu は除く）、fixed の連鎖の
  末端、機器ごとに 1 つ（optical > color > left）。向きは REP 103（optical は z 前方 = Isaac Lab の ros、ほかは x 前方 = world）。
  root までの関節に arm の関節があれば手先（最後の関節より先）・腕、なければ頭。視野角は Gazebo の horizontal_fov、なければ
  手 80°・頭 70°。足りない分（手のカメラのない腕・頭のカメラ）は DROID のプリセットで補う。project.yaml の雛形には cameras を
  書かない（書くと既定が効かない）。
- URDF のカメラの frame はカメラや手のメッシュの中にあることが多い（OpenArm の手のカメラは手のひらの 2 cm 内側で、画像が
  真っ黒になった）。URDF のカメラは手前 3 cm を描かない（`clip_near`）。
- launch_sim の `camera_mount` は、設定の向き（convention の約束）を光学 frame（ros）に直してから比べる（以前は ros 以外の
  明示指定で必ず失敗していた。world の約束の OpenArm の手のカメラで 120° のずれと出た）。
- `urdf2vla view` のカメラの欄は、すべてのカメラを光学 frame（viser と同じ OpenCV の約束）で扱い、動かしたものだけ取り付け先から
  見た姿勢の明示指定（ros）で保存する。触っていないものは元の preset のまま（台の配置が変われば追従する）。viser の get_render は
  手前 1 cm から描くので、筐体の中のカメラのプレビューには筐体が写る（「このカメラから見る」は near を合わせる）。
- 手先カメラは link の下に置き、launch_sim の `camera_mount` 検査で設定どおりの位置・向きかを確かめる
  （2.x 版の DUAL_SCORPION では描画側（Fabric）のローカル変換がずれて手先カメラが手首に埋まったため、環境を作った直後に
  書き直していた。3.0 版では Franka / CRX と双腕（Franka 2 本・DUAL_SCORPION）でずれないことを確認し、書き直しはやめた）。
- 正面が +x でないロボット（Fusion から出した URDF など）は、肩の並びと腕の伸びる向きから正面を推定して確認し、+x を向くように回して置く。
- 台の配置は、肩の高さと腕の長さから「肩から腕の長さの半分・斜め下 45°」に把持点が来るように決める（肩が高く腕が短いロボットは台を高くする）。
- 双腕の Embodiment は腕ごとに IK / 関節目標の ActionTerm とグリッパの ActionTerm を並べ、Arena の `ArmMode.DUAL_ARM` にする。
  state / action は腕の順、グリッパの順（sim の関節の並びは左右が交互になるが、collect の `label_replay` 検査で対応を確かめる）。
  台の中心は左右の肩の中央、scripted は物体に近い腕だけを動かし、もう一方は開いたまま保持する。既定の配置では物体が左半分・
  箱が右半分にあるので、毎回同じ腕（物体の側）が箱まで運ぶ（もう一方の腕はデモの中で動かない）。
  launch_sim の `grasp_hold` / `grasp_opening` はグリッパのある腕ごとに検査する。台の届く範囲の IK 検査（ReachChecker）は単腕だけ。
  DROID の zero-shot 評価は手先 1 つが前提なので、双腕では明示的なエラーにする。
- 双腕の Mimic（`generate_mimic`）は Arena の ArmMode.LEFT / RIGHT と同じ形: 手先（eef）は腕ごと（arm の名前）、元のデモで物体を
  掴んだ腕（subtask の信号 `grasp_<arm>`）が grasp → place、もう一方の腕は 1 区間で `object_ref=None`（Mimic は座標を写さず
  元のデモのまま再生する。物体の座標で写すと、物体の移動分だけ止まっている手も動く）。元のデモの選び方は作業する腕の
  nearest_neighbor_object で決まり、ほかの腕も同じデモを使う（`generation_select_src_per_arm=False`）。行動は
  [腕ごとの 7 次元 ..., グリッパごとの ±1 ...]（GR1T2 の Mimic と同じ並び）。成功判定の「離した」はすべてのグリッパが開く指令
  （以前は行動の最後の列だけを見ていて、双腕では左腕が持ったままでも成功になりえた）。元のデモは scripted か人の操作
  （`--source-device socket / keyboard`）。
- 双腕の URDF で、2 本の腕をつなぐ root link に質量（inertial）がないと、importer が腕ごとに別の articulation にする
  （import_robot が `Expected 1 prims at '/World/Robot', found 2` で止まる）。root link に inertial を書く。
- 片側だけ動く顎のグリッパは、arm の最後の関節として見えるので質問する。把持点は「固定側の顎と開いた顎の間」。
- URDF 変換は `merge_fixed_joints=False`（True だと `panda_hand` のような End Effector が剛体でなくなる）、
  USD は `build/usd/` に固定してキャッシュを効かせる（Isaac Lab 3.0 の importer は `build/usd/robot/robot.usda` + payloads の階層 USD を作る）。
- 質量のない frame（例: `end_effector`, `tool0`）を End Effector にした場合は、剛体の祖先に固定オフセット付きで取り付ける。
- シミュレーション系は常にカメラの描画を有効にし、`DISPLAY` なしで起動（`DISPLAY` が残ると起動が数分止まる）。
  コンテナ内の Python は `isaaclab.sh -p`（Isaac Sim の環境を読み込む。直接 `/opt/isaaclab-venv/bin/python` を使うと Isaac Sim が見つからない）。
- カメラ画像が step 間で更新されない問題（以前のプロジェクトで学習失敗の原因になった）を、launch_sim / 記録 / 評価で毎 step 検査する
  （直前の step と完全に同じ画像が 1 回でもあれば失敗）。`env.step` 内の描画で毎 step 更新されることを
  確認済み（Isaac Lab 2.3.2 / 3.0 + RTX 5080、Franka / CRX）なので、余分な描画ステップ（`task.render_fix`）は既定で無効。
  有効にすると 1 制御 step あたり物理が 1 step 増え、`task.control_hz`（= データセットの fps）もそれに合わせて変わる。
- 遅いグリッパ（URDF の速度上限が小さい）でも開閉検査の時間を自動で延ばす。
- mimic の follower の速度上限・トルク上限は URDF（なければ leader）の値を actuator に明示する。
- 把持点（TCP）は指の衝突メッシュ（STL）から指先を求めて決める（Franka: 0.100 m、Isaac Lab の値 0.103 m とほぼ一致）。
- 行動ラベルは「IK が出した関節目標 + グリッパのスカラー（0=閉, 1=開）」。teleop は目標手先姿勢を積算して IK で追従するので、
  腕の追従が遅れても指令どおりに動く。記録後、ラベルだけで腕を動かして記録時と同じ軌道になるかを検査する（把持前の区間で判定）。

## テスト

```bash
pytest            # ホスト側（URDF 解析・設定生成・質問の出方）。Franka / CRX-3iA / 合成双腕
```

## 検証状況（単腕は 2026-11-05、双腕は 2026-11-12。RTX 5080, Isaac Lab 3.0.0 / Isaac Sim 6.2.0 / Isaac Lab Arena b9e37c5）

単腕のカメラは既定の DROID 配置（手先 + 左右の外部カメラ、各 256×256）。双腕は腕ごとの手先 + 左右の外部の 4 台。
OpenArm は URDF にあるカメラ（両手の camera_view_frame + 頭の ZED Mini）の 3 台（2026-11-12、`feature/urdf-cameras`）。

| | Franka | CRX-3iA + Robotiq 2F-85（SimReady に付け替え） | Franka 2 本（双腕、`examples/dual_franka` の合成 URDF） | DUAL_SCORPION（双腕） | OpenArm v2.0（双腕、`examples/openarm`） |
|---|---|---|---|---|---|
| Phase 1（質問・導入・検査・起動） | 質問なしで通る。import_robot 9 検査・launch_sim 9 検査 OK | 初期姿勢（IK の提案）だけ質問。Robot Assembler でグリッパを取り付け、import_robot 9 検査・launch_sim 9 検査 OK | 質問なしで通る。import_robot 12 検査・launch_sim 18 検査（グリッパごとの grasp_hold / grasp_opening）・setup_teleoperation 8 検査 OK | 初期姿勢（IK の提案）を質問。import_robot 12 検査 OK。launch_sim は立方体だけ OK（果物は回転する顎 1 N·m で grasp_hold に失敗）。setup_teleoperation は腕 10 N·m だと止まっている腕が下がって action_label に失敗、60 N·m で OK | 質問なしで通る（例に初期姿勢・グリッパの開閉向きを書いてある）。import_robot 12 検査・launch_sim 15 検査（カメラ 3 台。回転する指なので grasp_opening はない）・setup_teleoperation 8 検査 OK。台・トレイ・作業の構え・初期姿勢は手で決めた（下記）。指は 2 N·m（URDF の 7 N·m では右手の grasp_hold に失敗）、solver_position_iterations 64（既定 32 では左手の grasp_hold が 2.2 cm ずれて失敗） |
| Phase 2（scripted 収集） | 4/4、ラベル再生・最初の画像の検査 OK | 4/4、ラベル再生・最初の画像の検査 OK | 8/11（`examples/dual_franka` から作り直したプロジェクト。毎回左腕。失敗 3 回は下降中の時間切れ）、ラベル再生 OK | 0/8（物体の真上の目標に手先が収まらず下降に進まない）。物体と箱を左腕の前に寄せる（main の旧配置）と真上までは行くが下降中に止まる（0/4）。原因は未特定 | 8/10（毎回左腕。失敗 2 回は物体への下降中の時間切れ。どちらも物体がトレイ側の端にあり、手先が把持の高さの 6 mm 上で止まる。開いた指がトレイの縁に当たるとみられる）、ラベル再生 OK。トレイを中心線の近く（y = 0.03）に置くと 4/8（置く点が joint2 の内転の限界にかかる） |
| Phase 3（変換） | GR00T ローダで読める、3 カメラの動画は元画像と一致 | 同左 | GR00T ローダで読める、4 カメラの動画は元画像と一致 | 未実施 | GR00T ローダで読める、3 カメラ（両手 + 頭）の動画は元画像と一致 |
| Phase 4（replay 評価、`--video`） | 4/4（動画で把持・配置を確認） | 4/4（同左） | 8/8（動画で左腕の把持・配置と右腕の静止を確認） | 未実施 | 8/8（動画で左腕の把持・配置と右腕の静止を確認） |

Isaac Lab 2.3.2 版（git タグ `isaaclab2-last`。公開前の履歴にあり、公開のリポジトリにはない）では、CRX は URDF のグリッパで収集 3/4〜6/7・replay 4/6、
DUAL_SCORPION は scripted 収集 0/8（回転する顎が物体を台に押し付けながら閉じ、腕が押し上げられる。URDF のトルクは仮値）だった。

双腕の Mimic（2026-11-12、元のデモ 5 個はスクリプトで記録、20 回の試行）: Franka 2 本 15/20（作業する腕は左）、OpenArm 20/20、
いずれも全数を画像付きで記録でき、止まっている腕の手の移動は 0 mm。単腕の Franka は 13/20（変更前の 75/100 と同程度）。

推論サーバは学習済み N1.7 checkpoint（別のプロジェクトで学習したもの）を読み込んで起動できることまで確認（2.x 版、GPU 6.4GB）。

未検証: GR00T の実際の学習（44GB 以上の GPU が必要。Isaac Lab 3.0 版では `train_vla` のジョブ作成も未実行。データセット側は
`convert_dataset` の GR00T ローダ検査で確認済み）と、学習済みモデルでの評価（`evaluate_vla --model`）。
Isaac Lab 3.0 版で試したのは scripted の収集だけで、socket / keyboard の teleop と `--livestream` は未検証（2.x 版では socket は検証済み）。

## 次の作業: NVIDIA Brev（L40S）で FANUC の例を最後まで

ここまでの検証は RTX 5080（16 GB）で、GR00T の学習（44 GB 以上）と学習済みモデルでの評価はまだしていない。Brev の L40S（48 GB）
では、FANUC CRX-3iA の例でこの残りを確かめ、結果をドキュメントに書く。

準備（インストールの「クラウドの GPU」の節と同じ）:

```bash
git clone https://github.com/momoiorg-repository/urdf2vla.git && cd urdf2vla
uv venv .venv && uv pip install -e '.[view,dev,docs]' xacro && source .venv/bin/activate
bash docker/build.sh                          # 1〜2 時間、約 100 GB。ディスクは 200 GB 以上のインスタンスで
bash docker/run.sh && bash docker/run.sh gr00t
urdf2vla doctor                               # ドライバ 580 以降（古いと警告）、image・コンテナ・ディスク
pytest                                        # 47 passed / 9 skipped（FANUC の URDF がまだない）
bash examples/crx3ia/fetch_urdf.sh            # ROS なし。PyPI の xacro で ROS 2 のものと同じ URDF になる
pytest                                        # 52 passed / 4 skipped（CRX の例の試験も走る。skip は下記）
```

FANUC の例（`docs/guides/your-robot.md` の手順。`import_robot` で初期姿勢だけ質問される）:

```bash
urdf2vla init projects/crx3ia --from examples/crx3ia/project.yaml
for s in inspect_robot configure_robot configure_task configure_camera import_robot; do urdf2vla $s --project projects/crx3ia; done
urdf2vla answer --project projects/crx3ia robot.default_pose=suggested
for s in configure_robot import_robot launch_sim configure_vla_interface setup_teleoperation; do urdf2vla $s --project projects/crx3ia; done
urdf2vla collect_dataset --project projects/crx3ia --device scripted --num-demos 50     # または generate_mimic
urdf2vla convert_dataset --project projects/crx3ia
urdf2vla train_vla       --project projects/crx3ia --dataset demos                      # 48 GB なのでその場で学習する
urdf2vla evaluate_vla    --project projects/crx3ia --model training/crx3ia_demos/checkpoints/<checkpoint> --episodes 20 --video
```

確かめて書くこと:

1. L40S での動作（RTX 5080 以外の GPU は初めて）: 各工程の検査、描画、時間、GPU メモリ。「検証状況」の表に L40S の列を足す。
2. GR00T の実際の学習（`train_vla`）: 学習時間、checkpoint、GPU メモリ。`docs/guides/training.md` に実測値を書く。
3. 学習済みモデルでの評価（`evaluate_vla --model`）: 成功率と動画。FANUC で学習した VLA はまだどこにもない
   （以前の FANUC 向けと思われたモデルは Franka のものだった）。
4. FANUC での Mimic（`generate_mimic`。単腕では Franka で 75/100）と、Mimic のデータで学習した場合との比較。
5. 人の操作（`--device socket` / `generate_mimic --source-device socket`）で記録した元のデモからの Mimic（経路はスクリプトで
   キーを送って確かめただけ）。

注意:

- `tests/test_inspect.py` の CRX の試験 3 つは、URDF の Robotiq グリッパ付きの非公開の CRX URDF（`URDF2VLA_CRX_URDF`）が要る。
  Brev では飛ばされる。公開の FANUC の例の試験は `fetch_urdf.sh` の後に走る。4 つ目の skip は OpenArm
  （`external/openarm_description` を clone すると走る）。
- Isaac Lab 2.x 版のデータは 3.0 で再生できない（四元数の並びと URDF importer が変わった。CRX は SimReady のグリッパになった）。記録し直す。
- git タグ `isaaclab2-last`（2.x 版のコード）は公開のリポジトリにない（公開前の履歴）。
- GR00T の元のモデル（nvidia/GR00T-N1.7-3B）は初回に Hugging Face から取得する。RTX 5080 のマシンでは途中で止まったことがある。
  止まったら実行し直すと続きから取得する。
- 2026-11-12 に、このマシンの新しい clone で Brev と同じ条件（ROS なし）を確かめた: PyPI の xacro の FANUC の URDF は
  ROS 2 Jazzy のものと link・関節・可動範囲・メッシュ・質量が同じ、pytest 47/9 → 取得後 52/4、`mkdocs build --strict`、`doctor`、
  FANUC の例のホスト側の工程（台を 10 cm 手前に寄せる警告も同じ）。Brev のインスタンスそのものではまだ動かしていない。

## 今後

- 学習済み checkpoint での評価（大きな GPU で train.sh を実行した後）
- 場面・タスクを SimReady アセット（NVIDIA の Hugging Face にある物体・台など）から選べるようにする（Arena の Object と
  配置の relations を使う）。いまは台・立方体・置き場所のプリミティブだけ
- SimReady のグリッパを増やす。Robotiq 2F-140 は閉リンクの残りの関節（inner knuckle は excludeFromArticulation）を受け身にしても指がばらけて何も保持できなかった。Hand-E は 2 つの顎を別々に駆動する（駆動する関節を複数持てるようにする）。SCHUNK は mm 単位のアセットで開きが小さい。xArm はりんごが抜ける
- 双腕: 左右どちらの腕のデータも作る（Mimic は 1 回の生成で 1 本の作業する腕。腕ごとに元のデモを記録して生成し、まとめる。
  scripted の自己確認は既定の配置では毎回同じ腕）、
  止まっている腕を IK ではなく関節目標で保持する（DUAL_SCORPION の 10 N·m では重力で下がり、IK が追いかけて関節がずれる）、
  台の届く範囲の IK 検査を双腕にも、Kit の keyboard（N で腕を切替。socket では確認済み）の確認、
  両方の腕で一緒に作業するタスク（Mimic の DUAL_ARM と腕どうしの協調の拘束 SubTaskConstraintConfig）、
  人の操作で記録した元のデモからの Mimic（記録の経路は socket で確認済み、実際の人のデモは未検証）、
  VR（Isaac Lab 3.0 の isaaclab_teleop: CloudXR + 手ごとの Se3 / グリッパの retargeter）で両腕を同時に操作する teleop
- 手先を真下に向けにくい腕（Enactic OpenArm v2.0: グリッパが前腕の延長上、手首の曲げは最大 90°）の自動配置。真下に向けるには
  前腕を水平か下向きにするので、届く高さは肩（0.70 m）からおよそ 0.3 m 以上下に限られる。いまの configure_task は肩から腕の長さで
  台を決め（OpenArm では台 0.42 m・作業の構えが KLT の縁の上の 0.63 m）、そこに真下向きで届く姿勢は上腕を肩より上に回すもの
  しかなく、IK の推奨姿勢がそれを選ぶ。`examples/openarm` は台・トレイ・作業の構え・初期姿勢を手で決めて通している
  （ホストの IK で届く範囲を調べた）。直すなら: 双腕にも ReachChecker を使い、台の高さも届く範囲から決める・
  容器を届く高さで選ぶ・推奨姿勢の探索で肘を下げた種を使う。物体と置き場所を左右の腕に分ける配置は、腕が体の中心線を
  越えられないロボット（OpenArm の joint2 の内転は 10°）では置けない
- URDF のカメラの名前: 腕のものは `<arm>_wrist_cam`、頭・体のものは link 名の順に `head_cam`, `head_cam_2` ...。URDF にカメラを
  足すと既存のカメラの名前が変わることがある（データセットの動画のキーになる）。link 名から名前を付ける、または保存した名前を固定する
- `urdf2vla view` のカメラの欄は、名前を変えた URDF のカメラを明示指定（その時点の姿勢）で保存する（preset: urdf に name を付けて残せる）
- URDF のないロボット（USD しかないもの）: USD 入力への対応（schema は双腕・関節の役割・mimic グリッパに対応済み）
