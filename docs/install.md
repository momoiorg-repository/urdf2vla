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
| Training | A GPU with **44 GB or more** (A100, H100, L40S, RTX 6000 Ada, …). It can be a different machine — see [Train and evaluate](guides/training.md) |

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

The fastest check is the [tutorial](tutorial.md): its first simulation step, `import_robot`, finishes in under a minute
once the shaders are compiled (the very first start of Isaac Sim can take several minutes).

## Optional: the documentation site

```bash
uv pip install -e '.[docs]'
mkdocs serve                # http://127.0.0.1:8000
```
