"""Build a hierarchical tree view of GCS buckets from flat listings.

Reads listing files from /tmp/bucket_listings/ produced by:
    gcloud storage ls -r gs://BUCKET/**

Writes a hierarchy file with each directory's children truncated at 100.
"""

from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_LISTING_DIRS = [
    Path("/tmp/bucket_listings"),
    Path(os.environ.get("TEMP", "")) / "bucket_listings",
    Path(os.environ.get("TMP", "")) / "bucket_listings",
]
LISTING_DIR = next((p for p in _DEFAULT_LISTING_DIRS if p.exists()), _DEFAULT_LISTING_DIRS[0])
OUTPUT_FILE = Path(__file__).resolve().parent.parent / "bucket_hierarchy.txt"
MAX_CHILDREN = 100


class Node:
    __slots__ = ("name", "children", "is_dir")

    def __init__(self, name: str, is_dir: bool = True):
        self.name = name
        self.children: dict[str, Node] = {}
        self.is_dir = is_dir

    def add_path(self, parts: list[str], is_dir_terminal: bool):
        if not parts:
            return
        head, *rest = parts
        if not head:
            return
        if head not in self.children:
            self.children[head] = Node(head, is_dir=bool(rest) or is_dir_terminal)
        child = self.children[head]
        if rest:
            child.is_dir = True
            child.add_path(rest, is_dir_terminal)
        else:
            if is_dir_terminal:
                child.is_dir = True


def render(node: Node, lines: list[str], prefix: str = "", is_root: bool = False):
    suffix = "/" if node.is_dir else ""
    if is_root:
        lines.append(f"{node.name}{suffix}")
    else:
        lines.append(f"{prefix}{node.name}{suffix}")

    child_names = sorted(node.children.keys(), key=lambda n: (not node.children[n].is_dir, n.lower()))
    total = len(child_names)
    truncated = total > MAX_CHILDREN
    shown = child_names[:MAX_CHILDREN] if truncated else child_names

    indent = prefix + ("  " if not is_root else "  ")
    for name in shown:
        render(node.children[name], lines, prefix=indent)

    if truncated:
        omitted = total - MAX_CHILDREN
        lines.append(f"{indent}... [{omitted} more entries omitted; {total} total in this directory]")


def parse_listing(path: Path, bucket: str) -> Node:
    root = Node(f"gs://{bucket}", is_dir=True)
    if not path.exists():
        return root
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("ERROR"):
                continue
            prefix = f"gs://{bucket}/"
            if not line.startswith(prefix):
                continue
            rel = line[len(prefix):]
            is_dir_terminal = rel.endswith("/")
            if is_dir_terminal:
                rel = rel[:-1]
            parts = rel.split("/") if rel else []
            if not parts:
                continue
            root.add_path(parts, is_dir_terminal)
    return root


def main():
    buckets = sorted(p.stem for p in LISTING_DIR.glob("*.txt"))
    out_lines: list[str] = []
    out_lines.append("# GCS Bucket Hierarchy")
    out_lines.append(f"# Truncation: directories with > {MAX_CHILDREN} children show only the first {MAX_CHILDREN}.")
    out_lines.append("")

    for bucket in buckets:
        listing = LISTING_DIR / f"{bucket}.txt"
        out_lines.append("=" * 80)
        root = parse_listing(listing, bucket)
        if not root.children:
            out_lines.append(f"gs://{bucket}/")
            out_lines.append("  (empty or inaccessible)")
        else:
            render(root, out_lines, is_root=True)
        out_lines.append("")

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text("\n".join(out_lines), encoding="utf-8")
    print(f"Wrote {OUTPUT_FILE} ({sum(1 for _ in out_lines)} lines, {OUTPUT_FILE.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
