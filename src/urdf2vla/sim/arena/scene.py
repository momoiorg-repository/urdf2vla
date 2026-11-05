"""Pick & Place の場面（床・台・物体・置き場所・照明）を Arena の Scene にする。

台の大きさ・位置はロボットの腕の長さから urdf2vla が決める（configure_task）ので、Arena の配置ソルバー（relations）は
使わず、プリミティブの Object を決まった位置に置く。物体の位置のばらつきは Task の reset イベントで与える。
"""

from __future__ import annotations

from urdf2vla.sim.arena.embodiment import FLOOR_Z


def make_scene(c):
    import isaaclab.sim as sim_utils
    from isaaclab_arena.assets.object import Object
    from isaaclab_arena.assets.object_type import ObjectType
    from isaaclab_arena.scene.scene import Scene
    from isaaclab_arena.utils.pose import Pose

    task = c.task
    table_h = task.table.top_z - FLOOR_Z
    obj = task.object

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
    target = fixed(
        "target",
        sim_utils.CylinderCfg(
            radius=task.target.radius, height=0.002, visual_material=sim_utils.PreviewSurfaceCfg(diffuse_color=tuple(task.target.color))
        ),
        (task.target.pos[0], task.target.pos[1], task.table.top_z + 0.001),
    )
    cube = Object(
        name="object",
        object_type=ObjectType.RIGID,
        spawner_cfg=sim_utils.CuboidCfg(
            size=tuple(obj.size),
            rigid_props=sim_utils.RigidBodyPropertiesCfg(max_depenetration_velocity=1.0),
            mass_props=sim_utils.MassPropertiesCfg(mass=obj.mass),
            collision_props=sim_utils.CollisionPropertiesCfg(),
            physics_material=sim_utils.RigidBodyMaterialCfg(static_friction=1.2, dynamic_friction=1.0),
            visual_material=sim_utils.PreviewSurfaceCfg(diffuse_color=tuple(obj.color)),
        ),
        initial_pose=Pose(position_xyz=(sum(obj.spawn_x) / 2, sum(obj.spawn_y) / 2, task.table.top_z + obj.size[2] / 2 + 0.002)),
    )
    cube.disable_reset_pose()  # 位置のばらつきは Task の reset イベントで与える（Arena の固定位置への reset と競合させない）
    light = fixed("light", sim_utils.DomeLightCfg(intensity=700.0, color=(1.0, 1.0, 1.0)), (0.0, 0.0, 0.0), prim_path="/World/Light")
    sun = fixed(
        "sun", sim_utils.DistantLightCfg(intensity=600.0, angle=1.0), (0.0, 0.0, 0.0), rot=(0.0, 0.3827, 0.0, 0.9239), prim_path="/World/Sun"
    )
    return Scene(assets=[floor, table, cube, target, light, sun])
