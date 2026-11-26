# Running on NVIDIA Brev

This page walks through urdf2vla on a cloud GPU from NVIDIA Brev, from a fresh instance to a trained model. The
FANUC CRX-3iA timings were measured on an **NVIDIA L40S (46 GB)**, 8 CPUs, 144 GB RAM, and Ubuntu 22.04.

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

## Set up with one script {#script}

On a fresh instance, one script does sections 1–4 of this page and starts both containers. Run it as the user you log
in with (it uses `sudo` where it needs to):

```bash
curl -fsSL https://raw.githubusercontent.com/momoiorg-repository/urdf2vla/main/docker/brev_setup.sh -o /tmp/urdf2vla_brev_setup.sh
bash /tmp/urdf2vla_brev_setup.sh
```

The script clones the repository itself, onto the disk with the most free space. If you've already cloned it, run
`bash docker/brev_setup.sh` in it instead: it uses that checkout where it is, and warns if its disk is too small for
training checkpoints (24 GB each).

| Step | What the script does |
|---|---|
| [Driver](#driver) | Below 580: releases Brev's holds, pins the 580 branch, installs 580 and reboots. After the reboot, setup continues by itself |
| [Disk](#disk) | If the system disk has less than 130 GB free and a larger disk exists, switches Docker to a new directory there and keeps the NVIDIA settings in `/etc/docker/daemon.json`. When the script clones the repository to the larger disk, it links it from `~/urdf2vla`. |
| [CDI](#cdi) | Generates `/etc/cdi/nvidia.yaml` and checks that a container sees the GPU |
| [Build and start](#build) | Adds you to the `docker` group, installs uv and the `urdf2vla` command (`.venv`), runs `docker/build.sh` and `docker/run.sh` for both containers, then `urdf2vla doctor` |

It takes about 30 minutes, longer if the driver needs upgrading. The setup runs as a systemd service, so it keeps going
if your SSH connection drops. To follow it:

```bash
bash ~/urdf2vla/docker/brev_setup.sh --status   # where it is, and what to do next
tail -f /var/log/urdf2vla-setup.log              # everything it prints
```

When you log in, the status is shown; once setup is done, the login also moves into the repository and activates
`.venv`. If a step fails, the log says which one. Fix the cause (the sections below explain each step) and run the
script again: it continues from the step that failed.

Then do [Hugging Face](#huggingface): accept the license and run `hf auth login`, or pass the token to the script
with `HF_TOKEN=hf_... bash /tmp/urdf2vla_brev_setup.sh` (it's saved to `~/.cache/huggingface/token` and never printed).
Accepting the license stays a step in the browser. Continue with the [tutorial](../tutorial.md).

!!! note
    `docker/run.sh` starts the simulation container with `ACCEPT_EULA=Y`, as it does on your own machine: using it means
    accepting the license of the Isaac Sim image (see
    [Isaac Sim's container installation](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/installation/install_container.html)).

### A Brev Launchable {#launchable}

In the [Brev Console](https://brev.nvidia.com), open **Launchables → Create Launchable**. Use the following settings
to make a VM that runs the setup above. Brev bills the account that deploys the VM. [NVIDIA Brev Launchables](https://docs.nvidia.com/brev/concepts/launchables)

| Setting | Value |
|---|---|
| Name | `urdf2vla` |
| Description | Build a pick-and-place simulation from a robot URDF, record demonstrations, and fine-tune GR00T N1.7. Connect over SSH and start with the Franka example. |
| Compute | One NVIDIA L40S, or another compatible GPU with at least 44 GB of VRAM for training |
| Disk storage | **300 GiB or more** |
| Software configuration | **VM Mode**, with Jupyter off |
| Setup script | Paste the contents of [docker/brev_launchable.sh](https://github.com/momoiorg-repository/urdf2vla/blob/main/docker/brev_launchable.sh) |
| Source | Select **I don’t have any code files**. The setup script clones the repo onto the instance. |
| Network for Isaac Sim (optional) | For public livestreaming, forward `49100/TCP` and `47998/UDP` and restrict both rules to the deployer's IP. Use the assigned public ports in the [desktop client](#desktop-streaming-client) or [browser viewer](#remapped-kit-ports). |
| Network for the URDF viewer (optional) | Add an authenticated **Secure Link** named `Robot viewer` for HTTP port `8080`. |
| Launch parameter (optional) | Add `HF_TOKEN` as an optional **Text** parameter with an empty default. Deployers can select a saved organization secret or sign in later with `hf auth login`. |
| View access | Select **Only my organization** for the first test deployment. |

The setup script defaults to `main`. To test changes before merging, set `URDF2VLA_REF` in the pasted script to the
branch containing them on the public repository. Use a release tag for a repeatable deployment. Brev runs the script
once. If the driver upgrade reboots the VM, the systemd service resumes
setup. To confirm it finished, SSH into the VM and check `bash ~/urdf2vla/docker/brev_setup.sh --status` and
`/var/log/urdf2vla-setup.log`.

Select **Create Launchable** to generate the deployment link. Deploy a test VM from that link and run the Franka
check below. After it passes, set the intended **View access** and use **My Launchables → Copy Markdown Badge** to
get a **Launch on Brev** button for your README or docs. [NVIDIA Brev sharing and badges](https://docs.nvidia.com/brev/concepts/launchables#embedding-launchables)

### Verify the deployment with Franka {#franka-smoke-test}

Run this on the Brev VM after setup finishes. On an existing VM, check `nvidia-smi`, `docker info`, and `df -h . /data`
first. Setup can upgrade the driver and reboot, or switch Docker to a new storage directory. If the storage directory
changes, existing containers and images stay in the previous directory.

```bash
cd ~/urdf2vla                         # or your existing checkout
source .venv/bin/activate
urdf2vla doctor
```

Resolve any `doctor` errors before starting. Signing in to Hugging Face can wait until training or model evaluation.
Run these commands in order and stop if any returns a status other than `ok`. If you already created this project,
use `urdf2vla status --project projects/brev_franka` to resume. The first Isaac Sim start may take several minutes
while Kit compiles shaders.

```bash
project=projects/brev_franka
urdf2vla init "$project" --from examples/franka/project.yaml
urdf2vla inspect_robot --project "$project"
urdf2vla configure_robot --project "$project"
urdf2vla configure_task --project "$project"
urdf2vla configure_camera --project "$project"
urdf2vla import_robot --project "$project"
urdf2vla launch_sim --project "$project"
```

Check the `*_start.png` and `*_end.png` images in `projects/brev_franka/snapshots/` for the Franka arm, table,
objects and bin. If you configured a Secure Link, run `urdf2vla view --project "$project" --port 8080` on the VM
and open **Robot viewer** in the Brev Console.

#### Record and replay three demonstrations {#franka-replay-test}

After the scene checks pass, run a short smoke test through Isaac Sim and the GR00T container. This test uses
recorded actions, so it requires no model checkpoint or Hugging Face sign-in. Stop any Kit livestream first to free
the GPU. Use a fresh dataset name for a repeated run.

```bash
project=projects/brev_franka
dataset=brev_smoke
urdf2vla configure_vla_interface --project "$project"
urdf2vla setup_teleoperation --project "$project"
urdf2vla collect_dataset --project "$project" --device scripted --num-demos 3 --dataset "$dataset"
urdf2vla convert_dataset --project "$project" --dataset "$dataset"
urdf2vla evaluate_vla --project "$project" --replay --dataset "$dataset" --episodes 3 --video
```

Recording can take more than three attempts because it saves only successful demonstrations. Check
`build/collect_report.yaml` for the saved count and `build/eval_report.yaml` for the replay results. Replay checks the
recorded actions and the server connection. The `--video` option saves one MP4 per episode in `build/eval_videos/`.

<figure>
  <video controls muted playsinline preload="none" width="100%" poster="../../assets/brev_franka_replay.jpg" src="../../assets/brev_franka_replay.mp4" aria-label="Franka replay from three cameras"></video>
  <figcaption>The Franka places a cube in the bin during replay. From left to right: wrist, left, and right camera.</figcaption>
</figure>

To train and evaluate a model, follow the [training guide](training.md).

### Stream the Isaac Sim Kit viewport {#kit-livestream}

Use a GPU with NVIDIA NVENC, such as the L40S. The workstation needs a route to both Kit ports. For public access,
use the provider firewall or the Brev **Ports** tab. Restrict public access to
the deployer's IP because the native WebRTC stream has no built-in authentication. [Brev networking options](https://docs.nvidia.com/brev/concepts/launchables#network) ·
[Isaac Sim 6.2 livestream clients](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/installation/manual_livestream_clients.html)

| Destination port | Protocol | Purpose |
|---|---|---|
| `49100` | TCP | WebRTC signaling |
| `47998` | UDP | WebRTC media |

Check the **Public Port** and **Destination Port** columns in Brev. The destination ports are Kit's ports on the VM.
When you connect through Brev's public endpoint, use the assigned public port numbers in your client.
[Kit stream settings](https://docs.omniverse.nvidia.com/kit/docs/omni.kit.livestream.app/latest/Overview.html)

After `import_robot` succeeds, run the command for your connection from the urdf2vla checkout on the VM.

=== "Public endpoint"

    Replace `STREAM_IPV4` with a public endpoint IPv4 address that the workstation can reach on both public ports.
    Mode `1` selects the Kit visualizer and advertises this address to the client.

    ```bash
    urdf2vla launch_sim --project projects/brev_franka --livestream 1 --public-ip STREAM_IPV4
    ```

=== "Private network"

    Connect the VM and workstation to the same routed private network or VPN. Use mode `2` to stream over that network.

    ```bash
    urdf2vla launch_sim --project projects/brev_franka --livestream 2
    ```

#### Connect with the Isaac Sim WebRTC Streaming Client {#desktop-streaming-client}

On your **workstation**, download the **Isaac Sim WebRTC Streaming Client** for your OS and CPU architecture.
[NVIDIA client downloads](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/installation/download.html#latest-release)

| Workstation | Installation |
|---|---|
| Windows | Run the `.exe` installer, then open **Isaac Sim WebRTC Streaming Client** from Start. |
| macOS | Open the `.dmg` for Apple silicon or Intel, then drag the app to **Applications**. |
| Ubuntu or Debian | From the download directory, run `sudo apt install ./isaacsim-webrtc-streaming-client-*-linux-*.deb` with the package for your architecture, then launch the app from the application menu. |

1. Keep `launch_sim` running on the Brev VM. Wait for `env ready` in the newest `*_launch_sim.log` under the project's `logs/` directory.
2. Open the client on your workstation. In **Server**, enter the IPv4 address supplied to `--public-ip` for mode `1`, or the reachable private IPv4 address of the VM for mode `2`.
3. For a Brev public endpoint, enter the TCP **Public Port** under **Signal** and the UDP **Public Port** under **Stream**. For a private or direct connection to Kit's default ports, use `49100` and `47998`.
4. Select **Connect**. Close the client to disconnect and press Ctrl+C in the VM terminal to stop Kit.

<figure markdown="span">
  ![Desktop client with Server, Signal port, Stream port, and Connect controls](../assets/brev_webrtc_client.png)
  <figcaption>Isaac Sim WebRTC Streaming Client 2.0.0 with its default values.</figcaption>
</figure>

If the client cannot connect, check the TCP port entered under **Signal** and close any other streaming client.
If it connects but shows a blank viewport, check the UDP port entered under **Stream** and try **View → Reload**.
[NVIDIA client setup and troubleshooting](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/installation/manual_livestream_clients.html#isaac-sim-short-webrtc-streaming-client)

#### Browser viewer for remapped ports {#remapped-kit-ports}

NVIDIA's browser viewer accepts separate public signaling and media ports. In `launch_sim --livestream 1`, set
`--public-ip` to a reachable IPv4 address of the public endpoint. Keep Kit on its default destination ports.
Build the viewer with the hostname and **public** port numbers shown in Brev, without the `:port` suffix on the
hostname. [Isaac Sim 6.2 browser viewer](https://github.com/isaac-sim/IsaacSim/blob/v6.2.0/tools/docker/web-viewer/Dockerfile)

```bash
viewer_dir=$(mktemp -d)
curl -fsSL https://raw.githubusercontent.com/isaac-sim/IsaacSim/v6.2.0/tools/docker/web-viewer/Dockerfile \
  -o "$viewer_dir/Dockerfile"
docker build -t urdf2vla/kit-web-viewer \
  --build-arg ISAACSIM_HOST=PUBLIC_HOSTNAME \
  --build-arg ISAACSIM_SIGNAL_PORT=PUBLIC_TCP_PORT \
  --build-arg ISAACSIM_STREAM_PORT=PUBLIC_UDP_PORT "$viewer_dir"
docker run --rm -p 127.0.0.1:8210:8210 urdf2vla/kit-web-viewer
```

From your workstation, forward the viewer's HTTP port using `brev port-forward INSTANCE --port 8210:8210` and open
`http://localhost:8210` in Chromium or Chrome. The browser uses the public Brev endpoint for WebRTC. Close other
streaming clients before connecting. Kit supports one client at a time. [Brev port forwarding](https://docs.nvidia.com/brev/cli/connectivity) ·
[Isaac Sim client limits](https://docs.isaacsim.omniverse.nvidia.com/6.2.0/installation/manual_livestream_clients.html)

<figure markdown="span">
  ![Streamed Isaac Sim viewport with the Franka pick-and-place table, bin, and lime](../assets/brev_webrtc_viewport.png)
  <figcaption>The Franka scene streamed through Brev to the browser viewer.</figcaption>
</figure>

The sections below are what the script does, step by step. Follow them to set up by hand, or when the script stops at a
step.

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
3. Log in on the instance (the token is saved in `~/.cache/huggingface/token`, which the containers share):

    ```bash
    hf auth login
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

![Four moments of one evaluation episode](../assets/crx3ia_model_steps.png)

Videos and images of the evaluation: [Trained models](../models.md#crx3ia).

## The GR00T server port {#port}

Brev runs NVIDIA DCGM, whose `nv-hostengine` listens on `127.0.0.1:5555`, the GR00T inference server's default
port. `evaluate_vla` moves to the next free port by itself and says so in its warnings; `doctor` reports it as info.

## Viewer and teleoperation over SSH

- `urdf2vla view` serves on port 8080: `ssh -L 8080:localhost:8080 <instance>` and open `http://localhost:8080`.
- Record with the terminal keyboard: `collect_dataset --device socket` in one SSH session and `urdf2vla teleop-keys`
  in a second one.
