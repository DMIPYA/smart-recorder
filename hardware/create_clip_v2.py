"""Build the editable Blender version of the clip-v2 assembly and printable parts."""

from math import cos, pi, sin
from pathlib import Path
import struct

import bpy
from mathutils import Matrix, Vector


def material(name, hex_color, metallic=0):
    def linear(channel):
        srgb = int(channel, 16) / 255
        return srgb / 12.92 if srgb <= 0.04045 else ((srgb + 0.055) / 1.055) ** 2.4

    color = (*[linear(hex_color[i:i + 2]) for i in (0, 2, 4)], 1)
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = color
    mat.use_nodes = True
    shader = mat.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = color
    shader.inputs["Metallic"].default_value = metallic
    shader.inputs["Roughness"].default_value = 0.46
    return mat


def prism(name, outline, bottom, top):
    if sum(x * v - u * y for (x, y), (u, v) in
           zip(outline, outline[1:] + outline[:1])) < 0:
        outline = list(reversed(outline))
    count = len(outline)
    vertices = [(x, y, z) for z in (bottom, top) for x, y in outline]
    faces = [tuple(reversed(range(count))), tuple(range(count, 2 * count))]
    faces.extend(
        (i, (i + 1) % count, (i + 1) % count + count, i + count)
        for i in range(count)
    )
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    return obj


def rounded_plate(name, width, length, radius, bottom, top):
    centers = (
        (width / 2 - radius, length / 2 - radius),
        (-width / 2 + radius, length / 2 - radius),
        (-width / 2 + radius, -length / 2 + radius),
        (width / 2 - radius, -length / 2 + radius),
    )
    outline = []
    for quarter, (x, y) in enumerate(centers):
        for step in range(10):
            angle = (quarter + step / 9) * pi / 2
            outline.append((x + radius * cos(angle), y + radius * sin(angle)))
    return prism(name, outline, bottom, top)


def box(name, center, size):
    x, y, z = center
    a, b, c = (side / 2 for side in size)
    outline = [(-a, -b), (a, -b), (a, b), (-a, b)]
    obj = prism(name, outline, -c, c)
    obj.location = (x, y, z)
    return obj


def cylinder(name, x, y, bottom, top, diameter):
    radius = diameter / 2
    outline = [(x + radius * cos(i * pi / 18), y + radius * sin(i * pi / 18))
               for i in range(36)]
    return prism(name, outline, bottom, top)


def finish(obj, mat):
    obj.data.materials.append(mat)
    return obj


def boolean(target, tool, operation="DIFFERENCE"):
    bpy.context.view_layer.objects.active = target
    modifier = target.modifiers.new(tool.name, "BOOLEAN")
    modifier.operation = operation
    modifier.solver = "EXACT"
    modifier.object = tool
    bpy.ops.object.modifier_apply(modifier=modifier.name)
    bpy.data.objects.remove(tool, do_unlink=True)


def join_components(main, *children):
    bpy.ops.object.select_all(action="DESELECT")
    bpy.context.view_layer.objects.active = main
    for component in (main, *children):
        component.select_set(True)
    bpy.ops.object.join()
    main.select_set(False)


def move_at(obj, frame, location):
    obj.location = location
    obj.keyframe_insert(data_path="location", frame=frame)


def stl(obj, name, orientation=None):
    """Write triangulated local coordinates in millimetres, flat side at Z=0."""
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    mesh.calc_loop_triangles()
    coordinates = [orientation(v.co) if orientation else v.co for v in mesh.vertices]
    bottom = min(v.z for v in coordinates)
    with (output / name).open("wb") as file:
        file.write(b"Smart Recorder V2 - millimetres".ljust(80, b" "))
        file.write(struct.pack("<I", len(mesh.loop_triangles)))
        for tri in mesh.loop_triangles:
            normal = orientation(tri.normal) if orientation else tri.normal
            coords = [part for i in tri.vertices for part in
                      (*coordinates[i].xy, coordinates[i].z - bottom)]
            file.write(struct.pack("<12fH", *normal, *coords, 0))
    evaluated.to_mesh_clear()


bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
root = Path(__file__).parent
output = root / "print-v2"
output.mkdir(exist_ok=True)
scene = bpy.context.scene
scene.name = "Smart Recorder V2"
scene.unit_settings.system = "METRIC"
scene.unit_settings.scale_length = 0.001
scene.unit_settings.length_unit = "MILLIMETERS"
scene.frame_start, scene.frame_end = 1, 60

shell_mat = material("Graphite", "222F3C")
lid_mat = material("Deep teal", "1D5353")
esp_mat = material("ESP32 PCB", "244550")
sd_mat = material("microSD PCB", "275F9B")
mic_mat = material("Microphone PCB", "825297")
retainer_mat = material("Retention arms", "EEA453")
metal = material("Connector and screws", "AEBFCC", 0.55)
gold = material("Contacts", "D2A450", 0.45)
dark = material("Electronics and controls", "18252D")
soft = material("Silicone pads", "6F9294")
light = material("Frosted light pipe", "C5E4E6")
wire_power = material("Supply wires", "C25348")
wire_other = material("Signal wires", "4E809A")

width, length, seam, lid_t = 64, 70, 16.6, 2.0
floor_t, pcb_bottom, pcb_thickness, xy_play = 2.0, 3.6, 1.55, 0.35
pcb_top = pcb_bottom + pcb_thickness
clamp_bottom = pcb_top + 0.35
fasteners = [(-27.5, -30.3), (-27.5, 30.3), (27.5, -30.3), (27.5, 30.3)]
usb_x, usb_z = -13, 6.45
sd_y, sd_z = -11, 6.55
mic_x, mic_y = 13.2, 24.5
boot_x, rst_x, button_y = -18.1, -7.9, 26
led_x, led_y = -13, 16.0

base = finish(rounded_plate("01 · Base | internal mounting", width, length, 7, 0, seam), shell_mat)
boolean(base, rounded_plate("Cavity", 60, 66, 5, floor_t, seam + 1))
eye = rounded_plate("Integrated cord eye", 14, 12, 4, 0.16, 6)
eye.location = (3, 39, 0)
boolean(base, eye, "UNION")
boolean(base, cylinder("Cord Ø4.5", 3, 39.4, -0.5, 6.5, 4.5))
for x, y in fasteners:
    boolean(base, cylinder("Lid boss", x, y, floor_t - 0.25, seam - 0.14, 7.0), "UNION")
    boolean(base, cylinder("Lid M2 insert", x, y, seam - 3.45, seam + 1, 3.1))
    boolean(base, cylinder("Lid screw clearance", x, y, seam - 5.4, seam - 3.0, 2.2))

# x, y, PCB width, PCB length, clamp axis, position along the opposite axis
boards = {
    "ESP32": (-13, 21.1, 18, 22.52, "X", 18.8),
    "MAX4466": (13.2, 19.8, 13.8, 20.8, "X", 24.5),
    "microSD": (6, sd_y, 42, 24, "Y", 8),
}


def base_block(name, x, y, width, length, top, bottom=floor_t - 0.14):
    block = box(name, (x, y, (bottom + top) / 2), (width, length, top - bottom))
    boolean(base, block, "UNION")


for name, (x, y, w, h, axis, across) in boards.items():
    # Support corners on bare PCB edges. The centre is open for solder joints.
    for a in (-1, 1):
        for b in (-1, 1):
            sx, sy = x + a * (w / 2 - 2.4), y + b * (h / 2 - 0.8)
            base_block(name + " corner seat", sx, sy, 3.4, 1.6, pcb_bottom)
            bx = x + a * (w / 2 + xy_play + 0.55)
            base_block(name + " X stop", bx, sy, 1.1, 2.6, 4.85)
            # There is a clear end stop on either side of each connector.
            by = y + b * (h / 2 + xy_play + 0.55)
            if name != "ESP32" or b != 1:
                base_block(name + " Y stop", sx, by, 2.7, 1.1, 4.85)
            else:
                base_block(name + " USB shoulder", sx, y + h / 2 - 0.27,
                           2.4, 0.62, pcb_bottom)

# A narrow top fence captures the PCB end while leaving the USB plug accessible.
for x in (usb_x - 7.9, usb_x + 7.9):
    base_block("ESP32 top fence", x, 33.09, 2.2, 1.0, 4.85)

# Each clamp stands on a fixed-height post, so hand tightening cannot flex the PCB.
clamps = []
for name, (x, y, w, h, axis, across) in boards.items():
    edge = (w if axis == "X" else h) / 2
    center = x if axis == "X" else y
    for side in (-1, 1):
        post = center + side * (edge + 3.65)
        contact = center + side * (edge - 0.3)
        px, py = (post, across) if axis == "X" else (across, post)
        cx, cy = (contact, across) if axis == "X" else (across, contact)
        # Broad root is keyed against a pair of small guide ribs.
        base_block(name + " clamp post", px, py, 5.6, 5.6, clamp_bottom)
        boolean(base, cylinder("Clamp M2 pilot", px, py, 2.05, clamp_bottom + 0.3, 1.62))
        for offset in (-3.08, 3.08):
            gx, gy = (px, py + offset) if axis == "X" else (px + offset, py)
            gw, gh = (3.5, 0.7) if axis == "X" else (0.7, 3.5)
            base_block(name + " clamp anti-rotation rib", gx, gy, gw, gh, clamp_bottom + 0.9)
        clamps.append((name, side, px, py, cx, cy, axis))

# Rear mounting for a removable clip. The two blind screw pockets are isolated from the electronics.
clip_mount_y = (-28.8, 5.4)
for y in clip_mount_y:
    base_block("Rear clip boss", -2, y, 7, 7, 5.65)
    boolean(base, cylinder("Rear clip insert", -2, y, -0.2, 3.25, 3.1))
    boolean(base, cylinder("Rear clip screw tip relief", -2, y, 3.0, 5.1, 2.2))

# Recessed routing lane between the mic/ESP32 and the SD board.
for x in (-19, 19):
    for y in (5.0, 7.0):
        base_block("Wire channel guide", x, y, 4.2, 0.9, 3.4)

boolean(base, box("USB plug opening", (usb_x, 35, usb_z), (13.2, 12, 7.2)))
boolean(base, box("SD card side slot", (32, sd_y, sd_z), (12, 13.4, 4.4)))
boolean(base, box("SD fingertip relief", (32, sd_y, sd_z + 1.3), (7.6, 20, 7.8)))
boolean(base, cylinder("Rear access to gain trim", 13.2, 17.2, -0.3, 2.2, 3.3))

lid = finish(rounded_plate("02 · Lid | vent + controls", width, length, 7, 0, lid_t), lid_mat)
# This locator skirt gives the screw joint a positive registration in X and Y.
for x in (-28.6, 28.6):
    boolean(lid, box("Lid locating lip", (x, 14, -0.57), (1.3, 11, 1.5)), "UNION")
for x in (-11, 11):
    boolean(lid, box("Lid locating lip", (x, -32, -0.57), (10, 1.3, 1.5)), "UNION")
for x, y in fasteners:
    boolean(lid, cylinder("Lid M2 clearance", x, y, -0.1, 2.2, 2.3))
    boolean(lid, cylinder("Lid screw counterbore", x, y, 0.88, 2.2, 4.15))
for i, (dx, dy) in enumerate(((0, 0), (-2.2, 0), (2.2, 0), (0, -2.2), (0, 2.2))):
    boolean(lid, cylinder(f"Microphone grille {i}", mic_x + dx, mic_y + dy, -0.1, 2.2, 1.35))
chimney = cylinder("Acoustic duct", mic_x, mic_y, -3.9, 0.15, 12)
boolean(chimney, cylinder("Duct bore", mic_x, mic_y, -4.1, 0.30, 8.5))
boolean(lid, chimney, "UNION")
boolean(lid, cylinder("BOOT plunger window", boot_x, button_y, -0.2, 2.2, 6.1))
boolean(lid, cylinder("Recessed reset pin", rst_x, button_y, -0.2, 2.2, 2.3))
boolean(lid, cylinder("Status light window", led_x, led_y, -0.2, 2.2, 2.7))

# The plunger feeds through the lid from the inside. Its collar captures it.
boot = finish(cylinder("03 · BOOT plunger", 0, 0, -0.4, 2.25, 5.7), retainer_mat)
boolean(boot, cylinder("Captive collar", 0, 0, -1.15, -0.23, 8.1), "UNION")
boolean(boot, cylinder("Plunger stem", 0, 0, -9.18, -1.01, 2.7), "UNION")
boolean(boot, box("Button grip", (0, 0, 2.23), (2.9, 0.6, 0.28)), "DIFFERENCE")

led = finish(cylinder("04 · Light pipe | clear PETG", 0, 0, -0.12, 1.97, 2.50), light)
boolean(led, cylinder("Light pipe flange", 0, 0, -0.9, -0.02, 4.4), "UNION")
boolean(led, cylinder("Light pipe stem", 0, 0, -10.15, -0.77, 2.4), "UNION")

gasket = finish(cylinder("Acoustic seal | 1.5 mm foam ring", mic_x, mic_y, 11.25, 12.75, 11.6), soft)
boolean(gasket, cylinder("Seal bore", mic_x, mic_y, 11.1, 12.9, 8.4))


def hardware_screw(name, shaft_length, head_h=1.25, head_d=3.8):
    screw = finish(cylinder(name, 0, 0, -shaft_length, 0, 2.0), metal)
    boolean(screw, cylinder("Screw head", 0, 0, -0.1, head_h, head_d), "UNION")
    boolean(screw, box("Screw head slot", (0, 0, head_h - 0.05), (2.45, .55, .5)))
    return screw


for index, (x, y) in enumerate(fasteners, 1):
    screw = hardware_screw(f"Lid · M2×6 · {index}", 6)
    move_at(screw, 1, (x, y, seam + 0.88))
    move_at(screw, 60, (x + 91, y, seam + 30))

for name, side, px, py, cx, cy, axis in clamps:
    size = (7.9, 5.1, 2.05) if axis == "X" else (5.1, 7.9, 2.05)
    x, y = (px * 0.58 + cx * 0.42, py * 0.58 + cy * 0.42)
    label = name + (" A" if side == -1 else " B")
    arm = finish(box("Clamp · " + label, (x, y, clamp_bottom + size[2] / 2), size), retainer_mat)
    boolean(arm, cylinder("M2 clearance", px, py, clamp_bottom - 0.3, clamp_bottom + 2.4, 2.3))
    pad = finish(box("Silicone pad · " + label, (cx, cy, pcb_top + .18),
                     (1.65, 4.4, .4) if axis == "X" else (4.4, 1.65, .4)), soft)
    screw = hardware_screw("Clamp · M2×5 · " + label, 5, 1.4)
    for part in (arm, pad, screw):
        if part == screw:
            part.location = (px, py, clamp_bottom + size[2])
        home = part.location.copy()
        move_at(part, 1, home)
        move_at(part, 60, home + Vector((0, 0, 18 if part != screw else 22)))
    if name == "ESP32" and side == 1:
        stl(arm, "pcb-clamp-print-6.stl")


def pcb(name, key, mat):
    x, y, w, h, _, _ = boards[key]
    return finish(box(name, (x, y, pcb_bottom + pcb_thickness / 2),
                      (w, h, pcb_thickness)), mat)


esp = pcb("05 · ESP32-C3 SuperMini", "ESP32", esp_mat)
esp_parts = [
    finish(box("USB-C receptacle", (usb_x, 32.15, usb_z), (8.9, 7.2, 2.8)), metal),
    finish(box("SoC", (-13, 20, 5.77), (6, 6, 1.2)), dark),
    finish(box("Ceramic antenna", (-13, 10.95, 5.6), (5.2, 1.2, .87)), gold),
    finish(box("Status LED", (led_x, led_y, 5.55), (1.6, 1.3, 0.65)), light),
]
for x in (boot_x, rst_x):
    esp_parts.append(finish(box("Tactile switch", (x, button_y, 6.16), (3.4, 3.4, 2.0)), metal))
    esp_parts.append(finish(cylinder("Switch dome", x, button_y, 7.13, 7.27, 1.6), dark))
for x in (-20.62, -5.38):
    for i in range(8):
        esp_parts.append(finish(cylinder("GPIO pad", x, 12.2 + 2.54 * i, 5.08, 5.25, 1.1), gold))
join_components(esp, *esp_parts)

mic = pcb("06 · GY-MAX4466", "MAX4466", mic_mat)
capsule = finish(cylinder("Capsule", mic_x, mic_y, 5.14, 11.28, 9.7), metal)
boolean(capsule, cylinder("Sound inlet", mic_x, mic_y, 10.99, 11.39, 1.6))
mic_parts = [
    capsule, finish(box("Gain trim | underside", (13.2, 17.2, 3.04), (3.4, 3.4, 1.08)), gold),
]
for x in (10.66, 13.2, 15.74):
    mic_parts.append(finish(cylinder("Solder pad", x, 10.4, 5.12, 5.24, 1.05), gold))
join_components(mic, *mic_parts)

sd = pcb("07 · microSD SPI", "microSD", sd_mat)
socket = finish(box("Card socket", (20.15, sd_y, 6.47), (15.7, 14.2, 2.62)), metal)
boolean(socket, box("Card channel", (22.7, sd_y, sd_z), (15.9, 11.4, 1.28)))
sd_parts = [socket, finish(box("SPI buffer", (-1, -10, 5.83), (6, 4.2, 1.32)), dark),
            finish(box("3.3 V regulator", (7.6, -15.3, 5.87), (5.1, 3.2, 1.4)), dark)]
for y in (-17.35, -14.81, -12.27, -9.73, -7.19, -4.65):
    sd_parts.append(finish(cylinder("Direct solder point", -12.4, y, 5.15, 5.45, 1.05), gold))
join_components(sd, *sd_parts)
card = finish(box("08 · microSD card", (22.9, sd_y, sd_z), (15, 11, 1.0)), dark)


def wire(name, points, mat, thickness=.36):
    curve = bpy.data.curves.new(name, "CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = 12
    curve.bevel_depth = thickness
    curve.bevel_resolution = 3
    spline = curve.splines.new("BEZIER")
    spline.bezier_points.add(len(points) - 1)
    for control, point in zip(spline.bezier_points, points):
        control.co = point
        control.handle_left_type = control.handle_right_type = "AUTO"
    obj = bpy.data.objects.new(name, curve)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(mat)


for i in range(3):
    wire("Mic wire route · " + str(i + 1),
         [(10.66 + i * 2.54, 10.4, 5.6), (10 + i, 7.3, 5.8),
          (1.5 + i, 7.5 - .65 * i, 6.8), (-5.38, 12.2 + i * 2.54, 5.6)],
         (wire_other, dark, wire_power)[i])
for i in range(6):
    wire("microSD wire route · " + str(i + 1),
         [(-12.4, -17.35 + i * 2.54, 5.6), (-17.7 - .87 * i, -18 + i * 2.54, 6.0),
          (-22.5 - .83 * i, -3 + .75 * i, 5.6), (-24.5 - .6 * i, 6.2 + .55 * i, 5.5),
          (-20.62, 12.2 + 2.54 * i, 5.6)],
         (dark, wire_power, wire_other, wire_other, wire_other, wire_other)[i])

# Clip profile is extruded sideways so it can print on a long edge, without a bridge.
clip_outline = [(-32, -.1), (11, -.1), (11, -4.8), (7, -7.7), (-22, -7.15),
                (-25, -5.7), (-27, -3.4), (-25.4, -3.3), (-22, -5.2),
                (6.5, -5.55), (8.2, -4), (8.2, -1.95), (-32, -1.95)]
clip = finish(prism("09 · Removable clothing clip", clip_outline, -7, 7), shell_mat)
for vertex in clip.data.vertices:
    u, v, w = vertex.co
    vertex.co = (w - 2, u, v)
# Axis permutation above is cyclic, so the face winding remains correct.
clip.data.update()
for y in clip_mount_y:
    boolean(clip, cylinder("Clip screw M2", -2, y, -2.8, 0.3, 2.3))
    boolean(clip, cylinder("Clip screw head pocket", -2, y, -2.5, -0.91, 3.9))
    boolean(clip, cylinder("Driver access", -2, y, -8.3, -3.25, 5.2))
    fastener = hardware_screw("Rear · M2×5", 5)
    fastener.rotation_euler.x = pi
    fastener.location = (-2, y, -.92)

move_at(lid, 1, (0, 0, seam))
move_at(lid, 60, (91, 0, seam + 21))
move_at(boot, 1, (boot_x, button_y, seam))
move_at(boot, 60, (91 + boot_x, button_y, seam + 17))
move_at(led, 1, (led_x, led_y, seam))
move_at(led, 60, (91 + led_x, led_y, seam + 17))
move_at(card, 1, card.location.copy())
move_at(card, 60, (41, sd_y, sd_z))
move_at(clip, 1, (0, 0, 0))
move_at(clip, 60, (0, 0, -9))
scene.timeline_markers.new("ASSEMBLED", frame=1)
scene.timeline_markers.new("EXPLODED", frame=60)
scene["Note"] = "Nominal board dimensions. Measure USB, microSD, BOOT and LED positions before printing."
scene["Fasteners"] = "Lid M2x6 + inserts, PCB clamps M2x5 direct into PETG pilot holes, rear clip M2x5 + inserts."
scene["Board retention"] = "Edge seats, end stops and two serviceable screw clamps with silicone pads per board."

stl(base, "base.stl")
stl(lid, "lid.stl", lambda v: Vector((-v.x, v.y, -v.z)))
stl(boot, "boot-plunger.stl", lambda v: Vector((-v.x, v.y, -v.z)))
stl(led, "light-pipe.stl", lambda v: Vector((-v.x, v.y, -v.z)))
stl(clip, "rear-clip.stl", lambda v: Vector((-v.z, v.y, v.x)))

for screen in bpy.data.screens:
    for area in screen.areas:
        if area.type == "VIEW_3D":
            view = area.spaces.active
            view.shading.color_type = "MATERIAL"
            view.shading.show_cavity = True
            view.overlay.show_overlays = False
            view.clip_start = 0.1
            view.region_3d.view_perspective = "ORTHO"
            view.region_3d.view_rotation = Vector((0.36, -0.55, 0.76)).to_track_quat("Z", "Y")
            view.region_3d.view_distance = 198
            view.region_3d.view_location = (44, 2, 18)

scene.frame_set(60)
bpy.context.preferences.filepaths.save_version = 0
bpy.ops.wm.save_as_mainfile(filepath=str(root / "clip-v2.blend"), compress=True)

scene.render.engine = "BLENDER_WORKBENCH"
scene.display.shading.light = "STUDIO"
scene.display.shading.color_type = "MATERIAL"
scene.display.shading.show_shadows = False
scene.display.shading.show_cavity = True
scene.display.shading.cavity_type = "BOTH"
scene.display.shading.show_specular_highlight = True
scene.display.shading.background_type = "WORLD"
scene.world.color = (0.25, 0.31, 0.36)
scene.render.resolution_x = 1280
scene.render.resolution_y = 880
scene.render.resolution_percentage = 100
bpy.ops.object.camera_add()
camera = bpy.context.object
scene.camera = camera
camera.data.type = "ORTHO"
camera.data.clip_end = 1000
for filename, frame, aim, direction, size in (
    ("clip-v2-internals.png", 60, (43, 2, 18), (0.25, -0.27, 0.93), 182),
    ("clip-v2-assembled.png", 1, (0, 3, 8), (0.32, 0.36, 0.87), 142),
):
    scene.frame_set(frame)
    back = Vector(direction).normalized()
    right = Vector((0, 1, 0)).cross(back).normalized()
    up = back.cross(right)
    camera.location = Vector(aim) + 210 * back
    camera.rotation_euler = Matrix((right, up, back)).transposed().to_euler()
    camera.data.ortho_scale = size
    scene.render.filepath = str(root / filename)
    bpy.ops.render.render(write_still=True)
