# Install

urdf2vla has two parts:

- the **`urdf2vla` command**, a small Python package you install on your machine, and
- two **Docker containers** that do the heavy work: one with Isaac Sim / Isaac Lab / Isaac Lab Arena for simulation, and
  one with GR00T N1.7 for data conversion, training and the inference server.

You run every command on your machine. urdf2vla starts the work inside the right container for you.

## What you need

| | |
|---|---|
| OS | Linux x86_64 (tested on Ubuntu 24.04) |
| GPU | NVIDIA RTX GPU (tested on an RTX 5080, 16 GB) and driver **580 or newer** |
| Docker | Docker with the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html), so that `docker run --gpus all` works |
| Disk | About **150 GB** free (the simulation image is ~60 GB and the GR00T image ~42 GB) |
| Python | Python 3.10 or newer and [uv](https://docs.astral.sh/uv/) |
| Network | Needed for the first build, the first import (Isaac Sim extensions and SimReady assets are downloaded) and the GR00T base model |
| Training | A GPU with **44 GB or more** (A100, H100, L40S, RTX 6000 Ada, …). It can be a different machine — see [Train and evaluate](guides/training.md), or run everything on a [cloud GPU](#cloud) |

Check that Docker can see your GPU before you start:

```bash
docker run --rm --gpus all ubuntu nvidia-smi
```

## 1. Install the `urdf2vla` command

```bash
git clone https://github.com/momoiorg-repository/urdf2vla.git
cd urdf2vla
uv venv .venv
uv pip install -e '.[view]'      # [view] adds the browser robot viewer
source .venv/bin/activate        # makes the `urdf2vla` command available in this shell
urdf2vla --help
```

## 2. Build the containers (once)

```bash
bash docker/build.sh
```

This downloads Isaac Lab Arena and Isaac-GR00T at fixed versions into `external/` and builds two images:

| Image | Contents | Size |
|---|---|---|
| `urdf2vla/isaaclab_arena:b9e37c5` | Isaac Sim 6.2.0, Isaac Lab 3.0.0, Isaac Lab Arena | ~60 GB |
| `urdf2vla/gr00t:23ace64` | Isaac GR00T N1.7 and its Python environment | ~42 GB |

!!! info "This takes a while"
    Expect **1–2 hours** and a lot of downloading the first time. Running `build.sh` again skips images that already
    exist (`--force` rebuilds). `bash docker/build.sh sim` or `bash docker/build.sh gr00t` builds only one of them.

## 3. Start the containers

```bash
bash docker/run.sh          # simulation container: urdf2vla-sim
bash docker/run.sh gr00t    # GR00T container: urdf2vla-gr00t
```

Both commands are safe to run again: if the container exists they just make sure it is running. They don't touch any
other containers on your machine. To remove one, add `--rm` (e.g. `bash docker/run.sh gr00t --rm`); cached downloads
are kept in Docker volumes.

The containers run as your own user, so every file urdf2vla writes in the repository stays owned by you.

## 4. Check that everything works

```bash
urdf2vla doctor
```

`doctor` checks your machine without changing anything: the GPU driver (including a kernel update that left the driver
behind), GPU memory, Docker and the NVIDIA Container Toolkit, both images and containers (and that the containers can see
the GPU and the repository), free disk space, and whether NVIDIA's asset server is reachable. Each problem comes with the
command that fixes it. Run it again whenever something looks wrong with the environment.

## 5. Choose how to continue

<div class="grid cards" markdown>

- **New to robot simulation? Let an AI agent drive.** Open the repository in Claude Code or Codex and say what you
  want; the agent runs the steps and asks you only about your robot. → [Use it with an AI agent](guides/claude-code.md)
- **Want to see each step yourself?** Run the whole pipeline once with the bundled Franka Panda.
  → [Tutorial](tutorial.md)

</div>

Either way, the first simulation step, `import_robot`, finishes in under a minute once the shaders are compiled (the
very first start of Isaac Sim can take several minutes).

## On a cloud GPU (NVIDIA Brev) {#cloud}

Everything above works the same on a cloud GPU machine, for example an NVIDIA Brev instance. An **L40S (48 GB)** runs
the simulation and is also big enough for GR00T training, so `train_vla` trains on the machine itself.

- **Instance:** Ubuntu 22.04 or 24.04 with Docker and the NVIDIA Container Toolkit, and at least **200 GB** of disk:
  about 100 GB for the two images, plus datasets and the GR00T base model.
- **Driver:** urdf2vla is tested with driver 580 or newer. `urdf2vla doctor` warns if the instance's driver is older.
  Pick an image with a newer driver, or upgrade it and reboot.
- **No display needed:** every step runs headless.
- **Tests and docs:** install the extras with `uv pip install -e '.[view,dev,docs]'`, then run `pytest` and
  `mkdocs build --strict`.
- **FANUC example:** there is no ROS, so install `xacro` into urdf2vla's environment before fetching the URDF:
  `uv pip install xacro && bash examples/crx3ia/fetch_urdf.sh`.
- **Browser viewer:** `urdf2vla view` serves on port 8080 on the instance. Forward it over SSH from your own computer
  and open `http://localhost:8080`:

    ```bash
    ssh -L 8080:localhost:8080 <instance>
    ```

- **Teleoperation:** record over SSH with the terminal keyboard: `collect_dataset --device socket` (or
  `generate_mimic --source-device socket`) in one SSH session and `urdf2vla teleop-keys` in a second one.
  `--livestream` (WebRTC) needs the streaming client to reach the instance directly, because its video doesn't go
  through an SSH tunnel; that hasn't been tried on Brev.

## Optional: the documentation site

```bash
uv pip install -e '.[docs]'
mkdocs serve                # http://127.0.0.1:8000
```
