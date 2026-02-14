# Task processor - parses task room message and returns reply text
import re
from pathlib import Path

def _extract_path_from_count_folders_intent(text):
    if "多少文件夹" not in text or "下" not in text:
        return None
    m = re.search(r"检查\s*([^\s]+)\s*下\s*有多少文件夹", text)
    if m:
        return m.group(1).strip()
    m = re.search(r"检查\s*(/[^\s]*)\s*.*多少文件夹", text)
    if m:
        return m.group(1).strip()
    return None

def process_task_message(message_text):
    path = _extract_path_from_count_folders_intent(message_text)
    if path is None:
        return "暂不支持该任务描述。请使用例如：检查 /home/caros 下有多少文件夹"
    p = Path(path)
    if not p.exists():
        return "路径不存在：" + path
    if not p.is_dir():
        return "不是目录：" + path
    count = sum(1 for _ in p.iterdir() if _.is_dir())
    return "目录 " + path + " 下共有 **" + str(count) + "** 个文件夹。"
