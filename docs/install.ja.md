# インストール

urdf2vla は 2 つの部分からなります。

- **`urdf2vla` コマンド**: 手元のマシンに入れる小さな Python パッケージ
- 重い処理をする 2 つの **Docker コンテナ**: シミュレーション用（Isaac Sim / Isaac Lab / Isaac Lab Arena）と、
  データ変換・学習・推論サーバ用（GR00T N1.7）

コマンドはすべて手元のマシンで実行します。urdf2vla が適切なコンテナの中で処理を動かします。

## 必要なもの

| | |
|---|---|
| OS | Linux x86_64（Ubuntu 24.04 で検証） |
| GPU | NVIDIA の RTX GPU（RTX 5080 16 GB で検証）とドライバ **580 以降** |
| Docker | Docker と [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)（`docker run --gpus all` が使えること） |
| ディスク | 空き **150 GB** 程度（シミュレーション用 image が約 60 GB、GR00T 用が約 42 GB） |
| Python | Python 3.10 以降と [uv](https://docs.astral.sh/uv/) |
| ネットワーク | 初回のビルド、初回の取り込み（Isaac Sim の拡張と SimReady アセットを取得）、GR00T のベースモデルの取得で必要 |
| 学習 | **44 GB 以上**の GPU（A100, H100, L40S, RTX 6000 Ada など）。別のマシンでもかまいません（[学習と評価](guides/training.md)）。全部を[クラウドの GPU](#cloud)で動かすこともできます |

始める前に、Docker から GPU が見えることを確かめます。

```bash
docker run --rm --gpus all ubuntu nvidia-smi
```

## 1. `urdf2vla` コマンドを入れる

```bash
git clone https://github.com/momoiorg-repository/urdf2vla.git
cd urdf2vla
uv venv .venv
uv pip install -e '.[view]'      # [view] はブラウザでロボットを見る画面の追加パッケージ
source .venv/bin/activate        # このシェルで urdf2vla コマンドが使えるようになる
urdf2vla --help
```

## 2. コンテナを作る（初回だけ）

```bash
bash docker/build.sh
```

Isaac Lab Arena と Isaac-GR00T を固定のバージョンで `external/` に取得し、2 つの image を作ります。

| image | 中身 | 大きさ |
|---|---|---|
| `urdf2vla/isaaclab_arena:b9e37c5` | Isaac Sim 6.2.0、Isaac Lab 3.0.0、Isaac Lab Arena | 約 60 GB |
| `urdf2vla/gr00t:23ace64` | Isaac GR00T N1.7 とその Python 環境 | 約 42 GB |

!!! info "時間がかかります"
    初回は **1〜2 時間**かかり、大量にダウンロードします。もう一度実行すると、作成済みの image はスキップします
    （`--force` で作り直し）。`bash docker/build.sh sim` / `bash docker/build.sh gr00t` で片方だけ作れます。

## 3. コンテナを起動する

```bash
bash docker/run.sh          # シミュレーション用コンテナ urdf2vla-sim
bash docker/run.sh gr00t    # GR00T 用コンテナ urdf2vla-gr00t
```

何度実行しても安全です。コンテナがあれば起動しているかを確かめるだけで、マシン上の他のコンテナには触れません。
削除するには `--rm` を付けます（例: `bash docker/run.sh gr00t --rm`）。ダウンロードしたキャッシュは Docker の volume に残ります。

コンテナはあなたのユーザで動くので、urdf2vla がリポジトリに書くファイルの所有者はあなたのままです。

## 4. 動作を確かめる

```bash
urdf2vla doctor
```

`doctor` は何も変更せずにマシンを検査します: GPU ドライバ（カーネルの更新でドライバが追いついていない場合を含む）、
GPU のメモリ、Docker と NVIDIA Container Toolkit、2 つの image とコンテナ（コンテナから GPU とリポジトリが見えるか）、
ディスクの空き、NVIDIA のアセットサーバに届くか。問題ごとに直すためのコマンドを示します。環境がおかしいと思ったら、
いつでももう一度実行してください。

## 5. 進め方を選ぶ

<div class="grid cards" markdown>

- **ロボットのシミュレーションが初めてなら、AI エージェントに任せる。** リポジトリを Claude Code か Codex で開いて
  やりたいことを伝えると、エージェントが工程を実行し、ロボットについてだけあなたに聞きます。
  → [AI エージェントから使う](guides/claude-code.md)
- **各工程を自分で確かめたいなら。** 同梱の Franka Panda で全工程を一度通します。→ [チュートリアル](tutorial.md)

</div>

どちらでも、最初のシミュレーションの工程 `import_robot` は、シェーダのコンパイルが済んでいれば 1 分以内に終わります
（Isaac Sim の最初の起動は数分かかることがあります）。

## クラウドの GPU（NVIDIA Brev）で使う {#cloud}

ここまでの手順は、クラウドの GPU のマシン（たとえば NVIDIA Brev のインスタンス）でも同じです。**L40S（48 GB）** なら
シミュレーションが動き、GR00T の学習にも足りるので、`train_vla` がそのマシンで学習します。

- **インスタンス:** Ubuntu 22.04 か 24.04、Docker と NVIDIA Container Toolkit、ディスクは **200 GB** 以上
  （2 つの image で約 100 GB、ほかにデータセットと GR00T の元のモデル）。
- **ドライバ:** urdf2vla はドライバ 580 以降で確かめています。インスタンスのドライバが古ければ `urdf2vla doctor` が警告します。
  新しいドライバのイメージを選ぶか、更新して再起動してください。
- **画面は要りません:** どの工程も画面なしで動きます。
- **テストとドキュメント:** `uv pip install -e '.[view,dev,docs]'` で追加のパッケージを入れ、`pytest` と
  `mkdocs build --strict` を実行します。
- **FANUC の例:** ROS がないので、URDF を取得する前に `xacro` を urdf2vla の環境に入れます:
  `uv pip install xacro && bash examples/crx3ia/fetch_urdf.sh`。
- **ブラウザの画面:** `urdf2vla view` はインスタンスのポート 8080 で開きます。自分の PC から SSH で転送し、
  `http://localhost:8080` を開きます。

    ```bash
    ssh -L 8080:localhost:8080 <インスタンス>
    ```

- **teleop:** SSH ではターミナルのキー操作で記録します。1 つ目の SSH で `collect_dataset --device socket`
  （または `generate_mimic --source-device socket`）、2 つ目の SSH で `urdf2vla teleop-keys`。`--livestream`（WebRTC）は、
  映像が SSH の転送を通らないので、配信のクライアントからインスタンスに直接つながる必要があります（Brev では試していません）。

## 任意: このドキュメントのサイト

```bash
uv pip install -e '.[docs]'
mkdocs serve                # http://127.0.0.1:8000
```
