"""Unified ``arknightsavatar`` entry point (subcommand dispatch).

Routes one subcommand to its module: the six orchestration entries
(run / pull / produce / derive-model / sync-cache / setup) plus the fourteen
single tools. Each target module exposes ``main(argv) -> int``; the exit code
is propagated.
"""

from __future__ import annotations

import importlib
import sys

from arknightsavatar import __version__

ORCHESTRATION: dict[str, tuple[str, str]] = {
    "run": ("arknightsavatar.run", "全流程编排：fetch → unpack → classify → match → extract → export-webp → npc-json"),
    "pull": ("arknightsavatar.pull", "设备侧资源获取：fetch（--with-apk 可选追加 pull-apk）"),
    "produce": ("arknightsavatar.produce", "离线生产：classify → match → extract → export-webp → npc-json"),
    "build-model": ("arknightsavatar.build_model", "从零构建推导模型：fetch → unpack → classify → match → detect-bases → derive-model（含设备/APK 资源拉取）"),
    "derive-model": ("arknightsavatar.derive_model", "由 face/head 识别报告重新拟合头像推导模型"),
    "sync-cache": ("arknightsavatar.sync_cache", "同步数据目录到 GitHub 数据仓库并自动增量提交"),
    "setup": ("arknightsavatar.setup", "初始化：全量同步（含 export）或交互选择分类下载数据文件"),
}

TOOLS: dict[str, tuple[str, str]] = {
    "fetch": ("arknightsavatar.fetch", "拉取 AB 资源到 data/raw（增量 + sha256 manifest）"),
    "unpack": ("arknightsavatar.unpack.unpacker", "把 AB 解包为全分辨率 RGBA PNG + meta.json"),
    "classify": ("arknightsavatar.classify", "立绘底图/差分分类，输出 characters_classified.json"),
    "sample-bases": ("arknightsavatar.sample_bases", "底图随机抽样（识别调试）"),
    "match": ("arknightsavatar.match", "头像模板匹配定位底图中的头像范围"),
    "detect": ("arknightsavatar.detect", "人脸识别（anime-face-detector YOLOv3，top-1）"),
    "detect-bases": ("arknightsavatar.detect_bases", "高置信底图人脸/头部识别 + 可视化"),
    "extract": ("arknightsavatar.extract", "三档裁切框提取 180×180 头像（核心生产步骤）"),
    "collage": ("arknightsavatar.collage", "差分头像拼贴（调试）"),
    "export-webp": ("arknightsavatar.export_webp", "PNG 头像转 WebP（RGBA）"),
    "npc-json": ("arknightsavatar.npc_json", "生成旧项目格式的 NPC 头像索引 JSON"),
    "manifest": ("arknightsavatar.manifest_tool", "生成增量更新数据文件（内容清单/version.json/变更清单/CSV）"),
    "pull-apk": ("arknightsavatar.pull_apk", "从设备拉取已安装游戏 APK 到本地"),
    "device-caps": ("arknightsavatar.device_caps", "检测 ADB 设备命令能力（tar/unzip 等批量拉取依赖）"),
}


def usage() -> str:
    lines = [
        "usage: arknightsavatar <subcommand> [options...]",
        "",
        "编排入口:",
    ]
    for name, (_, description) in ORCHESTRATION.items():
        lines.append(f"  {name:<14} {description}")
    lines.append("")
    lines.append("单工具:")
    for name, (_, description) in TOOLS.items():
        lines.append(f"  {name:<14} {description}")
    lines.append("")
    lines.append("options:")
    lines.append("  -h, --help     show this help message and exit")
    lines.append("  -V, --version  show version and exit")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(usage())
        return 0
    if argv[0] in ("-V", "--version"):
        print(f"arknightsavatar {__version__}")
        return 0

    name, rest = argv[0], argv[1:]
    table: dict[str, tuple[str, str]] = {}
    table.update(ORCHESTRATION)
    table.update(TOOLS)
    if name not in table:
        print(f"error: unknown subcommand: {name}\n", file=sys.stderr)
        print(usage(), file=sys.stderr)
        return 2

    module_name = table[name][0]
    try:
        module = importlib.import_module(module_name)
    except ImportError as error:
        print(f"error: cannot import {module_name}: {error}", file=sys.stderr)
        return 1
    return int(module.main(rest))


if __name__ == "__main__":
    raise SystemExit(main())
