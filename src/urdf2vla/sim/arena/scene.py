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


WOOD_MDL = "https://omniverse-content-production.s3-us-west-2.amazonaws.com/Assets/Isaac/6.2/NVIDIA/Materials/Base/Wood"
WOOD = {"oak": "Oak", "walnut": "Walnut"}


def _spawn_usd_rigid(prim_path, cfg, *args, **kwargs):
    """USD のアセットを生成し、中の剛体すべてに角速度の減衰・kinematic を付ける。

    Isaac Lab の rigid_props は instance（instanceable な prim の中身）を書き換えられず黙って飛ばすので
    （SimReady のりんごや KLT の箱は剛体が instance の中にある）、instance を解いてから付ける。
    """
    from pxr import Sdf, Usd, UsdPhysics

    import isaaclab.sim as sim_utils

    prim = sim_utils.spawn_from_usd(prim_path, cfg, *args, **kwargs)
    damping = getattr(cfg, "_urdf2vla_angular_damping", None)
    kinematic = getattr(cfg, "_urdf2vla_kinematic", False)
    if damping is None and not kinematic:
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
                if damping is not None:
                    p.CreateAttribute("physxRigidBody:angularDamping", Sdf.ValueTypeNames.Float).Set(float(damping))
                if kinematic:
                    UsdPhysics.RigidBodyAPI(p).CreateKinematicEnabledAttr().Set(True)
    return prim


def _uv_box(stage, path: str, size, uv_per_m: float = 1.0) -> None:
    """面ごとに UV（1 m あたり uv_per_m）を張った箱のメッシュ。UsdGeom.Cube には UV がなく、木目のテクスチャが出ない。"""
    from pxr import Gf, Sdf, UsdGeom

    sx, sy, sz = (float(v) / 2 for v in size)
    faces = [
        ([(-sx, -sy, sz), (sx, -sy, sz), (sx, sy, sz), (-sx, sy, sz)], 2 * sx, 2 * sy),
        ([(-sx, -sy, -sz), (-sx, sy, -sz), (sx, sy, -sz), (sx, -sy, -sz)], 2 * sy, 2 * sx),
        ([(-sx, -sy, -sz), (sx, -sy, -sz), (sx, -sy, sz), (-sx, -sy, sz)], 2 * sx, 2 * sz),
        ([(sx, sy, -sz), (-sx, sy, -sz), (-sx, sy, sz), (sx, sy, sz)], 2 * sx, 2 * sz),
        ([(sx, -sy, -sz), (sx, sy, -sz), (sx, sy, sz), (sx, -sy, sz)], 2 * sy, 2 * sz),
        ([(-sx, sy, -sz), (-sx, -sy, -sz), (-sx, -sy, sz), (-sx, sy, sz)], 2 * sy, 2 * sz),
    ]
    mesh = UsdGeom.Mesh.Define(stage, path)
    pts, idx, st = [], [], []
    for verts, lu, lv in faces:
        base = len(pts)
        pts += [Gf.Vec3f(*v) for v in verts]
        idx += [base, base + 1, base + 2, base + 3]
        st += [Gf.Vec2f(0, 0), Gf.Vec2f(lu * uv_per_m, 0), Gf.Vec2f(lu * uv_per_m, lv * uv_per_m), Gf.Vec2f(0, lv * uv_per_m)]
    mesh.CreatePointsAttr(pts)
    mesh.CreateFaceVertexCountsAttr([4] * 6)
    mesh.CreateFaceVertexIndicesAttr(idx)
    UsdGeom.PrimvarsAPI(mesh).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying).Set(st)


def _spawn_wood_table(prim_path, cfg, *args, **kwargs):
    """台: 衝突用の箱（見えなくする）と、木目の UV を張った見た目の箱（NVIDIA の木の MDL 材質）。"""
    from pxr import Usd, UsdGeom

    import isaaclab.sim as sim_utils

    prim = sim_utils.spawn_cuboid(prim_path, cfg, *args, **kwargs)
    wood = WOOD[cfg._urdf2vla_wood]
    for root in sim_utils.find_matching_prims(prim_path):
        for p in Usd.PrimRange(root):
            if p.IsA(UsdGeom.Gprim):
                UsdGeom.Imageable(p).MakeInvisible()  # 衝突はそのまま（PhysX は見える・見えないに関係しない）
        mesh_path = f"{root.GetPath()}/wood_visual"
        _uv_box(root.GetStage(), mesh_path, cfg.size)
        mat_path = f"{root.GetPath()}/wood_material"
        mdl = sim_utils.MdlFileCfg(mdl_path=f"{WOOD_MDL}/{wood}.mdl")
        mdl.func(mat_path, mdl)
        sim_utils.bind_visual_material(mesh_path, mat_path)
    return prim


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
    table_cfg = sim_utils.CuboidCfg(
        size=(task.table.size[0], task.table.size[1], table_h),
        collision_props=sim_utils.CollisionPropertiesCfg(),
        visual_material=sim_utils.PreviewSurfaceCfg(diffuse_color=(0.72, 0.6, 0.45)),
    )
    if task.table.material in WOOD:  # 木目の台（見た目だけ。衝突は同じ箱）
        table_cfg.func = _spawn_wood_table
        table_cfg._urdf2vla_wood = task.table.material
    table = fixed("table", table_cfg, (task.table.center[0], task.table.center[1], task.table.top_z - table_h / 2))
    tgt = task.target
    if tgt.is_container:
        # トレイなどの容器: 台の上に固定する（kinematic。物体が当たっても動かない）
        spawn = sim_utils.UsdFileCfg(
            func=_spawn_usd_rigid,
            usd_path=tgt.asset,
            scale=(tgt.scale,) * 3,
            variants=dict(tgt.variants),
            rigid_props=sim_utils.RigidBodyPropertiesCfg(kinematic_enabled=True),
        )
        spawn._urdf2vla_kinematic = True  # 剛体が instance の中にあるアセット（KLT の箱）でも固定する
        target = Object(
            name="target",
            object_type=ObjectType.RIGID,
            spawner_cfg=spawn,
            initial_pose=Pose(position_xyz=(tgt.pos[0], tgt.pos[1], task.table.top_z - tgt.bottom + 0.001)),
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
                func=_spawn_usd_rigid,
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
