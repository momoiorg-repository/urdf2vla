# Claude Code から使う

urdf2vla には [Claude Code](https://docs.claude.com/en/docs/claude-code/overview) 用の Agent Skill
（`.claude/skills/urdf2vla/SKILL.md`）が付いています。やりたいことを自分の言葉で伝えると、Agent が工程を実行し、
結果を読み、あなたが必要なときだけ戻ってきます。

```bash
cd urdf2vla
claude
```

```text
> 自分のロボットで VLA 開発を始めたい。URDF は /home/me/robots/arm.urdf
```

Agent はプロジェクトを作り、工程を順に実行し、カメラの画像を見せ、urdf2vla が返した質問をあなたに聞きます。

## Agent がすること・しないこと

Skill は Agent が urdf2vla のコマンドだけを使うように決めているので、Agent がすることは、あなたが手で実行できることと同じです。

| Agent が自分でする | Agent が先に聞く |
|---|---|
| 次の工程を実行する、止まっているコンテナを起動する | ロボットについてのこと: 手先の link、指の向き、初期姿勢、関節の役割 |
| Isaac Sim の最初の遅い起動のときに、タイムアウトを延ばして再実行する | 関節ゲインの変更、URDF の編集、Docker のマウントの追加 |
| カメラの配置・台の配置・スクリプトの把持の高さを `project.yaml` の overrides で直す | グリッパを SimReady のアセットに付け替えること |
| 把持の検査が失敗したときに、ソルバーの反復回数を増やす | ログから説明できないこと（関係するログの行を見せます） |

Agent はロボットの値を作り上げません。urdf2vla が提案（IK の初期姿勢など）を出したときも、それを見せて答えを待ちます。
生成されたファイルも直接は編集しません。変更は `project.yaml` か `urdf2vla answer` で行うので、次に実行しても残ります。

## ブラウザの画面と組み合わせる

見て答える質問（どの link が手先か、指はどちらに開くか、カメラがどこを向くか）では、Agent に
[`urdf2vla view`](view.md) を開くよう頼んでください。ブラウザで答えを保存し、Agent に「保存した」と伝えれば続きを実行します。

## Claude Code を使わない場合

urdf2vla は Agent がなくても動きます。各工程は JSON の結果を返す普通の `urdf2vla` コマンドなので、スクリプトからも、
他の Agent からも呼べます。`urdf2vla skills --json` は各工程の入出力と前提を、`urdf2vla status --project ...` は次に
実行するものを返します。
