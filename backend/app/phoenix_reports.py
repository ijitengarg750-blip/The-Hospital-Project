from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import math
from pathlib import Path
import struct
import subprocess
import zipfile

from PIL import Image, ImageDraw, ImageFont, ImageOps
import pydicom

from .dicom_import import embedded_archives


class PhoenixReportError(ValueError):
    pass


@dataclass(frozen=True)
class PupilMeasurement:
    pupil_x: float
    pupil_y: float
    reference_x: float
    reference_y: float
    radius_pixels: float
    timestamp_ms: int
    index: int
    contour: tuple[float, ...]
    valid: bool
    scale_mm_per_pixel: float

    @property
    def diameter_mm(self) -> float:
        return 2 * self.radius_pixels * self.scale_mm_per_pixel

    @property
    def center_mm(self) -> tuple[float, float]:
        return (
            (self.reference_x - self.pupil_x) * self.scale_mm_per_pixel,
            (self.pupil_y - self.reference_y) * self.scale_mm_per_pixel,
        )


@dataclass(frozen=True)
class Pupillography:
    scale_mm_per_pixel: float
    dynamic: tuple[PupilMeasurement, ...]
    static: tuple[PupilMeasurement, ...]


@dataclass(frozen=True)
class DamageRecord:
    source_frame: int
    center: tuple[int, int]
    rows: int
    columns: int
    values: tuple[float, ...]

    def value(self, row: int, column: int) -> float:
        return self.values[row * self.columns + column]


@dataclass(frozen=True)
class TearFilmAnalysis:
    breakup_times_ms: tuple[int, ...]
    selected_record_index: int
    duration_ms: int
    record_count: int
    record_size: int
    selected: DamageRecord

    @property
    def duration_seconds(self) -> float:
        return self.duration_ms / 1000

    @property
    def positive_breakup_times(self) -> tuple[int, ...]:
        return tuple(value for value in self.breakup_times_ms if value > 0)

    @property
    def nif_but_seconds(self) -> float | None:
        values = self.positive_breakup_times
        return min(values) / 1000 if values else None

    @property
    def niavg_but_seconds(self) -> float | None:
        values = self.positive_breakup_times
        return sum(values) / len(values) / 1000 if values else None

    @property
    def cumulative_damage(self) -> list[tuple[float, float]]:
        total = len(self.breakup_times_ms)
        return [
            (value / 1000, rank * 100 / total)
            for rank, value in enumerate(sorted(self.positive_breakup_times), start=1)
        ]


def _need(data: bytes | memoryview, offset: int, size: int, label: str) -> None:
    if offset < 0 or size < 0 or offset + size > len(data):
        raise PhoenixReportError(f"Truncated {label}")


def _parse_pupil_record(data: bytes, offset: int, scale: float) -> tuple[PupilMeasurement, int]:
    fixed_size = struct.calcsize("<5fIBI")
    _need(data, offset, fixed_size, "pupillography measurement")
    pupil_x, pupil_y, reference_x, reference_y, radius, timestamp, index, contour_count = struct.unpack_from(
        "<5fIBI", data, offset
    )
    if contour_count > 4096:
        raise PhoenixReportError("Invalid pupillography contour length")
    offset += fixed_size
    contour_size = contour_count * 4
    _need(data, offset, contour_size + 1, "pupillography contour")
    contour = struct.unpack_from(f"<{contour_count}f", data, offset)
    offset += contour_size
    valid = bool(data[offset])
    return PupilMeasurement(
        pupil_x, pupil_y, reference_x, reference_y, radius, timestamp, index,
        contour, valid, scale,
    ), offset + 1


def parse_pupillography(data: bytes) -> Pupillography:
    _need(data, 0, 6, "pupillography header")
    version, scale, dynamic_count = struct.unpack_from("<BfB", data)
    if version != 1 or not math.isfinite(scale) or scale <= 0 or dynamic_count > 100:
        raise PhoenixReportError("Unsupported pupillography data")
    offset = 6
    dynamic: list[PupilMeasurement] = []
    for _ in range(dynamic_count):
        item, offset = _parse_pupil_record(data, offset, scale)
        dynamic.append(item)
    static: list[PupilMeasurement] = []
    for _ in range(3):
        _need(data, offset, 1, "static pupillography flag")
        present = data[offset]
        offset += 1
        if present:
            item, offset = _parse_pupil_record(data, offset, scale)
            static.append(item)
    if offset != len(data):
        raise PhoenixReportError("Unexpected trailing pupillography data")
    return Pupillography(scale, tuple(dynamic), tuple(static))


def parse_tear_film(data: bytes) -> TearFilmAnalysis:
    _need(data, 0, 3, "tear-film header")
    version, zone_count = struct.unpack_from("<BH", data)
    if version != 7 or zone_count < 1 or zone_count > 1024:
        raise PhoenixReportError("Unsupported tear-film data")
    offset = 3
    _need(data, offset, zone_count * 4 + 12, "tear-film summary")
    breakup_times = struct.unpack_from(f"<{zone_count}i", data, offset)
    offset += zone_count * 4
    selected_index, duration_ms, record_count, record_size = struct.unpack_from("<HIHI", data, offset)
    offset += 12
    if not record_count or selected_index >= record_count or record_size < 10:
        raise PhoenixReportError("Invalid tear-film record table")
    _need(data, offset, record_count * record_size, "tear-film records")
    selected_offset = offset + selected_index * record_size
    source_frame, center_x, center_y, rows, columns = struct.unpack_from("<5H", data, selected_offset)
    value_count = rows * columns
    if not rows or not columns or value_count > 2_000_000 or 10 + value_count * 4 != record_size:
        raise PhoenixReportError("Invalid tear-film damage grid")
    values = struct.unpack_from(f"<{value_count}f", data, selected_offset + 10)
    selected = DamageRecord(source_frame, (center_x, center_y), rows, columns, values)
    return TearFilmAnalysis(
        breakup_times, selected_index, duration_ms, record_count, record_size, selected,
    )


REPORT_SIZE = (1600, 936)
STATIC_NAMES = ("Scotopic", "Mesopic", "Photopic")
SUPPORTED_STUDIES = {"pupillography", "tear film analysis"}
MAX_REPORT_MEMBER_BYTES = 60_000_000
MAX_VIDEO_BYTES = 500_000_000


def _font(size: int, bold: bool = False):
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _eye_label(laterality: str | None) -> str:
    return "OS" if (laterality or "").upper() == "L" else "OD"


def phoenix_seconds_text(value: float | None) -> str:
    """Match Phoenix's summary display: positive measurements are cut to one decimal."""
    if value is None:
        return "Not detected"
    tenths = math.floor(value * 10 + 1e-8)
    return f"{tenths / 10:.1f} s"


def _image(data: bytes) -> Image.Image:
    try:
        image = Image.open(BytesIO(data))
        image.load()
        return image.convert("RGB")
    except OSError as exc:
        raise PhoenixReportError("Unreadable Phoenix source image") from exc


def _png(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, "PNG", optimize=True)
    return output.getvalue()


def _paste_panel(canvas: Image.Image, source: Image.Image, box: tuple[int, int, int, int]) -> None:
    canvas.paste(ImageOps.fit(source, (box[2] - box[0], box[3] - box[1]), Image.Resampling.LANCZOS), box)


def _dashed_line(draw: ImageDraw.ImageDraw, points: tuple[float, float, float, float], *, fill, width: int = 1,
                 dash: int = 5, gap: int = 5) -> None:
    x0, y0, x1, y1 = points
    length = math.hypot(x1 - x0, y1 - y0)
    if not length:
        return
    dx, dy = (x1 - x0) / length, (y1 - y0) / length
    position = 0.0
    while position < length:
        end = min(length, position + dash)
        draw.line((x0 + dx * position, y0 + dy * position, x0 + dx * end, y0 + dy * end), fill=fill, width=width)
        position += dash + gap


def _phoenix_header(draw: ImageDraw.ImageDraw, title: str) -> None:
    draw.rectangle((0, 0, REPORT_SIZE[0] - 1, 69), fill=(247, 247, 247), outline=(185, 185, 185))
    draw.text((18, 28), "File", fill=(45, 45, 45), font=_font(12))
    draw.text((56, 28), "Edit", fill=(45, 45, 45), font=_font(12))
    draw.text((REPORT_SIZE[0] / 2, 8), title, fill=(45, 45, 45), font=_font(14), anchor="ma")
    draw.text((1512, 28), "Close", fill=(45, 45, 45), font=_font(12))


def _pupil_overlay(data: bytes, measurement: PupilMeasurement, label: str, eye: str) -> Image.Image:
    image = _image(data)
    draw = ImageDraw.Draw(image)
    x, y, radius = measurement.pupil_x, measurement.pupil_y, measurement.radius_pixels
    reference_x, reference_y = measurement.reference_x, measurement.reference_y
    white = (235, 235, 235)
    _dashed_line(draw, (0, reference_y, image.width, reference_y), fill=white)
    _dashed_line(draw, (reference_x, 0, reference_x, image.height), fill=white)
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline=white, width=2)
    draw.line((reference_x - 7, reference_y, reference_x + 7, reference_y), fill=white, width=2)
    draw.line((reference_x, reference_y - 7, reference_x, reference_y + 7), fill=white, width=2)
    draw.text((image.width - 55, 8), eye, fill=(250, 245, 245), font=_font(24, True), stroke_width=2, stroke_fill=(170, 20, 20))
    draw.text((image.width - 145, image.height - 32), label, fill=white, font=_font(18), stroke_width=1, stroke_fill=(40, 40, 40))
    return image


def _chart_frame(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], title: str) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = box
    draw.text((x0, y0 - 24), title, fill=(75, 75, 75), font=_font(14))
    draw.rectangle(box, outline=(175, 175, 175), width=1)
    return x0 + 38, y0 + 14, x1 - 12, y1 - 28


def _draw_grid(draw: ImageDraw.ImageDraw, plot: tuple[int, int, int, int], horizontal: int = 4, vertical: int = 4) -> None:
    x0, y0, x1, y1 = plot
    for index in range(1, horizontal):
        y = y0 + (y1 - y0) * index / horizontal
        draw.line((x0, y, x1, y), fill=(205, 205, 205), width=1)
    for index in range(1, vertical):
        x = x0 + (x1 - x0) * index / vertical
        draw.line((x, y0, x, y1), fill=(220, 220, 220), width=1)
    draw.line((x0, y1, x1, y1), fill=(95, 95, 95), width=1)
    draw.line((x0, y0, x0, y1), fill=(95, 95, 95), width=1)


def _position_plot(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    items: tuple[PupilMeasurement, ...],
    title: str,
    *,
    legend: bool = False,
) -> None:
    plot = _chart_frame(draw, box, title)
    x0, y0, x1, y1 = plot
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    radius = min(x1 - x0, y1 - y0) * .46
    for ring in range(1, 6):
        rr = radius * ring / 5
        draw.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), outline=(205, 205, 205), width=1)
    draw.line((x0, cy, x1, cy), fill=(190, 190, 190))
    draw.line((cx, y0, cx, y1), fill=(190, 190, 190))
    colors = ((44, 116, 190), (105, 167, 225), (85, 139, 205))
    for index, item in enumerate(items):
        px = cx + item.center_mm[0] / .5 * radius
        py = cy - item.center_mm[1] / .5 * radius
        color = colors[index % len(colors)]
        draw.line((px - 7, py, px + 7, py), fill=color, width=2)
        draw.line((px, py - 7, px, py + 7), fill=color, width=2)
    axis_font = _font(10)
    draw.text(((x0 + x1) / 2, y1 + 15), "xc [mm]", fill=(65, 65, 65), font=axis_font, anchor="ma")
    draw.text((x0 - 24, (y0 + y1) / 2), "yc [mm]", fill=(65, 65, 65), font=axis_font, anchor="mm")
    draw.text((cx + 2, cy + 2), "0", fill=(80, 80, 80), font=_font(9))
    for tick in range(1, 6):
        px = cx + radius * tick / 5
        draw.text((px - 6, cy + 3), f"{tick / 10:.1f}", fill=(90, 90, 90), font=_font(8))
    if legend:
        legend_x, legend_y = x1 - 138, y0 + 2
        for index, (name, item) in enumerate(zip(STATIC_NAMES, items)):
            color = colors[index % len(colors)]
            ly = legend_y + index * 16
            draw.line((legend_x, ly + 6, legend_x + 10, ly + 6), fill=color, width=1)
            draw.line((legend_x + 5, ly + 1, legend_x + 5, ly + 11), fill=color, width=1)
            xc, yc = item.center_mm
            draw.text((legend_x + 14, ly), f"{name}: ({xc:.2f}, {yc:.2f}) mm", fill=(60, 60, 60), font=_font(9))


def render_pupillography_report(
    measurements: Pupillography,
    images: dict[str, bytes],
    *,
    laterality: str | None,
    patient_name: str,
    patient_id: str,
    acquired: str,
    birthdate: str = "",
) -> bytes:
    if len(measurements.static) != 3 or not measurements.dynamic:
        raise PhoenixReportError("Incomplete pupillography measurement set")
    eye = _eye_label(laterality)
    wanted = ["Scotopic/000", "Mesopic/000", "Photopic/000"]
    dynamic_names = sorted(name for name in images if name.replace("\\", "/").startswith("Dynamic/"))
    if any(name not in images for name in wanted) or not dynamic_names:
        raise PhoenixReportError("Incomplete pupillography image set")

    canvas = Image.new("RGB", REPORT_SIZE, "white")
    draw = ImageDraw.Draw(canvas)
    _phoenix_header(draw, f"Phoenix - {patient_id},{patient_name} - {acquired} - #1 {eye}")
    boxes = ((26, 78, 575, 490), (620, 78, 1169, 490), (26, 507, 575, 919), (620, 507, 1169, 919))
    for box, name, label, item in zip(boxes[:3], wanted, STATIC_NAMES, measurements.static):
        _paste_panel(canvas, _pupil_overlay(images[name], item, label, eye), box)
    _paste_panel(canvas, _pupil_overlay(images[dynamic_names[-1]], measurements.dynamic[-1], "Dynamic", eye), boxes[3])

    sidebar_x = 1200
    draw.text((sidebar_x + 14, 78), f"{patient_id}, {patient_name}", fill=(25, 25, 25), font=_font(15, True))
    draw.text((sidebar_x + 14, 99), patient_id, fill=(75, 75, 75), font=_font(11))
    if birthdate:
        draw.text((sidebar_x + 14, 114), f"Birthdate: {birthdate}", fill=(75, 75, 75), font=_font(11))
    draw.text((sidebar_x + 14, 143), "Pupillography", fill=(55, 55, 55), font=_font(13, True))
    draw.text((sidebar_x + 14, 160), acquired, fill=(90, 90, 90), font=_font(10))

    plot = _chart_frame(draw, (sidebar_x + 12, 193, 1584, 325), "Static pupillometry")
    _draw_grid(draw, plot, horizontal=4, vertical=3)
    x0, y0, x1, y1 = plot
    draw.text((x0 - 29, (y0 + y1) / 2), "q [mm]", fill=(55, 55, 55), font=_font(10), anchor="mm")
    for tick in (2, 4, 6, 8):
        tick_y = y1 - tick / 8 * (y1 - y0)
        draw.text((x0 - 15, tick_y), str(tick), fill=(75, 75, 75), font=_font(9), anchor="rm")
    for index, (name, item) in enumerate(zip(STATIC_NAMES, measurements.static)):
        x = x0 + (index + .5) * (x1 - x0) / 3
        y = y1 - min(item.diameter_mm, 8) / 8 * (y1 - y0)
        draw.line((x - 24, y, x + 24, y), fill=(74, 146, 220), width=2)
        value = f"{item.diameter_mm:.2f}"
        draw.text((x - draw.textlength(value, font=_font(12)) / 2, y - 20), value, fill=(65, 65, 65), font=_font(12))
        draw.text((x - draw.textlength(name, font=_font(11)) / 2, y1 + 7), name, fill=(65, 65, 65), font=_font(11))

    _position_plot(draw, (sidebar_x + 12, 347, 1584, 478), measurements.static, "", legend=True)

    plot = _chart_frame(draw, (sidebar_x + 12, 519, 1584, 650), "Dynamic pupillometry")
    _draw_grid(draw, plot, horizontal=4, vertical=4)
    x0, y0, x1, y1 = plot
    max_time = max(2000, max(item.timestamp_ms for item in measurements.dynamic))
    points = [
        (x0 + item.timestamp_ms / max_time * (x1 - x0), y1 - min(item.diameter_mm, 5) / 5 * (y1 - y0))
        for item in measurements.dynamic
    ]
    draw.line(points, fill=(110, 110, 110), width=2)
    for x, y in points:
        draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=(100, 100, 100))
    draw.text((x0 - 29, (y0 + y1) / 2), "q [mm]", fill=(55, 55, 55), font=_font(10), anchor="mm")
    for tick in range(1, 6):
        tick_y = y1 - tick / 5 * (y1 - y0)
        draw.text((x0 - 15, tick_y), str(tick), fill=(75, 75, 75), font=_font(9), anchor="rm")
    for seconds in (0, 1, 2):
        tick_x = x0 + seconds * 1000 / max_time * (x1 - x0)
        draw.text((tick_x, y1 + 5), str(seconds), fill=(75, 75, 75), font=_font(9), anchor="ma")
    draw.text(((x0 + x1) / 2, y1 + 17), "Time [s]", fill=(55, 55, 55), font=_font(10), anchor="ma")
    _position_plot(draw, (sidebar_x + 12, 674, 1584, 804), measurements.dynamic, "")
    return _png(canvas)


def _damage_color(value: float) -> tuple[int, int, int, int]:
    if value <= 0:
        return 0, 235, 0, 150
    if value < .5:
        ratio = value * 2
        return int(255 * ratio), 245, 0, 190
    ratio = (value - .5) * 2
    return 255, int(245 * (1 - ratio)), 0, 205


def _damage_overlay(base: Image.Image, record: DamageRecord) -> Image.Image:
    image = base.convert("RGBA")
    overlay = Image.new("RGBA", image.size)
    pixels = overlay.load()
    scale_x, scale_y = image.width / 640, image.height / 480
    cx, cy = record.center
    image_cx, image_cy = cx * scale_x, cy * scale_y
    image_radius = record.rows * max(scale_x, scale_y)
    for y in range(max(0, int(image_cy - image_radius)), min(image.height, math.ceil(image_cy + image_radius))):
        for x in range(max(0, int(image_cx - image_radius)), min(image.width, math.ceil(image_cx + image_radius))):
            dx, dy = x / scale_x - cx, y / scale_y - cy
            radius = int(math.hypot(dx, dy))
            if radius >= record.rows:
                continue
            angle = math.atan2(dy, dx) / (2 * math.pi) % 1
            column = int(angle * record.columns) % record.columns
            value = record.value(radius, column)
            if math.isfinite(value):
                pixels[x, y] = _damage_color(max(0.0, min(1.0, value)))
    return Image.alpha_composite(image, overlay).convert("RGB")


def _breakup_map(base: Image.Image, analysis: TearFilmAnalysis) -> Image.Image:
    image = _damage_overlay(base, analysis.selected).convert("RGBA")
    draw = ImageDraw.Draw(image)
    scale_x, scale_y = image.width / 640, image.height / 480
    source_cx, source_cy = analysis.selected.center
    cx, cy = source_cx * scale_x, source_cy * scale_y
    outer_radius = analysis.selected.rows * min(scale_x, scale_y)

    weighted_x = weighted_y = weight = 0.0
    for radius in range(analysis.selected.rows):
        for column in range(analysis.selected.columns):
            value = analysis.selected.value(radius, column)
            if math.isfinite(value) and value > 0:
                angle = 2 * math.pi * column / analysis.selected.columns
                weighted_x += math.cos(angle) * radius * value
                weighted_y += math.sin(angle) * radius * value
                weight += value
    lesion_x = cx + (weighted_x / weight * scale_x if weight else 0)
    lesion_y = cy + (weighted_y / weight * scale_y if weight else 0)
    label_offsets = ((-30, -24), (8, -20), (-28, 7), (9, 13))
    for value, (dx, dy) in zip(sorted(analysis.positive_breakup_times), label_offsets):
        text = f"{value / 1000:.1f}s"
        draw.text((lesion_x + dx, lesion_y + dy), text, fill=(235, 235, 255, 255),
                  font=_font(10, True), stroke_width=1, stroke_fill=(65, 65, 180, 255))
    draw.ellipse((cx - outer_radius, cy - outer_radius, cx + outer_radius, cy + outer_radius),
                 outline=(225, 225, 225, 255), width=2)
    return image.convert("RGB")


def _tear_graph(size: tuple[int, int], analysis: TearFilmAnalysis) -> Image.Image:
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    plot = (55, 28, size[0] - 28, size[1] - 48)
    x0, y0, x1, y1 = plot
    draw.line((x0, y1, x1, y1), fill=(20, 20, 20), width=2)
    draw.polygon(((x1, y1), (x1 - 10, y1 - 5), (x1 - 10, y1 + 5)), fill=(20, 20, 20))
    draw.line((x0, y1, x0, y0), fill=(20, 20, 20), width=2)
    draw.polygon(((x0, y0), (x0 - 5, y0 + 10), (x0 + 5, y0 + 10)), fill=(20, 20, 20))
    max_seconds = max(20, math.ceil(analysis.duration_seconds))
    last_percent = analysis.cumulative_damage[-1][1] if analysis.cumulative_damage else 0
    max_percent = max(10, math.ceil(last_percent / 10) * 10)
    percent_step = max(1, max_percent // 10)
    for second in range(max_seconds + 1):
        x = x0 + second / max_seconds * (x1 - x0)
        draw.line((x, y1, x, y1 + 6), fill=(60, 60, 60))
        draw.text((x, y1 + 9), f"{second}s", fill=(70, 70, 70), font=_font(8), anchor="ma")
    for percent in range(percent_step, max_percent + 1, percent_step):
        y = y1 - percent / max_percent * (y1 - y0)
        draw.line((x0 - 5, y, x0, y), fill=(60, 60, 60))
        draw.text((x0 - 14, y), f"{percent}%", fill=(70, 70, 70), font=_font(8), anchor="rm")
    points = [
        (x0 + seconds / max_seconds * (x1 - x0), y1 - percent / max_percent * (y1 - y0))
        for seconds, percent in analysis.cumulative_damage
    ]
    if points:
        draw.line((x0, y1, points[0][0], y1), fill=(25, 165, 25), width=2)
        draw.line((points[0][0], y1, points[0][0], points[0][1]), fill=(240, 40, 40), width=2)
    if len(points) > 1:
        draw.line(points, fill=(240, 40, 40), width=2)
    if points:
        _dashed_line(draw, (x0, points[-1][1], points[-1][0], points[-1][1]), fill=(165, 165, 165), dash=4, gap=4)
        _dashed_line(draw, (points[-1][0], points[-1][1], points[-1][0], y1), fill=(165, 165, 165), dash=4, gap=4)
    for value, label in ((analysis.nif_but_seconds, "NIF-BUT"), (analysis.niavg_but_seconds, "NIAvg-BUT")):
        if value is None:
            continue
        x = x0 + value / max_seconds * (x1 - x0)
        draw.line((x, y0 + 35, x, y1), fill=(75, 75, 75), width=2)
        draw.polygon(((x - 5, y1 - 10), (x + 5, y1 - 10), (x, y1)), fill=(20, 20, 20))
        draw.text((x - 4, y0 + (105 if label == "NIF-BUT" else 45)), label, fill=(65, 65, 65), font=_font(9), anchor="ls")
    return image


def _color_scale(height: int) -> Image.Image:
    scale = Image.new("RGB", (42, height))
    pixels = scale.load()
    for y in range(height):
        seconds = y / max(1, height - 1) * 17
        if seconds < 8:
            ratio = seconds / 8
            color = (255, int(25 + 220 * ratio), 0)
        else:
            ratio = (seconds - 8) / 9
            color = (int(255 * (1 - ratio)), 255, 0)
        for x in range(scale.width):
            pixels[x, y] = color
    return scale


def render_tear_film_report(
    analysis: TearFilmAnalysis,
    base_image: bytes,
    *,
    laterality: str | None,
    patient_name: str,
    patient_id: str,
    acquired: str,
    birthdate: str = "",
) -> bytes:
    base = _image(base_image)
    eye = _eye_label(laterality)
    canvas = Image.new("RGB", REPORT_SIZE, "white")
    draw = ImageDraw.Draw(canvas)
    _phoenix_header(draw, f"Phoenix - {patient_id},{patient_name} - {acquired} - #1 {eye}")
    panels = ((130, 78, 680, 488), (770, 78, 1320, 488), (130, 505, 680, 920), (770, 505, 1320, 920))

    selected_time = (analysis.selected.source_frame + 1) / 24
    upper_left = base.copy()
    panel_draw = ImageDraw.Draw(upper_left)
    panel_draw.text((upper_left.width - 65, 8), eye, fill="white", font=_font(25, True), stroke_width=2, stroke_fill=(145, 35, 35))
    panel_draw.text((upper_left.width - 82, upper_left.height - 32), f"{selected_time:.1f} s", fill=(255, 235, 235), font=_font(17), stroke_width=1, stroke_fill=(190, 25, 25))
    _paste_panel(canvas, upper_left, panels[0])

    damage = _damage_overlay(base, analysis.selected)
    damage_draw = ImageDraw.Draw(damage)
    damage_draw.text((damage.width - 55, 8), eye, fill="white", font=_font(25, True), stroke_width=2, stroke_fill=(145, 35, 35))
    damage_draw.text((damage.width - 180, damage.height - 30), "Damage stream", fill=(255, 235, 235), font=_font(16), stroke_width=1, stroke_fill=(190, 25, 25))
    _paste_panel(canvas, damage, panels[1])

    breakup = _breakup_map(base, analysis)
    breakup_draw = ImageDraw.Draw(breakup)
    breakup_draw.text((breakup.width - 55, 8), eye, fill="white", font=_font(25, True), stroke_width=2, stroke_fill=(145, 35, 35))
    breakup_draw.text((breakup.width - 155, breakup.height - 30), "Break-up map", fill=(255, 235, 235), font=_font(16), stroke_width=1, stroke_fill=(190, 25, 25))
    _paste_panel(canvas, breakup, panels[2])
    _paste_panel(canvas, _tear_graph((550, 415), analysis), panels[3])
    draw.text((1280, 520), eye, fill=(105, 105, 105), font=_font(24, True))

    canvas.paste(_color_scale(845), (25, 75))
    for seconds in range(18):
        y = 75 + seconds / 17 * 845
        draw.line((18, y, 72, y), fill=(135, 135, 135))
        draw.text((38, y + 3), f"{seconds}s" if seconds < 17 else ">=17s", fill=(45, 45, 45), font=_font(9), anchor="ma")

    side_x = 1370
    draw.text((side_x, 78), f"{patient_id}, {patient_name}", fill=(25, 25, 25), font=_font(15, True))
    draw.text((side_x, 99), patient_id, fill=(75, 75, 75), font=_font(11))
    if birthdate:
        draw.text((side_x, 114), f"Birthdate: {birthdate}", fill=(75, 75, 75), font=_font(11))
    draw.text((side_x, 143), "Tear Film Analysis", fill=(55, 55, 55), font=_font(13, True))
    draw.text((side_x, 160), acquired, fill=(90, 90, 90), font=_font(10))
    draw.text((side_x, 201), f"Movie length: {analysis.duration_seconds:.0f} s", fill=(55, 55, 55), font=_font(12))
    draw.text((side_x, 238), "NIF-BUT", fill=(35, 35, 35), font=_font(15, True))
    draw.text((side_x, 264), "Non invasive first Breakup time", fill=(80, 80, 80), font=_font(10))
    nif = phoenix_seconds_text(analysis.nif_but_seconds)
    draw.text((side_x, 288), nif, fill=(30, 30, 30), font=_font(16, True))
    draw.text((side_x, 337), "NIAvg-BUT", fill=(35, 35, 35), font=_font(15, True))
    draw.text((side_x, 363), "Non invasive average Breakup time", fill=(80, 80, 80), font=_font(10))
    average = phoenix_seconds_text(analysis.niavg_but_seconds)
    draw.text((side_x, 387), average, fill=(30, 30, 30), font=_font(16, True))
    return _png(canvas)


def _archive_members(path: Path) -> tuple[object, dict[str, bytes]]:
    dataset = pydicom.dcmread(path, force=True, stop_before_pixels=True)
    archives = embedded_archives(dataset)
    if not archives:
        raise PhoenixReportError("Phoenix private archive is missing")
    members: dict[str, bytes] = {}
    try:
        for archive_blob in archives:
            with zipfile.ZipFile(BytesIO(archive_blob.data)) as archive:
                for info in archive.infolist():
                    if info.is_dir() or info.file_size > MAX_REPORT_MEMBER_BYTES:
                        continue
                    name = info.filename.replace("\\", "/").lstrip("/")
                    if ".." in name.split("/"):
                        continue
                    members[name] = archive.read(info)
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise PhoenixReportError("Phoenix private archive is unreadable") from exc
    return dataset, members


def _one_member(members: dict[str, bytes], suffix: str) -> bytes:
    matches = [value for name, value in members.items() if name.casefold().endswith(suffix)]
    if len(matches) != 1:
        raise PhoenixReportError(f"Expected one {suffix} source")
    return matches[0]


def _unwrap_trm(data: bytes) -> bytes:
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            files = [item for item in archive.infolist() if not item.is_dir()]
            if len(files) != 1 or files[0].file_size > MAX_REPORT_MEMBER_BYTES:
                raise PhoenixReportError("Unsupported tear-film measurement archive")
            return archive.read(files[0])
    except zipfile.BadZipFile as exc:
        raise PhoenixReportError("Unreadable tear-film measurement archive") from exc


def _selected_video_frame(path: Path, frame_index: int) -> bytes:
    dataset = pydicom.dcmread(path, force=True)
    pixel_data = bytes(getattr(dataset, "PixelData", b""))
    riff = pixel_data.find(b"RIFF")
    if riff < 0 or riff + 8 > len(pixel_data):
        raise PhoenixReportError("Embedded tear-film video is missing")
    video_size = struct.unpack_from("<I", pixel_data, riff + 4)[0] + 8
    if video_size > MAX_VIDEO_BYTES or riff + video_size > len(pixel_data):
        raise PhoenixReportError("Embedded tear-film video has an invalid size")
    command = (
        "ffmpeg", "-v", "error", "-i", "pipe:0", "-vf", f"select=eq(n\\,{frame_index})",
        "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1",
    )
    try:
        result = subprocess.run(
            command, input=pixel_data[riff:riff + video_size], capture_output=True,
            check=True, timeout=30,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise PhoenixReportError("Unable to decode the embedded tear-film video") from exc
    if not result.stdout.startswith(b"\x89PNG"):
        raise PhoenixReportError("The selected tear-film frame could not be decoded")
    return result.stdout


def dicom_has_phoenix_report(path: Path, title: str) -> bool:
    if title.casefold() not in SUPPORTED_STUDIES:
        return False
    try:
        _, members = _archive_members(path)
        suffix = ".pup" if title.casefold() == "pupillography" else ".trm"
        return sum(name.casefold().endswith(suffix) for name in members) == 1
    except (OSError, PhoenixReportError):
        return False


def render_dicom_report(
    path: Path,
    title: str,
    *,
    patient_name: str,
    patient_id: str,
    acquired: str,
    birthdate: str = "",
) -> bytes:
    dataset, members = _archive_members(path)
    laterality = str(getattr(dataset, "Laterality", "") or getattr(dataset, "ImageLaterality", ""))
    if title.casefold() == "pupillography":
        measurements = parse_pupillography(_one_member(members, ".pup"))
        image_members = {
            name: value for name, value in members.items()
            if name in {"Scotopic/000", "Mesopic/000", "Photopic/000"} or name.startswith("Dynamic/")
        }
        return render_pupillography_report(
            measurements, image_members, laterality=laterality, patient_name=patient_name,
            patient_id=patient_id, acquired=acquired, birthdate=birthdate,
        )
    if title.casefold() == "tear film analysis":
        analysis = parse_tear_film(_unwrap_trm(_one_member(members, ".trm")))
        return render_tear_film_report(
            analysis, _selected_video_frame(path, analysis.selected.source_frame), laterality=laterality,
            patient_name=patient_name, patient_id=patient_id, acquired=acquired, birthdate=birthdate,
        )
    raise PhoenixReportError("This study does not have a Phoenix reconstruction")
