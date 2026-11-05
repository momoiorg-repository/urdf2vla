# Use it from Claude Code

urdf2vla ships with an agent skill for [Claude Code](https://docs.claude.com/en/docs/claude-code/overview)
(`.claude/skills/urdf2vla/SKILL.md`). With it, you can describe what you want in your own words and let the agent run the
steps, read the results and come back only when it needs you.

```bash
cd urdf2vla
claude
```

```text
> I want to start VLA development with my robot. The URDF is /home/me/robots/arm.urdf
```

The agent creates the project, runs the steps in order, shows you the camera snapshots, and asks you the questions
urdf2vla returns — in your language.

## What the agent will and won't do

The skill keeps the agent to urdf2vla's own commands, so what it does is exactly what you could do by hand.

| The agent does by itself | The agent asks you first |
|---|---|
| Run the next step, start a stopped container | Anything about your robot: the hand link, finger direction, starting pose, joint roles |
| Retry with a longer timeout on the first, slow start of Isaac Sim | Changing joint gains, editing the URDF, adding a Docker mount |
| Fix camera placement, the table layout or scripted-grasp heights through `project.yaml` overrides | Replacing the gripper with a SimReady asset |
| Raise the solver iterations when a grasp check fails | Anything it can't explain from the logs — it shows you the relevant log lines |

It never invents values for your robot: when urdf2vla offers a suggestion (such as an IK starting pose), the agent shows
it to you and waits for your answer. It also never edits the generated files directly — changes go through
`project.yaml` or `urdf2vla answer`, so they survive the next run.

## Combining it with the browser viewer

For questions you answer by looking — which link is the hand, which way the fingers open, where a camera points — ask
the agent to open [`urdf2vla view`](view.md). Save your answers in the browser and tell the agent "saved"; it continues
from there.

## Without Claude Code

Nothing in urdf2vla depends on the agent. Every step is a plain `urdf2vla` command with a JSON result, so you can script
it or call it from another agent: `urdf2vla skills --json` lists the steps with their inputs, outputs and prerequisites,
and `urdf2vla status --project ...` tells you what to run next.
