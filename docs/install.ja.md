# インストール

手元のマシンには小さな `urdf2vla` コマンドを入れます。重い処理は 2 つの Docker コンテナの中で動きます。1 つは
Isaac Sim、Isaac Lab、Isaac Lab Arena（シミュレーション）、もう 1 つは GR00T N1.7（データ変換・学習・推論）です。

!!! tip "コマンドを実行する場所"
    このドキュメントのコマンドはすべて、**手元のマシンの端末**で、`urdf2vla` のフォルダで `source .venv/bin/activate`
    の後に実行します。コンテナの中に入る必要はありません。`urdf2vla` が適切なコンテナで処理を動かします。
    Docker を直接使うのは、下の手順 2 と 3 の一度だけです。

## 必要なもの

| | |
|---|---|
| OS | Linux x86_64（Ubuntu 22.04 か 24.04） |
| GPU | 16 GB 以上の NVIDIA の RTX GPU とドライバ **580 以降** |
| Docker | Docker と [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) |
| ディスク | 空き **150 GB**。このマシンで学習するなら **300 GB**（checkpoint が 1 つ 24 GB） |
| Python | Python 3.10 以降と [uv](https://docs.astral.sh/uv/) |
| Hugging Face | `nvidia/Cosmos-Reason2-2B` のライセンスに同意したアカウント。学習と評価で使います（[手順 5](#huggingface)） |
| 学習 | **44 GB 以上**の GPU（L40S, A100, H100, RTX 6000 Ada）。別のマシン（[学習と評価](guides/training.md#another-machine)）や[クラウドの GPU](guides/brev.md) でもかまいません |

## 1. `urdf2vla` コマンドを入れる

```bash
git clone https://github.com/momoiorg-repository/urdf2vla.git
cd urdf2vla
uv venv .venv
uv pip install -e '.[view]'      # [view] はブラウザでロボットを見る画面の追加パッケージ
source .venv/bin/activate
```

## 2. コンテナを作る（初回だけ）

```bash
bash docker/build.sh
```

Isaac Lab Arena と Isaac-GR00T を固定のバージョンで `external/` に取得し、2 つの image
`urdf2vla/isaaclab_arena:b9e37c5`（38 GB）と `urdf2vla/gr00t:23ace64`（26 GB）を作ります。初回は大量にダウンロードします。
L40S ではそれぞれ 13 分と 4 分かかりました。`bash docker/build.sh sim` か `gr00t` で片方だけ作れます。`--force` で作り直します。

## 3. コンテナを起動する

```bash
bash docker/run.sh          # シミュレーション用: urdf2vla-sim
bash docker/run.sh gr00t    # GR00T 用: urdf2vla-gr00t
```

もう一度実行しても、コンテナが動いていることを確かめるだけです。`--rm` で削除します（ダウンロードしたものはキャッシュに残ります）。

## 4. マシンを検査する

```bash
urdf2vla doctor
```

`doctor` は GPU ドライバ、Docker と Docker から GPU が使えるか、image とコンテナ、ディスクの空き、NVIDIA のアセットサーバと
Hugging Face を検査し、問題ごとに直すためのコマンドを示します。環境がおかしいと思ったら、いつでも実行してください。

## 5. Hugging Face（学習と評価で使う） {#huggingface}

GR00T N1.7 は vision-language モデル `nvidia/Cosmos-Reason2-2B` を使い、そのライセンスへの同意が必要です。

1. [huggingface.co/nvidia/Cosmos-Reason2-2B](https://huggingface.co/nvidia/Cosmos-Reason2-2B) でライセンスに同意します。
2. [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) で読み取り権限の token を作ります。
3. ログインします（token は `~/.cache/huggingface` に保存され、コンテナと共有されます）。

    ```bash
    hf auth login
    ```

## 次へ

<div class="grid cards" markdown>

- **AI エージェントに工程を任せる** → [AI エージェントから使う](guides/claude-code.md)
- **各工程を自分で実行する**（同梱の Franka で） → [チュートリアル](tutorial.md)
- **クラウドの GPU で**（ドライバ、Docker から GPU を使う設定、ディスクをスクリプト 1 つで準備） → [NVIDIA Brev で動かす](guides/brev.md)

</div>
