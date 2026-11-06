"""inspect_robot / configure_robot（ホストで実行。Isaac Sim 不要）。"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from urdf2vla.project import Project, file_hash
from urdf2vla.registry import Arg, skill
from urdf2vla.result import Inferred, Question, SkillResult
from urdf2vla.runtime.config import load_runtime
from urdf2vla.schema.robot import (
    ActuatorGroup,
    ArmConfig,
    AssetConfig,
    GripperConfig,
    JointRoles,
    Pose,
    RobotConfig,
    RobotMetadata,
)
from urdf2vla.grippers import get_gripper
from urdf2vla.urdf import analysis as A
from urdf2vla.urdf.kinematics import Transform
from urdf2vla.urdf.normalize import mesh_report, write_normalized_urdf
from urdf2vla.urdf.parser import UrdfError, UrdfModel, parse_urdf

# 既定の駆動ゲイン（重力を切った位置制御。Isaac Lab の Franka IK 用設定と同等）
ARM_GAINS = (400.0, 80.0)
GRIPPER_GAINS = {"prismatic": (2000.0, 100.0), "revolute": (200.0, 20.0)}
HOLD_GAINS = (1.0e4, 1.0e3)
PASSIVE_GAINS = (0.0, 10.0)


# --------------------------------------------------------------------------- shared helpers


def _robot_input(project: Project) -> dict[str, Any]:
    robot = project.config().get("robot") or {}
    if not robot.get("urdf"):
        raise UrdfError("project.yaml の robot.urdf に URDF のパスを書いてください。")
    return robot


def _load(project: Project) -> tuple[dict[str, Any], UrdfModel]:
    robot = _robot_input(project)
    return robot, parse_urdf(project.path(robot["urdf"]))


def _hint(robot_in: dict, answers: dict, key: str) -> Any:
    """回答（answers.yaml）> project.yaml の robot ヒントの順で値を引く。"""
    if key in answers:
        return answers[key]
    if key == "robot.front_axis":
        return robot_in.get("front_axis")
    if key == "arms.0.ee_link" and robot_in.get("end_effector"):
        ee = robot_in["end_effector"]
        return ee[0] if isinstance(ee, list) else ee
    if key.startswith("arms.") and key.endswith(".ee_link") and isinstance(robot_in.get("end_effector"), list):
        idx = int(key.split(".")[1])
        ee = robot_in["end_effector"]
        return ee[idx] if idx < len(ee) else None
    if key.startswith("grippers.") and key.endswith(".open_position"):
        idx = int(key.split(".")[1])
        opens = robot_in.get("gripper_open_position")
        if isinstance(opens, list):
            return opens[idx] if idx < len(opens) else None
        return opens
    if key.startswith("grippers.") and key.endswith(".open_close"):
        idx = int(key.split(".")[1])
        values = robot_in.get("gripper_open_close")
        if values is None:
            return None
        if isinstance(values, list) and len(values) == 2 and all(isinstance(v, (int, float)) for v in values):
            return values if idx == 0 else None
        if isinstance(values, list):
            return values[idx] if idx < len(values) else None
        if isinstance(values, dict):
            return values.get(str(idx))
        return None
    if key.startswith("joints.") and key.endswith(".role"):
        return (robot_in.get("joint_roles") or {}).get(key.split(".")[1])
    return None


def _control_joints(robot_in: dict, arm: str, arm_count: int) -> list[str] | None:
    """project.yaml の robot.control_joints。単腕ならリスト、双腕なら arm 名をキーにした dict。"""
    override = robot_in.get("control_joints")
    if isinstance(override, dict):
        value = override.get(arm)
        if isinstance(value, str):
            value = [value]
        return list(value) if value else None
    if isinstance(override, list) and arm_count == 1:
        return list(override)
    return None


def analyze(project: Project, model: UrdfModel) -> RobotMetadata:
    robot_in = _robot_input(project)
    runtime = load_runtime()
    root = A.root_link(model)
    art_root, dummy = A.articulation_root(model, root)
    # gripper 関節: 回答 > project.yaml > 自動検出
    forced = project.answers().get("robot.gripper_joints") or robot_in.get("gripper_joints")
    if isinstance(forced, str):
        forced = [forced]
    grippers = A.find_grippers(model, art_root, forced=forced or None)
    arms, others = A.find_arms(model, art_root, grippers)
    package_paths = [project.path(p) for p in robot_in.get("package_paths", [])]
    return RobotMetadata(
        robot_name=robot_in.get("name") or project.name,
        urdf_path=str(model.path),
        root_link=root,
        articulation_root=art_root,
        dummy_root=dummy,
        links=A.link_infos(model),
        joints=A.joint_infos(model),
        movable_joints=[j.name for j in model.movable_joints],
        arms=arms,
        grippers=grippers,
        other_joints=others,
        meshes=mesh_report(model, runtime, package_paths),
    )


def model_warnings(meta: RobotMetadata, model: UrdfModel) -> list[str]:
    """URDF の値が仮値・未設定に見えるときの注意（ロボットの事実なので自動では直さない）。"""
    out = []
    arm_joints = [model.joints[n] for a in meta.arms for n in a.joints]
    unlimited = [j.name for j in arm_joints if j.type == "continuous" or j.lower is None or j.upper is None]
    if unlimited:
        out.append(
            f"arm の関節 {len(unlimited)} 個に可動範囲（limit）がありません（{', '.join(unlimited[:4])}{' ...' if len(unlimited) > 4 else ''}）。"
            "シミュレーションでは関節が何回転でも回れるので、実機と違う姿勢を IK が選ぶことがあります。可動範囲が分かれば URDF に書くことを推奨します。"
        )
    efforts = {j.effort for j in model.movable_joints if j.effort is not None}
    if len(model.movable_joints) > 3 and len(efforts) == 1:
        e = next(iter(efforts))
        out.append(
            f"すべての関節のトルク上限が同じ値（{e}）です。仮の値かもしれません。"
            + ("この値ではシミュレーションで腕が指令に追従できないことがあります。" if e < 5 else "")
            + "実際の値が分かれば project.yaml の overrides.robot.actuators で設定できます。"
        )
    return out


def simready_key(robot_in: dict) -> str | None:
    """project.yaml の robot.gripper.simready（SimReady アセットのグリッパに付け替える）。"""
    return (robot_in.get("gripper") or {}).get("simready")


def robot_questions(meta: RobotMetadata, model: UrdfModel, robot_in: dict, answers: dict) -> tuple[list[Question], list[Inferred]]:
    """重要設定のうち、まだ決まっていないものを質問として返す。推測で埋めない。
    SimReady のグリッパに付け替えるときは、URDF のグリッパと End Effector についての質問はしない（付け替えるので不要）。"""
    questions, inferred = _robot_questions(meta, model, robot_in, answers)
    if simready_key(robot_in):

        def gripper_related(key: str) -> bool:
            return key.startswith("grippers.") or key == "robot.gripper_joints" or re.fullmatch(r"arms\.\d+\.ee_link", key) is not None

        questions = [q for q in questions if not gripper_related(q.key)]
        inferred = [i for i in inferred if not gripper_related(i.key)]
    return questions, inferred


def _robot_questions(meta: RobotMetadata, model: UrdfModel, robot_in: dict, answers: dict) -> tuple[list[Question], list[Inferred]]:
    questions: list[Question] = []
    inferred: list[Inferred] = [Inferred(key="root_link", value=meta.root_link, evidence="親を持たない唯一の link")]
    if meta.dummy_root:
        inferred.append(
            Inferred(
                key="articulation_root",
                value=meta.articulation_root,
                evidence=f"root link '{meta.root_link}' は質量も形状もない空 link なので、その子を articulation root にします",
            )
        )

    if not meta.arms:
        questions.append(
            Question(
                key="robot.control_joints",
                prompt="制御する arm の関節が見つかりませんでした。project.yaml の robot.control_joints に関節名を書いてください。",
                candidates=meta.movable_joints,
            )
        )
        return questions, inferred

    # 正面方向（台をどちらに置くか）。+x 以外ならロボットを回して置くので確認する
    if not robot_in.get("base_pose") and _hint(robot_in, answers, "robot.front_axis") is None:
        front, why = A.front_axis_guess(model, meta.articulation_root, meta.arms)
        if front == "+x":
            inferred.append(Inferred(key="robot.front_axis", value=front, evidence=why))
        else:
            questions.append(
                Question(
                    key="robot.front_axis",
                    prompt=f"ロボットの正面（作業台を置く側）はどの方向ですか？ URDF の座標で答えてください（推定: {front}）。",
                    candidates=["+x", "+y", "-x", "-y"],
                    reason=f"{why}。正面が +x でないロボットは、シミュレーションでは正面が +x を向くように回して置きます。",
                    suggestion=front,
                )
            )

    if not meta.grippers:
        # 片側の顎だけが動く直列グリッパは、arm の最後の関節として見えることが多い（SO-100 系など）。
        # どれがグリッパかで arm の関節と End Effector の候補が変わるので、先にこれだけ聞く
        last = [a.joints[-1] for a in meta.arms if a.joints]
        questions.append(
            Question(
                key="robot.gripper_joints",
                prompt=(
                    "グリッパ（指）の関節が自動では見つかりませんでした。指を動かす関節はどれですか？"
                    "片側の顎だけが動くグリッパなら、各 arm の最後の関節（候補）であることが多いです。"
                ),
                candidates=last,
                reason="関節名・mimic・同じ親を持つ指の組のどれにも当てはまりませんでした。"
                "回答はリスト（例: [joint7_R, joint7_L]）で、双腕なら両手分を答えてください。",
                suggestion=last or None,
            )
        )
        for i, arm in enumerate(meta.arms):
            inferred.append(Inferred(key=f"arms.{i}.joints", value=arm.joints, evidence=f"root から '{arm.tip_link}' までの可動関節（グリッパ確定前）"))
        return questions, inferred

    for i, arm in enumerate(meta.arms):
        inferred.append(
            Inferred(key=f"arms.{i}.joints", value=arm.joints, evidence=f"root から '{arm.tip_link}' までの可動関節")
        )
        key = f"arms.{i}.ee_link"
        chosen = _hint(robot_in, answers, key)
        valid = [c.link for c in arm.ee_candidates]
        if chosen is not None:
            if chosen not in model.links:
                questions.append(
                    Question(key=key, prompt=f"指定された End Effector '{chosen}' は URDF にありません。どれを使いますか？", candidates=valid)
                )
            continue
        strong = A.strong_ee(arm.ee_candidates)
        if len(strong) == 1:
            inferred.append(
                Inferred(key=key, value=strong[0].link, evidence="、".join(strong[0].reasons) + "（候補が 1 つだけ）")
            )
        else:
            cands = strong or arm.ee_candidates
            reasons = "; ".join(f"{c.link}: {'、'.join(c.reasons) or '根拠なし'}" for c in cands)
            questions.append(
                Question(
                    key=key,
                    prompt=f"arm '{arm.name}' の End Effector を一意に決められません。候補は {', '.join(c.link for c in cands)} です。どれを使いますか？",
                    candidates=[c.link for c in cands],
                    reason=reasons,
                )
            )

    for g_idx, g in enumerate(meta.grippers):
        inferred.append(
            Inferred(
                key=f"grippers.{g_idx}.joints",
                value=g.joints,
                evidence=f"'{g.mount_link}' に付く指関節（leader: {g.leader}"
                + (f", mimic: {', '.join(g.mimic)}" if g.mimic else "")
                + "）",
            )
        )
        leader = model.joints[g.leader]
        if leader.lower is None or leader.upper is None:
            # 可動範囲がない（continuous など）と、開閉の値を限界から決められない
            key = f"grippers.{g_idx}.open_close"
            if _hint(robot_in, answers, key) is None:
                questions.append(
                    Question(
                        key=key,
                        prompt=(
                            f"グリッパ '{g.mount_link}' の関節 {leader.name} には可動範囲がないので、開閉の角度を決められません。"
                            "指を開いたときと閉じたときの関節角 [rad] を [開, 閉] で答えてください。"
                        ),
                        reason="URDF の limit が無い（continuous）関節です。実機のグリッパの設定値などを参照してください。",
                    )
                )
            continue
        key = f"grippers.{g_idx}.open_position"
        if _hint(robot_in, answers, key) is not None:
            continue
        if leader.type == "prismatic":
            inferred.append(
                Inferred(
                    key=key,
                    value="upper",
                    evidence=f"直動の指関節は値が大きいほど指が開く（{leader.name}: 開={leader.upper}, 閉={leader.lower}）",
                )
            )
        else:
            questions.append(
                Question(
                    key=key,
                    prompt=(
                        f"グリッパ '{g.mount_link}' の leader 関節 {leader.name}（{leader.type}）は、"
                        f"どちらの限界で指が開きますか？ lower={leader.lower} / upper={leader.upper}"
                    ),
                    candidates=["lower", "upper"],
                    reason="回転関節の指は URDF からは開閉の向きが分かりません",
                )
            )

    for name in meta.other_joints:
        key = f"joints.{name}.role"
        if _hint(robot_in, answers, key) is not None:
            continue
        j = model.joints[name]
        questions.append(
            Question(
                key=key,
                prompt=f"関節 {name}（{j.type}）は arm にも gripper にも属しません。どう扱いますか？",
                candidates=["fixed_at_reset", "passive"],
                reason="fixed_at_reset = reset 時の姿勢で保持（昇降・首など）, passive = 駆動しない（車輪など）",
                suggestion="passive" if j.type == "continuous" else "fixed_at_reset",
            )
        )
    return questions, inferred


# --------------------------------------------------------------------------- inspect_robot


@skill(
    "inspect_robot",
    phase=1,
    runs_in="host",
    summary="URDF を解析し、link/joint 一覧・root・arm・gripper・End Effector 候補・メッシュの解決状況を出す",
    inputs=["project.yaml: robot.urdf"],
    outputs=["generated/robot_metadata.yaml"],
    passable_on_needs_input=True,
)
def inspect_robot(project: Project, opts: dict, result: SkillResult, log) -> None:
    robot_in, model = _load(project)
    meta = analyze(project, model)
    path = project.write_generated("robot_metadata.yaml", meta.model_dump(mode="json"))
    result.outputs["robot_metadata"] = project.rel(path)
    log.info("parsed %s: %d links, %d joints (%d movable)", model.path, len(model.links), len(model.joints), len(meta.movable_joints))

    questions, inferred = robot_questions(meta, model, robot_in, project.answers())
    result.inferred += inferred
    for q in questions:
        result.ask(q)
    result.warnings += model_warnings(meta, model)

    result.data = {
        "robot_name": meta.robot_name,
        "links": len(meta.links),
        "joints": len(meta.joints),
        "movable_joints": meta.movable_joints,
        "arms": [{"name": a.name, "joints": a.joints, "ee_candidates": [c.link for c in a.ee_candidates]} for a in meta.arms],
        "grippers": [{"mount_link": g.mount_link, "joints": g.joints, "leader": g.leader, "mimic": list(g.mimic)} for g in meta.grippers],
        "other_joints": meta.other_joints,
        "meshes": {"total": meta.meshes.total, "resolved": len(meta.meshes.resolved), "unresolved": meta.meshes.unresolved},
    }
    if meta.meshes.unresolved:
        result.fail(
            f"メッシュ {len(meta.meshes.unresolved)} 件の場所が分かりません（例: {meta.meshes.unresolved[0]}）。"
            "ROS パッケージの場所を project.yaml の robot.package_paths に追加してください。"
        )
    if result.status != "error":
        result.next = ["configure_robot"]


# --------------------------------------------------------------------------- configure_robot


def _default_pose_hint(project: Project, robot_in: dict, answers: dict) -> dict:
    """初期姿勢: 回答（"suggested" = import_robot が IK で求めた姿勢 / 関節名→値の dict）> project.yaml。"""
    import yaml

    ans = answers.get("robot.default_pose")
    if ans == "suggested":
        report = project.build / "import_report.yaml"
        if report.exists():
            suggested = (yaml.safe_load(report.read_text()) or {}).get("suggested_default_pose") or {}
            if suggested.get("joint_pos"):
                pose = dict(suggested["joint_pos"])
                # 採用した姿勢を回答として固定する（次の import_robot はもう提案しないので、レポートに頼らない）
                project.set_answers({"robot.default_pose": pose})
                return pose
        raise ValueError("推奨初期姿勢がまだありません。import_robot を先に実行してください。")
    if isinstance(ans, dict):
        return ans
    return robot_in.get("default_pose") or {}


def _perpendicular(a: tuple) -> tuple:
    from urdf2vla.urdf.kinematics import cross, normalize

    return tuple(normalize(cross(a, (1.0, 0.0, 0.0) if abs(a[0]) < 0.9 else (0.0, 1.0, 0.0))))


def _rotation_between(a_s: tuple, f_s: tuple, a_u: tuple, f_u: tuple):
    """座標系 s の (接近方向 a_s, 指の方向 f_s) を、座標系 u の (a_u, f_u) に重ねる回転（u ← s の四元数 xyzw）。"""
    from urdf2vla.urdf.kinematics import cross, normalize, quat_from_matrix

    def frame(a, f):
        a = normalize(a)
        f = normalize(tuple(fc - sum(x * y for x, y in zip(f, a)) * ac for fc, ac in zip(f, a)))
        return a, f, cross(a, f)

    s_cols, u_cols = frame(a_s, f_s), frame(a_u, f_u)
    # R = U · Sᵀ（列ベクトルが各基底）
    m = [[sum(u_cols[k][r] * s_cols[k][c] for k in range(3)) for c in range(3)] for r in range(3)]
    return quat_from_matrix(m)


def _clip(model: UrdfModel, joint: str, value: float) -> float:
    j = model.joints[joint]
    if j.lower is not None:
        value = max(value, j.lower)
    if j.upper is not None:
        value = min(value, j.upper)
    return round(value, 6)


def _gripper_config(model: UrdfModel, g, idx: int, arm_name: str, side: str, open_close: list | None = None) -> GripperConfig:
    leader = model.joints[g.leader]
    lo, hi = leader.lower or 0.0, leader.upper or 0.0
    open_v, close_v = (hi, lo) if side == "upper" else (lo, hi)
    if open_close is not None:  # 可動範囲のない関節: ユーザが答えた値
        open_v, close_v = float(open_close[0]), float(open_close[1])
    opened, closed = {}, {}
    for name in g.joints:
        if name == g.leader:
            o, c = open_v, close_v
        elif name in g.mimic:
            mult, off = g.mimic[name]
            o, c = mult * open_v + off, mult * close_v + off
        elif open_close is not None:
            o, c = open_v, close_v
        else:  # mimic なしの指は leader と同じ側の限界を使う
            j = model.joints[name]
            jlo, jhi = j.lower or 0.0, j.upper or 0.0
            o, c = (jhi, jlo) if side == "upper" else (jlo, jhi)
        opened[name] = _clip(model, name, o)
        closed[name] = _clip(model, name, c)
    return GripperConfig(
        name=f"{arm_name}_gripper",
        arm=arm_name,
        joints=list(g.joints),
        leader=g.leader,
        mimic=g.mimic,
        open=opened,
        close=closed,
    )


@skill(
    "configure_robot",
    phase=1,
    runs_in="host",
    summary="Robot metadata と回答から Isaac Sim / VLA 用の Robot configuration を作り、正規化 URDF を書き出す",
    inputs=["generated/robot_metadata.yaml", "answers.yaml", "project.yaml: robot.*"],
    outputs=["generated/robot_config.yaml", "build/robot.urdf"],
    requires=["inspect_robot"],
)
def configure_robot(project: Project, opts: dict, result: SkillResult, log) -> None:
    robot_in, model = _load(project)
    meta = analyze(project, model)
    answers = project.answers()
    questions, inferred = robot_questions(meta, model, robot_in, answers)
    if questions:
        for q in questions:
            result.ask(q)
        result.next = ["configure_robot"]
        return
    if meta.meshes.unresolved:
        result.fail("メッシュが未解決です。inspect_robot のエラーを先に解消してください。")
        return

    runtime = load_runtime()
    prov: dict[str, str] = {}
    inferred_keys = {i.key for i in inferred}

    def source(key: str) -> str:
        if key in answers:
            return "user"
        if _hint(robot_in, {}, key) is not None:
            return "user"
        return "inferred" if key in inferred_keys else "default"

    # ---- grippers
    grippers: list[GripperConfig] = []
    for g_idx, g in enumerate(meta.grippers):
        key = f"grippers.{g_idx}.open_position"
        side = _hint(robot_in, answers, key) or "upper"
        prov[key] = source(key)
        oc = _hint(robot_in, answers, f"grippers.{g_idx}.open_close")
        if oc is not None:
            prov[f"grippers.{g_idx}.open_close"] = "user"
        owner = next((c.name for c in meta.arms if c.gripper == g_idx), meta.arms[0].name)
        grippers.append(_gripper_config(model, g, g_idx, owner, side, oc))

    # ---- SimReady のグリッパに付け替える（URDF のグリッパの link を外し、同じ取付位置・向きに付ける）
    sr_key = simready_key(robot_in)
    sr = None
    sr_mount_link = None
    drop_links: set[str] = set()
    if sr_key:
        try:
            sr = get_gripper(sr_key)
        except ValueError as e:
            result.fail(str(e))
            return
        if len(meta.arms) != 1:
            result.fail("robot.gripper.simready は単腕のロボットだけで使えます。")
            return
        cand = meta.arms[0]
        ug = meta.grippers[cand.gripper] if cand.gripper is not None else None
        hint_mount = (robot_in.get("gripper") or {}).get("mount_link")
        a_s, f_s = tuple(sr.approach_axis), tuple(sr.finger_axis)
        if ug is not None:
            gb = ug.mount_link  # URDF のグリッパの付け根の link
            sr_mount_link = hint_mount or model.parent_joint(gb).parent
            T_m_gb = A.relative_transform(model, sr_mount_link, gb)
            a_u = tuple(A.approach_axis(model, gb, ug, meta.meshes.resolved)[0])
            f_u = tuple(A.finger_axis(model, gb, ug, list(a_u), meta.meshes.resolved) or _perpendicular(a_u))
            drop_links = set(A.subtree_links(model, gb))
            prov["grippers.0.mount"] = "inferred"
        else:
            if not hint_mount:
                result.fail("URDF にグリッパがないので、robot.gripper.mount_link（グリッパを取り付ける link）を指定してください。")
                return
            sr_mount_link = hint_mount
            T_m_gb, a_u, f_u = Transform(), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)
            prov["grippers.0.mount"] = "default"
            result.warnings.append(f"{sr.title} を '{sr_mount_link}' の +z 方向に、指が x 方向に開く向きで取り付けます。")
        mount = T_m_gb @ Transform((0.0, 0.0, 0.0), _rotation_between(a_s, f_s, a_u, f_u))
        grippers = [
            GripperConfig(
                name=f"{cand.name}_gripper",
                arm=cand.name,
                joints=[sr.driven],
                leader=sr.driven,
                open={sr.driven: sr.open},
                close={sr.driven: sr.close},
                simready=sr.key,
                mount_link=sr_mount_link,
                mount=Pose(**mount.rounded()),
            )
        ]
        prov["grippers.0"] = "user"

    # ---- arms
    arms: list[ArmConfig] = []
    for i, cand in enumerate(meta.arms):
        joints = cand.joints
        control_override = _control_joints(robot_in, cand.name, len(meta.arms))
        if control_override:
            bad = [n for n in control_override if n not in model.joints or not model.joints[n].movable]
            if bad:
                result.fail(f"robot.control_joints に可動関節でない名前があります: {bad}")
                return
            joints = control_override
            prov[f"arms.{i}.joints"] = "user"
        else:
            prov[f"arms.{i}.joints"] = "inferred"
        ee_key = f"arms.{i}.ee_link"
        if sr is not None:
            # SimReady のグリッパ: End Effector は取付 link。TCP・向きはアセットで測った値から
            ee = sr_mount_link
            body = A.rigid_ancestor(model, ee)
            ee_offset = A.relative_transform(model, body, ee) if body != ee else Transform()
            g_in_body = ee_offset @ Transform(tuple(grippers[0].mount.pos), tuple(grippers[0].mount.rot_xyzw))
            tcp_g = tuple(c * (sr.fingertip - A.PAD_FROM_TIP) for c in sr.approach_axis)
            rot_g = Transform((0, 0, 0), g_in_body.rot)
            prov[ee_key] = "inferred"
            prov[f"arms.{i}.approach_axis"] = prov[f"arms.{i}.tcp_offset"] = "simready"
            arms.append(
                ArmConfig(
                    name=cand.name,
                    joints=joints,
                    ee_link=ee,
                    ik_body=body,
                    ee_offset=Pose(**ee_offset.rounded()),
                    tcp_offset=Pose(**(g_in_body @ Transform(tcp_g)).rounded()),
                    approach_axis=[round(c, 6) for c in rot_g.apply(tuple(sr.approach_axis))],
                    finger_axis=[round(c, 6) for c in rot_g.apply(tuple(sr.finger_axis))],
                )
            )
            continue
        ee = _hint(robot_in, answers, ee_key) or A.strong_ee(cand.ee_candidates)[0].link
        prov[ee_key] = source(ee_key)
        body = A.rigid_ancestor(model, ee)
        ee_offset = A.relative_transform(model, body, ee) if body != ee else Transform()
        gcand = meta.grippers[cand.gripper] if cand.gripper is not None else None
        axis_ee, axis_src = A.approach_axis(model, ee, gcand, meta.meshes.resolved)
        gconf = next((g for g in grippers if g.arm == cand.name), None)
        tcp_ee, tcp_src = A.tcp_point(model, ee, gcand, axis_ee, meta.meshes.resolved, gconf.open if gconf else None)
        dist = round(math.sqrt(sum(c * c for c in tcp_ee)), 4)
        tcp = ee_offset @ Transform(tuple(tcp_ee))
        rot_body = Transform((0, 0, 0), ee_offset.rot)
        axis_body = [round(c, 6) for c in rot_body.apply(tuple(axis_ee))]
        fing_ee = A.finger_axis(model, ee, gcand, axis_ee, meta.meshes.resolved)
        fing_body = [round(c, 6) for c in rot_body.apply(tuple(fing_ee))] if fing_ee else None
        prov[f"arms.{i}.approach_axis"] = axis_src
        prov[f"arms.{i}.tcp_offset"] = "inferred" if tcp_src == "collision_mesh" else "default"
        if tcp_src == "heuristic":
            result.warnings.append(f"arm '{cand.name}' の指の衝突形状が読めなかったため、把持点（TCP）は目安の値です（{dist} m）。")
        arms.append(
            ArmConfig(
                name=cand.name,
                joints=joints,
                ee_link=ee,
                ik_body=body,
                ee_offset=Pose(**ee_offset.rounded()),
                tcp_offset=Pose(**tcp.rounded()),
                approach_axis=axis_body,
                finger_axis=fing_body,
            )
        )
        if body != ee:
            result.warnings.append(
                f"End Effector '{ee}' は質量のない frame なので、剛体 '{body}' に固定オフセット付きで取り付けます。"
            )

    # ---- joint roles
    roles = JointRoles()
    for name in meta.other_joints:
        role = _hint(robot_in, answers, f"joints.{name}.role")
        (roles.passive if role == "passive" else roles.fixed_at_reset).append(name)
        prov[f"joints.{name}.role"] = "user"

    # ---- default pose
    hint_pose: dict = _default_pose_hint(project, robot_in, answers)
    unknown = [n for n in hint_pose if n not in model.joints]
    if unknown:
        result.fail(f"robot.default_pose に存在しない関節があります: {unknown}")
        return
    default_pose: dict[str, float] = {}
    midpoint_used = []
    for j in model.movable_joints:
        if j.name in roles.passive or j.child in drop_links:
            continue
        if j.name in hint_pose:
            default_pose[j.name] = _clip(model, j.name, float(hint_pose[j.name]))
            continue
        gripper = next((g for g in grippers if j.name in g.joints), None)
        if gripper:
            default_pose[j.name] = gripper.open[j.name]
        elif any(j.name in a.joints for a in arms):
            # 可動範囲があれば中央、なければ 0（どちらも仮の姿勢なので警告する）
            default_pose[j.name] = round((j.lower + j.upper) / 2, 6) if j.lower is not None and j.upper is not None else 0.0
            midpoint_used.append(j.name)
        else:
            default_pose[j.name] = _clip(model, j.name, 0.0)
    if answers.get("robot.default_pose") == "suggested":
        prov["default_pose"] = "inferred"
    else:
        prov["default_pose"] = "user" if hint_pose and not midpoint_used else ("default" if midpoint_used else "inferred")
    if midpoint_used:
        result.warnings.append(
            "初期姿勢が指定されていないため、arm 関節は可動範囲の中央（範囲がなければ 0）にしました: "
            + ", ".join(midpoint_used)
            + "。作業しやすい姿勢を project.yaml の robot.default_pose に書くことを推奨します。"
        )

    # ---- actuators
    actuators: list[ActuatorGroup] = []

    if sr is not None:
        default_pose[sr.driven] = sr.open

    def _limit(name: str, attr: str) -> float | None:
        if name not in model.joints:  # SimReady のグリッパの関節（URDF にない）
            return None
        j = model.joints[name]
        v = getattr(j, attr)
        if not v and j.mimic and j.mimic.joint in model.joints:  # mimic の follower は leader の値を使う
            v = getattr(model.joints[j.mimic.joint], attr)
        return v or None

    # project.yaml の robot.effort_limits（関節名の正規表現 → 値）は URDF の値より優先する（URDF が仮値のとき用）
    effort_hint = robot_in.get("effort_limits") or {}

    def efforts(names):
        out = {}
        for n in names:
            hit = next((float(v) for k, v in effort_hint.items() if re.fullmatch(k, n)), None)
            v = hit if hit is not None else _limit(n, "effort")
            if v:
                out[n] = v
        return out

    def velocities(names):
        return {n: _limit(n, "velocity") for n in names if _limit(n, "velocity")}

    for arm in arms:
        actuators.append(ActuatorGroup(name=f"{arm.name}_arm", joints=arm.joints, stiffness=ARM_GAINS[0], damping=ARM_GAINS[1], effort_limit=efforts(arm.joints), velocity_limit=velocities(arm.joints)))
    for g in grippers:
        if g.simready:
            # 駆動するのは 1 関節。追従する関節は PhysX の mimic 拘束で動くのでゲイン 0（減衰も 0: 拘束と競合させない）
            actuators.append(ActuatorGroup(name=g.name, joints=g.joints, stiffness=sr.stiffness, damping=sr.damping, effort_limit={sr.driven: sr.effort}))
            actuators.append(ActuatorGroup(name=f"{g.name}_mimic", joints=list(sr.followers), stiffness=0.0, damping=0.0))
            continue
        jt = model.joints[g.leader].type
        st, dp = GRIPPER_GAINS.get(jt, GRIPPER_GAINS["revolute"])
        # mimic の follower も含めて全関節を駆動する（mimic の拘束は import_robot で無効にする）
        actuators.append(ActuatorGroup(name=g.name, joints=g.joints, stiffness=st, damping=dp, effort_limit=efforts(g.joints), velocity_limit=velocities(g.joints)))
    if roles.fixed_at_reset:
        actuators.append(ActuatorGroup(name="hold", joints=roles.fixed_at_reset, stiffness=HOLD_GAINS[0], damping=HOLD_GAINS[1], effort_limit=efforts(roles.fixed_at_reset), velocity_limit=velocities(roles.fixed_at_reset)))
    if roles.passive:
        actuators.append(ActuatorGroup(name="passive", joints=roles.passive, stiffness=PASSIVE_GAINS[0], damping=PASSIVE_GAINS[1]))
    prov["actuators"] = "user" if effort_hint else "default"

    # ---- base pose（空の root link を外した場合は、その固定関節の変換を base pose に移す）
    if robot_in.get("base_pose"):
        base_pose = Pose(**robot_in["base_pose"])
        prov["base_pose"] = "user"
    else:
        base = Transform()
        prov["base_pose"] = "default"
        if meta.dummy_root:
            j = model.parent_joint(meta.articulation_root)
            base = Transform.from_xyz_rpy(j.xyz, j.rpy)
            prov["base_pose"] = "urdf"
        # 正面を world の +x に向ける（台・カメラ・teleop は +x が正面の前提）
        front = _hint(robot_in, answers, "robot.front_axis") or "+x"
        yaw = {"+x": 0.0, "+y": -math.pi / 2, "-x": math.pi, "-y": math.pi / 2}[front]
        if yaw:
            base = Transform.from_xyz_rpy((0, 0, 0), (0, 0, yaw)) @ base
            prov["base_pose"] = "user"
        base_pose = Pose(**base.rounded())

    # ---- normalized URDF
    out_urdf = project.build / "robot.urdf"
    write_normalized_urdf(model, out_urdf, runtime, meta.meshes, drop_root=meta.root_link if meta.dummy_root else None, drop_links=drop_links)
    asset = AssetConfig(
        source_path=str(model.path),
        path=runtime.to_runtime(out_urdf),
        host_path=str(out_urdf),
    )
    if sr is not None:
        asset.solver_position_iterations = max(asset.solver_position_iterations, sr.solver_position_iterations)
        prov["asset.solver_position_iterations"] = "simready"

    config = RobotConfig(
        robot_name=meta.robot_name,
        asset=asset,
        root_link=meta.articulation_root,
        base_pose=base_pose,
        arms=arms,
        grippers=grippers,
        joint_roles=roles,
        default_pose=default_pose,
        joint_limits={
            **{j.name: [j.lower, j.upper] for j in model.movable_joints if j.child not in drop_links},
            **({sr.driven: [sr.lower, sr.upper]} if sr is not None else {}),
        },
        actuators=actuators,
        provenance=prov,
    )
    path = project.write_generated("robot_config.yaml", config.model_dump(mode="json"), section="robot")
    RobotConfig.model_validate(project.read_generated("robot_config.yaml"))  # overrides 適用後も妥当か
    result.outputs["robot_config"] = project.rel(path)
    result.outputs["urdf"] = project.rel(out_urdf)
    result.inferred += inferred
    result.data = {
        "arms": [{"name": a.name, "joints": a.joints, "ee_link": a.ee_link, "ik_body": a.ik_body, "tcp_offset": a.tcp_offset.pos} for a in arms],
        "grippers": [{"name": g.name, "open": g.open, "close": g.close} for g in grippers],
        "default_pose_source": prov["default_pose"],
        "inputs_hash": file_hash(Path(model.path), project.root / "answers.yaml", project.root / "project.yaml"),
    }
    log.info("robot_config written: %s", path)
    result.next = ["configure_task"]
