# 自动代码结构打印脚本，方便查看项目目录结构
"""
打印目录树（默认从脚本自身所在目录开始）。
示例：
  python print_tree.py
  python print_tree.py --root /path/to/project
  python print_tree.py --max-depth 3 --exclude .git __pycache__ node_modules .venv
  python print_tree.py --out tree.txt
"""

from __future__ import annotations
import argparse
import fnmatch
import os
from pathlib import Path
import sys
from typing import Iterable, List, Set

# 线条字符
BRANCH = "├── "
LAST = "└── "
VERT = "│   "
EMPTY = "    "

DEFAULT_EXCLUDES = {
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    ".env",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    ".html",
}


def safe_listdir(path: Path) -> List[Path]:
    """安全地列出目录内容，忽略权限错误。"""
    try:
        return list(path.iterdir())
    except Exception:
        return []


def get_py_header_comment(path: Path, max_len: int = 80) -> str:
    """
    从 .py 文件中读取“文件作用说明”这一行，用于树形视图中展示。

    约定：
    - 优先取第一行非空的以 '#' 开头的注释行；
    - 自动跳过 shebang (#!) 和常见的编码声明行；
    - 若没有符合规范的注释行，则返回空字符串。
    """
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    # 空行跳过
                    continue
                # 跳过 shebang
                if stripped.startswith("#!"):
                    continue
                # 跳过常见的编码声明
                if "coding" in stripped and stripped.startswith("#"):
                    continue
                # 命中我们的约定：第一条 # 注释就是文件说明
                if stripped.startswith("#"):
                    text = stripped.lstrip("#").strip()
                    if not text:
                        return ""
                    if len(text) > max_len:
                        text = text[: max_len - 3] + "..."
                    return text
                # 如果第一条非空且不是 # 开头，说明不符合规范，直接停止
                break
    except Exception:
        return ""
    return ""


def partition_children(
    entries: List[Path], exclude: Set[str], patterns: List[str]
) -> List[Path]:
    """按名称/通配符过滤，并按照目录优先、再按名字 排序"""

    def excluded(p: Path) -> bool:
        name = p.name
        if name in exclude:
            return True
        if any(fnmatch.fnmatch(name, pat) for pat in patterns):
            return True
        return False

    filtered = [e for e in entries if not excluded(e)]
    filtered.sort(key=lambda p: (not p.is_dir(), p.name.lower()))
    return filtered


def tree_lines(
    root: Path,
    prefix_parts: List[str],
    max_depth: int,
    exclude: Set[str],
    patterns: List[str],
    follow_symlinks: bool,
) -> Iterable[str]:
    """生成root的树状行（不包含root本身）。"""
    if max_depth == 0:
        return  # 达到最大深度

    children = partition_children(safe_listdir(root), exclude, patterns)

    for idx, child in enumerate(children):
        is_last = idx == len(children) - 1
        connector = LAST if is_last else BRANCH
        prefix = "".join(prefix_parts)
        display_name = child.name

        try:
            is_link = child.is_symlink()
        except Exception:
            is_link = False

        marker = ""
        if is_link:
            # 符号链接的标记
            try:
                target = os.readlink(child)
                marker = f" -> {target}"
            except OSError:
                marker = " -> ?"

        header_comment = ""
        # 仅对 .py 文件尝试读取文件说明注释, 同时跳过__init__.py
        if child.suffix == ".py" and child.name != "__init__.py":
            header_comment = get_py_header_comment(child)
            if header_comment:
                display_name = f"{display_name}  # {header_comment}"
            else:
                display_name = f"{display_name}"
        # 目录递归

        yield f"{prefix}{connector}{display_name}{marker}"
        proceed = False
        try:
            if child.is_dir():
                if is_link and not follow_symlinks:
                    proceed = False
                else:
                    proceed = True
        except Exception:
            proceed = False

        if proceed:
            next_prefix_parts = prefix_parts + ([EMPTY] if is_last else [VERT])
            for line in tree_lines(
                child,
                next_prefix_parts,
                max_depth - 1 if max_depth > 0 else -1,
                exclude,
                patterns,
                follow_symlinks,
            ):
                yield line


def print_tree(
    root: Path,
    max_depth: int = -1,
    exclude: Set[str] | None = None,
    patterns: List[str] | None = None,
    follow_symlinks: bool = False,
    out_file: Path | None = None,
) -> None:
    # Windows 控制台可能需要确保 UTF-8
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    exclude = set(exclude or set())
    patterns = list(patterns or [])

    lines = []
    title = f"{root.resolve()}"
    lines.append(title)
    lines.append("")

    # 根目录名称做标题（更直观）
    lines.append(root.name)
    for line in tree_lines(
        root,
        prefix_parts=[],
        max_depth=max_depth if max_depth != 0 else -1,
        exclude=exclude,
        patterns=patterns,
        follow_symlinks=follow_symlinks,
    ):
        lines.append(line)

    output = "\n".join(lines)
    if out_file:
        try:
            out_file.write_text(output + "\n", encoding="utf-8")
            print(f"已写入到文件：{out_file}")
        except Exception as e:
            print(f"写文件失败：{e}", file=sys.stderr)
            print(output)
    else:
        print(output)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="打印目录树（默认从脚本所在目录开始）。"
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="根目录（默认：脚本自身所在目录）",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=-1,
        help="最大深度，-1 表示无限（默认：-1）",
    )
    parser.add_argument(
        "--exclude",
        nargs="*",
        default=list(DEFAULT_EXCLUDES),
        help=f"按名称排除（默认：{', '.join(sorted(DEFAULT_EXCLUDES))}）",
    )
    parser.add_argument(
        "--pattern",
        nargs="*",
        default=[],
        help="按通配符排除，如 '*.pyc' 'build*'",
    )
    parser.add_argument(
        "--follow-symlinks",
        action="store_true",
        help="是否跟随符号链接（默认不跟随）",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="将结果写入文件（例如：tree.txt）",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    root = args.root

    if not root.exists():
        print(f"根目录不存在：{root}", file=sys.stderr)
        sys.exit(1)
    if not root.is_dir():
        print(f"指定路径不是目录：{root}", file=sys.stderr)
        sys.exit(1)

    print_tree(
        root=root,
        max_depth=args.max_depth,
        exclude=set(args.exclude),
        patterns=list(args.pattern),
        follow_symlinks=args.follow_symlinks,
        out_file=args.out,
    )


if __name__ == "__main__":
    main()
