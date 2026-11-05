"""RobotConfig → USD 変換と Isaac Lab の ArticulationCfg（Isaac Sim 内でのみ import）。"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from urdf2vla.schema.robot import RobotConfig


def _expr(name: str) -> str:
    """Isaac Lab の関節指定は正規表現なので、名前をそのまま一致させる。"""
    return re.escape(name)


COLLISION_TYPE = {"convex_hull": "Convex Hull", "convex_decomposition": "Convex Decomposition"}


def convert_urdf(cfg: RobotConfig, usd_dir: Path, force: bool = False) -> str:
    """URDF → USD（Isaac Lab 3.0 の URDF importer）。mimic は拘束（NewtonMimicAPI）として残るので、
    続けて disable_mimic_constraints を呼ぶ。"""
    from isaaclab.sim.converters import UrdfConverter, UrdfConverterCfg

    drive = UrdfConverterCfg.JointDriveCfg(
        drive_type="force",
        target_type="position",
        gains=UrdfConverterCfg.JointDriveCfg.PDGainsCfg(stiffness=100.0, damping=1.0),  # actuators で上書きされる
    )
    conv_cfg = UrdfConverterCfg(
        asset_path=cfg.asset.path,
        usd_dir=str(usd_dir),  # 固定ディレクトリにして hash キャッシュを効かせる
        usd_file_name="robot.usd",
        force_usd_conversion=force,
        make_instanceable=True,
        fix_base=cfg.asset.fix_base,
        merge_fixed_joints=False,  # True だと End Effector（panda_hand など）が剛体でなくなる
        self_collision=cfg.asset.self_collision,
        collision_type=COLLISION_TYPE[cfg.asset.collider],
        joint_drive=drive,
    )
    usd_path = UrdfConverter(conv_cfg).usd_path
    # importer は出力先が既にあると robot_1/ のように別名で作るので、古い出力を消しておく
    out_dir = Path(usd_path).parent
    for d in usd_dir.iterdir():
        if d.is_dir() and d != out_dir:
            shutil.rmtree(d)
    return usd_path


def disable_mimic_constraints(usd_path: str) -> list[str]:
    """URDF importer が付けた mimic の拘束を無効にする（mimic の関節は gripper spec で全関節を駆動する）。

    Isaac Lab 3.0 の importer は URDF の mimic を NewtonMimicAPI（newton:mimicEnabled）で書く。PhysX では
    multiplier -1 の回転関節の組（CRX の Robotiq 2F-85 の左右の knuckle）で拘束が発散し、leader が可動範囲を
    越えて回り続ける（Franka の +1 の直動指では効く）。ロボットによって壊れ方が変わるものに頼らず、
    Isaac Lab の Franka の設定と同じく全関節を駆動する。ルートレイヤに上書きを書くので冪等。
    """
    from pxr import Usd

    stage = Usd.Stage.Open(usd_path)
    disabled = []
    for prim in stage.Traverse():
        attr = prim.GetAttribute("newton:mimicEnabled")
        if attr and attr.IsValid() and attr.Get():
            attr.Set(False)
            disabled.append(prim.GetName())
    if disabled:
        stage.GetRootLayer().Save()
    return disabled


def attach_simready_gripper(usd_path: str, g) -> bool:
    """SimReady アセットのグリッパ（g.simready）を、Isaac Sim の Robot Assembler で腕の g.mount_link に取り付ける。

    結果はロボットの USD の variant（Gripper = <キー>）として保存される（payloads/Gripper/<キー>.usd）。
    すでに取り付け済みなら何もしない（False を返す）。Kit の起動後に呼ぶこと。
    """
    import omni.kit.app
    import omni.usd
    from pxr import Gf, Usd, UsdGeom, UsdPhysics

    from isaaclab.sim.utils import enable_extension

    from urdf2vla.grippers import get_gripper

    sr = get_gripper(g.simready)
    check_stage = Usd.Stage.Open(usd_path)  # prim より先に stage が消えないよう参照を持つ
    check = check_stage.GetDefaultPrim()
    if "Gripper" in check.GetVariantSets().GetNames() and sr.key in check.GetVariantSet("Gripper").GetVariantNames():
        return False
    del check, check_stage
    enable_extension("isaacsim.robot_setup.assembler")
    from isaacsim.robot_setup.assembler import RobotAssembler

    ctx, app = omni.usd.get_context(), omni.kit.app.get_app()
    ctx.open_stage(usd_path)
    for _ in range(10):
        app.update()
    stage = ctx.get_stage()
    base = stage.GetDefaultPrim().GetPath().pathString
    parent = next((p for p in stage.Traverse() if p.GetName() == g.mount_link), None)
    if parent is None:
        raise RuntimeError(f"取付先の link '{g.mount_link}' がロボットの USD にありません")
    gripper_prim = stage.DefinePrim(f"{base}/Gripper", "Xform")
    gripper_prim.GetReferences().AddReference(sr.url)
    for name, value in sr.variants.items():
        gripper_prim.GetVariantSet(name).SetVariantSelection(value)
    for _ in range(20):  # 参照先（ネット上のアセット）の読み込み
        app.update()
    gbase = next((p for p in Usd.PrimRange(gripper_prim) if p.GetName() == sr.base_body and p.HasAPI(UsdPhysics.RigidBodyAPI)), None)
    if gbase is None:
        raise RuntimeError(f"{sr.title} のアセット（{sr.url}）を読み込めませんでした（ネットワークを確認してください）")
    x, y, z, w = g.mount.rot_xyzw
    mount = UsdGeom.Xform.Define(stage, f"{parent.GetPath()}/urdf2vla_gripper_mount")
    mount.AddTransformOp().Set(Gf.Matrix4d().SetTransform(Gf.Rotation(Gf.Quatd(w, Gf.Vec3d(x, y, z))), Gf.Vec3d(*g.mount.pos)))
    asm = RobotAssembler()
    asm.begin_assembly(stage, base, str(mount.GetPath()), str(gripper_prim.GetPath()), str(gbase.GetPath()), "Gripper", sr.key)
    asm.assemble()
    task = asm.finish_assemble()
    for _ in range(1200):
        app.update()
        if task.done():
            break
    if not task.done():
        raise RuntimeError("Robot Assembler の保存が終わりませんでした")
    task.result()  # 保存に失敗していれば例外
    stage.GetRootLayer().Save()
    ctx.new_stage()  # 以降の検査は新しい stage で行う
    for _ in range(5):
        app.update()
    return True


def articulation_cfg(cfg: RobotConfig, usd_path: str, prim_path: str):
    import isaaclab.sim as sim_utils
    from isaaclab.actuators import ImplicitActuatorCfg
    from isaaclab.assets import ArticulationCfg

    actuators = {}
    for group in cfg.actuators:
        actuators[group.name] = ImplicitActuatorCfg(
            joint_names_expr=[_expr(j) for j in group.joints],
            stiffness=group.stiffness,
            damping=group.damping,
            joint_effort_limit={_expr(k): v for k, v in group.effort_limit.items()} or None,
            joint_velocity_limit={_expr(k): v for k, v in group.velocity_limit.items()} or None,
        )
    return ArticulationCfg(
        prim_path=prim_path,
        spawn=sim_utils.UsdFileCfg(
            usd_path=usd_path,
            # SimReady のグリッパを取り付けた variant を選ぶ
            variants={"Gripper": g.simready for g in cfg.grippers if g.simready} or None,
            rigid_props=sim_utils.RigidBodyPropertiesCfg(
                disable_gravity=cfg.asset.disable_gravity,
                max_depenetration_velocity=5.0,
            ),
            articulation_props=sim_utils.ArticulationRootPropertiesCfg(
                enabled_self_collisions=cfg.asset.self_collision,
                solver_position_iteration_count=cfg.asset.solver_position_iterations,
                solver_velocity_iteration_count=cfg.asset.solver_velocity_iterations,
            ),
        ),
        init_state=ArticulationCfg.InitialStateCfg(
            pos=tuple(cfg.base_pose.pos),
            rot=tuple(cfg.base_pose.rot_xyzw),
            joint_pos={_expr(k): v for k, v in cfg.default_pose.items()},
            joint_vel={".*": 0.0},
        ),
        actuators=actuators,
        soft_joint_pos_limit_factor=1.0,
    )


def usd_structure(usd_path: str) -> dict:
    """変換された USD を読み、link の prim パス・剛体・関節・articulation root を返す。"""
    from pxr import Usd, UsdPhysics

    stage = Usd.Stage.Open(usd_path)
    root = stage.GetDefaultPrim()
    root_path = str(root.GetPath())
    links, rigid, joints, art_roots, undriven, mimic = {}, [], {}, [], [], []
    for prim in Usd.PrimRange(root):
        path = str(prim.GetPath())
        rel = path[len(root_path) + 1 :] if path != root_path else ""
        if prim.HasAPI(UsdPhysics.ArticulationRootAPI):
            art_roots.append(rel or "/")
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            rigid.append(prim.GetName())
            links[prim.GetName()] = rel
        if prim.IsA(UsdPhysics.Joint):
            kind = "fixed"
            if prim.IsA(UsdPhysics.RevoluteJoint):
                kind = "revolute"
            elif prim.IsA(UsdPhysics.PrismaticJoint):
                kind = "prismatic"
            joints[prim.GetName()] = kind
            # 有効な mimic の拘束（NewtonMimicAPI の mimicEnabled、または PhysxMimicJointAPI）
            schemas = prim.GetAppliedSchemas()
            newton_on = "NewtonMimicAPI" in schemas and bool(prim.GetAttribute("newton:mimicEnabled").Get())
            if newton_on or any(s.startswith("PhysxMimicJointAPI") for s in schemas):
                mimic.append(prim.GetName())  # mimic の拘束で動く関節は駆動がなくてよい
            elif kind != "fixed" and not prim.HasAPI(UsdPhysics.DriveAPI, "linear" if kind == "prismatic" else "angular"):
                undriven.append(prim.GetName())
    # 質量のない frame（Xform）も link として拾う（カメラの取付先になりうる）
    for prim in root.GetChildren():
        if prim.GetName() not in links and prim.GetTypeName() == "Xform":
            links[prim.GetName()] = str(prim.GetPath())[len(root_path) + 1 :]
    return {
        "default_prim": root_path,
        "articulation_roots": art_roots,
        "link_paths": links,
        "rigid_bodies": rigid,
        "joints": joints,
        "undriven_joints": undriven,
        "mimic_joints": mimic,  # 有効な mimic の拘束がある関節
    }
