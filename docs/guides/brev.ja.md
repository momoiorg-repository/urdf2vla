# NVIDIA Brev で動かす

NVIDIA Brev のクラウドの GPU で、新しいインスタンスから学習済みモデルまで urdf2vla を動かす手順です。このページの
数値はすべて 1 台のインスタンスで測ったものです: **NVIDIA L40S（46 GB）**、CPU 8、メモリ 144 GB、Ubuntu 22.04、
FANUC CRX-3iA の例。

下の各項目は `urdf2vla doctor` が検査します。問題があれば直し方と、このページの該当の節へのリンクを表示します。
準備の各段階のあとで実行してください。

```bash
urdf2vla doctor
```

## 始める前に用意するもの

| | |
|---|---|
| インスタンス | インスタンスの上で学習するなら **44 GB 以上**の GPU 1 枚（L40S、A100、H100）。小さい GPU でも学習以外はすべて動きます |
| ディスク | Docker の image とリポジトリを置くディスクに **300 GB 以上**（[ディスク](#disk)） |
| Hugging Face | アカウント、read 権限のトークン、`nvidia/Cosmos-Reason2-2B` の利用規約への同意（[Hugging Face](#huggingface)） |
| 時間 | 準備 約 30 分、デモの記録 35 分、学習 1 回 2 時間 |

## スクリプト 1 つで準備する {#script}

新しいインスタンスなら、このページの 1〜4 と 2 つのコンテナの起動を、スクリプト 1 つで済ませられます。ログインする
ユーザで実行してください（要るところでは `sudo` を使います）:

```bash
curl -fsSL https://raw.githubusercontent.com/momoiorg-repository/urdf2vla/main/docker/brev_setup.sh -o /tmp/urdf2vla_brev_setup.sh
bash /tmp/urdf2vla_brev_setup.sh
```

リポジトリはスクリプトが空きのいちばん大きいディスクに clone します。clone 済みなら、その中で
`bash docker/brev_setup.sh` を実行してください。その場所のまま使い、学習の checkpoint（1 つ 24 GB）に空きが足りなければ
注意を表示します。

| 段階 | スクリプトがすること |
|---|---|
| [ドライバ](#driver) | 580 より古ければ、Brev の hold を外し、580 系に固定して 580 を入れ、再起動します。再起動の後は続きを自動で実行します |
| [ディスク](#disk) | システムのディスクの空きが 130 GB 未満で、もっと大きいディスクがあれば、Docker の保存先をそこに切り替えます。`/etc/docker/daemon.json` の NVIDIA の設定は維持します。リポジトリを大きいディスクに clone した場合は、`~/urdf2vla` からリンクします。 |
| [CDI](#cdi) | `/etc/cdi/nvidia.yaml` を作り、コンテナから GPU が見えることを確かめます |
| [ビルドと起動](#build) | あなたを `docker` グループに入れ、uv と `urdf2vla` コマンド（`.venv`）を入れ、`docker/build.sh` と 2 つのコンテナの `docker/run.sh`、最後に `urdf2vla doctor` を実行します |

約 30 分かかります（ドライバを上げるときはもっと）。準備は systemd のサービスとして動くので、SSH の接続が切れても
止まりません。進み具合は:

```bash
bash ~/urdf2vla/docker/brev_setup.sh --status   # どこまで進んだか、次に何をするか
tail -f /var/log/urdf2vla-setup.log              # 出力のすべて
```

ログインすると進み具合が表示されます。準備が終わっていれば、ログインしたときにリポジトリに移って `.venv` を有効に
します。途中の段階で止まったら、ログにどの段階かが出ます。原因を直して（各段階は下の節で説明しています）もう一度
スクリプトを実行すると、止まった段階から続けます。

その後 [Hugging Face](#huggingface) の準備をします: 利用規約に同意して `hf auth login`。トークンは
`HF_TOKEN=hf_... bash /tmp/urdf2vla_brev_setup.sh` のようにスクリプトに渡すこともできます（`~/.cache/huggingface/token`
に保存し、表示はしません）。利用規約への同意だけはブラウザで行います。続きは[チュートリアル](../tutorial.md)へ。

!!! note
    `docker/run.sh` は、手元のマシンと同じく `ACCEPT_EULA=Y` でシミュレーションのコンテナを起動します。使うことは
    Isaac Sim の image の利用規約に同意することです（[Isaac Sim のコンテナのインストール](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/installation/install_container.html)）。

### Brev の Launchable {#launchable}

[Brev コンソール](https://brev.nvidia.com)で **Launchables → Create Launchable** を開き、次の設定を使います。
VM の料金はデプロイしたアカウントにかかります。[NVIDIA Brev Launchables](https://docs.nvidia.com/brev/concepts/launchables)

| 設定 | 値 |
|---|---|
| Name | `urdf2vla` |
| Description | ロボットの URDF から Pick & Place の環境を作成し、デモの収集と GR00T N1.7 の学習を行います。SSH で接続し、Franka の例から始められます。 |
| Compute | NVIDIA L40S 1 枚。別の対応 GPU で学習する場合は VRAM 44 GB 以上 |
| Disk storage | **300 GiB 以上** |
| Software configuration | **VM Mode**、Jupyter なし |
| Setup script | [docker/brev_launchable.sh](https://github.com/momoiorg-repository/urdf2vla/blob/main/docker/brev_launchable.sh) の中身を貼る |
| Source | **I don’t have any code files**。リポジトリは setup script が clone します。 |
| Network（Isaac Sim、任意） | 公開配信では `49100/TCP` と `47998/UDP` を転送し、両方をデプロイした人の IP に制限します。割り当てられた公開ポート番号を[デスクトップクライアント](#desktop-streaming-client)か[ブラウザビューア](#remapped-kit-ports)に指定します。 |
| Network（URDF ビューア、任意） | HTTP ポート `8080` の認証付き **Secure Link** を追加し、名前を `Robot viewer` にします。 |
| Launch parameter（任意） | `HF_TOKEN` を任意の **Text** として追加し、既定値は空にします。組織の secret を選ぶか、後で `hf auth login` できます。 |
| View access | 最初のテストでは **Only my organization** を選びます。 |

既定のリビジョンは `main` です。マージ前にテストする場合は、貼り付けたスクリプトの `URDF2VLA_REF` に公開リポジトリ上の
対象ブランチを指定します。毎回同じ版を使うにはリリースタグを指定します。
Brev が setup script を実行するのは一度だけです。ドライバの更新で再起動した場合は systemd サービスが続きを実行します。
SSH で接続し、`bash ~/urdf2vla/docker/brev_setup.sh --status` と `/var/log/urdf2vla-setup.log` で完了を確認します。

**Create Launchable** でデプロイ用のリンクを発行し、そのリンクからテスト用の VM を作成します。下の Franka の確認が
通ったら **View access** を公開したい範囲に変更し、**My Launchables → Copy Markdown Badge** から README やドキュメント
用の **Launch on Brev** ボタンを取得できます。[NVIDIA Brev の共有とバッジ](https://docs.nvidia.com/brev/concepts/launchables#embedding-launchables)

### Franka でデプロイを確認する {#franka-smoke-test}

既存の VM ではセットアップ前に `nvidia-smi`、`docker info`、`df -h . /data` を確認します。セットアップにより、ドライバの
更新と再起動や、Docker の保存先の切り替えが行われる場合があります。切り替え前のイメージとコンテナは元の保存先に残ります。

セットアップが終わったら、VM の端末でリポジトリに移動し、`.venv` を有効にして `urdf2vla doctor` を実行します。
エラーを解消したら、[Franka チュートリアルの手順 1〜3](../tutorial.md#create) を `projects/brev_franka` で実行し、
`snapshots/` の `*_start.png` と `*_end.png` にアーム、台、物体、箱が写っていることを確認します。Secure Link を追加した
場合は、VM で `urdf2vla view --project projects/brev_franka --port 8080` を起動し、コンソールで **Robot viewer** を開きます。
初回は Kit がシェーダーをコンパイルするため、Isaac Sim の起動に数分かかることがあります。

#### デモ 3 件を記録して再生する {#franka-replay-test}

シーンの検査が通ったら、Isaac Sim と GR00T コンテナを使って記録から再生まで確認します。記録済みの行動を使うため、
学習済みチェックポイントや Hugging Face へのログインは不要です。Kit で配信中の場合は GPU を空けるために停止します。
繰り返し試す場合は、別のデータセット名を使います。

```bash
project=projects/brev_franka
dataset=brev_smoke
urdf2vla configure_vla_interface --project "$project"
urdf2vla setup_teleoperation --project "$project"
urdf2vla collect_dataset --project "$project" --device scripted --num-demos 3 --dataset "$dataset"
urdf2vla convert_dataset --project "$project" --dataset "$dataset"
urdf2vla evaluate_vla --project "$project" --replay --dataset "$dataset" --episodes 3 --video
```

記録では成功したデモだけを保存するため、3 回以上試行することがあります。`build/collect_report.yaml` で保存件数を、
`build/eval_report.yaml` で再生の結果を確認します。再生では記録された行動とサーバーとの通信を検査します。
`--video` を指定すると、`build/eval_videos/` にエピソードごとの MP4 が保存されます。

<figure>
  <video controls muted playsinline preload="none" width="100%" poster="../../../assets/brev_franka_replay.jpg" src="../../../assets/brev_franka_replay.mp4" aria-label="3 台のカメラで見た Franka のデモ再生"></video>
  <figcaption>Franka がキューブを箱に入れるデモの再生。左から手先、左、右のカメラ。</figcaption>
</figure>

モデルを学習して評価する手順は[学習と評価](training.md)にあります。

### Isaac Sim の Kit ビューポートを配信する {#kit-livestream}

L40S など、NVIDIA NVENC に対応する GPU を使います。手元のマシンから Kit の両方のポートに到達できる必要があります。
公開配信には、プロバイダーのファイアウォールか Brev の **Ports** タブを使います。
WebRTC 配信自体には認証がないため、両方の公開ポートをデプロイした人の IP に制限します。[Brev のネットワーク設定](https://docs.nvidia.com/brev/concepts/launchables#network) ·
[Isaac Sim 6.2 の配信クライアント](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/installation/manual_livestream_clients.html)

| 宛先ポート | プロトコル | 用途 |
|---|---|---|
| `49100` | TCP | WebRTC シグナリング |
| `47998` | UDP | WebRTC 映像 |

Brev の **Public Port** と **Destination Port** を確認します。宛先ポートは VM 上で Kit が使う番号です。Brev の公開
エンドポイントに接続するときは、クライアントに割り当てられた公開ポート番号を指定します。

`import_robot` が成功したら、VM のリポジトリで接続方法に対応するコマンドを実行します。

=== "公開エンドポイント"

    `STREAM_IPV4` は、手元から両方の公開ポートに接続できるエンドポイントの IPv4 アドレスに置き換えます。
    モード `1` は Kit ビジュアライザーを有効にし、このアドレスをクライアントに通知します。

    ```bash
    urdf2vla launch_sim --project projects/brev_franka --livestream 1 --public-ip STREAM_IPV4
    ```

=== "プライベートネットワーク"

    VM と手元のマシンを、相互に通信できる同じプライベートネットワークまたは VPN につなぎます。

    ```bash
    urdf2vla launch_sim --project projects/brev_franka --livestream 2
    ```

#### Isaac Sim WebRTC Streaming Client で接続する {#desktop-streaming-client}

**手元のマシン**の OS と CPU アーキテクチャに合う **Isaac Sim WebRTC Streaming Client** を取得します。
[NVIDIA のクライアント配布ページ](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/installation/download.html#latest-release)

| 手元の OS | インストール方法 |
|---|---|
| Windows | `.exe` インストーラーを実行し、スタートメニューから **Isaac Sim WebRTC Streaming Client** を開きます。 |
| macOS | Apple シリコンまたは Intel 向けの `.dmg` を開き、アプリを **Applications** にドラッグします。 |
| Ubuntu または Debian | ダウンロード先で、対象の `.deb` パッケージを `sudo apt install ./isaacsim-webrtc-streaming-client-*-linux-*.deb` でインストールし、アプリケーションメニューから開きます。 |

1. Brev VM で `launch_sim` を起動したまま、プロジェクトの `logs/` にある最新の `*_launch_sim.log` に `env ready` が出るまで待ちます。
2. 手元でクライアントを開きます。**Server** には、モード `1` なら `--public-ip` に渡した IPv4、モード `2` なら到達可能な VM のプライベート IPv4 を入力します。
3. Brev の公開エンドポイントを使う場合は、TCP の **Public Port** を **Signal** に、UDP の **Public Port** を **Stream** に入力します。Kit の既定ポートに直接接続する場合は `49100` と `47998` を使います。
4. **Connect** を押します。接続を切るにはクライアントを閉じ、Kit を停止するには VM の端末で Ctrl+C を押します。

<figure markdown="span">
  ![デスクトップクライアントの Server、Signal、Stream の入力欄と Connect ボタン](../assets/brev_webrtc_client.png)
  <figcaption>Isaac Sim WebRTC Streaming Client 2.0.0 の既定値。</figcaption>
</figure>

接続できない場合は **Signal** に入力した TCP ポートを確認し、ほかの配信クライアントを閉じます。接続できても画面が
空白の場合は、**Stream** に入力した UDP ポートを確認し、**View → Reload** を試します。
[NVIDIA のクライアント設定とトラブルシューティング](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/installation/manual_livestream_clients.html#isaac-sim-short-webrtc-streaming-client)

#### ポート番号が異なる場合のブラウザビューア {#remapped-kit-ports}

NVIDIA のブラウザビューアには、シグナリングと映像の公開ポートを個別に指定できます。`launch_sim --livestream 1` の
`--public-ip` に公開エンドポイントの到達可能な IPv4 アドレスを渡し、Kit は既定の宛先ポートで起動します。ビューアの
ビルドには、Brev に表示されたホスト名（`:port` の部分を除く）と **Public Port** の番号を使います。
[Isaac Sim 6.2 のブラウザビューア](https://github.com/isaac-sim/IsaacSim/blob/v6.2.0/tools/docker/web-viewer/Dockerfile)

```bash
viewer_dir=$(mktemp -d)
curl -fsSL https://raw.githubusercontent.com/isaac-sim/IsaacSim/v6.2.0/tools/docker/web-viewer/Dockerfile \
  -o "$viewer_dir/Dockerfile"
docker build -t urdf2vla/kit-web-viewer \
  --build-arg ISAACSIM_HOST=PUBLIC_HOSTNAME \
  --build-arg ISAACSIM_SIGNAL_PORT=PUBLIC_TCP_PORT \
  --build-arg ISAACSIM_STREAM_PORT=PUBLIC_UDP_PORT "$viewer_dir"
docker run --rm -p 127.0.0.1:8210:8210 urdf2vla/kit-web-viewer
```

手元のマシンで `brev port-forward INSTANCE --port 8210:8210` を実行し、Chromium または Chrome で
`http://localhost:8210` を開きます。ブラウザは WebRTC に Brev の公開エンドポイントを使います。Kit に同時に接続できるのは
クライアント 1 台です。既存の接続を閉じてから開いてください。[Brev のポートフォワーディング](https://docs.nvidia.com/brev/cli/connectivity) ·
[Isaac Sim クライアントの制限](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/installation/manual_livestream_clients.html)

<figure markdown="span">
  ![Franka の作業台、箱、ライムを表示している Isaac Sim の配信画面](../assets/brev_webrtc_viewport.png)
  <figcaption>Brev 経由でブラウザビューアに配信された Franka のシーン。</figcaption>
</figure>

下の節は、スクリプトがすることを 1 段階ずつ説明したものです。手で準備するときや、スクリプトがどこかの段階で止まった
ときに使ってください。

## 1. ドライバ 580 以降 {#driver}

Isaac Sim 6.2 と GR00T の image には**ドライバ 580 以降**が要ります。古いドライバでは、最初の Isaac Sim の工程が
`The NVIDIA driver on your system is too old (found version 12070)` で止まります（image の PyTorch が CUDA 13 向け）。

`nvidia-smi` で確かめます。古ければ、新しいドライバの入ったインスタンスのイメージを選ぶのが早いです。
その場で上げる手順（このインスタンスは 565.57.01 でした）:

```bash
# Brev は入っているドライバを hold している。hold を外す（nvidia-modprobe、nvidia-settings、nvidia-prime も）
sudo apt-mark unhold $(apt-mark showhold | grep -E 'nvidia|cuda-drivers')
# ドライバのパッケージを 580 の系列にそろえる（これがないと CUDA のリポジトリから新しい系列が混ざる）
apt-get download nvidia-driver-pinning-580 && sudo dpkg -i nvidia-driver-pinning-580_*.deb
# 古いドライバを外して 580 を入れる（カーネルモジュールは DKMS でビルドされる）
sudo apt-get purge -y $(dpkg -l | awk '$1 ~ /^[ih]i$/ && $2 ~ /565/ {print $2}')
sudo apt-get install -y nvidia-driver-580-open nvidia-utils-580 nvidia-compute-utils-580
sudo reboot
```

再起動のあと `nvidia-smi` が 580 を示します。続けて CDI の定義を作り直してください（次の節）。

## 2. Docker から GPU を使う（CDI） {#cdi}

Brev の NVIDIA Container Toolkit は **CDI モード**で、`docker run --gpus all` は `/etc/cdi/nvidia.yaml` にある GPU の
定義を使います。このインスタンスではこのファイルがなく、`docker run --gpus all` が次のエラーで止まりました。

```text
could not apply required modification to OCI specification: error modifying OCI spec:
failed to inject CDI devices: unresolvable CDI devices nvidia.com/gpu=all
```

作ります:

```bash
sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
docker run --rm --gpus all ubuntu nvidia-smi        # GPU が表示されれば OK
```

- ドライバが **580 より古い**と `nvidia-ctk` が落ちます（`nvSandboxUtilsShutdown` で `free(): invalid pointer`）。
  `--feature-flag=disable-nvsandboxutils` を付けます。
- **ドライバを更新したら作り直します。** ファイルにはドライバのライブラリの版が書かれていて、このインスタンスでは
  更新のあとも自動では作り直されませんでした。

## 3. Docker の保存先を大きいディスクに {#disk}

Brev のインスタンスはシステムのディスクが小さく（このインスタンスは 124 GB）、大きいディスクが `/data`（295 GB）に
あります。Docker の image とリポジトリを `/data` に置きます。

```bash
# Docker: /etc/docker/daemon.json に "data-root": "/data/docker" を足す（nvidia の runtime の設定は残す）
sudo systemctl restart docker && docker info | grep "Root Dir"
# リポジトリ
cd /data && git clone https://github.com/momoiorg-repository/urdf2vla.git
```

容量を使うもの（実測）:

| | |
|---|---|
| image | Isaac Lab Arena 38 GB、GR00T 26 GB（と、層を共有する 26 GB の base image）。合わせて約 64 GB。ビルドのキャッシュが最大 50 GB（`docker builder prune` で消せる） |
| データセット | FANUC のデモ 50 個: HDF5 で 2.6 GB、変換後 61 MB |
| GR00T のモデル | `~/.cache/huggingface` に 6.5 GB（`nvidia/GR00T-N1.7-DROID`）+ 4.6 GB（`nvidia/Cosmos-Reason2-2B`） |
| **checkpoint** | **1 つ 24 GB**（重み 11 GB + optimizer の状態 13 GB）。`train_vla` は既定で 2 つ残す（`--save-total-limit`） |

足りなくなるのは checkpoint です。1 回の学習で 5 つ残していたとき、2 回の学習で 295 GB のディスクがいっぱいになり、
2 回目は保存の途中で止まりました。今の `train_vla` は、checkpoint の分の空きがなければ始めません。学習が終わった
checkpoint の `optimizer.pt` は学習の再開にしか使わないので、消して空きを作れます（重みは `evaluate_vla` に使えます）。

## 4. ビルドと起動 {#build}

```bash
uv venv .venv && uv pip install -e '.[view,dev,docs]' xacro && source .venv/bin/activate
bash docker/build.sh            # L40S で Arena の image 13 分、GR00T の image 4 分
bash docker/run.sh && bash docker/run.sh gr00t
urdf2vla doctor
```

`docker/build.sh` は SSH が切れても続くように実行してください（`tmux`、または `setsid nohup … &`）。起動したシェルが
終わるとビルドも止まります。

## 5. Hugging Face {#huggingface}

GR00T N1.7 は画像と言語のモデル `nvidia/Cosmos-Reason2-2B` を使います。これは **gated**（利用規約への同意が要る）で、
アクセスできるまで学習と評価が `401 Client Error … Cannot access gated repo` で止まります。

1. [huggingface.co/nvidia/Cosmos-Reason2-2B](https://huggingface.co/nvidia/Cosmos-Reason2-2B) を開き、利用規約に同意します。
2. [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) で read 権限のトークンを作ります。
3. インスタンスでログインします（トークンは `~/.cache/huggingface/token` に保存され、コンテナと共有されます）。

    ```bash
    hf auth login
    ```

インスタンスを止めるとホームディレクトリが消える場合は、トークンを `/data` にも置き（例: `/data/secrets/hf_token`、
権限 600）、再開のあと `~/.cache/huggingface/token` にコピーし直します。

## 6. 例を動かす

手順は[自分のロボットで](your-robot.md)と同じです。L40S、FANUC CRX-3iA での実測:

| 工程 | 結果 | 時間 | GPU メモリの最大 |
|---|---|---|---|
| `import_robot`（1 回目） | 検査 10/10、初期姿勢を質問 | 447 秒（Isaac Sim の初回の起動でシェーダのキャッシュを作る） | 4.9 GB |
| `import_robot`（`answer robot.default_pose=suggested` の後） | 10/10 | 37 秒 | 4.8 GB |
| `launch_sim` | 22/22（カメラ、物体、把持） | 204 秒 | 5.5 GB |
| `setup_teleoperation` | 4/4 | 64 秒 | 5.5 GB |
| `collect_dataset --device scripted --num-demos 50` | 59 回中 50 回成功（84.7 %） | 33 分 | 5.5 GB |
| `convert_dataset` | 5/5 | 68 秒 | – |
| `convert_dataset --layout droid` | 6/6（100 エピソード: 各デモを左と右のカメラで） | 3 分 | – |
| `train_vla`（urdf2vla の並び、カメラ 3 台、10,000 step、batch 32） | release_check 10/10 | 約 2 時間 40 分（1.07 step/秒） | 41.9 GB |
| `train_vla`（`--layout droid`、カメラ 2 台） | release_check 10/10 | 2 時間 8 分（1.38 step/秒） | 40.9 GB |
| `evaluate_vla --episodes 20 --video` | [結果](#results)を参照 | 10〜11 分 | 12.4 GB |

学習と評価は同時に GPU に載りません（41 GB + Isaac Sim + 推論サーバ）。学習中に別の checkpoint を評価するには、
checkpoint を保存した直後に学習を止めて評価し、同じ `train_vla` を実行し直します。最後の checkpoint から再開します。

30 分の評価の前に、モデルがグリッパを開くかを確かめられます（1 分ほど、シミュレーションなし）。

```bash
urdf2vla train_vla --project projects/crx3ia --dataset demos_droid --check-only training/crx3ia_demos_droid/checkpoints/checkpoint-10000
```

## 結果 {#results}

どちらのモデルも `nvidia/GR00T-N1.7-DROID`（単腕のロボットの既定）から、同じスクリプトのデモ 50 個で fine-tune し、
同じ 20 エピソード（同じ乱数の種: 毎回ランダムな物体と位置）で評価しました。

| | urdf2vla の並び（既定） | `--layout droid` |
|---|---|---|
| モデルが見るカメラ | 手先 + 左 + 右 | 手先 + 外部カメラ 1 台（学習はエピソードごとに左か右、評価は左） |
| 腕の状態と行動 | 関節目標 6 | DROID の手先の姿勢 + 関節 7（7 つ目は 0 で埋める） |
| **成功** | **20/20** | 19/20（洋梨を 1 回離さなかった） |
| りんご / 立方体 / ライム / 洋梨 | 5/5、7/7、3/3、5/5 | 5/5、7/7、3/3、4/5 |
| エピソードの長さ（中央値） | 174 step（8.7 秒） | 173 step |
| 置いた位置と目標の距離（中央値） | 15 mm | 12 mm |

カメラ 3 台のモデルは Hugging Face にあります:
[`umegan/urdf2vla-crx3ia-gr00t-n1.7`](https://huggingface.co/umegan/urdf2vla-crx3ia-gr00t-n1.7)（NVIDIA Open Model
License。urdf2vla での評価の手順はモデルカードにあります）。

**既定の urdf2vla の並び**（全カメラ、`convert_dataset` に `--layout` を付けない）を使ってください。同等以上の結果で、
毎フレーム 3 方向の画像をモデルが見ます。DROID の並びは学習が約 30 % 速いので、試験用に残しています。

最初の DROID の並びのモデルは、グリッパを開いてから 13 step で終わるエピソードで学習し、0/3 でした。どの物体も掴んで
箱まで運び、離しませんでした。今の `convert_dataset` は最後のフレームを足して開く動作を学べるようにし、それを検査し
（`gripper_timing`）、`train_vla` は評価の前に学習したモデルを検査します（`release_check`: そのモデルは 0/10、上の 2 つは 10/10）。

![Four moments of one evaluation episode](../assets/crx3ia_model_steps.png)

評価の動画と画像: [学習済みモデル](../models.md#crx3ia)。

## GR00T のサーバのポート {#port}

Brev では NVIDIA DCGM の `nv-hostengine` が `127.0.0.1:5555`（GR00T の推論サーバの既定のポート）を使っています。
`evaluate_vla` は空いているポートに自動で移り、警告で知らせます。`doctor` は info として表示します。

## SSH でのビューアと遠隔操作

- `urdf2vla view` はポート 8080 で動きます: `ssh -L 8080:localhost:8080 <インスタンス>` で転送し、`http://localhost:8080` を開きます。
- 端末のキーボードで記録します: 1 つ目の SSH で `collect_dataset --device socket`、2 つ目で `urdf2vla teleop-keys`。
