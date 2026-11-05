"""Pick & Place の場面（床・台・物体の候補・置き場所・照明）を Arena の Scene にする。

台の大きさ・位置はロボットの腕の長さから urdf2vla が決める（configure_task）ので、Arena の配置ソルバー（relations）は
使わず、Object を決まった位置に置く。物体の候補はすべて場面に入れ、ロボットの後ろの床に置いておく（どのカメラにも写らない）。
エピソードの始めに 1 つを選んで台の上に置く（urdf2vla.sim.state.SimView.start_episode）。
"""

from __future__ import annotations

from urdf2vla.sim.arena.embodiment import FLOOR_Z

ANGULAR_DAMPING = 2.0  # 丸い物体の角速度の減衰（転がり抵抗の代わり）
PARK_X = -1.5  # 使わない物体を置く場所（ロボットの後ろの床）
PARK_DY = 0.25


def _spawn_usd_damped(prim_path, cfg, *args, **kwargs):
    """USD のアセットを生成し、中の剛体すべてに角速度の減衰を付ける。

    Isaac Lab の rigid_props は instance（instanceable な prim の中身）を書き換えられず黙って飛ばすので
    （SimReady のりんごは剛体が instance の中にあり、減衰が付かなかった）、instance を解いてから付ける。
    """
    from pxr import Usd, UsdPhysics

    import isaaclab.sim as sim_utils

    prim = sim_utils.spawn_from_usd(prim_path, cfg, *args, **kwargs)
    damping = getattr(cfg, "_urdf2vla_angular_damping", None)
    if damping is None:
        return prim
    for root in sim_utils.find_matching_prims(prim_path):
        changed = True
        while changed:  # instance を解くと中身が見えるようになるので、なくなるまで繰り返す
            changed = False
            for p in Usd.PrimRange(root):
                if p.IsInstance():
                    p.SetInstanceable(False)
                    changed = True
        for p in Usd.PrimRange(root):
            if p.HasAPI(UsdPhysics.RigidBodyAPI):
                p.CreateAttribute("physxRigidBody:angularDamping", _float()).Set(float(damping))
    return prim


def _float():
    from pxr import Sdf

    return Sdf.ValueTypeNames.Float


def parked_pose(task, i: int) -> tuple[float, float, float]:
    """物体の候補 i を置いておく位置（env 座標）。"""
    return (PARK_X, -0.6 + PARK_DY * i, FLOOR_Z - task.objects[i].bottom_z + 0.005)


def make_scene(c):
    import isaaclab.sim as sim_utils
    from isaaclab_arena.assets.object import Object
    from isaaclab_arena.assets.object_type import ObjectType
    from isaaclab_arena.scene.scene import Scene
    from isaaclab_arena.utils.pose import Pose

    task = c.task
    table_h = task.table.top_z - FLOOR_Z

    def fixed(name: str, spawn, pos, rot=(0.0, 0.0, 0.0, 1.0), prim_path: str | None = None) -> Object:
        return Object(
            name=name,
            prim_path=prim_path,
            object_type=ObjectType.BASE,
            spawner_cfg=spawn,
            initial_pose=Pose(position_xyz=tuple(float(v) for v in pos), rotation_xyzw=tuple(float(v) for v in rot)),
        )

    floor = fixed(
        "floor",
        sim_utils.CuboidCfg(
            size=(12.0, 12.0, 0.02),
            collision_props=sim_utils.CollisionPropertiesCfg(),
            visual_material=sim_utils.PreviewSurfaceCfg(diffuse_color=(0.3, 0.3, 0.32)),
        ),
        (0.0, 0.0, FLOOR_Z - 0.01),
        prim_path="/World/Floor",
    )
    table = fixed(
        "table",
        sim_utils.CuboidCfg(
            size=(task.table.size[0], task.table.size[1], table_h),
            collision_props=sim_utils.CollisionPropertiesCfg(),
            visual_material=sim_utils.PreviewSurfaceCfg(diffuse_color=(0.72, 0.6, 0.45)),
        ),
        (task.table.center[0], task.table.center[1], task.table.top_z - table_h / 2),
    )
    tgt = task.target
    if tgt.is_container:
        # トレイなどの容器: 台の上に固定する（kinematic。物体が当たっても動かない）
        target = Object(
            name="target",
            object_type=ObjectType.RIGID,
            usd_path=tgt.asset,
            scale=(tgt.scale,) * 3,
            initial_pose=Pose(position_xyz=(tgt.pos[0], tgt.pos[1], task.table.top_z - tgt.bottom + 0.001)),
            spawn_cfg_addon={"variants": dict(tgt.variants), "rigid_props": sim_utils.RigidBodyPropertiesCfg(kinematic_enabled=True)},
        )
    else:
        target = fixed(
            "target",
            sim_utils.CylinderCfg(radius=tgt.radius, height=0.002, visual_material=sim_utils.PreviewSurfaceCfg(diffuse_color=tuple(tgt.color))),
            (tgt.pos[0], tgt.pos[1], task.table.top_z + 0.001),
        )
    objects = []
    for i, obj in enumerate(task.objects):
        pose = Pose(position_xyz=parked_pose(task, i))
        if obj.asset is None:
            o = Object(
                name=f"object_{i}",
                object_type=ObjectType.RIGID,
                spawner_cfg=sim_utils.CuboidCfg(
                    size=tuple(obj.size),
                    rigid_props=sim_utils.RigidBodyPropertiesCfg(max_depenetration_velocity=1.0),
                    mass_props=sim_utils.MassPropertiesCfg(mass=obj.mass),
                    collision_props=sim_utils.CollisionPropertiesCfg(),
                    physics_material=sim_utils.RigidBodyMaterialCfg(static_friction=1.2, dynamic_friction=1.0),
                    visual_material=sim_utils.PreviewSurfaceCfg(diffuse_color=tuple(obj.color)),
                ),
                initial_pose=pose,
            )
        else:
            # PhysX には転がり抵抗がないので、丸い果物は台の上で倒れて転がり出す（りんごは 2 秒ほどで倒れて 10 cm 転がった）。
            # 角速度の減衰で抑える。箱の形の物体には付けない（掴んだときに指の面に沿って回り切れず、指がめり込む）
            spawn = sim_utils.UsdFileCfg(
                func=_spawn_usd_damped,
                usd_path=obj.asset,
                scale=(obj.scale,) * 3,
                variants=dict(obj.variants),
                rigid_props=sim_utils.RigidBodyPropertiesCfg(max_depenetration_velocity=1.0),
            )
            if not obj.box:
                spawn._urdf2vla_angular_damping = ANGULAR_DAMPING
            o = Object(name=f"object_{i}", object_type=ObjectType.RIGID, spawner_cfg=spawn, initial_pose=pose)
        objects.append(o)
    light = fixed("light", sim_utils.DomeLightCfg(intensity=700.0, color=(1.0, 1.0, 1.0)), (0.0, 0.0, 0.0), prim_path="/World/Light")
    sun = fixed(
        "sun", sim_utils.DistantLightCfg(intensity=600.0, angle=1.0), (0.0, 0.0, 0.0), rot=(0.0, 0.3827, 0.0, 0.9239), prim_path="/World/Sun"
    )
    return Scene(assets=[floor, table, *objects, target, light, sun])
