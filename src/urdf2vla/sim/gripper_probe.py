"""SimReady のグリッパのアセットを測る（開発用: urdf2vla.grippers に新しいグリッパを足すとき）。

  python -m urdf2vla.sim.gripper_probe <ISAAC_ASSETS_ROOT からの USD のパス> [--variant 名前=値 ...] [--out file.yaml]
  例: python -m urdf2vla.sim.gripper_probe Isaac/Robots/Robotiq/Hand-E/Robotiq_Hand_E_edit.usd

Isaac Sim のコンテナ（urdf2vla-sim）で実行する。ホストからは:
  docker exec -u $(id -un) -e HOME=$HOME -e ACCEPT_EULA=Y -e PRIVACY_CONSENT=Y urdf2vla-sim bash -c \
    'cd /workspace/urdf2vla && PYTHONPATH=src /workspaces/isaaclab_arena/submodules/IsaacLab/isaaclab.sh -p \
     -m urdf2vla.sim.gripper_probe Isaac/Robots/Robotiq/Hand-E/Robotiq_Hand_E_edit.usd --out /tmp/hand_e.yaml'
（urdf2vla の Skill と同じ実行ユーザ・環境。root で ~/.cache を使わない）
Kit を起動して USD を開き、YAML で出す:
- variant sets と選べる値（--variant で選んでから測る）
- 剛体（RigidBodyAPI）ごとの衝突形状の外接箱。座標はグリッパの最初の剛体（取付面の base）の座標
- 関節（revolute / prismatic）: 2 つの剛体、軸、可動範囲、drive（stiffness / damping / maxForce）、mimic の属性
値はアセットの作者が置いた姿勢（ふつうは開いた状態）で測る。推測で埋めずに、この出力から SimReadyGripper の値を決める。
"""

from __future__ import annotations

import argparse
import os
import sys


def _vec(v) -> list[float]:
    return [round(float(x), 5) for x in v]


def probe(url: str, variants: dict[str, str]) -> dict:
    import omni.kit.app
    import omni.usd
    from pxr import Gf, Usd, UsdGeom, UsdPhysics

    app = omni.kit.app.get_app()
    ctx = omni.usd.get_context()
    ctx.new_stage()
    stage = ctx.get_stage()
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    root = stage.DefinePrim("/Gripper", "Xform")
    root.GetReferences().AddReference(url)
    for _ in range(30):  # 参照先（ネット上のアセット）の読み込み
        app.update()
    out: dict = {"url": url, "variant_sets": {}, "selected": {}, "bodies": {}, "joints": {}}
    for p in Usd.PrimRange(root):
        vs = p.GetVariantSets()
        for name in vs.GetNames():
            v = vs.GetVariantSet(name)
            out["variant_sets"][f"{p.GetPath()}:{name}"] = {"choices": v.GetVariantNames(), "default": v.GetVariantSelection()}
    for name, value in variants.items():
        root.GetVariantSet(name).SetVariantSelection(value)
        out["selected"][name] = value
    for _ in range(30):
        app.update()

    bodies = [p for p in Usd.PrimRange(root) if p.HasAPI(UsdPhysics.RigidBodyAPI)]
    if not bodies:
        raise RuntimeError(f"剛体がありません（読み込めていない?）: {url}")
    xf = UsdGeom.XformCache()
    base_inv = xf.GetLocalToWorldTransform(bodies[0]).GetInverse()
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.proxy, UsdGeom.Tokens.guide], useExtentsHint=False)
    out["base_body"] = bodies[0].GetName()
    for b in bodies:
        lo, hi = [1e9] * 3, [-1e9] * 3
        n = 0
        it = iter(Usd.PrimRange(b, Usd.TraverseInstanceProxies(Usd.PrimAllPrimsPredicate)))  # 衝突形状は instance の中にあることが多い
        for c in it:
            if c != b and c.HasAPI(UsdPhysics.RigidBodyAPI):
                it.PruneChildren()  # 子の剛体は別に数える
                continue
            if not c.HasAPI(UsdPhysics.CollisionAPI):
                continue
            box = cache.ComputeWorldBound(c).ComputeAlignedRange()
            if box.IsEmpty():
                continue
            for corner in range(8):
                q = base_inv.Transform(Gf.Range3d(box.GetMin(), box.GetMax()).GetCorner(corner))
                lo = [min(a, float(x)) for a, x in zip(lo, q)]
                hi = [max(a, float(x)) for a, x in zip(hi, q)]
            n += 1
        origin = base_inv.Transform(xf.GetLocalToWorldTransform(b).ExtractTranslation())
        out["bodies"][b.GetName()] = {
            "origin_in_base": _vec(origin),
            "collision_box_in_base": [_vec(lo), _vec(hi)] if n else None,
            "colliders": n,
        }

    for p in Usd.PrimRange(root):
        kind = "revolute" if p.IsA(UsdPhysics.RevoluteJoint) else "prismatic" if p.IsA(UsdPhysics.PrismaticJoint) else None
        if kind is None:
            continue
        j = UsdPhysics.RevoluteJoint(p) if kind == "revolute" else UsdPhysics.PrismaticJoint(p)
        b0, b1 = j.GetBody0Rel().GetTargets(), j.GetBody1Rel().GetTargets()
        info = {
            "type": kind,
            "body0": b0[0].name if b0 else None,
            "body1": b1[0].name if b1 else None,
            "axis": j.GetAxisAttr().Get(),
            "lower": j.GetLowerLimitAttr().Get(),  # revolute は度、prismatic は m
            "upper": j.GetUpperLimitAttr().Get(),
        }
        for inst in ("angular", "linear"):
            d = UsdPhysics.DriveAPI.Get(p, inst)
            if d and d.GetStiffnessAttr().HasAuthoredValue():
                info["drive"] = {"type": inst, "stiffness": d.GetStiffnessAttr().Get(), "damping": d.GetDampingAttr().Get(), "max_force": d.GetMaxForceAttr().Get()}
        mimic = {}
        for prop in p.GetProperties():
            if "imic" in prop.GetName():
                if isinstance(prop, Usd.Relationship):
                    mimic[prop.GetName()] = [t.name for t in prop.GetTargets()]
                else:
                    v = prop.Get()
                    mimic[prop.GetName()] = float(v) if isinstance(v, float) else str(v)
        if mimic:
            info["mimic"] = mimic
        info["api_schemas"] = [str(s) for s in p.GetAppliedSchemas()]
        excl = p.GetAttribute("physics:excludeFromArticulation")
        if excl and excl.Get():
            info["exclude_from_articulation"] = True  # 閉リンクを閉じる関節（articulation の関節としては存在しない）
        out["joints"][p.GetName()] = info
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("usd", help="ISAAC_ASSETS_ROOT からの相対パス、または URL")
    p.add_argument("--variant", action="append", default=[], help="名前=値（例: Physics=Physx_Mimic）")
    p.add_argument("--out", default=None)
    args = p.parse_args()

    from isaaclab.app import launch_simulation

    ctx = launch_simulation(None, {"headless": True, "require_kit": True})
    ctx.__enter__()
    import yaml

    from urdf2vla.grippers import ISAAC_ASSETS_ROOT

    url = args.usd if "://" in args.usd else f"{ISAAC_ASSETS_ROOT}/{args.usd}"
    try:
        result = probe(url, dict(v.split("=", 1) for v in args.variant))
        text = yaml.safe_dump(result, sort_keys=False, allow_unicode=True, width=200)
        if args.out:
            with open(args.out, "w") as f:
                f.write(text)
        print("=== gripper_probe ===\n" + text, flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"=== gripper_probe failed: {type(e).__name__}: {e}", flush=True)
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
