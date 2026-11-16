# urdf2vla

**ロボットの URDF から、NVIDIA Isaac Sim で学習・評価した VLA（Vision-Language-Action）モデルまで。**

<video autoplay loop muted playsinline width="100%" src="../assets/crx3ia_model.mp4"></video>
<p class="caption"><em>urdf2vla で学習した GR00T N1.7 のモデルが、Isaac Sim で FANUC CRX-3iA を動かすところ（評価で 20/20）。
各フレームはモデルが見る画像: 手先カメラと左右のカメラ。
<a href="models/">学習済みモデル</a> · <a href="https://huggingface.co/umegan/urdf2vla-crx3ia-gr00t-n1.7">Hugging Face</a></em></p>

## 誰のためのツールか

URDF のある腕を持っていて、それで VLA 開発を始めたいロボット開発者のためのツールです。Isaac Sim や Omniverse、VLA の
学習に先に詳しくならなくても、シミュレーションの環境を作り、デモを記録し、VLA を fine-tune して、どれだけうまく動くかを
測れます。

## できること

- **URDF を、検査済みの Pick & Place の環境として Isaac Sim に取り込む。** 台・物体・箱・カメラは腕の届く範囲から
  配置します。urdf2vla が聞くのは URDF から分からないこと（どの link が手先か、よい初期姿勢）だけで、ブラウザで
  ロボットを見ながら答えられます。
- **グリッパの取り付け・付け替え**を 1 行で。NVIDIA のアセットライブラリのグリッパを使います。
- **デモを記録する。** scripted の制御、キーボード、または少ないデモを Isaac Lab Mimic で多数に増やします。
- **NVIDIA Isaac GR00T N1.7 を fine-tune し**、**シミュレーションでモデルを評価する。** 成功率、失敗ごとの理由、動画が
  得られます。
- **すべてをクラウドの GPU（NVIDIA Brev）で動かす**ことも、AI エージェント（Claude Code、Codex）に工程を任せてロボットに
  ついての質問にだけ答えることもできます。

どの工程も 1 つのコマンドで、自分の結果を検査します。次へ進むか、質問するか、何を変えればよいかを伝えます。

```mermaid
flowchart LR
  U[ロボットの URDF] --> S[Isaac Sim の環境<br/>+ 物理の検査] --> R[デモ] --> T[GR00T N1.7<br/>fine-tune] --> E[シミュレーション<br/>での評価]
```

## 結果

| ロボット | モデル | シミュレーションでの成功 |
|---|---|---|
| FANUC CRX-3iA + Robotiq 2F-85 | [`umegan/urdf2vla-crx3ia-gr00t-n1.7`](https://huggingface.co/umegan/urdf2vla-crx3ia-gr00t-n1.7) | **20/20** |

scripted のデモ 50 本から、NVIDIA L40S 1 台で約 2 時間 40 分で学習しました。動画と詳細:
[学習済みモデル](models.md)。

## 今後の発展

- **実機:** 学習したモデルをロボットのコンピュータ（Jetson Thor、RTX GPU）で動かし、シミュレーションと実際のカメラ・
  照明・物体との差を埋める。
- **シーンとタスクを増やす:** NVIDIA の SimReady アセットのシーンと物体、複数の物体を同時に、Pick & Place 以外のタスク。
- **2 本の腕の協調**、グリッパの追加、USD だけで記述されたロボット。
- **データを増やし、質を上げる:** 人が記録したデモを Mimic で増やす。

## いまの範囲

2 本指のグリッパの付いた単腕・双腕、タスクは 1 つ（物体を箱に入れる）、シミュレーションのみ。学習には **44 GB 以上**の
GPU が必要です。それ以外は 16 GB の RTX GPU で動きます。

## はじめに読むもの

<div class="grid cards" markdown>

- **[インストール](install.md)** — 必要なものと、コンテナの作り方。
- **[AI エージェントから使う](guides/claude-code.md)** — Claude Code や Codex に工程を任せる。
- **[チュートリアル](tutorial.md)** — 同梱の Franka Panda で全工程を一度通す。
- **[自分のロボットを使う](guides/your-robot.md)** — FANUC CRX-3iA を例に、URDF から始める。

</div>
