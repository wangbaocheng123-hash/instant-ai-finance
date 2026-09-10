#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys
from pathlib import Path


DEFAULT_DATABASE = Path(
    os.environ.get(
        "MODEL_DOWNLOADER_DATABASE",
        "/var/lib/model-downloader/library.sqlite3",
    )
)


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def status(connection: sqlite3.Connection) -> None:
    video_count = connection.execute(
        "SELECT COUNT(*) FROM videos"
    ).fetchone()[0]
    downloaded = connection.execute(
        "SELECT COUNT(*) FROM videos WHERE download_status = 'downloaded'"
    ).fetchone()[0]
    comment_count = connection.execute(
        "SELECT COUNT(*) FROM comments"
    ).fetchone()[0]
    last_scan = connection.execute(
        """
        SELECT started_at, finished_at, success, visible_today,
               downloaded, message
        FROM scan_runs
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    print(f"作品记录：{video_count}")
    print(f"已下载：{downloaded}")
    print(f"评论记录：{comment_count}")
    if last_scan:
        print(
            "最近扫描："
            f"{last_scan['started_at']}，"
            f"{'成功' if last_scan['success'] else '失败'}，"
            f"今日可见 {last_scan['visible_today']} 个，"
            f"新下载 {last_scan['downloaded']} 个"
        )
        if last_scan["message"]:
            print(f"说明：{last_scan['message']}")


def list_videos(
    connection: sqlite3.Connection,
    limit: int,
) -> None:
    rows = connection.execute(
        """
        SELECT video_id, published_at, title, download_status,
               file_size, comment_count, file_path
        FROM videos
        ORDER BY published_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    for row in rows:
        size = row["file_size"] or 0
        print(
            f"{row['published_at']}  {row['video_id']}  "
            f"{row['download_status']}  {size} 字节  "
            f"{row['comment_count']} 条评论"
        )
        print(f"  {row['title']}")
        if row["file_path"]:
            print(f"  {row['file_path']}")


def export_comments(
    connection: sqlite3.Connection,
    video_id: str,
    output: Path,
) -> None:
    rows = connection.execute(
        """
        SELECT comment_id, parent_comment_id, author_name, author_uid,
               text, created_at, digg_count, reply_count, ip_label,
               collected_at
        FROM comments
        WHERE video_id = ?
        ORDER BY created_at, comment_id
        """,
        (video_id,),
    ).fetchall()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "评论ID",
                "父评论ID",
                "用户昵称",
                "用户标识",
                "评论内容",
                "发布时间",
                "点赞数",
                "回复数",
                "IP属地",
                "采集时间",
            ]
        )
        for row in rows:
            writer.writerow(list(row))
    print(f"已导出 {len(rows)} 条评论：{output}")


def main() -> int:
    parser = argparse.ArgumentParser(description="模型下载器视频与评论库工具")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="查看视频库和评论库概况")
    list_parser = subparsers.add_parser("list", help="列出最近视频")
    list_parser.add_argument("--limit", type=int, default=20)
    export_parser = subparsers.add_parser(
        "export-comments",
        help="把指定作品评论导出为 CSV",
    )
    export_parser.add_argument("video_id")
    export_parser.add_argument("output", type=Path)
    args = parser.parse_args()

    if not args.database.exists():
        print(f"数据库尚不存在：{args.database}", file=sys.stderr)
        return 1
    with connect(args.database) as connection:
        if args.command == "status":
            status(connection)
        elif args.command == "list":
            list_videos(connection, max(1, min(args.limit, 500)))
        elif args.command == "export-comments":
            export_comments(connection, args.video_id, args.output)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
