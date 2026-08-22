# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ##### END GPL LICENSE BLOCK #####

# <pep8 compliant>

import os
import bpy
from string import Template
from bpy_extras import anim_utils

from . import shared as shared
from .PyCoD import xanim as XAnim


class CustomTemplate(Template):
    delimiter = '%'
    idpattern = r'[a-z][a-z0-9]*'

    def format(self, action, base, number):
        args = {'d': number, 'number': number,
                's': action, 'action': action,
                'b': base,   'base': base}
        return self.substitute(args)


def get_action_fcurves(action, ob=None):
    '''
    Returns a list of all fcurves for the given action.

    Blender 4.4+ introduced slotted/layered actions, and Blender 5.0 removed
    the old direct `action.fcurves` shortcut for non-legacy actions. This
    handles both the old (legacy) and new (slotted) action layouts so the
    rest of the exporter doesn't need to care which one it's dealing with.
    '''
    if action is None:
        return []

    # Legacy actions (created pre-4.4, or empty actions) still expose
    # .fcurves directly.
    is_legacy = getattr(action, 'is_action_legacy', True)
    if is_legacy:
        return list(action.fcurves)

    # Slotted action: fcurves live inside a channelbag tied to a specific
    # slot. Prefer the slot the object is actually subscribed to; fall back
    # to the action's first slot if that isn't available.
    slot = None
    if ob is not None and ob.animation_data is not None:
        slot = ob.animation_data.action_slot
    if slot is None and action.slots:
        slot = action.slots[0]
    if slot is None:
        return []

    channelbag = anim_utils.action_get_channelbag_for_slot(action, slot)
    return list(channelbag.fcurves) if channelbag else []


def calc_frame_range(action, ob=None):
    '''
    action.frame_range is inaccurate for actions with 0 or 1 keyframe(s)
    This function returns the real (inclusive) frame range for a given action
    '''
    fcurves = get_action_fcurves(action, ob)
    if len(fcurves) == 0:
        return (0, 0)
    keys = [fcurve.keyframe_points for fcurve in fcurves]
    points = [point for keyframe_points in keys for point in keyframe_points]
    frames = [point.co[0] for point in points]
    return (min(frames), max(frames))


def collect_notes(markers, frame_start, frame_end):
    seen = []
    for m in markers:
        if frame_start <= m.frame <= frame_end:
            seen.append((m.frame - frame_start, m.name))
    seen.sort(key=lambda x: x[0])
    return [XAnim.Note(frame, name) for frame, name in seen]


def export_action(self, context, progress, action,
                  filepath,
                  target_format,
                  global_scale=1.0,
                  framerate=30,
                  frame_range=None,
                  use_selection=False,
                  use_notetracks=False,
                  use_notetrack_file=False,
                  use_notetrack_mode='ACTION'
                  ):

    ob = context.object

    anim = XAnim.Anim()
    anim.version = 3
    anim.framerate = framerate

    if use_selection:
        pose_bones = context.selected_pose_bones
    else:
        pose_bones = ob.pose.bones

    for bone in pose_bones:
        anim.parts.append(XAnim.PartInfo(bone.name))

    if frame_range is None:
        frame_range = calc_frame_range(action, ob)

    frame_start = int(frame_range[0])
    frame_end = int(frame_range[1])

    for frame_number in range(frame_start, frame_end + 1):
        context.scene.frame_set(frame_number)
        frame = XAnim.Frame(frame_number - frame_start)
        for bone in pose_bones:
            offset = tuple(bone.head * global_scale)
            m = bone.matrix.to_3x3().transposed()
            matrix = [tuple(v) for v in m]
            part = XAnim.FramePart(offset, matrix)
            frame.parts.append(part)
        anim.frames.append(frame)

    if use_notetracks:
        if use_notetrack_mode == 'SCENE':
            markers = context.scene.timeline_markers
        elif use_notetrack_mode == 'ACTION':
            markers = action.pose_markers
            # If the action has no pose markers, fall back to scene timeline
            # markers so the user isn't silently left with an empty notetrack.
            if len(markers) == 0:
                markers = context.scene.timeline_markers
        else:
            markers = []

        anim.notes = collect_notes(markers, frame_start, frame_end)

    header_msg = shared.get_metadata_string(filepath)
    if target_format == 'XANIM_BIN':
        anim.WriteFile_Bin(filepath,
                           header_message=header_msg)
    else:
        anim.WriteFile_Raw(filepath,
                           header_message=header_msg,
                           embed_notes=(not use_notetrack_file))
    return


def save(self, context, filepath="",
         target_format='XANIM_EXPORT',
         use_selection=False,
         global_scale=1.0,
         apply_unit_scale=False,
         use_all_actions=False,
         filename_format="%action",
         use_notetracks=True,
         use_notetrack_mode='ACTION',
         use_notetrack_file=False,
         use_notetrack_format='1',
         use_frame_range_mode='ACTION',
         frame_start=1,
         frame_end=250,
         use_custom_framerate=False,
         use_framerate=30
         ):

    if not use_notetracks:
        use_notetrack_file = False

    if apply_unit_scale:
        global_scale /= shared.calculate_unit_scale_factor(context.scene)

    ob = bpy.context.object
    if ob.type != 'ARMATURE':
        return "An armature must be selected!"

    if ob.animation_data is None:
        return "The selected armature has no animation data!"

    progress = None

    actions = []
    if use_all_actions:
        actions = bpy.data.actions
    else:
        actions = [ob.animation_data.action]

    frame_original = context.scene.frame_current
    action_original = ob.animation_data.action

    if not use_custom_framerate:
        framerate = context.scene.render.fps
    else:
        framerate = use_framerate

    if use_frame_range_mode == 'SCENE':
        frame_range = (context.scene.frame_start, context.scene.frame_end)
    elif use_frame_range_mode == 'CUSTOM':
        frame_range = (frame_start, frame_end)
    else:
        frame_range = None

    filename_format = CustomTemplate(filename_format)
    path = os.path.dirname(filepath) + os.sep
    basename, ext = os.path.splitext(os.path.basename(filepath))

    for index, action in enumerate(actions):
        if use_all_actions:
            filename = filename_format.format(action.name, basename, index)
            filepath = path + filename + "." + target_format
            ob.animation_data.action = action
        export_action(self, context, progress, action, filepath,
                      target_format,
                      global_scale,
                      framerate,
                      frame_range,
                      use_selection,
                      use_notetracks,
                      use_notetrack_file,
                      use_notetrack_mode)

    ob.animation_data.action = action_original
    context.scene.frame_set(frame_original)
