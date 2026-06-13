#!/usr/bin/env python3
"""Convert Quaver .qp mapsets to osu!mania .osz archives."""

from __future__ import annotations

import argparse
import ctypes
import math
import os
import re
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any


INVALID_FILENAME_CHARS = r'<>:"/\|?*'
MAP_OPTION_ALIASES = {
    "ln-to-tap": "ln-to-tap",
    "ln-to-normal": "ln-to-tap",
    "long-to-normal": "ln-to-tap",
    "all-ln": "all-ln",
    "all-long": "all-ln",
    "all-to-ln": "all-ln",
    "nosv": "nosv",
    "no-sv": "nosv",
}
MAP_OPTION_HELP = "ln-to-tap, all-ln, nosv"


class ConversionError(RuntimeError):
    """Raised when a .qp package cannot be converted."""


def load_qua(path: Path) -> dict[str, Any]:
    """Load a Quaver .qua file.

    PyYAML is used when installed. A small fallback parser is included so the
    converter still handles the common .qua structures without dependencies.
    """

    text = path.read_text(encoding="utf8")
    try:
        import yaml  # type: ignore
    except ImportError:
        data = parse_simple_yaml(text)
    else:
        data = yaml.safe_load(text)

    if not isinstance(data, dict):
        raise ConversionError(f"{path.name} is not a valid Quaver YAML map")
    return data


def parse_simple_yaml(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    current_key: str | None = None
    current_item: dict[str, Any] | None = None

    for raw_line in text.splitlines():
        line_without_comment = strip_yaml_comment(raw_line).rstrip()
        if not line_without_comment.strip():
            continue

        indent = len(line_without_comment) - len(line_without_comment.lstrip(" "))
        line = line_without_comment.lstrip(" ")

        if indent == 0 and not line.startswith("- "):
            key, value, has_value = split_yaml_key_value(line)
            current_key = key
            current_item = None
            if has_value:
                result[key] = parse_scalar(value)
            else:
                result[key] = None
            continue

        if current_key is None:
            continue

        if line.startswith("- "):
            if not isinstance(result.get(current_key), list):
                result[current_key] = []
            list_value = result[current_key]
            assert isinstance(list_value, list)

            rest = line[2:].strip()
            if rest and ":" in rest:
                item_key, item_value, _ = split_yaml_key_value(rest)
                current_item = {item_key: parse_scalar(item_value)}
                list_value.append(current_item)
            elif rest:
                current_item = None
                list_value.append(parse_scalar(rest))
            else:
                current_item = {}
                list_value.append(current_item)
            continue

        if current_item is not None and ":" in line:
            item_key, item_value, _ = split_yaml_key_value(line)
            current_item[item_key] = parse_scalar(item_value)

    return result


def strip_yaml_comment(line: str) -> str:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote == '"':
            escaped = True
            continue
        if char in {"'", '"'}:
            if quote == char:
                quote = None
            elif quote is None:
                quote = char
            continue
        if char == "#" and quote is None:
            if index == 0 or line[index - 1].isspace():
                return line[:index]
    return line


def split_yaml_key_value(line: str) -> tuple[str, str, bool]:
    if ":" not in line:
        return line.strip(), "", False
    key, value = line.split(":", 1)
    return key.strip(), value.strip(), bool(value.strip())


def parse_scalar(value: str) -> Any:
    if value == "":
        return ""
    if value in {"null", "Null", "NULL", "~"}:
        return None
    if value in {"true", "True", "TRUE"}:
        return True
    if value in {"false", "False", "FALSE"}:
        return False
    if value == "[]":
        return []
    if value == "{}":
        return {}
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def convert_qp_to_osz(
    qp_path: Path | str,
    osz_path: Path | str | None = None,
    creator_name: str | None = None,
    map_options: set[str] | list[str] | None = None,
) -> Path:
    qp_path = Path(qp_path)
    normalized_map_options = normalize_map_options(map_options or set())
    if qp_path.suffix.lower() != ".qp":
        raise ConversionError(f"input must be a .qp file: {qp_path}")
    if not qp_path.is_file():
        raise ConversionError(f"input file does not exist: {qp_path}")

    with tempfile.TemporaryDirectory(prefix="qptoosu_") as temp_name:
        workdir = Path(temp_name)
        extract_zip_safely(qp_path, workdir)

        qua_files = sorted(workdir.rglob("*.qua"))
        if not qua_files:
            raise ConversionError(f"no .qua files found in {qp_path}")

        maps = [(qua_file, load_qua(qua_file)) for qua_file in qua_files]
        output_path = resolve_output_path(qp_path, maps[0][1], osz_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        osu_dir = workdir / "_osu"
        osu_dir.mkdir()
        osu_files: list[Path] = []
        used_names: set[str] = set()

        for index, (qua_file, qua) in enumerate(maps, start=1):
            osu_name = make_unique_osu_filename(qua, index, used_names)
            osu_path = osu_dir / osu_name
            osu_path.write_text(
                build_osu_file(
                    qua,
                    qua_file.name,
                    creator_name=creator_name,
                    map_options=normalized_map_options,
                ),
                encoding="utf8",
            )
            osu_files.append(osu_path)

        write_osz(output_path, workdir, maps, osu_files)
        return output_path


def extract_zip_safely(zip_path: Path, target_dir: Path) -> None:
    target_root = target_dir.resolve()
    with zipfile.ZipFile(zip_path, "r") as archive:
        for member in archive.infolist():
            member_path = target_dir / member.filename
            resolved = member_path.resolve()
            if target_root != resolved and target_root not in resolved.parents:
                raise ConversionError(f"unsafe path in archive: {member.filename}")
        archive.extractall(target_dir)


def resolve_output_path(
    qp_path: Path, first_qua: dict[str, Any], osz_path: Path | str | None
) -> Path:
    title = str(first_qua.get("Title") or qp_path.stem)
    default_name = sanitize_filename(title) + ".osz"

    if osz_path is None:
        return qp_path.with_name(default_name)

    path = Path(osz_path)
    if path.exists() and path.is_dir():
        return path / default_name
    if path.suffix.lower() != ".osz":
        return path.with_suffix(".osz")
    return path


def make_unique_osu_filename(
    qua: dict[str, Any], index: int, used_names: set[str]
) -> str:
    title = sanitize_filename(str(qua.get("Title") or f"Converted {index}"))
    version = sanitize_filename(str(qua.get("DifficultyName") or f"Difficulty {index}"))
    name = f"{title} [{version}].osu"
    if name not in used_names:
        used_names.add(name)
        return name

    stem = name[:-4]
    counter = 2
    while f"{stem} ({counter}).osu" in used_names:
        counter += 1
    unique_name = f"{stem} ({counter}).osu"
    used_names.add(unique_name)
    return unique_name


def sanitize_filename(value: str) -> str:
    translated = "".join(
        "_" if char in INVALID_FILENAME_CHARS else char for char in value
    )
    translated = re.sub(r"\s+", " ", translated).strip(" .")
    return translated or "Converted"


def normalize_map_options(raw_options: set[str] | list[str]) -> set[str]:
    normalized: set[str] = set()
    for raw_option in raw_options:
        for part in str(raw_option).split(","):
            option = part.strip().lower()
            if not option:
                continue
            mapped = MAP_OPTION_ALIASES.get(option)
            if mapped is None:
                raise ConversionError(
                    f"unknown map option: {option} "
                    f"(available: {MAP_OPTION_HELP})"
                )
            normalized.add(mapped)

    if "ln-to-tap" in normalized and "all-ln" in normalized:
        raise ConversionError("ln-to-tap and all-ln cannot be used together")
    return normalized


def build_osu_file(
    qua: dict[str, Any],
    source_name: str = "source.qua",
    creator_name: str | None = None,
    map_options: set[str] | None = None,
) -> str:
    map_options = map_options or set()
    keys = parse_keys(qua.get("Mode"))
    timing_points = build_timing_points(qua, map_options)
    hit_objects = build_hit_objects(qua, keys, map_options)
    creator = (
        creator_name if creator_name is not None else text_or_empty(qua.get("Creator"))
    )

    lines: list[str] = [
        "osu file format v14",
        "",
        "[General]",
        f"AudioFilename: {required_text(qua, 'AudioFile', source_name)}",
        f"PreviewTime: {int_or_default(qua.get('SongPreviewTime'), -1)}",
        "Mode: 3",
        "",
        "[Metadata]",
        f"Title:{text_or_empty(qua.get('Title'))}",
        f"Artist:{text_or_empty(qua.get('Artist'))}",
        f"Creator:{creator}",
        f"Version:{text_or_empty(qua.get('DifficultyName'))}",
        "",
        "[Difficulty]",
        f"CircleSize:{keys}",
        "",
        "[Events]",
    ]

    background = text_or_empty(qua.get("BackgroundFile"))
    if background:
        lines.append(f'0,0,"{background}",0,0')
    lines.extend(
        [
            "",
            "[TimingPoints]",
            *timing_points,
            "",
            "[HitObjects]",
            *hit_objects,
            "",
        ]
    )
    return "\n".join(lines)


def required_text(qua: dict[str, Any], key: str, source_name: str) -> str:
    value = text_or_empty(qua.get(key))
    if not value:
        raise ConversionError(f"{source_name} is missing required field: {key}")
    return value


def text_or_empty(value: Any) -> str:
    return "" if value is None else str(value)


def int_or_default(value: Any, default: int) -> int:
    if value is None or value == "":
        return default
    return int(float(value))


def parse_keys(mode: Any) -> int:
    if isinstance(mode, str):
        match = re.fullmatch(r"Keys(\d+)", mode.strip(), flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
        if mode.strip().isdigit():
            return int(mode.strip())
    if isinstance(mode, int):
        return mode
    raise ConversionError(f"unsupported Quaver mode: {mode!r}")


def build_timing_points(qua: dict[str, Any], map_options: set[str]) -> list[str]:
    bpm_lines: list[str] = []
    slider_velocity_lines: list[str] = []
    for timing_point in sorted_maps(qua.get("TimingPoints"), "StartTime"):
        start = int_or_default(timing_point.get("StartTime"), 0)
        bpm = float(timing_point.get("Bpm") or 0)
        if bpm <= 0:
            raise ConversionError(f"invalid BPM at {start}: {bpm}")
        beat_length = 60000 / bpm
        meter = int_or_default(timing_point.get("Signature"), 4)
        bpm_lines.append(
            f"{start},{format_number(beat_length)},{meter},1,0,100,1,0"
        )

    if not bpm_lines:
        raise ConversionError("map is missing TimingPoints with BPM data")

    if "nosv" not in map_options:
        for slider_velocity in sorted_maps(qua.get("SliderVelocities"), "StartTime"):
            multiplier = float(slider_velocity.get("Multiplier") or 0)
            if multiplier == 0:
                continue
            start = int_or_default(slider_velocity.get("StartTime"), 0)
            milliseconds_per_beat = -100 / multiplier
            slider_velocity_lines.append(
                f"{start},{format_number(milliseconds_per_beat)},4,1,0,100,0,0"
            )

    lines = bpm_lines + slider_velocity_lines
    return sorted(
        lines, key=lambda line: (int(line.split(",", 1)[0]), timing_sort_key(line))
    )


def timing_sort_key(line: str) -> int:
    return 0 if line.endswith(",1,0") else 1


def sorted_maps(value: Any, start_key: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConversionError(f"expected list for YAML field, got {type(value).__name__}")
    maps = [item for item in value if isinstance(item, dict)]
    return sorted(maps, key=lambda item: int_or_default(item.get(start_key), 0))


def build_hit_objects(
    qua: dict[str, Any], keys: int, map_options: set[str]
) -> list[str]:
    hit_objects = sorted_maps(qua.get("HitObjects"), "StartTime")
    lines: list[str] = []
    for hit_object in hit_objects:
        start = int_or_default(hit_object.get("StartTime"), 0)
        lane = int_or_default(hit_object.get("Lane"), 1)
        if lane < 1 or lane > keys:
            raise ConversionError(f"lane {lane} is outside Keys{keys} at {start}")
        x = lane_to_x(lane, keys)
        end = hit_object.get("EndTime")
        if "ln-to-tap" in map_options:
            lines.append(f"{x},192,{start},1,0,0:0:0:0:")
        elif "all-ln" in map_options:
            end_time = int_or_default(end, start + 1)
            if end_time <= start:
                end_time = start + 1
            lines.append(f"{x},192,{start},128,0,{end_time}:0:0:0:0:")
        elif end is not None and int_or_default(end, start) > start:
            lines.append(
                f"{x},192,{start},128,0,{int_or_default(end, start)}:0:0:0:0:"
            )
        else:
            lines.append(f"{x},192,{start},1,0,0:0:0:0:")
    return lines


def lane_to_x(lane: int, keys: int) -> int:
    return int((lane - 0.5) * 512 / keys)


def format_number(value: float) -> str:
    if math.isclose(value, round(value), abs_tol=1e-9):
        return str(int(round(value)))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def write_osz(
    output_path: Path,
    workdir: Path,
    maps: list[tuple[Path, dict[str, Any]]],
    osu_files: list[Path],
) -> None:
    asset_names = collect_asset_names(maps)
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for asset_name in asset_names:
            asset_path = find_extracted_asset(workdir, asset_name)
            archive.write(asset_path, arcname=asset_name)
        for osu_file in osu_files:
            archive.write(osu_file, arcname=osu_file.name)


def collect_asset_names(maps: list[tuple[Path, dict[str, Any]]]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for _, qua in maps:
        for key in ("AudioFile", "BackgroundFile"):
            name = text_or_empty(qua.get(key))
            if name and name not in seen:
                names.append(name)
                seen.add(name)
    return names


def find_extracted_asset(workdir: Path, asset_name: str) -> Path:
    direct_path = workdir / asset_name
    if direct_path.is_file():
        return direct_path

    basename_matches = [
        path for path in workdir.rglob(Path(asset_name).name) if path.is_file()
    ]
    if len(basename_matches) == 1:
        return basename_matches[0]
    raise ConversionError(f"referenced asset not found in package: {asset_name}")


def should_pause_at_exit(argv: list[str]) -> bool:
    if os.name != "nt" or len(argv) != 2 or Path(argv[1]).suffix.lower() != ".qp":
        return False
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return False

    try:
        process_ids = (ctypes.c_ulong * 2)()
        process_count = ctypes.windll.kernel32.GetConsoleProcessList(process_ids, 2)
    except AttributeError:
        return False
    return process_count <= 1


def pause_if_needed(should_pause: bool) -> None:
    if should_pause:
        input("Press Enter to close...")


def main() -> int:
    pause_on_exit = should_pause_at_exit(sys.argv)
    parser = argparse.ArgumentParser(
        description="Convert a Quaver .qp package to an osu!mania .osz archive."
    )
    parser.add_argument("qp_path", type=Path, help="input .qp file")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="output .osz path or output directory",
    )
    parser.add_argument(
        "--rename",
        metavar="USER",
        help="replace the osu! metadata Creator value",
    )
    parser.add_argument(
        "--mapoption",
        action="append",
        default=[],
        metavar="OPTION",
        help=f"map conversion option; repeat or comma-separate ({MAP_OPTION_HELP})",
    )
    args = parser.parse_args()

    try:
        output = convert_qp_to_osz(
            args.qp_path,
            args.output,
            creator_name=args.rename,
            map_options=args.mapoption,
        )
    except (ConversionError, zipfile.BadZipFile, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        pause_if_needed(pause_on_exit)
        return 1

    print(f"Created: {output}")
    pause_if_needed(pause_on_exit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
