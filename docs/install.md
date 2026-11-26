# Set up on local Linux

For a cloud GPU, start with [NVIDIA Brev setup](guides/brev.md#launchable).

For a local Linux machine, use the steps below. The `urdf2vla` command runs simulation in a container with
Isaac Sim, Isaac Lab, and Isaac Lab Arena. A second container runs GR00T N1.7 for conversion, training, and inference.

!!! tip "Where to run commands"
    Every command in these docs runs **in a terminal on your machine**, in the `urdf2vla` folder, after
    `source .venv/bin/activate`. You never enter a container: `urdf2vla` starts the work in the right one for you.
    Docker itself only appears once, in steps 2 and 3 below.

## What you need

| | |
|---|---|
| OS | Linux x86_64 (Ubuntu 22.04 or 24.04) |
| GPU | NVIDIA RTX GPU with 16 GB or more and driver **580 or newer** |
| Docker | Docker with the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) |
| Disk | **150 GB** free; **300 GB** if you train on this machine (each checkpoint is 24 GB) |
| Python | Python 3.10 or newer and [uv](https://docs.astral.sh/uv/) |
| Hugging Face | An account with the license of `nvidia/Cosmos-Reason2-2B` accepted, for training and evaluation ([step 5](#huggingface)) |
| Training | A GPU with **44 GB or more** (L40S, A100, H100, RTX 6000 Ada). It can be another machine ([Fine-tune and evaluate GR00T](guides/training.md#another-machine)) or a [cloud GPU](guides/brev.md) |

## 1. Install the `urdf2vla` command

```bash
git clone https://github.com/momoiorg-repository/urdf2vla.git
cd urdf2vla
uv venv .venv
uv pip install -e '.[view]'      # [view] adds the browser robot viewer
source .venv/bin/activate
```

## 2. Build the containers (once)

```bash
bash docker/build.sh
```

This fetches Isaac Lab Arena and Isaac-GR00T at fixed versions into `external/` and builds two images,
`urdf2vla/isaaclab_arena:b9e37c5` (38 GB) and `urdf2vla/gr00t:23ace64` (26 GB). The first build downloads a lot; on an
L40S it took 13 and 4 minutes. `bash docker/build.sh sim` or `gr00t` builds one of them; `--force` rebuilds.

## 3. Start the containers

```bash
bash docker/run.sh          # simulation: urdf2vla-sim
bash docker/run.sh gr00t    # GR00T: urdf2vla-gr00t
```

Running them again just makes sure the container is running. `--rm` removes it (downloads stay cached).

## 4. Check the machine

```bash
urdf2vla doctor
```

`doctor` checks the GPU driver, Docker and its access to the GPU, the images and containers, disk space, NVIDIA's asset
server and Hugging Face, and prints the command that fixes each problem. Run it whenever the environment looks wrong.

## 5. Hugging Face (for training and evaluation) {#huggingface}

GR00T N1.7 uses the vision-language model `nvidia/Cosmos-Reason2-2B`, which needs its license accepted:

1. Accept the license at [huggingface.co/nvidia/Cosmos-Reason2-2B](https://huggingface.co/nvidia/Cosmos-Reason2-2B).
2. Create a token with read access at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens).
3. Log in (the token is saved in `~/.cache/huggingface`, which the containers share):

    ```bash
    hf auth login
    ```

## Next

<div class="grid cards" markdown>

- Run each command yourself with the bundled robot. [Franka CLI tutorial](tutorial.md)
- Describe your goal and let an agent run the commands. [Run with Codex or Claude Code](guides/claude-code.md)
- Call the CLI from your own script. [Shell script automation](reference.md#automation)

</div>
