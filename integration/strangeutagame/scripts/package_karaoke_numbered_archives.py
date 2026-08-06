#!/usr/bin/env python3
"""Build numbered HEVC and AV1 album ZIPs without duplicating video files."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .karaoke_album import (
        DEFAULT_MANIFEST_PATH,
        delivery_display_title,
        numbered_video_filename,
        sha256_file,
    )
except ImportError:  # pragma: no cover - direct script execution
    from karaoke_album import (  # type: ignore[no-redef]
        DEFAULT_MANIFEST_PATH,
        delivery_display_title,
        numbered_video_filename,
        sha256_file,
    )


REPO_ROOT = Path(__file__).resolve().parents[1]
LANES = {
    "hevc444": {
        "label": "HEVC444",
        "source_dir": "hevc444",
    },
    "av1-420": {
        "label": "AV1-420",
        "source_dir": "av1-420",
    },
}
PROFILES = ("standard", "wide")
DEFAULT_COMPRESSION_LEVEL = 6
class NumberedPackageError(RuntimeError):
    """Raised when a numbered release archive cannot be built or verified."""


def _resolve(value: Path | str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


def load_manifest(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("album"), dict):
        raise NumberedPackageError(f"invalid album manifest: {path}")
    tracks = value.get("tracks")
    if not isinstance(tracks, list):
        raise NumberedPackageError("numbered package requires a track list")
    numbers = [int(track["track_number"]) for track in tracks]
    five_track_album = len(tracks) == 5 and sorted(numbers) == [1, 2, 3, 4, 5]
    single_track_album = (
        value["album"].get("collection_contract") == "single-track"
        and len(tracks) == 1
        and numbers == [1]
    )
    if not (five_track_album or single_track_album):
        raise NumberedPackageError(f"unexpected track numbers: {numbers}")
    return value


def display_title(track: dict[str, Any]) -> str:
    return delivery_display_title(track)


def numbered_filename(track: dict[str, Any]) -> str:
    return numbered_video_filename(track)


def select_profiles(requested: list[str]) -> tuple[str, ...]:
    if not requested:
        return PROFILES
    selected = set(requested)
    return tuple(profile for profile in PROFILES if profile in selected)


def playlist_text(
    manifest: dict[str, Any],
    *,
    lane_label: str,
    profile: str,
) -> str:
    album = manifest["album"]
    lines = [
        "#EXTM3U",
        f"#PLAYLIST:{album['title']} - {lane_label} - {profile}",
    ]
    for track in sorted(manifest["tracks"], key=lambda item: int(item["track_number"])):
        seconds = float(track["expected_duration_ms"]) / 1000.0
        lines.extend(
            [
                f"#EXTINF:{seconds:.3f},{track['artist']} - {display_title(track)}",
                numbered_filename(track),
            ]
        )
    return "\n".join(lines) + "\n"


def _write_text_entry(
    archive: zipfile.ZipFile,
    name: str,
    text: str,
    *,
    compression_level: int,
) -> None:
    info = zipfile.ZipInfo(name, date_time=(2014, 8, 17, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    archive.writestr(
        info,
        text.encode("utf-8"),
        compresslevel=compression_level,
    )


def write_package_checksum_manifest(package: dict[str, Any]) -> Path:
    """Write hashes for the exact folder inputs and their published ZIP."""

    archive_path = Path(str(package["path"]))
    profiles = list(package["profiles"])
    video_entries = [
        entry
        for entry in package["entries"]
        if entry.get("kind") in {"video", "lossless-video"}
    ]
    lines: list[str] = []
    for entry in video_entries:
        filename = str(entry["entry"]).rsplit("/", 1)[-1]
        label = filename if len(profiles) == 1 else f"{entry['profile']}/{filename}"
        lines.append(f"{entry['sha256']} *{label}")
    lines.append(f"{package['sha256']} *{archive_path.name}")
    output = archive_path.with_name(f"{archive_path.stem}_SHA256SUMS.txt")
    temporary = output.with_name(f".{output.stem}.{uuid.uuid4().hex}.partial.txt")
    try:
        temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()
    return output


def build_archive(
    *,
    manifest: dict[str, Any],
    deliverable_root: Path,
    lane: str,
    profiles: tuple[str, ...] = PROFILES,
    compression_level: int = DEFAULT_COMPRESSION_LEVEL,
) -> dict[str, Any]:
    if not profiles or any(profile not in PROFILES for profile in profiles):
        raise NumberedPackageError(f"invalid package profiles: {profiles}")
    if compression_level not in range(1, 10):
        raise NumberedPackageError(
            f"ZIP DEFLATE compression level must be 1..9, got {compression_level}"
        )
    lane_config = LANES[lane]
    lane_label = str(lane_config["label"])
    album_title = str(manifest["album"]["title"])
    profile_suffix = f"-{profiles[0]}" if len(profiles) == 1 else ""
    package_root = f"{album_title}_{lane_label}{profile_suffix}"
    output = deliverable_root / "video" / f"{package_root}.zip"
    temporary = output.with_name(f".{output.stem}.{uuid.uuid4().hex}.partial.zip")
    album_info = deliverable_root / "ALBUM_INFO.txt"
    if not album_info.is_file():
        raise FileNotFoundError(f"missing album info: {album_info}")

    # Lossless companions are optional when the selected source audio is lossy.
    # If any companion exists, require the complete selected matrix so archives
    # cannot silently mix MP4-only and dual-delivery tracks.
    lossless_sources: dict[tuple[str, int], Path] = {}
    if lane == "av1-420":
        candidates = {
            (profile, int(track["track_number"])): (
                deliverable_root
                / "video"
                / "av1-420-lossless"
                / profile
                / Path(numbered_filename(track)).with_suffix(".mkv")
            )
            for profile in profiles
            for track in manifest["tracks"]
        }
        present = {
            key: path
            for key, path in candidates.items()
            if path.is_file() and path.stat().st_size > 0
        }
        if present and len(present) != len(candidates):
            missing = next(path for key, path in candidates.items() if key not in present)
            raise FileNotFoundError(
                "incomplete lossless package matrix; missing input: "
                f"{missing}"
            )
        lossless_sources = present

    entries: list[dict[str, Any]] = []
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(
            temporary,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=compression_level,
            allowZip64=True,
        ) as archive:
            info_name = f"{package_root}/ALBUM_INFO.txt"
            _write_text_entry(
                archive,
                info_name,
                album_info.read_text(encoding="utf-8"),
                compression_level=compression_level,
            )
            entries.append({"entry": info_name, "kind": "album-info"})
            for profile in profiles:
                playlist_name = f"{package_root}/{profile}/PLAYLIST.m3u8"
                _write_text_entry(
                    archive,
                    playlist_name,
                    playlist_text(
                        manifest,
                        lane_label=lane_label,
                        profile=profile,
                    ),
                    compression_level=compression_level,
                )
                entries.append({"entry": playlist_name, "kind": "playlist"})
                for track in sorted(
                    manifest["tracks"], key=lambda item: int(item["track_number"])
                ):
                    source = (
                        deliverable_root
                        / "video"
                        / str(lane_config["source_dir"])
                        / profile
                        / numbered_filename(track)
                    )
                    if not source.is_file() or source.stat().st_size == 0:
                        raise FileNotFoundError(f"missing numbered package input: {source}")
                    entry_name = (
                        f"{package_root}/{profile}/{numbered_filename(track)}"
                    )
                    archive.write(
                        source,
                        entry_name,
                        compress_type=zipfile.ZIP_DEFLATED,
                        compresslevel=compression_level,
                    )
                    entries.append(
                        {
                            "entry": entry_name,
                            "kind": "video",
                            "profile": profile,
                            "track_number": int(track["track_number"]),
                            "title": display_title(track),
                            "vocal": str(track["artist"]),
                            "source": str(source.resolve()),
                            "size_bytes": source.stat().st_size,
                            "sha256": sha256_file(source),
                        }
                    )
                    lossless_source = lossless_sources.get(
                        (profile, int(track["track_number"]))
                    )
                    if lossless_source is not None:
                        lossless_filename = Path(numbered_filename(track)).with_suffix(
                            ".mkv"
                        )
                        lossless_entry_name = (
                            f"{package_root}/{profile}/{lossless_filename}"
                        )
                        archive.write(
                            lossless_source,
                            lossless_entry_name,
                            compress_type=zipfile.ZIP_DEFLATED,
                            compresslevel=compression_level,
                        )
                        entries.append(
                            {
                                "entry": lossless_entry_name,
                                "kind": "lossless-video",
                                "profile": profile,
                                "track_number": int(track["track_number"]),
                                "title": display_title(track),
                                "vocal": str(track["artist"]),
                                "source": str(lossless_source.resolve()),
                                "size_bytes": lossless_source.stat().st_size,
                                "sha256": sha256_file(lossless_source),
                            }
                        )
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()

    member_hashes: dict[str, str] = {}
    with zipfile.ZipFile(output, mode="r") as archive:
        archive_names = archive.namelist()
        bad_entry = archive.testzip()
        archive_infos = archive.infolist()
        for info in archive_infos:
            digest = hashlib.sha256()
            with archive.open(info, mode="r") as member:
                while chunk := member.read(1024 * 1024):
                    digest.update(chunk)
            member_hashes[info.filename] = digest.hexdigest()
    expected_names = [str(entry["entry"]) for entry in entries]
    if archive_names != expected_names or bad_entry is not None:
        raise NumberedPackageError(
            f"archive verification failed: names_match={archive_names == expected_names}, "
            f"bad_entry={bad_entry}"
        )
    info_by_name = {info.filename: info for info in archive_infos}
    for entry in entries:
        info = info_by_name[str(entry["entry"])]
        entry["archive_size_bytes"] = info.compress_size
        entry["archive_compression"] = "deflate"
        if entry["kind"] in {"video", "lossless-video"}:
            packaged_hash = member_hashes[str(entry["entry"])]
            if packaged_hash != entry["sha256"]:
                raise NumberedPackageError(
                    "packaged video hash mismatch for "
                    f"track {entry['track_number']} ({entry['entry']}): "
                    f"source={entry['sha256']} archive={packaged_hash}"
                )
            entry["archive_sha256"] = packaged_hash
    uncompressed_size = sum(info.file_size for info in archive_infos)
    compressed_payload_size = sum(info.compress_size for info in archive_infos)
    result = {
        "lane": lane,
        "label": lane_label,
        "path": str(output.resolve()),
        "size_bytes": output.stat().st_size,
        "sha256": sha256_file(output),
        "entry_count": len(entries),
        "video_entry_count": sum(
            entry["kind"] in {"video", "lossless-video"} for entry in entries
        ),
        "compatibility_video_entry_count": sum(
            entry["kind"] == "video" for entry in entries
        ),
        "lossless_video_entry_count": sum(
            entry["kind"] == "lossless-video" for entry in entries
        ),
        "profiles": list(profiles),
        "numbered": True,
        "compression": {
            "algorithm": "deflate",
            "level": compression_level,
            "selection": "balanced-speed-ratio-for-precompressed-media",
            "uncompressed_size_bytes": uncompressed_size,
            "compressed_payload_size_bytes": compressed_payload_size,
            "payload_ratio": round(
                compressed_payload_size / max(uncompressed_size, 1), 6
            ),
        },
        "entries": entries,
    }
    result["sha256_manifest"] = str(write_package_checksum_manifest(result).resolve())
    return result


def write_report(path: Path, packages: list[dict[str, Any]]) -> None:
    track_numbers = sorted(
        {
            int(entry["track_number"])
            for package in packages
            for entry in package.get("entries", [])
            if entry.get("kind") == "video"
        }
    )
    value = {
        "schema_version": "karaoke-numbered-packages/v2",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "pass",
        "track_numbers": track_numbers,
        "packages": packages,
    }
    temporary = path.with_name(f".{path.stem}.{uuid.uuid4().hex}.partial.json")
    try:
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="deliverable root override, used for isolated staging",
    )
    parser.add_argument(
        "--lane",
        action="append",
        choices=tuple(LANES),
        default=[],
        help="package one lane; repeat for both (default: both)",
    )
    parser.add_argument(
        "--profile",
        action="append",
        choices=PROFILES,
        default=[],
        help="package one profile; repeat for both (default: both)",
    )
    parser.add_argument(
        "--compression-level",
        type=int,
        choices=range(1, 10),
        default=DEFAULT_COMPRESSION_LEVEL,
        metavar="1..9",
        help=(
            "ZIP DEFLATE level (default: 6, balanced for already-compressed media; "
            "use 9 only when maximum DEFLATE is explicitly required)"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    try:
        manifest_path = _resolve(args.manifest)
        manifest = load_manifest(manifest_path)
        deliverable_root = (
            _resolve(args.root)
            if args.root
            else (
                manifest_path.parent / manifest["paths"]["deliverable_directory"]
            ).resolve()
        )
        lanes = args.lane or list(LANES)
        profiles = select_profiles(args.profile)
        packages = [
            build_archive(
                manifest=manifest,
                deliverable_root=deliverable_root,
                lane=lane,
                profiles=profiles,
                compression_level=args.compression_level,
            )
            for lane in lanes
        ]
        report = deliverable_root / "validation" / "numbered_packages_report.json"
        write_report(report, packages)
        print(
            json.dumps(
                {
                    "status": "pass",
                    "report": str(report),
                    "packages": [
                        {
                            key: package[key]
                            for key in (
                                "lane",
                                "path",
                                "size_bytes",
                                "sha256",
                                "entry_count",
                            )
                        }
                        for package in packages
                    ],
                },
                ensure_ascii=True,
                indent=2,
            )
        )
        return 0
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
