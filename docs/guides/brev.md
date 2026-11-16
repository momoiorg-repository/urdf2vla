# Running on NVIDIA Brev

This page walks through urdf2vla on a cloud GPU from NVIDIA Brev, from a fresh instance to a trained model. Every
number on it was measured on one instance: an **NVIDIA L40S (46 GB)**, 8 CPUs, 144 GB RAM, Ubuntu 22.04, with the
FANUC CRX-3iA example.

`urdf2vla doctor` checks each point below. When something is wrong, it prints the fix and links to the section here.
Run it after every step of the setup:

```bash
urdf2vla doctor
```

## What you need before you start

| | |
|---|---|
| Instance | One GPU with **44 GB or more** to train on the instance itself (L40S, A100, H100). Smaller GPUs can run everything except training |
| Disk | **300 GB or more** on the disk that holds Docker's images and the repository (see [Disk](#disk)) |
| Hugging Face | An account, a token with read access, and the license of `nvidia/Cosmos-Reason2-2B` accepted (see [Hugging Face](#huggingface)) |
| Time | About 30 minutes of setup, 35 minutes of recording, 2 hours per training run |

## 1. Driver 580 or newer {#driver}

Isaac Sim 6.2 and the GR00T image need driver **580 or newer**. With an older driver, the first Isaac Sim step stops
with `The NVIDIA driver on your system is too old (found version 12070)`: the image's PyTorch is built for CUDA 13.

Check with `nvidia-smi`. If the driver is older, the fastest fix is to pick an instance image with a newer driver.
To upgrade in place (our instance came with 565.57.01):

```bash
# Brev holds the installed driver; release the holds (also on nvidia-modprobe, nvidia-settings, nvidia-prime)
sudo apt-mark unhold $(apt-mark showhold | grep -E 'nvidia|cuda-drivers')
# keep every driver package on the 580 branch (otherwise the CUDA repository mixes in a newer branch)
apt-get download nvidia-driver-pinning-580 && sudo dpkg -i nvidia-driver-pinning-580_*.deb
# remove the old driver, install 580 (the kernel module is built with DKMS)
sudo apt-get purge -y $(dpkg -l | awk '$1 ~ /^[ih]i$/ && $2 ~ /565/ {print $2}')
sudo apt-get install -y nvidia-driver-580-open nvidia-utils-580 nvidia-compute-utils-580
sudo reboot
```

After the reboot, `nvidia-smi` shows 580, and you must regenerate the CDI spec (next section).

## 2. GPU access from Docker (CDI) {#cdi}

The NVIDIA Container Toolkit on Brev runs in **CDI mode**: `docker run --gpus all` uses a description of the GPU in
`/etc/cdi/nvidia.yaml`. On our instance that file was missing, and `docker run --gpus all` failed with:

```text
could not apply required modification to OCI specification: error modifying OCI spec:
failed to inject CDI devices: unresolvable CDI devices nvidia.com/gpu=all
```

Generate it:

```bash
sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
docker run --rm --gpus all ubuntu nvidia-smi        # should print the GPU
```

- With a driver **older than 580**, `nvidia-ctk` crashes (`free(): invalid pointer` in `nvSandboxUtilsShutdown`). Add
  `--feature-flag=disable-nvsandboxutils`.
- **After a driver upgrade, generate it again.** The file names the driver's library versions, and it was not
  refreshed by itself after our upgrade.

## 3. Docker's storage on the large disk {#disk}

Brev instances have a small system disk (124 GB on ours) and a large one under `/data` (295 GB). Put Docker's images
and the repository on `/data`:

```bash
# Docker: add "data-root": "/data/docker" to /etc/docker/daemon.json (keep the nvidia runtime entries), then
sudo systemctl restart docker && docker info | grep "Root Dir"
# repository
cd /data && git clone https://github.com/momoiorg-repository/urdf2vla.git
```

What takes the space (measured):

| | |
|---|---|
| Images | Isaac Lab Arena 38 GB, GR00T 26 GB (plus a 26 GB base image that shares its layers). About 64 GB, plus up to 50 GB of build cache (`docker builder prune` frees it) |
| Dataset | 50 FANUC demos: 2.6 GB (HDF5), 61 MB converted |
| GR00T models | 6.5 GB (`nvidia/GR00T-N1.7-DROID`) + 4.6 GB (`nvidia/Cosmos-Reason2-2B`) in `~/.cache/huggingface` |
| **Checkpoints** | **24 GB each** (11 GB weights + 13 GB optimizer state). `train_vla` keeps 2 by default (`--save-total-limit`) |

Checkpoints are the part that runs out. With 5 kept per run, two runs filled the 295 GB disk and the second one
died while saving. `train_vla` now refuses to start when there isn't room for its checkpoints. To free space after a
run, delete `optimizer.pt` from finished checkpoints: it's only needed to resume training, and the weights stay
usable for `evaluate_vla`.

## 4. Build and start {#build}

```bash
uv venv .venv && uv pip install -e '.[view,dev,docs]' xacro && source .venv/bin/activate
bash docker/build.sh            # Arena image 13 min, GR00T image 4 min on the L40S
bash docker/run.sh && bash docker/run.sh gr00t
urdf2vla doctor
```

Run `docker/build.sh` in a way that survives your SSH session ending (`tmux`, or `setsid nohup … &`): the build
stops if the shell that started it exits.

## 5. Hugging Face {#huggingface}

GR00T N1.7 uses the vision-language model `nvidia/Cosmos-Reason2-2B`, which is **gated**: training and evaluation
stop with `401 Client Error … Cannot access gated repo` until you have access.

1. Open [huggingface.co/nvidia/Cosmos-Reason2-2B](https://huggingface.co/nvidia/Cosmos-Reason2-2B) and accept the
   license.
2. Create a token with read access at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens).
3. Log in inside the GR00T container. The token is saved in `~/.cache/huggingface/token`, which the containers share
   with the host:

    ```bash
    docker exec -it -u $(id -un) -e HOME=$HOME urdf2vla-gr00t /workspace/.venv/bin/hf auth login
    ```

Keep a copy of the token on `/data` (for example `/data/secrets/hf_token`, mode 600) if your instance's home
directory doesn't survive a stop, and copy it back to `~/.cache/huggingface/token` afterwards.

## 6. Run the example

The steps are the same as in [Bring your own robot](your-robot.md). Measured on the L40S with the FANUC CRX-3iA:

| Step | Result | Time | Peak GPU memory |
|---|---|---|---|
| `import_robot` (first run) | 10/10 checks, asks for the starting pose | 447 s (first start of Isaac Sim fills the shader cache) | 4.9 GB |
| `import_robot` (after `answer robot.default_pose=suggested`) | 10/10 | 37 s | 4.8 GB |
| `launch_sim` | 22/22 (cameras, objects, grasp holds) | 204 s | 5.5 GB |
| `setup_teleoperation` | 4/4 | 64 s | 5.5 GB |
| `collect_dataset --device scripted --num-demos 50` | 50 of 59 attempts (84.7 %) | 33 min | 5.5 GB |
| `convert_dataset` | 5/5 | 68 s | – |
| `convert_dataset --layout droid` | 6/6 (100 episodes: each demo with the left and the right camera) | 3 min | – |
| `train_vla` (urdf2vla layout, 3 cameras, 10,000 steps, batch 32) | release check 10/10 | about 2 h 40 min (1.07 steps/s) | 41.9 GB |
| `train_vla` (`--layout droid`, 2 cameras) | release check 10/10 | 2 h 8 min (1.38 steps/s) | 40.9 GB |
| `evaluate_vla --episodes 20 --video` | see [Results](#results) | 10–11 min | 12.4 GB |

Training and evaluation don't fit on the GPU at the same time (41 GB + Isaac Sim + the inference server). To
evaluate a checkpoint while another training runs, stop the training right after it saves a checkpoint, evaluate,
and run the same `train_vla` command again: it resumes from the last checkpoint.

Before a 30-minute evaluation, check that the model releases the gripper (about a minute, no simulation):

```bash
urdf2vla train_vla --project projects/crx3ia --dataset demos_droid --check-only training/crx3ia_demos_droid/checkpoints/checkpoint-10000
```

## Results {#results}

Both models were fine-tuned from `nvidia/GR00T-N1.7-DROID` (the default for single-arm robots) on the same 50 scripted
demos, and evaluated on the same 20 episodes (same seed: random object and position each episode).

| | urdf2vla layout (default) | `--layout droid` |
|---|---|---|
| Cameras the model sees | wrist + left + right | wrist + one exterior (training: left or right per episode; evaluation: left) |
| Arm state and actions | 6 joint targets | DROID's end-effector pose + 7 joints (the 7th padded) |
| **Success** | **20/20** | 19/20 (one pear never released) |
| Apple / cube / lime / pear | 5/5, 7/7, 3/3, 5/5 | 5/5, 7/7, 3/3, 4/5 |
| Median episode | 174 steps (8.7 s) | 173 steps |
| Median distance from the target | 15 mm | 12 mm |

The 3-camera model is on Hugging Face:
[`umegan/urdf2vla-crx3ia-gr00t-n1.7`](https://huggingface.co/umegan/urdf2vla-crx3ia-gr00t-n1.7) (NVIDIA Open Model
License; its model card shows how to evaluate it with urdf2vla).

**Use the default urdf2vla layout** (all cameras, `convert_dataset` without `--layout`). It was as good or better,
and every frame shows the model all three views. The DROID layout trains about 30 % faster; keep it for experiments.

A first DROID-layout model trained on episodes that ended 13 steps after the gripper opened scored 0/3: it grasped
and carried every object to the bin but never let go. `convert_dataset` now holds the final frame so the release
is learned, checks it (`gripper_timing`), and `train_vla` checks the trained model before you evaluate it
(`release_check`: 0/10 for that model, 10/10 for both models above).

## The GR00T server port {#port}

Brev runs NVIDIA DCGM, whose `nv-hostengine` listens on `127.0.0.1:5555`, the GR00T inference server's default
port. `evaluate_vla` moves to the next free port by itself and says so in its warnings; `doctor` reports it as info.

## Viewer and teleoperation over SSH

- `urdf2vla view` serves on port 8080: `ssh -L 8080:localhost:8080 <instance>` and open `http://localhost:8080`.
- Record with the terminal keyboard: `collect_dataset --device socket` in one SSH session and `urdf2vla teleop-keys`
  in a second one.
