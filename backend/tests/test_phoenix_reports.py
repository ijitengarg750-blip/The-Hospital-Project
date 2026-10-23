from __future__ import annotations

import math
import struct
from io import BytesIO

import pytest
from PIL import Image

from app.phoenix_reports import (
    parse_pupillography,
    parse_tear_film,
    phoenix_seconds_text,
    render_pupillography_report,
    render_tear_film_report,
)


def _pupil_record(*, pupil_x: float, pupil_y: float, reference_x: float, reference_y: float,
                  radius: float, timestamp_ms: int, index: int) -> bytes:
    contour = [radius] * 512
    return (
        struct.pack("<5fIBI", pupil_x, pupil_y, reference_x, reference_y, radius,
                    timestamp_ms, index, len(contour))
        + struct.pack("<512f", *contour)
        + b"\x01"
    )


def test_pupillography_parser_reconstructs_phoenix_measurements():
    scale = 0.02304147556424141
    dynamic = [_pupil_record(pupil_x=100, pupil_y=100, reference_x=100, reference_y=100,
                             radius=80 + index, timestamp_ms=index * 200, index=index)
               for index in range(7)]
    static = [
        _pupil_record(pupil_x=100, pupil_y=100, reference_x=101.405, reference_y=100.1114,
                      radius=114.384483, timestamp_ms=0, index=0),
        _pupil_record(pupil_x=105, pupil_y=102, reference_x=100.059, reference_y=103.939,
                      radius=103.146446, timestamp_ms=0, index=0),
        _pupil_record(pupil_x=107, pupil_y=101, reference_x=100.633, reference_y=103.399,
                      radius=85.634239, timestamp_ms=0, index=0),
    ]
    payload = b"\x01" + struct.pack("<fB", scale, len(dynamic)) + b"".join(dynamic)
    payload += b"".join(b"\x01" + record for record in static)

    result = parse_pupillography(payload)

    assert result.scale_mm_per_pixel == pytest.approx(scale)
    assert [round(item.diameter_mm, 2) for item in result.static] == [5.27, 4.75, 3.95]
    assert result.static[0].center_mm == pytest.approx((0.03238, -0.002567), abs=1e-4)
    assert [item.timestamp_ms for item in result.dynamic] == list(range(0, 1400, 200))


def test_tear_film_parser_reconstructs_phoenix_summary_and_graph():
    breakup = [8500, 13000, 0, -1, 0, 0, 8000, 11000] + [0] * 80
    rows, columns = 189, 512
    field = [math.nan] * (rows * columns)
    field[0] = 0.0
    field[1] = 0.5
    record = struct.pack("<5H", 414, 346, 240, rows, columns) + struct.pack(f"<{len(field)}f", *field)
    payload = (
        b"\x07"
        + struct.pack("<H", len(breakup))
        + struct.pack(f"<{len(breakup)}i", *breakup)
        + struct.pack("<HIHI", 0, 18416, 1, len(record))
        + record
    )

    result = parse_tear_film(payload)

    assert result.duration_seconds == pytest.approx(18.416)
    assert result.nif_but_seconds == pytest.approx(8.0)
    assert result.niavg_but_seconds == pytest.approx(10.125)
    assert result.cumulative_damage == pytest.approx([
        (8.0, 100 / 88), (8.5, 200 / 88), (11.0, 300 / 88), (13.0, 400 / 88),
    ])
    assert result.selected.source_frame == 414
    assert result.selected.center == (346, 240)
    assert result.selected.value(0, 1) == pytest.approx(0.5)


def test_phoenix_summary_uses_truncated_tenths_not_standard_rounding():
    # Devi's Phoenix screenshot reports 3.2 s and 6.6 s for these decoded values.
    assert phoenix_seconds_text(3.25) == "3.2 s"
    assert phoenix_seconds_text(6.6625) == "6.6 s"


def _jpeg() -> bytes:
    output = BytesIO()
    Image.new("L", (800, 600), 80).save(output, "JPEG")
    return output.getvalue()


def test_renderers_create_complete_phoenix_sized_png_views():
    scale = 0.02304147556424141
    records = [_pupil_record(pupil_x=400, pupil_y=300, reference_x=400, reference_y=300,
                             radius=90 + index, timestamp_ms=index * 200, index=index)
               for index in range(7)]
    pupil_payload = b"\x01" + struct.pack("<fB", scale, 7) + b"".join(records)
    pupil_payload += b"".join(b"\x01" + records[index] for index in range(3))
    pupil_png = render_pupillography_report(
        parse_pupillography(pupil_payload),
        {"Scotopic/000": _jpeg(), "Mesopic/000": _jpeg(), "Photopic/000": _jpeg(), "Dynamic/048": _jpeg()},
        laterality="L", patient_name="Test Patient", patient_id="10042", acquired="12/23/2025 2:18 PM",
    )

    breakup = [8000, 8500, 11000, 13000] + [0] * 84
    rows, columns = 189, 512
    values = [0.0] * (rows * columns)
    values[100] = 1.0
    record = struct.pack("<5H", 414, 400, 300, rows, columns) + struct.pack(f"<{len(values)}f", *values)
    tear_payload = b"\x07" + struct.pack("<H", 88) + struct.pack("<88i", *breakup)
    tear_payload += struct.pack("<HIHI", 0, 18416, 1, len(record)) + record
    tear_png = render_tear_film_report(
        parse_tear_film(tear_payload), _jpeg(), laterality="L", patient_name="Test Patient",
        patient_id="10042", acquired="12/23/2025 2:18 PM",
    )

    for content in (pupil_png, tear_png):
        image = Image.open(BytesIO(content))
        assert image.format == "PNG"
        assert image.size == (1600, 936)
