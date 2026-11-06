# Troubleshooting

When a step fails, its JSON result has `"status": "error"` and `errors` names the failed check. Three commands help:

```bash
urdf2vla doctor                                  # checks the machine: GPU driver, Docker, images, containers, disk
urdf2vla diagnose --project projects/myrobot     # matches the last log against known problems and suggests a fix
urdf2vla status   --project projects/myrobot     # which steps have run and what to run next
```

If the problem looks like the environment rather than your robot — a container won't start, the GPU isn't found —
start with `urdf2vla doctor`.

Each step also writes a report with the measured values behind every check: `build/import_report.yaml`,
`build/launch_report.yaml`, `build/teleop_report.yaml`, `build/collect_report.yaml`, `build/convert_report.yaml`,
`build/eval_report.yaml`. Full logs are in `logs/`.

!!! tip "Changing settings"
    Never edit files in `generated/` — they are rewritten on the next run. Put changes under `overrides:` in
    `project.yaml` (same structure as the generated file) and re-run the step that generates that file, then the steps
    after it. `urdf2vla status` shows what needs to run again.

## By symptom

### The gripper closes but the object slips out or falls

| Failed check | Step | What it means | What to do |
|---|---|---|---|
| `joint_limits_hold` | `import_robot` | A finger joint was pushed past its limits — the simulator isn't enforcing them. Common with simplified URDF grippers. | Replace the gripper with a SimReady asset: `robot.gripper.simready` ([guide](guides/your-robot.md#simready)), then redo from `configure_robot`. |
| `grasp_hold` | `launch_sim` | With the object between the fingers, it drifted away after being released. | Raise `overrides.robot.asset.solver_position_iterations` (default 32; SimReady grippers use 64) and redo from `import_robot`. If the gripper's force is unrealistic, check the effort limits. |
| `grasp_opening` | `launch_sim` | Prismatic fingers closed further than the object is wide: they sank into it. | Raise `overrides.robot.asset.solver_position_iterations` and redo from `import_robot`. |
| `object_on_table:<object>` | `launch_sim` | The object doesn't come to rest on the table at the height its size implies. | Usually an asset problem (its rigid body or collision shape doesn't match its look). Remove it from `task.objects` and report it with `build/launch_report.yaml` (`object_frames`). |
| `asset_size:<object>` | `launch_sim` | The spawned object's size differs from the catalog by more than 15 % — a wrong scale or unit in the asset. | Remove it from `task.objects` and report it. |

With several objects, the grasp checks are reported per object (`grasp_hold:<gripper>:<object>`). An object that fails
them can be left out of `task.objects` while you keep the others. Objects wider than the gripper opens are already left
out by `configure_task`, with a warning.

### The robot doesn't hold its pose, shakes or explodes

| Failed check | What it means | What to do |
|---|---|---|
| `no_nan` | The simulation diverged. | Lower the joint gains (see below), check for links without mass or inertia in the URDF, and turn self-collision off if you enabled it (`overrides.robot.asset.self_collision`). |
| `default_pose_tracking` | A joint can't reach the starting pose. | Check the joint's effort limit (`robot.effort_limits` if the URDF values are placeholders) and the starting pose; raise the stiffness (see below). |
| `within_limits` | The starting pose is outside a joint's limits. | Fix `robot.default_pose`. |
| `gripper_motion` | The gripper doesn't reach its open or closed position. | Check the open direction (`grippers.0.open_position`) and the finger joints' effort and velocity limits. |

**Changing joint gains:** `actuators` is a list, and a list in `overrides` replaces the generated one entirely. Copy the
whole `actuators:` list from `generated/robot_config.yaml` to `overrides.robot.actuators` in `project.yaml`, change
`stiffness` / `damping`, and redo from `configure_robot`.

### The hand moves the wrong way

| Failed check | Step | What to do |
|---|---|---|
| `ik_direction:<arm>:+x` / `+z` / `-y` | `setup_teleoperation` | Commanding the hand to move along an axis moved it elsewhere. Check the robot's front (`robot.front_axis`), the hand link (`arms.0.ee_link`) and that the starting pose isn't near a singularity (a fully stretched arm). |
| `action_label` | `setup_teleoperation` | The joint targets the controller produces don't match the robot. Usually follows from one of the problems above. |

### A camera shows nothing, or the wrong thing

| Failed check | What it means | What to do |
|---|---|---|
| `camera_not_black` | The image is (almost) black — usually a camera inside the robot's geometry. | Move it: `cameras:` in `project.yaml`, or for the wrist camera an `offset` (`- {preset: wrist, offset: 0.12}`). Redo from `configure_camera`. |
| `camera_alive`, `camera_frames_updated` | The image didn't change between two steps. A frozen camera ruins training data. | Set `overrides.task.render_fix: true` and redo from `configure_task` (each control step then renders once more). |
| `camera_mount` | The wrist camera isn't where it was configured. | Report it as a bug, with `build/launch_report.yaml`. |
| `first_frame_fresh` | An episode's first image still shows the previous episode. | Don't use that dataset; record again under a new `--dataset` name. |

Look at the pictures: `snapshots/<camera>_start.png` and `_end.png` from `launch_sim`, or open
[`urdf2vla view`](guides/view.md), which draws every camera's field of view.

### Recording fails or succeeds rarely

`build/collect_report.yaml` lists every attempt (`object_lift_m`: did the object come up? `object_to_target_m`: how far
from the target did it end?) and `snapshots/collect_fail_*.png` shows the last frame of failed attempts.

- **The object is never lifted** — a grasping problem: see the gripper section above, or adjust the grasp height
  (`overrides.teleop.grasp_height_offset`).
- **It's lifted but not placed** — the target is hard to reach: move it closer with `overrides.task.target.pos`.
- **`label_replay` fails** — replaying the recorded actions doesn't reproduce the motion, so the labels don't describe
  what the robot did. Check `ik_direction` and the gripper checks first.

### Conversion or training fails

| Failed check | What to do |
|---|---|
| `gr00t_loader` | GR00T couldn't load the converted dataset. Re-run `convert_dataset --force`; if it persists, report it with `build/convert_report.yaml`. |
| `video_matches:<camera>` | The encoded video differs from the recorded images. Re-run `convert_dataset --force`. |
| `finetune_args_valid` | GR00T rejected the training arguments. Check the values you passed to `train_vla`. |
| "This GPU can't train GR00T N1.7" warning | Not an error: train on a machine with 44 GB or more — see [Train and evaluate](guides/training.md#another-machine). |

## Environment problems

Run `urdf2vla doctor` first: it checks all of these and prints the fix for each.

| Message or symptom | Cause | What to do |
|---|---|---|
| `nvidia-smi` fails, or `nvidia-container-cli: … driver not loaded` | The GPU driver isn't loaded. Often the kernel was updated automatically and the driver modules for the new kernel weren't. | `urdf2vla doctor` prints the `apt install` command that updates the driver and its kernel modules together (then reboot), or the older kernel you can boot instead. |
| `permission denied` on the Docker socket | Your user isn't in the `docker` group. | `sudo usermod -aG docker $USER`, then log in again. |
| "コンテナ … が起動していません" / `No such container` | The container isn't running. | `bash docker/run.sh` (and `bash docker/run.sh gr00t` for conversion, training and evaluation). |
| "…秒以内に終わりませんでした" (timed out) | The first start of Isaac Sim compiles shaders and can take several minutes. | Run the step again with a larger `--timeout`. |
| `GLXBadFBConfig` | OpenGL setup doesn't match the GPU (common on laptops with two GPUs). | Add `__NV_PRIME_RENDER_OFFLOAD: "1"` and `__GLX_VENDOR_LIBRARY_NAME: nvidia` under `env:` in `runtime.yaml`. |
| Mesh files can't be opened | The URDF references meshes the container can't see. | Check `urdf2vla show --project … robot_metadata` (meshes) and add the folder to `robot.package_paths`. |
| `CUDA out of memory` | Another process uses the GPU. | Check `nvidia-smi`; stop other training jobs or containers. |
| `urdf2vla view` says a package is missing | The viewer's extra packages aren't installed. | `uv pip install -e '.[view]'` |

## Asking for help

[Open an issue](https://github.com/momoiorg-repository/urdf2vla/issues) with the command you ran, its JSON output, the report file of that step (`build/*_report.yaml`) and the
end of the log in `logs/`. `urdf2vla diagnose` prints the relevant part of the log.
