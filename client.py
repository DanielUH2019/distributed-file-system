#!/usr/bin/env python3
"""DFS client CLI — create, read, delete, and size commands."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from client_logic import configure_logging, create_file, delete_file, get_file_size, read_file
from exceptions import ClientError, ConfigurationError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Distributed file system client")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create", help="Upload a local text file")
    create_parser.add_argument("filepath", type=Path, help="Path to the local text file")

    read_parser = subparsers.add_parser("read", help="Download a file from the cluster")
    read_parser.add_argument("filename", help="Remote file name")
    read_parser.add_argument(
        "output_path",
        nargs="?",
        type=Path,
        default=None,
        help="Optional output path (defaults to ./<filename>)",
    )

    delete_parser = subparsers.add_parser("delete", help="Delete a remote file")
    delete_parser.add_argument("filename", help="Remote file name")

    size_parser = subparsers.add_parser("size", help="Print remote file size in bytes")
    size_parser.add_argument("filename", help="Remote file name")

    return parser


async def run_command(args: argparse.Namespace) -> int:
    if args.command == "create":
        remote_name = await create_file(args.filepath)
        print(f"Created {remote_name}")
        return 0

    if args.command == "read":
        destination = await read_file(args.filename, args.output_path)
        print(f"Wrote {destination}")
        return 0

    if args.command == "delete":
        await delete_file(args.filename)
        print(f"Deleted {args.filename}")
        return 0

    if args.command == "size":
        size = await get_file_size(args.filename)
        print(size)
        return 0

    raise ClientError(f"Unknown command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return asyncio.run(run_command(args))
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except ClientError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
