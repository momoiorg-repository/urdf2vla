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
| 学習 | **44 GB 以上**の GPU（A100, H100, L40S, RTX 6000 Ada など）。別のマシンでもかまいません（[学習と評価](guides/training.md)） |

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

一番早いのは [チュートリアル](tutorial.md) です。最初のシミュレーションの工程 `import_robot` は、シェーダのコンパイルが
済んでいれば 1 分以内に終わります（Isaac Sim の最初の起動は数分かかることがあります）。

## 任意: このドキュメントのサイト

```bash
uv pip install -e '.[docs]'
mkdocs serve                # http://127.0.0.1:8000
```
