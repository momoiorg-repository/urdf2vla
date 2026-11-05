# Bring your own robot

This guide takes you from your own URDF to a running simulation. Do the [tutorial](../tutorial.md) with the Franka
first if you haven't: the steps are the same, and you'll know what success looks like.

## 1. Check your URDF

urdf2vla reads everything it can from the URDF. Before you start, make sure that:

- **Meshes can be found.** `package://my_robot_description/meshes/...` paths are resolved by searching the URDF's
  parent folders and any folders you list in `robot.package_paths`. Meshes outside the repository are copied into the
  project automatically, so the container can read them.
- **Joint limits and efforts are real values.** urdf2vla warns when every joint has the same effort or a joint has no
  limits — CAD exporters often write placeholders. You can override efforts without editing the URDF (see below).
- **The gripper is in the URDF**, or you plan to attach a [SimReady gripper](#simready).
  Supported today: single-arm robots with a two-finger gripper (prismatic or revolute fingers, with or without `mimic`).

## 2. Create a project

```bash
urdf2vla init projects/myrobot --urdf /path/to/my_robot.urdf
```

This creates `projects/myrobot/project.yaml`, the only file you edit. Everything urdf2vla derives goes into
`generated/`, `build/`, `snapshots/` and `logs/` next to it — don't edit those by hand.

```yaml title="projects/myrobot/project.yaml"
name: myrobot

robot:
  urdf: /path/to/my_robot.urdf
  # Optional. Anything you write here is not asked again.
  # end_effector: tool0
  # gripper_joints: [finger_left, finger_right]
  # gripper_open_position: upper        # fingers open at the joint's lower or upper limit
  # default_pose: {joint1: 0.0, joint2: -0.6}
  # effort_limits: {"joint[1-6]": 50.0} # N·m, joint-name regex → value
  # base_pose: {pos: [0, 0, 0], rot_xyzw: [0, 0, 0, 1]}
  # package_paths: [../my_ws/src]

cameras:          # DROID layout: wrist + left + right
  - preset: wrist
  - preset: left
  - preset: right

overrides:        # change generated values without editing generated/
  robot: {}
  task: {}
  cameras: {}
```

!!! note "Messages are in Japanese"
    urdf2vla's command output, questions and the comments in `project.yaml` are currently in Japanese. The keys
    (`arms.0.ee_link`, `status`, …) and file names are the same in every language, and this guide explains each of them.

## 3. Run the setup steps and answer questions

```bash
urdf2vla inspect_robot    --project projects/myrobot
urdf2vla configure_robot  --project projects/myrobot
urdf2vla configure_task   --project projects/myrobot
urdf2vla configure_camera --project projects/myrobot
urdf2vla import_robot     --project projects/myrobot
urdf2vla launch_sim       --project projects/myrobot
```

Every command prints a JSON result. `"status"` tells you what to do next:

| `status` | Exit code | Meaning |
|---|---|---|
| `ok` | 0 | Done. `next` names the following step. Read any `warnings`. |
| `needs_input` | 2 | urdf2vla needs a fact about your robot. Answer the `questions`, then run the step in `next`. |
| `error` | 1 | A step or a check failed. `errors` says which; run `urdf2vla diagnose --project ...` and see [Troubleshooting](../troubleshooting.md). |

### The questions you may get

| Key | You answer | Asked when |
|---|---|---|
| `arms.0.ee_link` | the link that is the robot's hand | more than one link could be the hand |
| `grippers.0.open_position` | `lower` or `upper` | a revolute finger joint's opening direction isn't clear |
| `grippers.0.open_close` | `[open, closed]` angles in rad | a finger joint has no limits |
| `robot.gripper_joints` | list of finger joint names | no gripper was found automatically |
| `robot.front_axis` | `+x`, `+y`, `-x` or `-y` | the robot doesn't face +x; it is rotated so it does |
| `joints.<name>.role` | `fixed_at_reset` or `passive` | a movable joint is neither arm nor gripper |
| `robot.default_pose` | `suggested`, `keep` or `{joint: value, …}` | after import, the hand doesn't point down at the table; `suggested` is an IK solution 20 cm above the table |

Answer on the command line, then run the step named in `next`:

```bash
urdf2vla answer --project projects/myrobot arms.0.ee_link=tool0 grippers.0.open_position=lower
urdf2vla configure_robot --project projects/myrobot
```

…or **answer in the browser**, looking at the robot: [`urdf2vla view`](view.md) shows the candidates in 3D and saves
your answers to the same file (`answers.yaml`).

<figure markdown="span">
  ![The question panel of urdf2vla view](../assets/view_questions.png){ width="900" }
  <figcaption>A starting-pose question in <code>urdf2vla view</code>: the hand didn't point down at the table, so
  <code>import_robot</code> proposed a pose found by inverse kinematics. <b>提案の姿勢を見る</b> (show the suggested
  pose) moves the robot to it before you answer <code>suggested</code>.</figcaption>
</figure>

## 4. Check the result

`launch_sim` saves what each camera sees at the start and end of a short test into `snapshots/`, and runs physics
checks — including picking up the cube — before you spend time recording. If every check is `OK`, continue with
[recording demonstrations](recording.md).

## 5. Swap in a SimReady gripper {#simready}

Some URDF grippers don't behave in simulation even though they look right: closed linkages are simplified, mimic joints
are converted badly, or joint limits aren't enforced. The symptom is a gripper that opens and closes but can't hold the
cube — urdf2vla's `joint_limits_hold` and `grasp_hold` checks fail.

The fix is one line in `project.yaml`: replace the URDF gripper with a physics-ready gripper from NVIDIA's
SimReady assets (the robot and gripper assets that ship with Isaac Sim).

```yaml
robot:
  urdf: /path/to/my_robot.urdf
  gripper: {simready: robotiq_2f_85}
```

- `configure_robot` removes the URDF gripper's links and mounts the SimReady gripper at the same place, aligning its
  approach and finger directions. The hand and finger questions are no longer asked.
- `import_robot` converts the arm and attaches the gripper with Isaac Sim's **Robot Assembler**. The gripper asset is
  downloaded the first time.
- If your URDF has no gripper, say where to mount it: `gripper: {simready: robotiq_2f_85, mount_link: flange}`.
- Supported: `robotiq_2f_85` (Robotiq 2F-85). The tuned values (grip force, solver iterations, grasp point) live in
  `src/urdf2vla/grippers.py`.

## 6. Adjust the scene and cameras

The table, cube and target are placed from your arm's reach. To change them, write the generated keys under
`overrides.task` (same structure as `generated/task.yaml`) and run `configure_task` again:

```yaml
overrides:
  task:
    object: {size: [0.04, 0.04, 0.06], mass: 0.05}
    target: {radius: 0.05}
    language_instruction: "put the red block on the green circle"
```

Cameras: the default is the [DROID](https://droid-dataset.github.io/) layout (a wrist camera plus left and right
cameras looking down at the table, 256×256). Other presets are `front` and `side`. If you have a real camera to match,
describe it explicitly:

```yaml
cameras:
  - preset: wrist
  - {name: head_cam, mount: link, parent_link: head_link, pos: [0, 0, 0], rot_xyzw: [0, 0, 0, 1],
     convention: ros, width: 640, height: 480, intrinsic_matrix: [600, 0, 320, 0, 600, 240, 0, 0, 1]}
```

Quaternions are always `[x, y, z, w]`.

## Limitations

- **Dual-arm robots** can be inspected, configured and imported (with all checks), but `launch_sim` and later steps
  don't support them yet.
- One task: pick and place with primitive shapes (box, cylinder target).
- Grippers: two-finger parallel or single-moving-jaw grippers. Suction and multi-finger hands are not supported.
