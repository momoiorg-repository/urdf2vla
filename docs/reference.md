# Reference

## Commands

| Command | What it does |
|---|---|
| `urdf2vla init DIR --urdf URDF` | Create a project for a URDF. `--from examples/<robot>/project.yaml` starts from an example instead. |
| `urdf2vla <skill> --project DIR [options]` | Run one step (see below). `urdf2vla <skill> --help` lists its options. |
| `urdf2vla status --project DIR` | Each step's last result and what to run next. |
| `urdf2vla answer --project DIR key=value …` | Record answers to questions in `answers.yaml`. Values are YAML: `'robot.default_pose={j1: 0.0, j2: 0.3}'`. |
| `urdf2vla show --project DIR <name>` | Print a generated file, e.g. `robot_metadata`, `robot_config`. |
| `urdf2vla diagnose --project DIR` | Match the last failed step's log against known problems. |
| `urdf2vla view --project DIR [--port 8080]` | The [browser viewer](guides/view.md). Needs `.[view]`. |
| `urdf2vla teleop-keys [--port 50555]` | Send terminal key presses to `collect_dataset --device socket`. |
| `urdf2vla skills [--json]` | The steps with their inputs, outputs and prerequisites (machine-readable with `--json`). |

## Steps (skills)

| Step | What it does |
|---|---|
| `inspect_robot` | Reads the URDF: links and joints, the arm, the gripper (including mimic joints), hand-link candidates, mesh files. Asks about anything ambiguous. |
| `configure_robot` | Builds the robot configuration from the URDF and your answers, and writes a normalized URDF (`package://` resolved, empty root link removed). |
| `configure_task` | Places the table, the cube and the target from the arm's reach and shoulder height. |
| `configure_camera` | Places the cameras: DROID layout by default (`wrist`, `left`, `right`), or `front` / `side` presets, or explicit cameras. |
| `import_robot` | Converts the URDF to USD (attaching a SimReady gripper if configured) and runs the robot checks. Proposes an IK starting pose if the hand doesn't point down. |
| `launch_sim` | Builds the pick-and-place environment with Isaac Lab Arena, saves camera snapshots, checks cameras, scene and a test grasp. `--livestream` keeps it running for a WebRTC client. |
| `configure_vla_interface` | Defines the VLA's inputs (camera images, arm joints + gripper value) and outputs (joint targets + gripper command). |
| `setup_teleoperation` | Configures hand-space control through inverse kinematics and checks the hand moves as commanded. |
| `collect_dataset` | Records successful demonstrations (`--device scripted`, `keyboard` or `socket`) to HDF5 and checks them. |
| `convert_dataset` | Converts the recording to LeRobot v2.1 with GR00T N1.7's modality files and checks it with GR00T's loader. |
| `train_vla` | Writes and checks a GR00T N1.7 fine-tuning job; trains if the GPU has 44 GB or more, otherwise packages the job. |
| `evaluate_vla` | Runs episodes with a GR00T inference server (`--model`) or a dataset replay (`--replay`) and reports the success rate. |

Where each step runs, what it needs first, what it reads and writes, and its options
(*host* = your machine, *sim* = the simulation container, *gr00t* = the GR00T container):

--8<-- "skills-table.md"

## The result of every step

Every step prints one JSON object and saves it in `logs/latest_<step>.json`:

| Field | Meaning |
|---|---|
| `status` | `ok`, `needs_input` or `error` (exit code 0, 2 or 1) |
| `next` | The step(s) to run next |
| `questions` | With `needs_input`: `key`, `prompt`, `candidates`, `suggestion`, `reason` |
| `inferred` | Values derived from the URDF that you may want to confirm |
| `warnings` | Things worth knowing that didn't stop the step |
| `errors` | Why the step failed |
| `outputs` | Files written (relative to the project) |
| `data` | The step's main results, including `checks` |
| `log` | The full log file |

## `project.yaml`

```yaml
name: myrobot

robot:
  urdf: /path/to/robot.urdf            # required
  package_paths: [../my_ws/src]        # extra folders to resolve package:// paths
  end_effector: tool0                  # the hand link (otherwise asked if ambiguous)
  control_joints: [joint1, joint2]     # arm joints (otherwise detected)
  gripper_joints: [finger_l, finger_r] # finger joints (otherwise detected)
  gripper_open_position: upper         # fingers open at the lower or upper joint limit
  default_pose: {joint1: 0.0}          # starting pose [rad or m]
  effort_limits: {"joint[1-6]": 50.0}  # N·m; joint-name regex → value, overrides the URDF
  base_pose: {pos: [0, 0, 0], rot_xyzw: [0, 0, 0, 1]}
  gripper: {simready: robotiq_2f_85}   # replace the URDF gripper (+ mount_link if the URDF has none)

cameras:                               # presets: wrist, left, right, front, side — or explicit
  - preset: wrist
  - preset: left
  - preset: right

overrides:                             # same structure as the files in generated/
  robot: {}                            #   generated/robot_config.yaml
  task: {}                             #   generated/task.yaml
  cameras: {}                          #   generated/cameras.yaml
  teleop: {}                           #   generated/teleop.yaml
```

Frequently used overrides:

| Key | Default | Effect |
|---|---|---|
| `overrides.robot.asset.solver_position_iterations` | 32 (64 with a SimReady gripper) | Contact accuracy; raise when fingers sink into objects |
| `overrides.task.object.size` / `.mass` | `[0.045, 0.045, 0.045]` m / 0.1 kg | The cube |
| `overrides.task.target.radius` | 0.06 m | The target, also the success radius (`task.success.radius`) |
| `overrides.task.language_instruction` | "pick up the red cube and place it on the green target" | The task text given to the VLA |
| `overrides.task.episode_length_s` | 30 s | Episode timeout |
| `overrides.task.render_fix` | false | Render once more per step if a camera freezes |
| `overrides.teleop.pos_step` / `.rot_step` | 0.008 m / 0.04 rad | Keyboard step size per control step |
| `overrides.teleop.grasp_height_offset` | 0 m | Scripted grasp height relative to the cube center |

## Project folder

```text
projects/<name>/
  project.yaml      your settings            answers.yaml   your answers
  generated/        robot_metadata, robot_config, task, cameras, vla_interface, teleop (do not edit)
  build/            robot.urdf, usd/, meshes/, *_report.yaml, eval_videos/
  snapshots/        camera images from launch_sim, collect_dataset and evaluate_vla
  datasets/         <name>.hdf5 recordings, <name>_gr00t/ converted data
  training/         <job>/train.sh, <job>.tar.gz, <job>/checkpoints/
  logs/             one log and one JSON result per run; latest_<step>.json
  state.json        the last status of every step
```

## `runtime.yaml`

Where the steps run. The defaults match `docker/build.sh` and `docker/run.sh`.

| Key | Default | Meaning |
|---|---|---|
| `mode` | `docker` | `docker`, or `local` to run in the current Python environment (inside an Isaac Lab installation) |
| `container` / `image` | `urdf2vla-sim` / `urdf2vla/isaaclab_arena:b9e37c5` | The simulation container |
| `python` | `…/isaaclab.sh -p` | How Python is started in that container |
| `mounts` | the repository at `/workspace/urdf2vla` | Host folders visible in the containers |
| `env` | — | Extra environment variables |
| `gr00t.container` / `gr00t.image` | `urdf2vla-gr00t` / `urdf2vla/gr00t:23ace64` | The GR00T container |

## Checks

| Step | Checks |
|---|---|
| `import_robot` | `articulation_root`, `all_joints_driven`, `mimic_constraints_off`, `joint_name_match`, `bodies_exist`, `no_nan`, `default_pose_tracking`, `within_limits`, `gripper_motion:<gripper>`, `joint_limits_hold:<gripper>` |
| `launch_sim` | `camera_alive:<camera>`, `camera_not_black:<camera>`, `camera_mount:<camera>`, `no_nan`, `object_on_table`, `grasp_hold:<gripper>`, `grasp_opening:<gripper>` |
| `setup_teleoperation` | `ik_direction:<arm>:+x`, `ik_direction:<arm>:+z`, `ik_direction:<arm>:-y`, `action_label:<arm>` |
| `collect_dataset` | `camera_frames_updated`, `first_frame_fresh`, `label_replay` |
| `convert_dataset` | `gr00t_loader`, `video_matches:<camera>` |
| `train_vla` | `finetune_args_valid` |
| `evaluate_vla` | `camera_frames_updated` |

What a failure means and what to change: [Troubleshooting](troubleshooting.md).
