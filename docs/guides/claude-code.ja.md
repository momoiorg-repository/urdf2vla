# AI エージェントから使う

**ロボットのシミュレーションが初めてなら、エージェントに任せてください。** urdf2vla には Agent Skill が付いていて、
コーディングエージェントが工程を実行し、結果を読み、直してよいものは直し、ロボットについての事実が必要なときだけ
あなたに戻ってきます。[Claude Code](https://docs.claude.com/en/docs/claude-code/overview) と OpenAI の
[Codex](https://developers.openai.com/codex) のどちらでも使え、どちらも同じ Skill のファイル
（`.agents/skills/urdf2vla/SKILL.md`。[Agent Skills](https://agentskills.io) の形式）を読みます。

=== "Claude Code"

    ```bash
    cd urdf2vla
    claude
    ```

    Claude Code は、同じファイルへのリンク `.claude/skills/urdf2vla/SKILL.md` から Skill を見つけます。

=== "Codex"

    ```bash
    cd urdf2vla
    codex
    ```

    Codex は `.agents/skills/` から Skill を見つけます。urdf2vla は Docker と Isaac Sim を動かすので、Codex の
    サンドボックスの既定では許されないことがあります。Codex が聞いてきたら許可するか、それを許すモードで起動してください。

あとは、やりたいことを自分の言葉で伝えます。

```text
> 自分のロボットで VLA 開発を始めたい。URDF は /home/me/robots/arm.urdf
```

エージェントは最初にマシンを検査し（`urdf2vla doctor`）、プロジェクトを作り、工程を順に実行し、カメラの画像を見せ、
urdf2vla が返した質問をあなたに聞きます。

## エージェントがすること・しないこと

Skill はエージェントが urdf2vla のコマンドだけを使うように決めているので、エージェントがすることは、あなたが手で実行できることと同じです。

| エージェントが自分でする | エージェントが先に聞く |
|---|---|
| 次の工程を実行する、止まっているコンテナを起動する | ロボットについてのこと: 手先の link、指の向き、初期姿勢、関節の役割 |
| 環境がおかしいときに `urdf2vla doctor` を実行し、直し方を伝える | `sudo` が要ること: GPU ドライバの更新、`docker` グループへの追加 |
| Isaac Sim の最初の遅い起動のときに、タイムアウトを延ばして再実行する | 関節ゲインの変更、URDF の編集、Docker のマウントの追加 |
| カメラの配置・台の配置・スクリプトの把持の高さを `project.yaml` の overrides で直す | NVIDIA のアセットライブラリのグリッパを付ける・付け替えること |
| 把持の検査が失敗したときに、ソルバーの反復回数を増やす | ログから説明できないこと（関係するログの行を見せます） |

エージェントはロボットの値を作り上げません。urdf2vla が提案（IK の初期姿勢など）を出したときも、それを見せて答えを待ちます。
生成されたファイルも直接は編集しません。変更は `project.yaml` か `urdf2vla answer` で行うので、次に実行しても残ります。

## ブラウザの画面と組み合わせる

見て答える質問（どの link が手先か、指はどちらに開くか、カメラがどこを向くか）では、エージェントに
[`urdf2vla view`](view.md) を開くよう頼んでください。ブラウザで答えを保存し、エージェントに「保存した」と伝えれば続きを実行します。

## エージェントを使わない場合

urdf2vla はエージェントがなくても動きます。各工程は JSON の結果を返す普通の `urdf2vla` コマンドなので、手で実行しても、
スクリプトからも、他のエージェントからも呼べます。`urdf2vla skills --json` は各工程の入出力と前提を、
`urdf2vla status --project ...` は次に実行するものを、`urdf2vla doctor` はマシンの検査結果を返します。
