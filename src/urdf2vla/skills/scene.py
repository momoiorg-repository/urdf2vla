"""configure_task / configure_camera（ホストで実行）。"""

from __future__ import annotations

import math

from urdf2vla.project import Project
from urdf2vla.registry import Arg, skill
from urdf2vla.result import SkillResult
from urdf2vla.schema.camera import CameraConfig, CamerasConfig
from urdf2vla.schema.robot import RobotConfig
from urdf2vla.schema.task import ObjectConfig, TableConfig, TargetConfig, TaskConfig
from urdf2vla import objects as catalog
from urdf2vla.urdf import analysis as A
from urdf2vla.urdf.kinematics import Transform, cross, look_at_ros, normalize
from urdf2vla.urdf.parser import parse_urdf


def _robot(project: Project) -> RobotConfig:
    return RobotConfig.model_validate(project.read_generated("robot_config.yaml"))


def arm_geometry(project: Project, cfg: RobotConfig) -> tuple[list[list[float]], list[float]]:
    """arm ごとの肩（最初の関節の先の link）の位置（env 座標、base_pose を反映）と腕の長さの目安。
    腕の長さは肩から TCP までの関節間距離の合計（肩までの取り付けオフセットは含めない）。"""
    model = parse_urdf(cfg.asset.host_path)
    base = Transform(tuple(cfg.base_pose.pos), tuple(cfg.base_pose.rot_xyzw))
    shoulders, lengths = [], []
    for arm in cfg.arms:
        # 肩 = 最初の水平軸の関節（台座の旋回軸の次のピッチ軸など）。なければ最初の関節
        first = model.joints[arm.joints[0]]
        for name in arm.joints:
            j = model.joints[name]
            axis = Transform((0, 0, 0), base.rot).apply(A.joint_axis_in(model, cfg.root_link, j))
            if abs(axis[2]) < 0.7:
                first = j
                break
        shoulders.append([round(c, 4) for c in base.apply(A.relative_transform(model, cfg.root_link, first.child).pos)])
        length = sum(math.sqrt(sum(c * c for c in j.xyz)) for j in A.path_joints(model, first.child, arm.ik_body))
        lengths.append(round(length + math.sqrt(sum(c * c for c in arm.tcp_offset.pos)), 4))
    return shoulders, lengths


GRASP_CLEAR = 0.05  # 把持点の台からの高さの目安
SQRT_HALF = math.sqrt(0.5)
GRASP_MARGIN = 0.012  # 物体の幅がグリッパの開きよりこれ以上小さいこと [m]（指の厚み・位置のずれ）


def gripper_opening(project: Project, cfg: RobotConfig) -> float | None:
    """グリッパの指先の最大の開き [m]。SimReady はカタログの値、直動の指は可動量の和。
    双腕なら狭い方（すべての物体を両手で扱えるようにするため）、回転の指が 1 つでもあれば分からない（None）。"""
    if not cfg.grippers:
        return None
    meta = project.read_generated("robot_metadata.yaml")
    types = {j["name"]: j["type"] for j in meta.get("joints", [])}
    openings = []
    for g in cfg.grippers:
        if g.simready:
            from urdf2vla.grippers import get_gripper

            opening = get_gripper(g.simready).max_opening or None
        elif all(types.get(j) == "prismatic" for j in g.joints):
            opening = round(sum(abs(g.open[j] - g.close[j]) for j in g.joints), 4)
        else:
            return None
        if opening is None:
            return None
        openings.append(opening)
    return min(openings) if openings else None


# --------------------------------------------------------------------------- configure_task


@skill(
    "configure_task",
    phase=1,
    runs_in="host",
    summary="Pick & Place のシーン（台・物体・置き場所）をロボットの腕の長さから配置する",
    inputs=["generated/robot_config.yaml", "project.yaml: task.*"],
    outputs=["generated/task.yaml"],
    requires=["configure_robot"],
)
def configure_task(project: Project, opts: dict, result: SkillResult, log) -> None:
    cfg = _robot(project)
    shoulders, lengths = arm_geometry(project, cfg)
    length = min(lengths)
    base = cfg.base_pose.pos
    # 作業域の前で Pick & Place する。把持点は肩から腕の長さの半分の距離、なるべく斜め下 45° に置く。
    # 肩が高くて下まで届かないロボットは台を高くする（台の上面が base より上になる）
    # 双腕では肩の間（左右の中央）が両手の作業域。正面側の肩を基準に台を置き、腕ごとの構えは home_tcp に残す
    shoulder_z = max(sh[2] for sh in shoulders)
    shoulder_x = max(sh[0] for sh in shoulders)
    h = shoulder_z - base[2]
    r = 0.5 * length
    if h - GRASP_CLEAR <= r * SQRT_HALF:
        top_z = base[2]
        drop = max(h - GRASP_CLEAR, 0.0)
        d = math.sqrt(max(r * r - drop * drop, (0.3 * length) ** 2))
    else:
        drop = r * SQRT_HALF
        top_z = shoulder_z - drop - GRASP_CLEAR
        d = drop
    s = min(max(length / 1.09, 0.4), 1.5)  # Franka を 1 とした大きさの比
    cy = sum(sh[1] for sh in shoulders) / len(shoulders)
    spread = max(sh[1] for sh in shoulders) - min(sh[1] for sh in shoulders)
    wx, wy = shoulder_x + d, cy
    # 台はロボットの正面中央に。手前の端がロボットの base にかからないようにする
    size_x = max(min(0.65 * length, 2 * (wx - base[0] - 0.06)), 0.2)
    size_y = max(1.04 * length, spread + 0.3)
    home = {}
    for arm, sh in zip(cfg.arms, shoulders):
        home[arm.name] = [round(wx, 3), round(sh[1], 3), round(top_z + GRASP_CLEAR + 0.6 * drop, 3)]

    # 物体と置き場所: project.yaml の task.objects（カタログの key のリスト）と task.container（key、"disc" は円）
    spec = project.config().get("task") or {}
    keys = list(spec.get("objects") or catalog.DEFAULT_OBJECTS)
    container = spec.get("container", catalog.DEFAULT_CONTAINER)
    opening = gripper_opening(project, cfg)
    objs, dropped = [], []
    for key in keys:
        if key == "cube":
            objs.append(ObjectConfig())
            continue
        try:
            a = catalog.get_object(key)
        except KeyError as e:
            result.fail(str(e))
            return
        if opening is not None and a.grasp_width > opening - GRASP_MARGIN:
            dropped.append(f"{key}（幅 {a.grasp_width * 100:.1f} cm）")
            continue
        objs.append(ObjectConfig(key=a.key, name=a.name, asset=a.url, variants=dict(a.variants), scale=a.scale,
                                 size=a.scaled_size, bottom=a.scaled_bottom, box=a.box))  # fmt: skip
    if dropped:
        result.warnings.append(
            f"グリッパの開き（{opening * 100:.1f} cm）に入らない物体を除きました: {', '.join(dropped)}。"
        )
    if not objs:
        result.fail(f"グリッパの開き（{(opening or 0) * 100:.1f} cm）に入る物体がありません。task.objects に小さい物体（例: cube, lime）を選んでください。")
        return
    spawn_x = [round(wx - 0.06 * s, 3), round(wx + 0.06 * s, 3)]
    spawn_y = [round(wy + 0.06 * s, 3), round(wy + 0.16 * s, 3)]
    if container in (None, "disc"):
        target = TargetConfig(pos=[round(wx, 3), round(wy - 0.12 * s, 3)], radius=0.06)
    else:
        try:
            c = catalog.get_container(container)
        except KeyError as e:
            result.fail(str(e))
            return
        size = c.scaled_size
        # 容器は物体を置く範囲の右（-y）に、縁と物体の間に隙間をあけて置く
        widest = max(o.grasp_width for o in objs)
        ty = min(wy - 0.12 * s, spawn_y[0] - widest / 2 - 0.04 - size[1] / 2)
        target = TargetConfig(pos=[round(wx, 3), round(ty, 3)], key=c.key, name=c.name, asset=c.url, variants=dict(c.variants),
                              scale=c.scale, size=size, bottom=c.scaled_bottom, radius=round(min(size[0], size[1]) / 2, 3))  # fmt: skip
        if abs(ty - cy) + size[1] / 2 > size_y / 2 or size[0] > size_x:
            size_y = round(max(size_y, 2 * (abs(ty - cy) + size[1] / 2) + 0.04), 3)
            size_x = round(max(size_x, size[0] + 0.04), 3)
    task = TaskConfig(
        table=TableConfig(center=[round(wx, 3), round(cy, 3)], size=[round(size_x, 3), round(size_y, 3), 0.05], top_z=round(top_z, 3)),
        objects=objs,
        spawn_x=spawn_x,
        spawn_y=spawn_y,
        target=target,
        instruction="put the {object} in the {target}" if target.is_container else TaskConfig.model_fields["instruction"].default,
        home_tcp=home,
    )
    path = project.write_generated("task.yaml", task.model_dump(mode="json"), section="task")
    TaskConfig.model_validate(project.read_generated("task.yaml"))
    result.outputs["task"] = project.rel(path)
    result.data = {
        "arm_length_estimate": length,
        "arms": [arm.name for arm in cfg.arms],
        "workspace_center": [round(wx, 3), round(wy, 3), round(top_z, 3)],
        "control_hz": task.control_hz,
        "objects": [o.key for o in task.objects],
        "target": task.target.key,
        "instructions": [task.instruction_for(i) for i in range(len(task.objects))],
        "gripper_opening_m": opening,
    }
    result.warnings.append(
        "台・物体・置き場所はロボットの腕の長さから自動配置しました（launch_sim の snapshot で確認してください）。"
        "変更は project.yaml の overrides.task に書きます。"
    )
    log.info("task: work=(%.3f, %.3f) top=%.3f length=%.3f", wx, wy, top_z, length)
    result.next = ["configure_camera"]


# --------------------------------------------------------------------------- configure_camera


def _wrist_camera(project: Project, cfg: RobotConfig, spec: dict) -> CameraConfig:
    arm = cfg.arms[int(spec.get("arm", 0))]
    gripper = cfg.gripper_for(arm.name)
    ee_from_body = Transform(tuple(arm.ee_offset.pos), tuple(arm.ee_offset.rot_xyzw))
    approach = tuple(Transform((0, 0, 0), ee_from_body.inverse().rot).apply(tuple(arm.approach_axis)))

    # 指が開閉する方向と直交する側にカメラを付ける（指が視界をふさがないように）
    finger_axis = None
    if arm.finger_axis:
        finger_axis = tuple(Transform((0, 0, 0), ee_from_body.inverse().rot).apply(tuple(arm.finger_axis)))
    if finger_axis is None or abs(sum(a * b for a, b in zip(normalize(finger_axis), approach))) > 0.9:
        finger_axis = (0.0, 1.0, 0.0) if abs(approach[1]) < 0.9 else (1.0, 0.0, 0.0)
    lateral = normalize(cross(approach, normalize(finger_axis)))

    tcp_dist = math.sqrt(sum(c * c for c in arm.tcp_offset.pos)) if gripper else 0.1
    # グリッパ本体の外側に付ける: 手先付近の衝突形状が側方にどこまで張り出しているかを両側で調べ、
    # 張り出しの小さい側の外側（余白 2.5cm）に置く。本体の中に埋まると何も写らない
    side, offset = 1.0, float(spec.get("offset", 0.08))
    # SimReady のグリッパは URDF にメッシュがないので、既定の余白（本体の外側 8cm）で置く
    if "offset" not in spec and not (gripper and gripper.simready):
        model = parse_urdf(cfg.asset.host_path)
        meta = project.read_generated("robot_metadata.yaml")
        mesh_paths = (meta.get("meshes") or {}).get("resolved", {})
        links = {arm.ee_link, arm.ik_body} | (A.gripper_links(model, gripper) if gripper else set())
        pts = A.collision_points(model, arm.ee_link, links, mesh_paths)
        near = [p for p in pts if -0.05 <= sum(a * b for a, b in zip(p, approach)) <= tcp_dist + 0.02]
        if near:
            best = None
            for sgn in (1.0, -1.0):
                extent = max(sgn * sum(a * b for a, b in zip(p, lateral)) for p in near)
                off = max(0.08, extent + 0.025)
                if best is None or off < best[1] - 1e-6:
                    best = (sgn, off)
            side, offset = best
    lateral = tuple(side * c for c in lateral)
    eye = tuple(lateral[i] * offset - approach[i] * 0.02 for i in range(3))
    target = tuple(approach[i] * (tcp_dist + 0.06) for i in range(3))
    rot = look_at_ros(eye, target, up=lateral)
    in_body = ee_from_body @ Transform(eye, rot)
    return CameraConfig(
        name=spec.get("name", "wrist_cam"),
        mount="link",
        parent_link=arm.ee_link,
        mount_body=arm.ik_body,
        pos=[round(c, 5) for c in in_body.pos],
        rot_xyzw=[round(c, 6) for c in in_body.rot],
        convention="ros",
        width=int(spec.get("width", 256)),
        height=int(spec.get("height", 256)),
        clipping_range=[0.01, 3.0],
        preset="wrist",
    )


# DROID（https://droid-dataset.github.io/）のカメラ配置: 手先 1 台 + ロボットの左右の高い位置から作業台を見下ろす外部カメラ 2 台。
# DROID の外部カメラ（Franka の base から前 0.05 m・左右 0.57 m・上 0.66 m、広角）を、台の大きさで拡大縮小して使う
DROID_PRESETS = ("wrist", "left", "right")
DROID_FOCAL_LENGTH = 2.1  # DROID の外部カメラと同じ焦点距離
DROID_APERTURE = 2.94  # 正方形の画像で画角 約 70°（DROID は 16:9 で水平 104° / 垂直 71°）


def _droid_side_camera(project: Project, cfg: RobotConfig, spec: dict, side: str) -> CameraConfig:
    """DROID の外部カメラの配置: ロボットの左（+y）/ 右（-y）の高い位置から、台の中央を斜めに見下ろす。"""
    task = TaskConfig.model_validate(project.read_generated("task.yaml"))
    cx, cy = task.table.center
    span = max(task.table.size[0], 0.6)  # Franka の DROID の作業域（約 0.6 m）を基準に拡大縮小
    bx = cfg.base_pose.pos[0]
    sgn = 1.0 if side == "left" else -1.0
    eye = (bx + 0.08 * span, cy + sgn * 0.95 * span, task.table.top_z + 1.1 * span)
    lookat = (cx, cy, task.table.top_z + 0.05)
    rot = look_at_ros(eye, lookat)
    return CameraConfig(
        name=spec.get("name", f"{side}_cam"),
        mount="world",
        pos=[round(c, 4) for c in eye],
        rot_xyzw=[round(c, 6) for c in rot],
        convention="ros",
        width=int(spec.get("width", 256)),
        height=int(spec.get("height", 256)),
        focal_length=float(spec.get("focal_length", DROID_FOCAL_LENGTH)),
        horizontal_aperture=float(spec.get("horizontal_aperture", DROID_APERTURE)),
        preset=side,
    )


def _world_camera(project: Project, spec: dict, side: str) -> CameraConfig:
    task = TaskConfig.model_validate(project.read_generated("task.yaml"))
    cx, cy = task.table.center
    span = max(task.table.size[0], 0.6)
    # 台の手前寄り・少し上を注視して、台の上の物体とロボットの手先が両方入るようにする
    lookat = (cx - 0.25 * span, cy, task.table.top_z + 0.32 * span)
    if side == "front":  # 台の向こう側からロボットの方を見る
        eye = (cx + 1.3 * span, cy, task.table.top_z + 0.85 * span)
    else:  # 横から
        eye = (cx - 0.1 * span, cy - 1.6 * span, task.table.top_z + 0.7 * span)
    rot = look_at_ros(eye, lookat)
    return CameraConfig(
        name=spec.get("name", f"{side}_cam"),
        mount="world",
        pos=[round(c, 4) for c in eye],
        rot_xyzw=[round(c, 6) for c in rot],
        convention="ros",
        width=int(spec.get("width", 256)),
        height=int(spec.get("height", 256)),
        preset=side,
    )


def _explicit_camera(cfg: RobotConfig, spec: dict, result: SkillResult) -> CameraConfig | None:
    cam = CameraConfig.model_validate(spec)
    if cam.mount == "link":
        model = parse_urdf(cfg.asset.host_path)
        if cam.parent_link not in model.links:
            result.fail(f"カメラ '{cam.name}' の parent_link '{cam.parent_link}' は URDF にありません")
            return None
        body = A.rigid_ancestor(model, cam.parent_link)
        if body != cam.parent_link:
            t = A.relative_transform(model, body, cam.parent_link) @ Transform(tuple(cam.pos), tuple(cam.rot_xyzw))
            cam.pos, cam.rot_xyzw = [round(c, 6) for c in t.pos], [round(c, 6) for c in t.rot]
        cam.mount_body = body
    return cam


@skill(
    "configure_camera",
    phase=1,
    runs_in="host",
    summary="カメラ（既定は DROID と同じ手先 + 左右の外部カメラ。正面・横のプリセット、または取付 link と位置姿勢の明示指定も可）を設定する",
    inputs=["generated/robot_config.yaml", "generated/task.yaml", "project.yaml: cameras"],
    outputs=["generated/cameras.yaml"],
    requires=["configure_task"],
    args=[Arg(("--preset",), {"help": "project.yaml に cameras がないときに使うプリセット（既定: wrist,left,right = DROID の配置）"})],
)
def configure_camera(project: Project, opts: dict, result: SkillResult, log) -> None:
    cfg = _robot(project)
    specs = project.config().get("cameras")
    if not specs:
        presets = (opts.get("preset") or ",".join(DROID_PRESETS)).split(",")
        specs = [{"preset": p.strip()} for p in presets if p.strip()]
    cams: list[CameraConfig] = []
    for spec in specs:
        preset = spec.get("preset")
        if preset == "wrist" and "arm" not in spec and len(cfg.arms) > 1:
            # 双腕: 腕ごとに手先カメラを付ける（<arm>_wrist_cam）
            for i, arm in enumerate(cfg.arms):
                cams.append(_wrist_camera(project, cfg, {**spec, "arm": i, "name": f"{arm.name}_wrist_cam"}))
            continue
        if preset == "wrist":
            cam = _wrist_camera(project, cfg, spec)
        elif preset in ("left", "right"):
            cam = _droid_side_camera(project, cfg, spec, preset)
        elif preset in ("front", "side"):
            cam = _world_camera(project, spec, preset)
        elif preset is None:
            cam = _explicit_camera(cfg, spec, result)
        else:
            result.fail(f"未知のカメラ preset '{preset}'（wrist / left / right / front / side、または明示指定）")
            return
        if cam is None:
            return
        cams.append(cam)
    names = [c.name for c in cams]
    if len(set(names)) != len(names):
        result.fail(f"カメラ名が重複しています: {names}")
        return
    data = CamerasConfig(cameras=cams).model_dump(mode="json")
    path = project.write_generated("cameras.yaml", data, section="cameras")
    CamerasConfig.model_validate(project.read_generated("cameras.yaml"))
    result.outputs["cameras"] = project.rel(path)
    result.data = {"cameras": [{"name": c.name, "mount": c.mount_body or "world", "preset": c.preset, "resolution": [c.width, c.height]} for c in cams]}
    result.next = ["import_robot"]
