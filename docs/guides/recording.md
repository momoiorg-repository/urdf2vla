# Record demonstrations

A VLA learns from demonstrations: camera images, the robot's state and the action taken at every step. urdf2vla records
them in Isaac Sim with `collect_dataset` and keeps only the **successful** episodes.

Before recording, run the two preparation steps once:

```bash
urdf2vla configure_vla_interface --project projects/myrobot   # what the VLA sees and outputs
urdf2vla setup_teleoperation     --project projects/myrobot   # checks the hand moves as commanded
```

## What gets recorded

| | Contents |
|---|---|
| Images | Every camera (default: one wrist per arm, plus left and right), 256×256 RGB, at the control rate (20 Hz by default) |
| State | Every arm's joint positions + one gripper value per gripper (0 = closed, 1 = open) |
| Action | Every arm's joint targets + every gripper command — what the controller was told to do |
| Task text | The instruction for this episode, naming its object, e.g. "put the apple in the bin" |

Each episode puts one of the task's objects on the table at a random position. It counts as a success when the
object rests inside the bin and the gripper has let go,
held for a few steps. Episodes that fail are discarded, so the dataset contains only good demonstrations. Recordings go
to `datasets/<name>.hdf5` (`demos` by default; choose another with `--dataset`).

## Three ways to record

=== "Scripted (no human)"

    A scripted controller picks up the object and puts it in the bin, with a new random object and position each episode.
    On a dual-arm robot it uses the nearer arm and keeps the other arm open at its initial pose. This is the quickest
    way to get a dataset and to check the whole pipeline.

    ```bash
    urdf2vla collect_dataset --project projects/myrobot --device scripted --num-demos 50
    ```

    It tries up to 3× `--num-demos` episodes (`--max-attempts` to change). Failed attempts are summarized in
    `build/collect_report.yaml` with a snapshot in `snapshots/collect_fail_*.png`, so you can tell whether grasping or
    placing went wrong.

=== "Keyboard in the Isaac Sim view"

    Drive the robot yourself while watching Isaac Sim through **WebRTC livestreaming** — the simulator runs in the
    container, and you see it in NVIDIA's streaming client.

    ```bash
    urdf2vla collect_dataset --project projects/myrobot --device keyboard --livestream
    ```

    1. Install the *Isaac Sim WebRTC Streaming Client* (see "Livestream Clients" in the
       [Isaac Sim documentation](https://docs.isaacsim.omniverse.nvidia.com/)).
    2. Connect it to the machine running urdf2vla (`127.0.0.1` on the same machine, otherwise its IP address).
    3. Click into the streamed view so it receives your keys, and drive with the keys below.

    Recording is paced to real time, so the robot moves at the speed you see.

=== "Keyboard in a terminal"

    No GUI at all: the simulation runs headless and a second terminal sends your keys over a local socket.

    ```bash
    urdf2vla collect_dataset --project projects/myrobot --device socket --num-demos 10
    # in a second terminal, once the first one is waiting:
    urdf2vla teleop-keys
    ```

    Useful over SSH. You don't see the scene, so it's mostly for quick tests; watch the saved snapshots.

## Keys

| Keys | Moves the hand |
|---|---|
| ++w++ / ++s++ | forward / back (along the robot's front, +x) |
| ++a++ / ++d++ | left / right (±y) |
| ++q++ / ++e++ | up / down (±z) |
| ++z++ / ++x++, ++t++ / ++g++, ++c++ / ++v++ | roll, pitch, yaw |
| ++k++ | open / close the gripper on the active arm |
| ++n++ | switch the active arm (dual-arm robots) |
| ++r++ | discard this episode and start again |
| ++esc++ | finish recording |

The hand moves in small steps (8 mm and 0.04 rad per control step); change `pos_step` and `rot_step` under
`overrides.teleop` in `project.yaml` if it feels too slow or too fast. An episode is saved automatically as soon as the
object has been placed in the bin. The instruction for the episode is printed when it starts.

## How many demonstrations?

Start with **50 scripted demonstrations** to get the whole pipeline working end to end. For a policy that works from
many different objects and positions, plan for more — typically hundreds. Human (keyboard) demonstrations add
variety that a scripted controller doesn't have.

## Checks during recording

`collect_dataset` checks the recording itself, because these problems are invisible in the numbers and ruin training:

| Check | Fails when |
|---|---|
| `camera_frames_updated` | a camera image doesn't change between two steps (a frozen camera) |
| `first_frame_fresh` | an episode's first image still shows the previous episode's scene |
| `label_replay` | replaying only the recorded actions doesn't reproduce the recorded motion |

See [Troubleshooting](../troubleshooting.md) for what to do when one fails.
