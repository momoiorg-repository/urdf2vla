"""VLA interface → GR00T の modality 定義（meta/modality.json と N1.7 の modality config）。純 Python。"""

from __future__ import annotations

from urdf2vla.schema.vla import VlaInterface


def modality_json(vla: VlaInterface) -> dict:
    """LeRobot データセットの meta/modality.json（observation.state / action のどの範囲がどのグループか）。"""
    out: dict = {"state": {}, "action": {}, "video": {}, "annotation": {"human.task_description": {"original_key": "task_index"}}}
    for section, spec, key in (("state", vla.state, "observation.state"), ("action", vla.action, "action")):
        start = 0
        for g in spec.groups:
            entry = {"start": start, "end": start + g.dim}
            if section == "state":
                entry["original_key"] = key
            out[section][g.name] = entry
            start += g.dim
    for img in vla.images:
        out["video"][img.key] = {"original_key": f"observation.images.{img.key}"}
    return out


def modality_config_py(vla: VlaInterface) -> str:
    """GR00T N1.7 の modality config（import すると NEW_EMBODIMENT として登録される）。
    arm は state からの相対行動（RELATIVE）、グリッパは絶対値（ABSOLUTE）で学習する。"""
    videos = [i.key for i in vla.images]
    groups = [g.name for g in vla.action.groups]
    action_cfgs = []
    for g in vla.action.groups:
        if g.kind == "joint_position":
            action_cfgs.append(
                f"        ActionConfig(rep=ActionRepresentation.RELATIVE, type=ActionType.NON_EEF, format=ActionFormat.DEFAULT, state_key={g.name!r}),"
            )
        else:
            action_cfgs.append(
                "        ActionConfig(rep=ActionRepresentation.ABSOLUTE, type=ActionType.NON_EEF, format=ActionFormat.DEFAULT),"
            )
    return f'''# urdf2vla が自動生成した GR00T N1.7 の modality config（vla_interface.yaml から）
from gr00t.configs.data.embodiment_configs import register_modality_config
from gr00t.data.embodiment_tags import EmbodimentTag
from gr00t.data.types import ActionConfig, ActionFormat, ActionRepresentation, ActionType, ModalityConfig

config = {{
    "video": ModalityConfig(delta_indices=[0], modality_keys={videos!r}),
    "state": ModalityConfig(delta_indices=[0], modality_keys={[g.name for g in vla.state.groups]!r}),
    "action": ModalityConfig(
        delta_indices=list(range({vla.action_horizon})),
        modality_keys={groups!r},
        action_configs=[
{chr(10).join(action_cfgs)}
        ],
    ),
    "language": ModalityConfig(delta_indices=[0], modality_keys=["annotation.human.task_description"]),
}}

register_modality_config(config, embodiment_tag=EmbodimentTag.NEW_EMBODIMENT)
'''
