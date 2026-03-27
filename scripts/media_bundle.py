#!/usr/bin/env python3

from __future__ import annotations

import argparse
import shutil
import tarfile
from datetime import datetime
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="打包或恢复聊天项目的 media 文件。")
    subparsers = parser.add_subparsers(dest="command", required=True)

    pack_parser = subparsers.add_parser("pack", help="把 media 目录打包成 tar.gz")
    pack_parser.add_argument("--media-dir", default="media", help="媒体目录，默认 media")
    pack_parser.add_argument("--output", default="", help="输出压缩包路径，默认自动生成")

    restore_parser = subparsers.add_parser("restore", help="从 tar.gz 恢复 media 目录")
    restore_parser.add_argument("--archive", required=True, help="压缩包路径")
    restore_parser.add_argument("--target-dir", default="media", help="恢复到哪个目录，默认 media")
    restore_parser.add_argument("--replace", action="store_true", help="恢复前先删除目标目录")

    return parser


def ensure_media_dir(media_dir: Path) -> None:
    if not media_dir.exists():
        raise SystemExit(f"媒体目录不存在: {media_dir}")
    if not media_dir.is_dir():
        raise SystemExit(f"媒体路径不是目录: {media_dir}")


def pack_media(media_dir: Path, output_path: Path) -> None:
    ensure_media_dir(media_dir)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output_path, "w:gz") as tar:
        tar.add(media_dir, arcname=media_dir.name)
    print(f"已打包媒体文件: {output_path}")


def restore_media(archive_path: Path, target_dir: Path, replace: bool) -> None:
    if not archive_path.exists():
        raise SystemExit(f"压缩包不存在: {archive_path}")

    target_parent = target_dir.parent
    target_parent.mkdir(parents=True, exist_ok=True)

    if replace and target_dir.exists():
        shutil.rmtree(target_dir)

    with tarfile.open(archive_path, "r:gz") as tar:
        members = tar.getmembers()
        top_levels = {member.name.split("/", 1)[0] for member in members if member.name}
        if len(top_levels) != 1:
            raise SystemExit("压缩包结构不符合预期，无法安全恢复。")
        archive_root = next(iter(top_levels))
        tar.extractall(path=target_parent)

    extracted_dir = target_parent / archive_root
    if extracted_dir != target_dir:
        if target_dir.exists():
            shutil.rmtree(target_dir)
        extracted_dir.rename(target_dir)

    print(f"已恢复媒体文件到: {target_dir}")


def main() -> int:
    args = build_parser().parse_args()
    project_dir = Path(__file__).resolve().parent.parent

    if args.command == "pack":
        media_dir = (project_dir / args.media_dir).resolve()
        if args.output:
            output_path = Path(args.output).resolve()
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = (project_dir / "backups" / f"media_bundle_{timestamp}.tar.gz").resolve()
        pack_media(media_dir, output_path)
        return 0

    archive_path = Path(args.archive).resolve()
    target_dir = (project_dir / args.target_dir).resolve()
    restore_media(archive_path, target_dir, args.replace)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
