"""Generate the images and videos used by the documentation site (docs/assets/).

Everything comes from real urdf2vla runs, so the pictures always match what users see:

  uv pip install -e '.[docs-media]' && playwright install chromium
  # run the tutorial first (docs/tutorial.md) so projects/tutorial_franka has snapshots and eval videos
  python docs/make_media.py                       # all media
  python docs/make_media.py --only view            # only the browser viewer images

The CRX-3iA media (--only crx) need the FANUC URDF (bash examples/crx3ia/fetch_urdf.sh) and the projects listed in
CRX_GRIPPER_PROJECTS run through launch_sim (projects/fanuc_crx3ia also through evaluate_vla --replay --video).

The question screenshot needs a project with an open question. The script creates
projects/docs_franka_pose (the Franka example with a starting pose whose hand points forward) and runs the steps up
to import_robot, which proposes an IK starting pose and asks `robot.default_pose`. This needs the simulation container (bash docker/run.sh).
"""

from __future__ import annotations

import argparse
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "docs" / "assets"
URDF2VLA = [sys.executable, "-m", "urdf2vla.cli"]
VIEWPORT = {"width": 1400, "height": 1000}
CHROMIUM_ARGS = ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"]


def ffmpeg() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print("$", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run(cmd, cwd=ROOT, check=False, **kw)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --------------------------------------------------------------------------- simulation media


def _feature_video(project: Path) -> Path:
    """動画に使うエピソード: NVIDIA の SimReady の物体（りんご・ライム・洋梨）を運ぶ最初のもの（なければ最初のエピソード）。
    Arena のライブラリの物体（ルービックキューブ）は元のデータセットの利用条件を確かめていないので、公開する画像には使わない。"""
    import yaml

    from urdf2vla.objects import OBJECTS

    videos = sorted((project / "build" / "eval_videos").glob("*_ep*.mp4"))
    report = yaml.safe_load((project / "build" / "eval_report.yaml").read_text())
    for e in report.get("episodes", []):
        key = e.get("object")
        if key in OBJECTS and OBJECTS[key].source.startswith("NVIDIA") and e.get("video"):
            return project / e["video"]
    return videos[0]


def snapshots(project: Path) -> None:
    """そのエピソードの最初のフレーム（手先・左・右のカメラを横に並べたもの）。"""
    src = _feature_video(project)
    out = ASSETS / "tutorial_cameras.png"
    run([ffmpeg(), "-y", "-loglevel", "error", "-i", str(src), "-frames:v", "1", str(out)])
    print("wrote", out, "from", src.name)


def eval_video(project: Path) -> None:
    """evaluate_vla --video の 1 エピソード → ブラウザで再生できる H.264 の MP4 と、README 用の GIF。"""
    src = _feature_video(project)
    mp4 = ASSETS / "eval_replay.mp4"
    run([ffmpeg(), "-y", "-loglevel", "error", "-i", str(src), "-vf", "scale=iw*2:ih*2:flags=lanczos",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "26", "-preset", "slow", "-movflags", "+faststart", "-an", str(mp4)])  # fmt: skip
    gif = ASSETS / "eval_replay.gif"
    run([ffmpeg(), "-y", "-loglevel", "error", "-i", str(src), "-vf",
         "fps=10,scale=768:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96[p];[b][p]paletteuse=dither=bayer:bayer_scale=4",
         str(gif)])  # fmt: skip
    print("wrote", mp4, gif, "from", src.name)


# --------------------------------------------------------------------------- browser viewer media


class Viewer:
    """urdf2vla view をバックグラウンドで起動し、実際のポートを読む。"""

    def __init__(self, project: Path):
        self.port = free_port()
        self.proc = subprocess.Popen(
            [*URDF2VLA, "view", "--project", str(project), "--host", "127.0.0.1", "--port", str(self.port)],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )  # fmt: skip
        deadline = time.time() + 120
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            if not line and self.proc.poll() is not None:
                raise RuntimeError("urdf2vla view exited before it started")
            m = re.search(r"view: http://localhost:(\d+)", line)
            if m:
                self.port = int(m.group(1))
                return
        raise RuntimeError("urdf2vla view did not start")

    @property
    def url(self) -> str:
        return f"http://localhost:{self.port}"

    def close(self) -> None:
        self.proc.terminate()
        self.proc.wait(timeout=20)


def open_page(p, url: str):
    browser = p.chromium.launch(args=CHROMIUM_ARGS)
    page = browser.new_page(viewport=VIEWPORT)
    page.goto(url)
    page.wait_for_timeout(10000)  # 3D scene and meshes
    # headless chromium は WebGL をソフトウェアで描くので、その通知を閉じる
    for b in page.locator("button.mantine-Notification-closeButton").all():
        b.click()
    page.wait_for_timeout(500)
    return browser, page


def joint_inputs(page):
    """関節のスライダの横の数値入力（上から関節の順）。"""
    return page.locator("input.mantine-NumberInput-input:visible")


def set_joint(page, i: int, value: float) -> None:
    box = joint_inputs(page).nth(i)
    box.fill(f"{value:.3f}")
    box.press("Enter")


def view_media(project: Path, question_project: Path | None) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        viewer = Viewer(project)
        try:
            browser, page = open_page(p, viewer.url)
            page.screenshot(path=str(ASSETS / "view_franka.png"))
            print("wrote", ASSETS / "view_franka.png")
            joints_gif(page)
            browser.close()
        finally:
            viewer.close()
        if question_project is not None:
            viewer = Viewer(question_project)
            try:
                browser, page = open_page(p, viewer.url)
                page.get_by_role("button", name="提案の姿勢を見る").click()
                page.wait_for_timeout(1500)
                page.screenshot(path=str(ASSETS / "view_questions.png"))
                print("wrote", ASSETS / "view_questions.png")
                browser.close()
            finally:
                viewer.close()


def joints_gif(page) -> None:
    """関節を動かし、グリッパを閉じて開く様子の GIF（3D の部分だけを切り出す）。"""
    frames = Path(tempfile.mkdtemp(prefix="urdf2vla_gif_"))
    clip = {"x": 250, "y": 60, "width": 800, "height": 700}
    n = 0

    def shot(repeat: int = 1) -> None:
        nonlocal n
        page.wait_for_timeout(120)
        for _ in range(repeat):
            page.screenshot(path=str(frames / f"{n:04d}.png"), clip=clip)
            n += 1

    shot(6)
    # 肩（関節 1）を左右に振り、手首（関節 4）を曲げて戻す
    start1 = float(joint_inputs(page).nth(0).input_value() or 0.0)
    for v in [start1 + d for d in (0.15, 0.3, 0.45, 0.6, 0.45, 0.3, 0.15, 0.0, -0.15, -0.3, -0.45, -0.3, -0.15, 0.0)]:
        set_joint(page, 0, v)
        shot()
    start4 = float(joint_inputs(page).nth(3).input_value() or 0.0)
    for v in [start4 + d for d in (0.2, 0.4, 0.6, 0.4, 0.2, 0.0)]:
        set_joint(page, 3, v)
        shot()
    page.get_by_role("button", name="グリッパを閉じる").click()
    shot(3)
    page.get_by_role("button", name="グリッパを開く").click()
    shot(3)
    out = ASSETS / "view_joints.gif"
    run([ffmpeg(), "-y", "-loglevel", "error", "-framerate", "6", "-i", str(frames / "%04d.png"), "-vf",
         "split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer:bayer_scale=3", str(out)])  # fmt: skip
    shutil.rmtree(frames)
    print("wrote", out)


# --------------------------------------------------------------------------- FANUC CRX-3iA and SimReady grippers

SIMREADY_ROOT = "https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Isaac/6.2/Isaac/Robots"
# NVIDIA の Isaac Sim のアセットのサムネイル（アセットのフォルダの .thumbs/）。ライセンスがフォルダにあるものだけを使う
SIMREADY_THUMBS = [
    ("Robotiq 2F-85", "Robotiq/2F-85/.thumbs/256x256/Robotiq_2F_85_edit.usd.png", "CC BY 4.0", "Robotiq/2F-85/LICENSE"),
    ("Robotiq 2F-140", "Robotiq/2F-140/.thumbs/256x256/Robotiq_2F_140_physics_edit.usd.png", "BSD-3-Clause", "Robotiq/LICENSE"),
    ("Robotiq Hand-E", "Robotiq/Hand-E/.thumbs/256x256/Robotiq_Hand_E_edit.usd.png", "BSD-3-Clause", "Robotiq/LICENSE"),
    ("SCHUNK EGK 25", "Schunk/egk_25/.thumbs/256x256/schunk_egk_25.usd.png", "CC BY 4.0", "Schunk/egk_25/LICENSE"),
    ("SCHUNK EGU 50", "Schunk/egu_50/.thumbs/256x256/schunk_egu_50.usd.png", "CC BY 4.0", "Schunk/egu_50/LICENSE"),
]
# CRX-3iA に付け替えたグリッパ（projects/<名前> は examples/crx3ia から作り、robot.gripper.simready だけを変えたもの）
CRX_GRIPPER_PROJECTS = [
    ("Robotiq 2F-85", "fanuc_crx3ia"),
    ("UFactory xArm Gripper", "fanuc_crx3ia_ufactory_xarm"),
]


def _font(size: int):
    from PIL import ImageFont

    for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _labelled_row(images: list, labels: list[str], out: Path, tile: int) -> None:
    from PIL import Image, ImageDraw

    pad, text_h = 12, 34
    row = Image.new("RGB", (len(images) * (tile + pad) + pad, tile + text_h + pad), "white")
    draw = ImageDraw.Draw(row)
    for i, (im, label) in enumerate(zip(images, labels)):
        x = pad + i * (tile + pad)
        row.paste(im.convert("RGB").resize((tile, tile)), (x, pad))
        w = draw.textlength(label, font=_font(18))
        draw.text((x + (tile - w) / 2, tile + pad + 6), label, fill=(40, 40, 40), font=_font(18))
    row.save(out)
    print("wrote", out)


def simready_thumbs() -> None:
    """Isaac Sim のアセットライブラリにある SimReady のグリッパ（NVIDIA のサムネイル）を横に並べる。"""
    import io
    import urllib.request

    from PIL import Image

    images = []
    for _, path, _, _ in SIMREADY_THUMBS:
        with urllib.request.urlopen(f"{SIMREADY_ROOT}/{path}", timeout=60) as r:
            images.append(Image.open(io.BytesIO(r.read())))
    _labelled_row(images, [t[0] for t in SIMREADY_THUMBS], ASSETS / "simready_grippers.png", 256)


# グリッパを見せる画像だけのカメラ（正面から、解像度を上げて寄る）。学習用のカメラ（256×256）とは別のプロジェクトで撮る
GALLERY_CAMERA = {"preset": "front", "width": 768, "height": 768, "focal_length": 3.2}


def crx_gallery_project(name: str) -> Path | None:
    """projects/<name>（launch_sim まで済んだもの）を写して、カメラだけを GALLERY_CAMERA にして launch_sim を実行する。"""
    import yaml

    src, dst = ROOT / "projects" / name, ROOT / "projects" / f"docs_{name}_gallery"
    snap = dst / "snapshots" / "front_cam_start.png"
    if snap.exists():
        return snap
    if not (src / "logs" / "latest_launch_sim.json").exists():
        print("skip", name, "(run it through launch_sim first)")
        return None
    shutil.copytree(src, dst, dirs_exist_ok=True, ignore=shutil.ignore_patterns("datasets", "eval_videos", "logs", "snapshots"))
    (dst / "logs").mkdir(exist_ok=True)
    for f in (src / "logs").glob("latest_*.json"):
        shutil.copy(f, dst / "logs" / f.name)
    cfg = yaml.safe_load((dst / "project.yaml").read_text())
    cfg["name"] = dst.name
    cfg["cameras"] = [GALLERY_CAMERA]
    (dst / "project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))
    for step in ("configure_camera", "launch_sim"):
        run([*URDF2VLA, step, "--project", str(dst)], stdout=subprocess.DEVNULL)
    return snap if snap.exists() else None


def crx_media() -> None:
    """CRX-3iA: グリッパを付け替えたプロジェクトを正面から写した画像、カメラ画像、再生の動画。"""
    from PIL import Image

    images, labels = [], []
    for label, name in CRX_GRIPPER_PROJECTS:
        snap = crx_gallery_project(name)
        if snap is None:
            continue
        images.append(Image.open(snap))
        labels.append(label)
    if images:
        _labelled_row(images, labels, ASSETS / "crx3ia_grippers.png", 440)
    project = ROOT / "projects" / "fanuc_crx3ia"
    if (project / "build" / "eval_report.yaml").exists():
        src = _feature_video(project)
        run([ffmpeg(), "-y", "-loglevel", "error", "-i", str(src), "-frames:v", "1", str(ASSETS / "crx3ia_cameras.png")])
        run([ffmpeg(), "-y", "-loglevel", "error", "-i", str(src), "-vf",
             "fps=10,scale=768:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96[p];[b][p]paletteuse=dither=bayer:bayer_scale=4",
             str(ASSETS / "crx3ia_replay.gif")])  # fmt: skip
        print("wrote crx3ia_cameras.png, crx3ia_replay.gif from", src.name)


def crx_question_project() -> Path:
    """CRX-3iA の例（FANUC の URDF、examples/crx3ia/fetch_urdf.sh で取得）を import_robot まで進めたプロジェクト。
    FANUC の URDF のゼロ姿勢は手先が下を向かないので、import_robot が robot.default_pose（IK の提案）を質問する。"""
    proj = ROOT / "projects" / "docs_crx3ia_pose"
    if not (proj / "logs" / "latest_import_robot.json").exists():
        run([*URDF2VLA, "init", str(proj), "--from", "examples/crx3ia/project.yaml", "--name", "docs_crx3ia_pose", "--force"])
        for step in ("inspect_robot", "configure_robot", "configure_task", "configure_camera", "import_robot"):
            run([*URDF2VLA, step, "--project", str(proj)], stdout=subprocess.DEVNULL)
    return proj


def crx_view_media(project: Path) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        viewer = Viewer(project)
        try:
            browser, page = open_page(p, viewer.url)
            page.get_by_role("button", name="提案の姿勢を見る").click()
            page.wait_for_timeout(1500)
            page.screenshot(path=str(ASSETS / "view_questions_crx3ia.png"))
            print("wrote", ASSETS / "view_questions_crx3ia.png")
            browser.close()
        finally:
            viewer.close()


def question_project() -> Path:
    """Franka の例で、初期姿勢を手先が前を向く姿勢にしたプロジェクト。import_robot が robot.default_pose（IK の提案）を質問する。"""
    proj = ROOT / "projects" / "docs_franka_pose"
    if not (proj / "logs" / "latest_import_robot.json").exists():
        import yaml

        run([*URDF2VLA, "init", str(proj), "--from", "examples/franka/project.yaml", "--force"])
        cfg = yaml.safe_load((proj / "project.yaml").read_text())
        cfg["name"] = "docs_franka_pose"
        # 前腕が水平で手先が前を向く姿勢（上から掴むのに向かない）。Franka はゼロ姿勢だと手先が下を向くので質問されない
        cfg["robot"]["default_pose"] = {"panda_joint1": 0.0, "panda_joint2": 0.0, "panda_joint3": 0.0, "panda_joint4": -1.5708,
                                        "panda_joint5": 0.0, "panda_joint6": 0.0, "panda_joint7": 0.7854}
        (proj / "project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))
        for step in ("inspect_robot", "configure_robot", "configure_task", "configure_camera", "import_robot"):
            run([*URDF2VLA, step, "--project", str(proj)], stdout=subprocess.DEVNULL)
    return proj


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default="projects/tutorial_franka", help="a project that has finished the tutorial")
    ap.add_argument("--only", choices=["sim", "view", "crx"], help="only simulation media, only viewer media, or only the CRX-3iA / gripper media")
    ap.add_argument("--no-question", action="store_true", help="skip the question screenshot (needs the sim container)")
    args = ap.parse_args()
    project = (ROOT / args.project).resolve()
    ASSETS.mkdir(parents=True, exist_ok=True)
    if args.only in (None, "sim"):
        snapshots(project)
        eval_video(project)
    if args.only in (None, "view"):
        view_media(project, None if args.no_question else question_project())
    if args.only in (None, "crx"):
        simready_thumbs()
        crx_media()
        if not args.no_question:
            crx_view_media(crx_question_project())
    total = sum(f.stat().st_size for f in ASSETS.iterdir()) / 1e6
    print(f"docs/assets: {total:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
