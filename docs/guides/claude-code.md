# Run with Codex or Claude Code

Tell a coding agent what you want to do in plain language. It uses urdf2vla's Agent Skill to run the CLI commands,
read the results, and ask you for decisions about your robot as needed. Both agents can read the same file at
`.agents/skills/urdf2vla/SKILL.md`. [Codex](https://developers.openai.com/codex) ·
[Claude Code](https://docs.claude.com/en/docs/claude-code/overview) · [Agent Skills format](https://agentskills.io)

=== "Claude Code"

    ```bash
    cd urdf2vla
    claude
    ```

    Claude Code finds the skill through `.claude/skills/urdf2vla/SKILL.md`, a link to the same file.

=== "Codex"

    ```bash
    cd urdf2vla
    codex
    ```

    Codex finds the skill in `.agents/skills/`. urdf2vla runs Docker and Isaac Sim, which Codex's sandbox may not
    allow by default: approve those commands when Codex asks, or start it in a mode that allows them.

Then say what you want, in your own words and language:

```text
> I want to start VLA development with my robot. The URDF is /home/me/robots/arm.urdf
```

The agent checks your machine first (`urdf2vla doctor`), creates the project, runs the steps in order, shows you the
camera snapshots, and asks you the questions urdf2vla returns.

## What the agent will and won't do

The skill keeps the agent to urdf2vla's own commands, so what it does is exactly what you could do by hand.

| The agent does by itself | The agent asks you first |
|---|---|
| Run the next step, start a stopped container | Anything about your robot: the hand link, finger direction, starting pose, joint roles |
| Run `urdf2vla doctor` when the environment looks wrong, and tell you the fix | Anything that needs `sudo`: updating the GPU driver, adding you to the `docker` group |
| Retry with a longer timeout on the first, slow start of Isaac Sim | Changing joint gains, editing the URDF, adding a Docker mount |
| Fix camera placement, the table layout or scripted-grasp heights through `project.yaml` overrides | Attaching or replacing the gripper with one from NVIDIA's asset library |
| Raise the solver iterations when a grasp check fails | Anything it can't explain from the logs — it shows you the relevant log lines |

It never invents values for your robot: when urdf2vla offers a suggestion (such as an IK starting pose), the agent shows
it to you and waits for your answer. It also never edits the generated files directly — changes go through
`project.yaml` or `urdf2vla answer`, so they survive the next run.

## Combining it with the browser viewer

For questions you answer by looking — which link is the hand, which way the fingers open, where a camera points — ask
the agent to open [`urdf2vla view`](view.md). Save your answers in the browser and tell the agent "saved"; it continues
from there.

## Run the same commands yourself {#without-an-agent}

Follow the [Franka CLI tutorial](../tutorial.md) to run each command yourself, or ask the agent to “run the Franka
tutorial” to follow along. For repeatable runs, see [shell script automation](../reference.md#automation).
When you're ready to use your own URDF, see [Simulate your own robot](your-robot.md).
