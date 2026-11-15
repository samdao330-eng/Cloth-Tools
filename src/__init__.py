"""Cloth Tools: buttons for animators using the robe rigs (monk, abbot).

Chay vai : bake the cloth for the shot (pre-roll from the T-pose first). Blender is busy while it works.
Thu dang : same, for the pose of the current frame only (works with or without keys).
Tat vai  : Cloth_Sim = 0, the robe follows the bones (fast viewport).

The rig stores the switch as the custom property 'Cloth_Sim' (0/1) and its drivers work without this add-on;
the add-on only adds the buttons. Its own bookkeeping lives in hidden properties (names start with "_").
"""
import bpy

PREROLL = 20          # frames before the shot: T-pose -> first pose
STATIC_PRE = 16       # single pose: frames from the T-pose to the held pose
STATIC_FRAMES = 30    # then frames to settle
# (solver quality, collision quality) per preset
PRESETS = {"FAST": (5, 6), "FINAL": (12, 6)}
# rough cost, seconds per frame, only for the estimate shown in the panel
SECONDS_PER_FRAME = {"FAST": 0.3, "FINAL": 0.6}



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


OLD_PROPS = ("Cloth_Preview", "Cloth_Stashed_Action", "Cloth_Done")


def restore_animation(rig):
    """Put back the action that a pose preview parked (stored by name on the rig)."""
    name = rig.get("_om_stash", "") or rig.get("Cloth_Stashed_Action", "")   # old name from versions before 1.3
    for k in OLD_PROPS:
        if k in rig:
            del rig[k]
    if name and name in bpy.data.actions:
        if rig.animation_data is None:
            rig.animation_data_create()
        rig.animation_data.action = bpy.data.actions[name]
    rig["_om_stash"] = ""
    rig["_om_preview"] = 0


def _rest_keys(rig, frame, added):
    for pb in rig.pose.bones:
        if not pb.name.startswith("c_"):
            continue
        pb.location = (0, 0, 0)
        pb.rotation_quaternion = (1, 0, 0, 0)
        pb.rotation_euler = (0, 0, 0)
        pb.scale = (1, 1, 1)
        rp = "rotation_quaternion" if pb.rotation_mode == "QUATERNION" else "rotation_euler"
        for path in ("location", rp, "scale"):
            pb.keyframe_insert(path, frame=frame)
            added.append((pb, path, frame))


class Job:
    """One cloth run: prepare() sets everything up, step() simulates one frame, finish() puts the scene back."""

    def __init__(self, context, rig, pose_only, final):
        sc = context.scene
        self.sc, self.rig, self.pose_only = sc, rig, pose_only
        self.mods = cloth_mods()
        ad = rig.animation_data
        self.act0 = ad.action if ad else None
        self.had_action = self.act0 is not None
        if not pose_only and rig.get("_om_stash", ""):
            restore_animation(rig)
            ad = rig.animation_data
            self.act0 = ad.action if ad else None
            self.had_action = self.act0 is not None
        if pose_only and self.had_action:
            sc.frame_set(sc.frame_current)          # make sure the pose of this frame is evaluated
        self.static = pose_only or action_first_frame(rig) is None
        self.keep_range = (sc.frame_start, sc.frame_end)
        self.keep_frame = sc.frame_current
        start, end = sc.frame_start, sc.frame_end
        if self.static:
            end = start + STATIC_PRE + STATIC_FRAMES
            sc.frame_end = end
        self.pre = start if self.static else start - PREROLL
        self.end = end
        self.frames = list(range(self.pre, end + 1))
        self.added, self.saved, self.props = [], {}, {}
        q, cq = PRESETS["FINAL" if final else "FAST"]
        for o, m in self.mods:
            with context.temp_override(object=o, point_cache=m.point_cache):
                bpy.ops.ptcache.free_bake()
            m.settings.quality = q
            m.collision_settings.collision_quality = cq
            m.point_cache.frame_start, m.point_cache.frame_end = self.pre, end
        rig["Cloth_Sim"] = 1
        first = action_first_frame(rig)
        if self.static:
            for pb in rig.pose.bones:
                if pb.name.startswith("c_"):
                    self.saved[pb.name] = (tuple(pb.location), tuple(pb.rotation_quaternion), tuple(pb.rotation_euler), tuple(pb.scale))
                    self.props[pb.name] = {k: pb[k] for k in pb.keys() if isinstance(pb[k], (int, float)) and not k.startswith("_")}
            if self.had_action:                      # keys exist: park the animation, work on a temporary action
                ad.action = None
        if self.static or (first is not None and first > self.pre):
            sc.frame_set(self.pre)
            _rest_keys(rig, self.pre, self.added)
            if self.static:                          # the pose the animator set, reached STATIC_PRE frames later and held
                for pb in rig.pose.bones:
                    if pb.name in self.saved:
                        pb.location, pb.rotation_quaternion, pb.rotation_euler, pb.scale = self.saved[pb.name]
                        for k, v in self.props[pb.name].items():
                            pb[k] = v
                        rp = "rotation_quaternion" if pb.rotation_mode == "QUATERNION" else "rotation_euler"
                        for path in ("location", rp, "scale"):
                            pb.keyframe_insert(path, frame=self.pre + STATIC_PRE)
                            self.added.append((pb, path, self.pre + STATIC_PRE))

    def bake(self):
        self.sc.frame_start = self.pre
        bpy.ops.ptcache.bake_all(bake=True)

    def finish(self):
        sc, rig = self.sc, self.rig
        sc.frame_start, sc.frame_end = self.keep_range
        for pb, path, fr in self.added:
            try:
                pb.keyframe_delete(path, frame=fr)
            except RuntimeError:
                pass
        if self.static:
            if rig.animation_data and rig.animation_data.action and rig.animation_data.action is not self.act0:
                tmp = rig.animation_data.action
                rig.animation_data.action = None
                if tmp.users == 0:
                    bpy.data.actions.remove(tmp)
            if self.had_action:
                if self.pose_only:                   # park the animation so the previewed pose stays visible
                    self.act0.use_fake_user = True
                    rig["_om_stash"] = self.act0.name
                else:
                    rig.animation_data.action = self.act0
            for pb in rig.pose.bones:
                if pb.name in self.saved:
                    pb.location, pb.rotation_quaternion, pb.rotation_euler, pb.scale = self.saved[pb.name]
                    for k, v in self.props[pb.name].items():
                        pb[k] = v
        sc.frame_set(self.end if self.static else self.keep_frame)
        rig["_om_preview"] = 1 if (self.pose_only and self.had_action) else 0


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
    bl_description = "Chạy vải cho đoạn Frame Start đến End (tự thêm 20 frame đệm từ tư thế nghỉ). Sửa animation thì chạy lại"
    pose_only: bpy.props.BoolProperty(name="Chỉ xem dáng hiện tại", default=False, options={"SKIP_SAVE"})

    def execute(self, context):
        rig = find_rig(context)
        if rig is None:
            self.report({"ERROR"}, "Không tìm thấy rig có thuộc tính Cloth_Sim")
            return {"CANCELLED"}
        if not cloth_mods():
            self.report({"ERROR"}, "Không có áo nào dùng Cloth trong file này")
            return {"CANCELLED"}
        job = Job(context, rig, self.pose_only, context.scene.om_cloth_final)
        try:
            job.bake()
        finally:
            job.finish()
        ok = all(m.point_cache.is_baked for _, m in job.mods)
        self.report({"INFO"} if ok else {"WARNING"}, f"Đã chạy vải {job.pre} → {job.end}" if ok else "Chưa chạy xong, thử lại")
        return {"FINISHED"}


class OM_OT_clear(bpy.types.Operator):
    bl_idname = "om_cloth.clear"
    bl_label = "Xoá bake"
    bl_description = "Xoá vải đã tính"

    def execute(self, context):
        for o, m in cloth_mods():
            with context.temp_override(object=o, point_cache=m.point_cache):
                bpy.ops.ptcache.free_bake()
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
        done = bool(mods) and all(m.point_cache.is_baked for _, m in mods)
        sim_on = float(rig.get("Cloth_Sim", 0)) > 0.5
        box = lay.box()
        box.label(text="Vải thật: BẬT" if sim_on else "Vải thật: TẮT (tạo dáng)", icon="PHYSICS" if sim_on else "ARMATURE_DATA")
        box.label(text="Đã tính" if done else "Chưa tính", icon="CHECKMARK" if done else "DOT")
        col = lay.column(align=True)
        col.scale_y = 2.0
        col.operator("om_cloth.run", text="Chạy vải", icon="PLAY")
        if rig.get("_om_preview", 0):
            lay.label(text="Đang xem dáng: animation tạm tắt. Bấm Chạy vải để quay lại", icon="ERROR")
        op = lay.operator("om_cloth.run", text="Thử dáng", icon="POSE_HLT")
        op.pose_only = True
        lay.prop(sc, "om_cloth_final", text="Chất lượng đẹp (chậm hơn)")
        row = lay.row(align=True)
        row.operator("om_cloth.pose", text="Tắt vải (tạo dáng)", icon="POSE_HLT")
        row.operator("om_cloth.clear", text="Xoá bake", icon="TRASH")
        static = action_first_frame(rig) is None
        frames = STATIC_PRE + STATIC_FRAMES + 1 if static else sc.frame_end - sc.frame_start + 1 + PREROLL
        q = "FINAL" if sc.om_cloth_final else "FAST"
        if static:
            lay.label(text="Chưa có key: vải giữ dáng hiện tại")
        else:
            lay.label(text=f"Đoạn: {sc.frame_start} đến {sc.frame_end} (+{PREROLL} frame đệm)")
        lay.label(text=f"Ước tính: {frames * SECONDS_PER_FRAME[q] / 60:.1f} phút")


CLASSES = (OM_OT_pose, OM_OT_run, OM_OT_clear, OM_PT_cloth)


def register():
    bpy.types.Scene.om_cloth_final = bpy.props.BoolProperty(name="Chất lượng đẹp", default=True)
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
    if hasattr(bpy.types.Scene, "om_cloth_final"):
        del bpy.types.Scene.om_cloth_final
