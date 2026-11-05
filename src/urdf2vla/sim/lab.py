"""Isaac Lab 3.0 の API を urdf2vla から使うための小さな補助（Isaac Sim 内でのみ import）。

Isaac Lab 3.0 で変わった点をここに集める:
- asset.data.* とカメラの出力は ProxyArray。torch.Tensor が要るときは .torch（暗黙の変換は将来なくなる）
- シミュレーションへの書き込みは *_index（キーワード引数）
- 四元数は (x, y, z, w)
"""

from __future__ import annotations

import torch


def T(x) -> torch.Tensor:
    """ProxyArray → torch.Tensor（すでに Tensor ならそのまま）。"""
    return x.torch if hasattr(x, "torch") else x


def write_joint_state(robot, q: torch.Tensor) -> None:
    """関節角を q に置き、速度 0、関節目標も q にする（全環境・全関節。q の形は (num_envs, num_joints)）。"""
    robot.write_joint_position_to_sim_index(position=q)
    robot.write_joint_velocity_to_sim_index(velocity=torch.zeros_like(q))
    robot.set_joint_position_target_index(target=q)


def write_root_pose(asset, pose: torch.Tensor) -> None:
    """剛体・articulation の root を pose（world 座標の [x, y, z, qx, qy, qz, qw]）に置き、速度 0 にする。"""
    asset.write_root_pose_to_sim_index(root_pose=pose)
    asset.write_root_velocity_to_sim_index(root_velocity=torch.zeros(pose.shape[0], 6, device=pose.device))


def jacobian_w(robot, body_idx: int, joint_ids: list[int]) -> torch.Tensor:
    """body の link 原点での幾何ヤコビアン（world 座標）。形は (num_envs, 6, len(joint_ids))。"""
    jb = body_idx - 1 if robot.is_fixed_base else body_idx  # 固定 base では root の行がない
    cols = [j + robot.num_base_dofs for j in joint_ids]
    return T(robot.data.body_link_jacobian_w)[:, jb, :, cols]


def env_origin(env) -> torch.Tensor:
    return T(env.scene.env_origins)[0]


def camera_rgb(env, name: str) -> torch.Tensor:
    """カメラの RGB 画像（env 0, H×W×3, uint8）。"""
    return T(env.scene[name].data.output["rgb"])[0][..., :3]


def render_fix(env, task) -> None:
    """RTX カメラを確実に更新する対策（task.render_fix が True のときだけ）。

    物理を 1 step 余分に進めるので、記録と評価で同じ設定を使うこと（task.control_hz に反映済み）。
    """
    if task.render_fix and env.sim.has_rtx_sensors():
        env.sim.step(render=True)
        env.scene.update(dt=env.physics_dt)
        env.obs_buf = env.observation_manager.compute()
