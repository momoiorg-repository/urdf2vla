"""URDF の構造解析: root・arm・gripper・End Effector 候補を求める。

ここでは「URDF から確実に言えること」だけを決め、曖昧なものは候補として返す。
どれを採用するかは configure_robot がユーザの回答（answers.yaml）と合わせて決める。
"""

from __future__ import annotations

import re

from urdf2vla.schema.robot import (
    ArmCandidate,
    EECandidate,
    GripperCandidate,
    JointInfo,
    LinkInfo,
)
from urdf2vla.urdf.kinematics import Transform
from urdf2vla.urdf.parser import Joint, UrdfError, UrdfModel

GRIPPER_NAME_RE = re.compile(r"finger|knuckle|gripper|jaw|claw|thumb|pinch", re.IGNORECASE)
EE_NAME_RE = re.compile(
    r"(^|_)(ee|eef|end_?effector|tcp|tool0?|hand|grasp(_frame)?|gripper_center)($|_)", re.IGNORECASE
)


# --------------------------------------------------------------------------- tree helpers


def root_link(model: UrdfModel) -> str:
    children = {j.child for j in model.joints.values()}
    roots = [name for name in model.links if name not in children]
    if len(roots) != 1:
        raise UrdfError(f"root link が 1 つに決まりません（候補: {roots}）。URDF が 1 本の木になっているか確認してください。")
    return roots[0]


def articulation_root(model: UrdfModel, root: str) -> tuple[str, bool]:
    """'world' のような空の root link は飛ばし、実体のある最初の link を返す。"""
    link = model.links[root]
    kids = model.child_joints(root)
    if link.is_empty and len(kids) == 1 and kids[0].type == "fixed":
        return kids[0].child, True
    return root, False


def path_joints(model: UrdfModel, start: str, end: str) -> list[Joint]:
    """start から end（start の子孫）までの関節列。"""
    chain: list[Joint] = []
    link = end
    while link != start:
        j = model.parent_joint(link)
        if j is None:
            raise UrdfError(f"{end} は {start} の子孫ではありません")
        chain.append(j)
        link = j.parent
    return list(reversed(chain))


def _joint_motion(j: Joint, q: float) -> Transform:
    import math

    from urdf2vla.urdf.kinematics import normalize

    if not q or not j.movable:
        return Transform()
    ax = normalize(j.axis)
    if j.type == "prismatic":
        return Transform(tuple(a * q for a in ax))
    h = math.sin(q / 2)
    return Transform((0, 0, 0), (ax[0] * h, ax[1] * h, ax[2] * h, math.cos(q / 2)))  # (x, y, z, w)


def _from_root(model: UrdfModel, link: str, q: dict[str, float] | None = None) -> Transform:
    t = Transform()
    for j in path_joints(model, root_link(model), link):
        t = t @ Transform.from_xyz_rpy(j.xyz, j.rpy)
        if q and j.name in q:
            t = t @ _joint_motion(j, q[j.name])
    return t


def relative_transform(model: UrdfModel, start: str, end: str, q: dict[str, float] | None = None) -> Transform:
    """start -> end の変換（start と end は木のどこにあってもよい）。q に関節角があればその姿勢、なければ 0。"""
    if start == end:
        return Transform()
    return _from_root(model, start, q).inverse() @ _from_root(model, end, q)


def joint_axis_in(model: UrdfModel, frame: str, joint: Joint) -> tuple[float, float, float]:
    """関節軸（関節 frame で定義）を frame 座標で表す。"""
    t = relative_transform(model, frame, joint.parent) @ Transform.from_xyz_rpy(joint.xyz, joint.rpy)
    return Transform((0, 0, 0), t.rot).apply(joint.axis)


def fixed_descendants(model: UrdfModel, link: str) -> list[str]:
    """link から fixed 関節だけで辿れる子孫（link 自身を含む）。"""
    out = [link]
    for j in model.child_joints(link):
        if j.type == "fixed":
            out += fixed_descendants(model, j.child)
    return out


def subtree_links(model: UrdfModel, link: str) -> list[str]:
    out = [link]
    for j in model.child_joints(link):
        out += subtree_links(model, j.child)
    return out


def rigid_ancestor(model: UrdfModel, link: str) -> str:
    """質量を持つ link まで fixed 関節を遡る（Isaac Sim で剛体として扱える link）。"""
    cur = link
    while not model.links[cur].has_inertial:
        j = model.parent_joint(cur)
        if j is None or j.type != "fixed":
            return cur
        cur = j.parent
    return cur


# --------------------------------------------------------------------------- grippers


def _is_gripper_joint(model: UrdfModel, j: Joint, art_root: str, mimic_related: set[str]) -> bool:
    if not j.movable:
        return False
    if GRIPPER_NAME_RE.search(j.name) or GRIPPER_NAME_RE.search(j.child):
        return True
    if j.name in mimic_related:
        return True
    if j.type == "continuous" or j.parent == art_root:  # 車輪などは除外
        return False
    # 同じ親 link に可動関節の兄弟があり、どちらも先が短い → 指
    siblings = [s for s in model.child_joints(j.parent) if s.movable and s.name != j.name]
    if not siblings:
        return False

    def depth(link: str) -> int:
        return max([0] + [(1 if c.movable else 0) + depth(c.child) for c in model.child_joints(link)])

    return depth(j.child) <= 2 and all(depth(s.child) <= 2 for s in siblings)


def find_grippers(model: UrdfModel, art_root: str, forced: list[str] | None = None) -> list[GripperCandidate]:
    """gripper 関節を見つけ、取付 link ごとにまとめる。forced（ユーザ指定）があればそれを使う。"""
    mimic_related: set[str] = set()
    for j in model.joints.values():
        if j.mimic:
            mimic_related |= {j.name, j.mimic.joint}
    if forced:
        bad = [n for n in forced if n not in model.joints or not model.joints[n].movable]
        if bad:
            raise UrdfError(f"gripper 関節に可動関節でない名前があります: {bad}")
        gjoints = [model.joints[n] for n in forced]
    else:
        gjoints = [j for j in model.movable_joints if _is_gripper_joint(model, j, art_root, mimic_related)]
    gnames = {j.name for j in gjoints}

    groups: dict[str, list[Joint]] = {}
    for j in gjoints:
        mount = j.parent
        pj = model.parent_joint(mount)
        while pj is not None and (pj.name in gnames or (pj.type == "fixed" and _under_gripper(model, pj, gnames))):
            mount = pj.parent
            pj = model.parent_joint(mount)
        groups.setdefault(mount, []).append(j)

    out: list[GripperCandidate] = []
    for mount, joints in groups.items():
        followers = {j.name: j for j in joints if j.mimic and j.mimic.joint in {k.name for k in joints}}
        leaders = [j for j in joints if j.name not in followers]
        leader = leaders[0] if leaders else joints[0]
        types = sorted({j.type for j in joints})
        out.append(
            GripperCandidate(
                mount_link=mount,
                joints=[j.name for j in joints],
                leader=leader.name,
                joint_type=types[0] if len(types) == 1 else "mixed",
                mimic={n: [f.mimic.multiplier, f.mimic.offset] for n, f in followers.items()},
            )
        )
    return out


def _under_gripper(model: UrdfModel, fixed_joint: Joint, gnames: set[str]) -> bool:
    """fixed 関節の上流に gripper 関節がある（= gripper 内部の固定部品）か。"""
    pj = model.parent_joint(fixed_joint.parent)
    while pj is not None:
        if pj.name in gnames:
            return True
        if pj.movable:
            return False
        pj = model.parent_joint(pj.parent)
    return False


# --------------------------------------------------------------------------- arms & EE


def find_arms(
    model: UrdfModel, art_root: str, grippers: list[GripperCandidate]
) -> tuple[list[ArmCandidate], list[str]]:
    gnames = {n for g in grippers for n in g.joints}
    tips: list[tuple[str, int | None]] = [(g.mount_link, i) for i, g in enumerate(grippers)]
    if not tips:
        # gripper がない: 可動関節の連鎖の末端ごとに arm 候補を作る
        leaves = [name for name in subtree_links(model, art_root) if not model.child_joints(name)]
        seen: set[tuple[str, ...]] = set()
        for leaf in leaves:
            movable = tuple(j.name for j in path_joints(model, art_root, leaf) if j.movable)
            if movable and movable not in seen:
                seen.add(movable)
                tips.append((leaf, None))

    paths = []
    for tip, gi in tips:
        movable = [j for j in path_joints(model, art_root, tip) if j.movable and j.name not in gnames]
        paths.append((tip, gi, movable))

    # 複数の arm が共有する関節（胴体・昇降など）は arm から外して other_joints に回す
    counts: dict[str, int] = {}
    for _, _, movable in paths:
        for j in movable:
            counts[j.name] = counts.get(j.name, 0) + 1
    shared = {n for n, c in counts.items() if c > 1}

    arms: list[ArmCandidate] = []
    for idx, (tip, gi, movable) in enumerate(paths):
        joints = [j for j in movable if j.name not in shared]
        if not joints:
            continue
        arm = ArmCandidate(
            name=_arm_name(joints, idx, len(paths)),
            joints=[j.name for j in joints],
            tip_link=joints[-1].child,
            gripper=gi,
        )
        arm.ee_candidates = ee_candidates(model, arm, grippers[gi] if gi is not None else None)
        arms.append(arm)

    used = {n for a in arms for n in a.joints} | gnames
    others = [j.name for j in model.movable_joints if j.name not in used]
    return arms, others


SIDE_RE = {
    "left": re.compile(r"(^|[_\-])(left|l)([_\-]|$)|left", re.IGNORECASE),
    "right": re.compile(r"(^|[_\-])(right|r)([_\-]|$)|right", re.IGNORECASE),
}


def _arm_name(joints: list[Joint], idx: int, n: int) -> str:
    """arm の名前。関節名・link 名の大半に left/right（l_, _L, r_, _R など）が付いていればそれを使う。"""
    if n == 1:
        return "main"
    names = [j.name for j in joints] + [j.child for j in joints]
    for side, rx in SIDE_RE.items():
        if sum(bool(rx.search(x)) for x in names) > len(names) / 2:
            return side
    return f"arm{idx}"


def front_axis_guess(model: UrdfModel, root: str, arms: list[ArmCandidate]) -> tuple[str, str]:
    """ロボットの正面方向（root 座標の +x / -x / +y / -y）の推定と根拠。
    双腕なら肩を結ぶ線に直交し、腕が伸びている側。単腕なら腕が伸びている水平方向。分からなければ +x（ROS の慣例）。"""
    if not arms:
        return "+x", "arm がないので ROS の慣例（+x が正面）"
    shoulders, tips = [], []
    for a in arms:
        first = model.joints[a.joints[0]]
        shoulders.append(relative_transform(model, root, first.child).pos)
        tips.append(relative_transform(model, root, a.tip_link).pos)
    reach = [sum(t[i] - s[i] for t, s in zip(tips, shoulders)) / len(arms) for i in range(2)]
    lateral = [shoulders[0][i] - shoulders[-1][i] for i in range(2)]
    if len(arms) >= 2 and (lateral[0] ** 2 + lateral[1] ** 2) ** 0.5 > 0.05:
        k = 1 if abs(lateral[0]) >= abs(lateral[1]) else 0  # 肩が x 方向に並んでいれば正面は y 方向
        sign = "+" if reach[k] >= 0 else "-"
        axis = sign + "xy"[k]
        return axis, f"肩が {'xy'[1 - k]} 方向に並び、腕は {axis} 側に伸びている"
    k = 0 if abs(reach[0]) >= abs(reach[1]) else 1
    if (reach[0] ** 2 + reach[1] ** 2) ** 0.5 < 0.05:
        return "+x", "初期姿勢で腕がほぼ真上を向いているので、ROS の慣例（+x が正面）"
    axis = ("+" if reach[k] >= 0 else "-") + "xy"[k]
    return axis, f"腕が {axis} 側に伸びている"


def ee_candidates(model: UrdfModel, arm: ArmCandidate, gripper: GripperCandidate | None) -> list[EECandidate]:
    out: list[EECandidate] = []
    for link in fixed_descendants(model, arm.tip_link):
        reasons = []
        if gripper and link == gripper.mount_link:
            reasons.append("gripper の取付 link")
        if EE_NAME_RE.search(link):
            reasons.append("End Effector を示す名前")
        out.append(EECandidate(link=link, reasons=reasons, rigid=model.links[link].has_inertial))
    # 根拠のある候補を先に
    out.sort(key=lambda c: (-len(c.reasons), 0 if c.rigid else 1))
    return out


def strong_ee(cands: list[EECandidate]) -> list[EECandidate]:
    return [c for c in cands if c.reasons]


def collision_points(
    model: UrdfModel, frame: str, links, mesh_paths: dict[str, str], q: dict[str, float] | None = None
) -> list[tuple[float, float, float]]:
    """links の衝突形状（STL / OBJ / box）の頂点を frame 座標で返す（q があればその関節角の姿勢で）。"""
    from urdf2vla.urdf.meshes import mesh_vertices

    out = []
    for link in links:
        t_link = relative_transform(model, frame, link, q)
        for col in model.links[link].collisions:
            t = t_link @ Transform.from_xyz_rpy(col.xyz, col.rpy)
            pts = None
            if col.mesh and col.mesh in mesh_paths:
                verts = mesh_vertices(mesh_paths[col.mesh])
                if verts:
                    pts = [tuple(v[i] * col.scale[i] for i in range(3)) for v in verts]
            elif col.box:
                hx, hy, hz = (c / 2 for c in col.box)
                pts = [(sx * hx, sy * hy, sz * hz) for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]
            if pts:
                out += [t.apply(p) for p in pts]
    return out


def gripper_links(model: UrdfModel, gripper: GripperCandidate) -> set[str]:
    return {l for name in gripper.joints for l in subtree_links(model, model.joints[name].child)}


def approach_axis(
    model: UrdfModel, ee_body: str, gripper: GripperCandidate | None, mesh_paths: dict[str, str] | None = None
) -> tuple[list[float], str]:
    """ee_body 座標での把持の接近方向。
    指が 2 本以上なら指関節の取付位置の方向、片側の顎だけ動く直列グリッパなら動く顎の衝突形状の重心の方向。"""
    from urdf2vla.urdf.kinematics import normalize

    if gripper is None:
        return [0.0, 0.0, 1.0], "default"
    pts = []
    if len(gripper.joints) == 1 and mesh_paths:
        pts = collision_points(model, ee_body, gripper_links(model, gripper), mesh_paths)
    if not pts:
        for name in gripper.joints:
            j = model.joints[name]
            pts.append(relative_transform(model, ee_body, j.parent).apply(j.xyz))
    mean = [sum(p[i] for p in pts) / len(pts) for i in range(3)]
    try:
        axis = normalize(tuple(mean))
    except ValueError:
        return [0.0, 0.0, 1.0], "default"
    # 主軸に丸める（URDF のフレームは通常、軸に沿って定義されている）
    k = max(range(3), key=lambda i: abs(axis[i]))
    snapped = [0.0, 0.0, 0.0]
    snapped[k] = 1.0 if axis[k] > 0 else -1.0
    return snapped, "inferred"


def finger_axis(
    model: UrdfModel, ee_body: str, gripper: GripperCandidate | None, approach: list[float], mesh_paths: dict[str, str] | None = None
) -> list[float] | None:
    """ee_body 座標で指が開閉する方向（直動関節の軸 / 指の付け根の並び / 片側の顎なら顎の先端が動く方向）。
    接近方向と直交化して返す。"""
    from urdf2vla.urdf.kinematics import cross, normalize

    if gripper is None:
        return None
    axis = None
    pts = []
    for name in gripper.joints:
        j = model.joints[name]
        if j.type == "prismatic":
            axis = joint_axis_in(model, ee_body, j)
            break
        pts.append(relative_transform(model, ee_body, j.parent).apply(j.xyz))
    if axis is None and len(pts) >= 2:
        axis = tuple(a - b for a, b in zip(pts[0], pts[-1]))
    if axis is None and len(gripper.joints) == 1 and mesh_paths:
        # 回転する顎 1 枚: 先端（接近方向に最も先の点）が関節軸まわりに動く方向
        j = model.joints[gripper.joints[0]]
        verts = collision_points(model, ee_body, gripper_links(model, gripper), mesh_paths)
        if verts:
            tip = max(verts, key=lambda p: sum(a * b for a, b in zip(p, approach)))
            hinge = pts[0]
            axis = cross(joint_axis_in(model, ee_body, j), tuple(t - h for t, h in zip(tip, hinge)))
    if axis is None:
        return None
    dot = sum(a * b for a, b in zip(axis, approach))
    perp = tuple(a - dot * b for a, b in zip(axis, approach))
    try:
        return [round(c, 6) for c in normalize(perp)]
    except ValueError:
        return None


PAD_FROM_TIP = 0.012  # 指先の先端から把持点（指の腹の中心）までの距離の目安


def fingertip_extent(model: UrdfModel, ee_body: str, gripper: GripperCandidate, axis: list[float], mesh_paths: dict[str, str]) -> float | None:
    """指の衝突形状のうち、接近方向に最も先の点までの距離。形状が読めなければ None。
    片側の顎だけ動くグリッパでは固定側の顎が取付 link に含まれるので、取付 link も含める。"""
    links = set(gripper_links(model, gripper))
    if len(gripper.joints) == 1:
        links.add(gripper.mount_link)
    pts = collision_points(model, ee_body, links, mesh_paths)
    if not pts:
        return None
    return max(sum(a * b for a, b in zip(p, axis)) for p in pts)


def tcp_point(
    model: UrdfModel,
    ee_body: str,
    gripper: GripperCandidate | None,
    axis: list[float],
    mesh_paths: dict[str, str],
    q_open: dict[str, float] | None = None,
) -> tuple[list[float], str]:
    """ee_body 座標での把持点（TCP）。グリッパを開いた姿勢で、各指（片側だけ動く顎なら固定側の顎も）の
    先端付近の重心を求め、その中点を接近方向に垂直な位置、指先から PAD_FROM_TIP 手前を接近方向の位置にする。
    左右対称の 2 本指なら中央、片側だけ動く顎なら固定側の顎と開いた顎の間になる。"""
    if gripper is None:
        return [0.0, 0.0, 0.0], "default"
    bodies = [subtree_links(model, model.joints[n].child) for n in gripper.joints]
    if len(gripper.joints) == 1:
        bodies.append([gripper.mount_link])  # 固定側の顎
    centroids, tips = [], []
    for links in bodies:
        pts = collision_points(model, ee_body, links, mesh_paths, q_open)
        if not pts:
            continue
        proj = [sum(a * b for a, b in zip(p, axis)) for p in pts]
        tip = max(proj)
        near = [p for p, d in zip(pts, proj) if d > tip - 0.02]
        centroids.append([sum(p[i] for p in near) / len(near) for i in range(3)])
        tips.append(tip)
    if not centroids:
        dist, src = tcp_distance(model, ee_body, gripper, axis, mesh_paths)
        return [round(a * dist, 4) for a in axis], src
    mid = [sum(c[i] for c in centroids) / len(centroids) for i in range(3)]
    along = sum(a * b for a, b in zip(mid, axis))
    lateral = [m - along * a for m, a in zip(mid, axis)]
    d = max(tips) - PAD_FROM_TIP
    return [round(lateral[i] + axis[i] * d, 4) for i in range(3)], "collision_mesh"


def tcp_distance(
    model: UrdfModel, ee_body: str, gripper: GripperCandidate | None, axis: list[float], mesh_paths: dict[str, str] | None = None
) -> tuple[float, str]:
    """ee_body 原点から把持点までの距離。衝突メッシュから指先を求め、読めなければ指の付け根 + 4cm の目安。"""
    if gripper is None:
        return 0.0, "default"
    tip = fingertip_extent(model, ee_body, gripper, axis, mesh_paths or {})
    if tip is not None:
        return round(tip - PAD_FROM_TIP, 4), "collision_mesh"
    best = 0.0
    for name in gripper.joints:
        child = model.joints[name].child
        for link in subtree_links(model, child):
            p = relative_transform(model, ee_body, link).pos
            best = max(best, sum(a * b for a, b in zip(p, axis)))
    return round(best + 0.04, 4), "heuristic"


# --------------------------------------------------------------------------- export


def link_infos(model: UrdfModel) -> list[LinkInfo]:
    return [
        LinkInfo(name=l.name, mass=l.mass, rigid=l.has_inertial, n_meshes=len(l.meshes))
        for l in model.links.values()
    ]


def joint_infos(model: UrdfModel) -> list[JointInfo]:
    out = []
    for j in model.joints.values():
        out.append(
            JointInfo(
                name=j.name,
                type=j.type,
                parent=j.parent,
                child=j.child,
                lower=j.lower,
                upper=j.upper,
                effort=j.effort,
                velocity=j.velocity,
                mimic_of=j.mimic.joint if j.mimic else None,
                mimic_multiplier=j.mimic.multiplier if j.mimic else 1.0,
                mimic_offset=j.mimic.offset if j.mimic else 0.0,
            )
        )
    return out
