"""Cloth Tools: buttons for animators using the robe rigs (monk, abbot).

1. Tao dang  : Cloth_Sim = 0, the robe follows the bones, fast viewport.
2. Chay vai  : bake every cloth cache of the scene (with a pre-roll from the rest pose), Cloth_Sim = 1.
3. Xuat Alembic : write the baked robe (and kasaya) to //cache for rendering.

The rig stores the switch as the custom property 'Cloth_Sim' (0/1) and its drivers work without this add-on;
the add-on only adds the buttons.
"""
import os
import bpy
from bpy.props import EnumProperty

PREROLL = 20
STATIC_FRAMES = 30   # frames the cloth needs to settle on a held pose
# (solver quality, collision quality) per preset
PRESETS = {"FAST": (5, 6), "FINAL": (12, 6)}
# measured bake cost, seconds per frame, on a normal desktop (the monk is the heavier rig)
SECONDS_PER_FRAME = {"FAST": 0.5, "FINAL": 1.0}
LONG_SECONDS = 60


def find_rig(context):
    ob = context.active_object
    if ob and ob.type == "ARMATURE" and "Cloth_Sim" in ob:
        return ob
    for o in context.scene.objects:
        if o.type == "ARMATURE" and "Cloth_Sim" in o:
            return o
    return None


def cloth_mods():
    return [(o, m) for o in bpy.data.objects if o.type == "MESH" for m in o.modifiers if m.type == "CLOTH"]


def action_first_frame(rig):
    """First keyframe of the rig's action, or None. Works with the layered Actions of Blender 4.4+/5.x and the old fcurves API."""
    ad = rig.animation_data
    if not ad or not ad.action:
        return None
    act = ad.action
    try:
        rng = act.curve_frame_range
        return float(rng[0]) if rng[1] >= rng[0] and len(rig.pose.bones) and _has_keys(act) else None
    except AttributeError:
        pass
    best = None
    for fc in getattr(act, "fcurves", []):
        for k in fc.keyframe_points:
            best = k.co[0] if best is None else min(best, k.co[0])
    return best


def _has_keys(act):
    try:
        for layer in act.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    for fc in bag.fcurves:
                        if len(fc.keyframe_points):
                            return True
        return False
    except AttributeError:
        return True


def restore_animation(rig):
    """Put back the action that a pose preview parked (stored by name on the rig)."""
    name = rig.get("Cloth_Stashed_Action", "")
    if name and name in bpy.data.actions:
        if rig.animation_data is None:
            rig.animation_data_create()
        rig.animation_data.action = bpy.data.actions[name]
    rig["Cloth_Stashed_Action"] = ""
    rig["Cloth_Preview"] = 0


class OM_OT_pose(bpy.types.Operator):
    bl_idname = "om_cloth.pose"
    bl_label = "Tạo dáng (tắt vải)"
    bl_description = "Tắt mô phỏng vải: áo đi theo xương ngay, viewport nhanh. Dùng khi tạo dáng và đặt key"

    def execute(self, context):
        rig = find_rig(context)
        if rig is None:
            self.report({"ERROR"}, "Không tìm thấy rig có thuộc tính Cloth_Sim")
            return {"CANCELLED"}
        rig["Cloth_Sim"] = 0
        restore_animation(rig)
        rig.update_tag()
        context.view_layer.update()
        return {"FINISHED"}


class OM_OT_run(bpy.types.Operator):
    bl_idname = "om_cloth.run"
    bl_label = "Chạy vải"
    bl_description = "Bake vải cho đoạn Frame Start đến End (tự thêm 20 frame đệm từ tư thế nghỉ). Sửa animation thì chạy lại"
    pose_only: bpy.props.BoolProperty(name="Chỉ xem dáng hiện tại", default=False, options={"SKIP_SAVE"})
    quality: EnumProperty(name="Chất lượng", items=[("FAST", "Nhanh (xem thử)", ""), ("FINAL", "Đẹp (bản cuối)", "")], default="FAST")

    def invoke(self, context, event):
        sc = context.scene
        self.quality = "FINAL" if sc.om_cloth_final else "FAST"
        rig = find_rig(context)
        static = rig is not None and (self.pose_only or action_first_frame(rig) is None)
        frames = PREROLL + STATIC_FRAMES + 1 if static else sc.frame_end - sc.frame_start + 1 + PREROLL
        est = frames * SECONDS_PER_FRAME[self.quality]
        if est >= LONG_SECONDS:
            msg = (f"Sẽ chạy {frames} frame, ước tính khoảng {est / 60:.1f} phút. "
                   "Blender đứng hình trong lúc tính, hiện 'Not Responding' là bình thường. "
                   "Nếu không đúng ý, hủy rồi sửa Frame Start/End cho đúng đoạn cảnh.")
            try:
                return context.window_manager.invoke_confirm(self, event, title="Chạy vải", message=msg, confirm_text="Vẫn chạy")
            except (TypeError, AttributeError):
                return context.window_manager.invoke_props_dialog(self, width=420)
        return self.execute(context)

    def execute(self, context):
        rig = find_rig(context)
        if rig is None:
            self.report({"ERROR"}, "Không tìm thấy rig có thuộc tính Cloth_Sim")
            return {"CANCELLED"}
        mods = cloth_mods()
        if not mods:
            self.report({"ERROR"}, "Không có áo nào dùng Cloth trong file này")
            return {"CANCELLED"}
        sc = context.scene
        self.quality = "FINAL" if sc.om_cloth_final else "FAST"
        if not self.pose_only and rig.get("Cloth_Stashed_Action", ""):
            restore_animation(rig)
        ad = rig.animation_data
        act0 = ad.action if ad else None
        if self.pose_only and act0 is not None:
            sc.frame_set(sc.frame_current)     # make sure the pose of this frame is evaluated
        static = self.pose_only or action_first_frame(rig) is None   # one pose held, let the cloth settle
        keep_range = (sc.frame_start, sc.frame_end)
        start, end = sc.frame_start, sc.frame_end
        if static:
            end = start + PREROLL + STATIC_FRAMES   # T-pose -> held pose (PREROLL frames) -> settle
            sc.frame_end = end
        pre = start if static else start - PREROLL
        q, cq = PRESETS[self.quality]
        keep_frame = sc.frame_current
        for o, m in mods:
            with context.temp_override(object=o, point_cache=m.point_cache):
                bpy.ops.ptcache.free_bake()
            m.settings.quality = q
            m.collision_settings.collision_quality = cq
            m.point_cache.frame_start, m.point_cache.frame_end = pre, end
        rig["Cloth_Sim"] = 1
        # pre-roll: temporary rest-pose keys, only if the animation does not already start earlier
        added = []
        first = action_first_frame(rig)
        saved = {}
        props = {}
        had_action = act0 is not None
        if static:
            for pb in rig.pose.bones:
                if pb.name.startswith("c_"):
                    saved[pb.name] = (tuple(pb.location), tuple(pb.rotation_quaternion), tuple(pb.rotation_euler), tuple(pb.scale))
                    props[pb.name] = {k: pb[k] for k in pb.keys() if isinstance(pb[k], (int, float)) and not k.startswith("_")}
        if static and had_action:          # keys exist: park the animation, work on a temporary action
            ad.action = None
        if static or (first is not None and first > pre):
            sc.frame_set(pre)
            for pb in rig.pose.bones:
                if not pb.name.startswith("c_"):
                    continue
                pb.location = (0, 0, 0)
                pb.rotation_quaternion = (1, 0, 0, 0)
                pb.rotation_euler = (0, 0, 0)
                pb.scale = (1, 1, 1)
                rp = "rotation_quaternion" if pb.rotation_mode == "QUATERNION" else "rotation_euler"
                for path in ("location", rp, "scale"):
                    pb.keyframe_insert(path, frame=pre)
                    added.append((pb, path, pre))
            if static:   # the pose the animator set, reached PREROLL frames later and held
                for pb in rig.pose.bones:
                    if pb.name in saved:
                        pb.location, pb.rotation_quaternion, pb.rotation_euler, pb.scale = saved[pb.name]
                        for k, v in props[pb.name].items():
                            pb[k] = v
                        for k, v in props[pb.name].items():
                            pb[k] = v
                        rp = "rotation_quaternion" if pb.rotation_mode == "QUATERNION" else "rotation_euler"
                        for path in ("location", rp, "scale"):
                            pb.keyframe_insert(path, frame=pre + PREROLL)
                            added.append((pb, path, pre + PREROLL))
        sc.frame_start = pre
        try:
            bpy.ops.ptcache.bake_all(bake=True)
        finally:
            sc.frame_start, sc.frame_end = keep_range
            for pb, path, fr in added:
                try:
                    pb.keyframe_delete(path, frame=fr)
                except RuntimeError:
                    pass
            if static:
                if rig.animation_data and rig.animation_data.action and rig.animation_data.action is not act0:
                    act = rig.animation_data.action
                    rig.animation_data.action = None
                    if act.users == 0:
                        bpy.data.actions.remove(act)
                if had_action:
                    if self.pose_only:      # park the animation so the previewed pose stays visible
                        act0.use_fake_user = True
                        rig["Cloth_Stashed_Action"] = act0.name
                    else:
                        rig.animation_data.action = act0
                for pb in rig.pose.bones:
                    if pb.name in saved:
                        pb.location, pb.rotation_quaternion, pb.rotation_euler, pb.scale = saved[pb.name]
            sc.frame_set(end if static else keep_frame)
            rig["Cloth_Preview"] = 1 if (self.pose_only and had_action) else 0
        ok = all(m.point_cache.is_baked for _, m in mods)
        self.report({"INFO"} if ok else {"WARNING"}, f"Đã chạy vải {pre} → {end}" if ok else "Bake chưa xong, thử lại")
        return {"FINISHED"}


class OM_OT_clear(bpy.types.Operator):
    bl_idname = "om_cloth.clear"
    bl_label = "Xoá bake"
    bl_description = "Xoá vải đã bake"

    def execute(self, context):
        for o, m in cloth_mods():
            with context.temp_override(object=o, point_cache=m.point_cache):
                bpy.ops.ptcache.free_bake()
        return {"FINISHED"}


class OM_OT_abc(bpy.types.Operator):
    bl_idname = "om_cloth.abc"
    bl_label = "Xuất Alembic"
    bl_description = "Xuất áo (và cà sa) đã bake ra thư mục cache cạnh file .blend, để render nhẹ"

    def execute(self, context):
        sc = context.scene
        if not bpy.data.filepath:
            self.report({"ERROR"}, "Lưu file .blend trước")
            return {"CANCELLED"}
        names = [n for n in ("upper001", "gasa001") if n in bpy.data.objects]
        if not names:
            self.report({"ERROR"}, "Không thấy áo để xuất")
            return {"CANCELLED"}
        folder = bpy.path.abspath("//cache")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, os.path.splitext(os.path.basename(bpy.data.filepath))[0] + "_robe.abc")
        for o in context.view_layer.objects:
            o.select_set(False)
        for n in names:
            bpy.data.objects[n].select_set(True)
        context.view_layer.objects.active = bpy.data.objects[names[0]]
        bpy.ops.wm.alembic_export(filepath=path, start=sc.frame_start, end=sc.frame_end, selected=True,
                                  evaluation_mode="RENDER", export_hair=False, export_particles=False,
                                  init_scene_frame_range=False, uvs=True, normals=True, face_sets=False, apply_subdiv=False)
        self.report({"INFO"}, path)
        return {"FINISHED"}


class OM_PT_cloth(bpy.types.Panel):
    bl_label = "Cloth Tools"
    bl_idname = "OM_PT_cloth"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Cloth Tools"

    def draw(self, context):
        lay = self.layout
        rig = find_rig(context)
        if rig is None:
            lay.label(text="Không thấy rig OM (thuộc tính Cloth_Sim)", icon="ERROR")
            return
        sc = context.scene
        mods = cloth_mods()
        baked = bool(mods) and all(m.point_cache.is_baked for _, m in mods)
        sim_on = float(rig.get("Cloth_Sim", 0)) > 0.5
        box = lay.box()
        box.label(text="Vải thật: BẬT" if sim_on else "Vải thật: TẮT (tạo dáng)", icon="PHYSICS" if sim_on else "ARMATURE_DATA")
        box.label(text="Đã bake" if baked else "Chưa bake", icon="CHECKMARK" if baked else "DOT")
        col = lay.column(align=True)
        col.scale_y = 2.0
        col.operator("om_cloth.run", text="Chạy vải", icon="PLAY")
        if int(rig.get("Cloth_Preview", 0)):
            lay.label(text="Đang xem dáng: animation tạm tắt. Bấm Chạy vải để quay lại", icon="ERROR")
        op = lay.operator("om_cloth.run", text="Xem thử dáng hiện tại (kể cả khi có key)", icon="POSE_HLT")
        op.pose_only = True
        lay.prop(sc, "om_cloth_final", text="Chất lượng đẹp (chậm hơn 2 lần)")
        row = lay.row(align=True)
        row.operator("om_cloth.pose", text="Tắt vải (tạo dáng)", icon="POSE_HLT")
        row.operator("om_cloth.clear", text="Xoá bake", icon="TRASH")
        static = action_first_frame(rig) is None
        frames = PREROLL + STATIC_FRAMES + 1 if static else sc.frame_end - sc.frame_start + 1 + PREROLL
        q = "FINAL" if sc.om_cloth_final else "FAST"
        if static:
            lay.label(text="Chưa có key: chạy vải giữ nguyên dáng hiện tại")
        else:
            lay.label(text=f"Đoạn: {sc.frame_start} đến {sc.frame_end} (+{PREROLL} frame đệm)")
        lay.label(text=f"Ước tính: {frames * SECONDS_PER_FRAME[q] / 60:.1f} phút")
        if frames * SECONDS_PER_FRAME[q] >= LONG_SECONDS:
            lay.label(text="Đoạn dài: kiểm tra Frame End trước khi chạy", icon="ERROR")
        box2 = lay.box()
        box2.label(text="Nâng cao (không bắt buộc)")
        box2.operator("om_cloth.abc", icon="EXPORT")


CLASSES = (OM_OT_pose, OM_OT_run, OM_OT_clear, OM_OT_abc, OM_PT_cloth)


def register():
    bpy.types.Scene.om_cloth_final = bpy.props.BoolProperty(name="Chất lượng đẹp", default=False)
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
    if hasattr(bpy.types.Scene, "om_cloth_final"):
        del bpy.types.Scene.om_cloth_final
