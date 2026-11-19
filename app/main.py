

import argparse
import os
import sys
import json
import subprocess
from pathlib import Path

import frontmatter
from openai import OpenAI

API_KEY = os.getenv("OPENROUTER_API_KEY")
BASE_URL = os.getenv("OPENROUTER_BASE_URL", default="https://openrouter.ai/api/v1")
ENV = os.getenv("env")

SKILLS_DIR = Path(".claude/skills")


def parse_skill_md(path: Path):
    """解析 SKILL.md，返回 (metadata dict, 正文内容)"""
    post = frontmatter.load(path)
    return dict(post.metadata or {}), post.content


def advertised_skills() -> str:
    """构建告知模型可用技能的系统提示"""
    if not SKILLS_DIR.exists():
        return ""
    lines = ["You have access to the following skills:\n"]
    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        try:
            metadata, _ = parse_skill_md(skill_md)
        except Exception as e:
            print(f"警告：解析 {skill_md} 失败: {e}", file=sys.stderr)
            continue
        name = metadata.get("name", skill_dir.name)
        desc = metadata.get("description", "").strip()
        lines.append(f"- {name}: {desc}")
    return "\n".join(lines) + "\n"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("-p", required=True)
    args = p.parse_args()

    if not API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not set")

    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

    system_prompt = advertised_skills()
    user_prompt = args.p

    # 处理 / 开头的显式技能调用
    if user_prompt.startswith("/"):
        parts = user_prompt[1:].split(maxsplit=1)
        skill_name = parts[0] if parts else ""
        user_query = parts[1] if len(parts) > 1 else ""

        skill_md_path = SKILLS_DIR / skill_name / "SKILL.md"
        if not skill_md_path.exists():
            print(f"错误：未找到技能 [{skill_name}]", file=sys.stderr)
            sys.exit(1)

        metadata, skill_content = parse_skill_md(skill_md_path)
        system_prompt += (
            f"\n## 已激活技能：{metadata.get('name', skill_name)}\n{skill_content}\n"
        )
        user_prompt = user_query

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    model_name = "anthropic/claude-haiku-4.5" if ENV != "local" else "openrouter/free"

    tools = [
        {
            "type": "function",
            "function": {
                "name": "Read",
                "description": "Read and return the contents of a file",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "The path to the file to read",
                        }
                    },
                    "required": ["file_path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "Bash",
                "description": "Execute a shell command",
                "parameters": {
                    "type": "object",
                    "required": ["command"],
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "The command to execute",
                        }
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "Write",
                "description": "Write content to a file",
                "parameters": {
                    "type": "object",
                    "required": ["file_path", "content"],
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "The path of the file to write to",
                        },
                        "content": {
                            "type": "string",
                            "description": "The content to write to the file",
                        },
                    },
                },
            },
        },
    ]

    while True:
        chat = client.chat.completions.create(
            model=model_name,
            messages=messages,
            tools=tools,
        )

        if not chat.choices:
            raise RuntimeError("no choices in response")

        response_message = chat.choices[0].message
        # 转成 dict 追加，兼容不同 SDK 版本
        messages.append(response_message.model_dump(exclude_none=True))

        # 无工具调用 = 最终回复
        if not response_message.tool_calls:
            if response_message.content:
                print(response_message.content)
            break

        # 逐个执行工具调用
        for tc in response_message.tool_calls:
            try:
                args_dict = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError as e:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.function.name,
                    "content": f"Error parsing tool arguments: {e}",
                })
                continue

            name = tc.function.name

            if name == "Read":
                try:
                    with open(args_dict["file_path"], encoding="utf-8") as f:
                        result = f.read()
                except Exception as e:
                    result = f"Error reading file: {e}"

            elif name == "Bash":
                try:
                    proc = subprocess.run(
                        args_dict["command"],
                        shell=True,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        timeout=30,
                    )
                    if proc.returncode == 0:
                        result = (
                            f"[Exit code: 0] Command executed successfully\n"
                            f"Output:\n{proc.stdout}"
                        )
                    else:
                        result = (
                            f"[Exit code: {proc.returncode}] Command failed\n"
                            f"STDERR:\n{proc.stderr}\nSTDOUT:\n{proc.stdout}"
                        )
                except subprocess.TimeoutExpired:
                    result = "Error: Command timed out after 30 seconds"
                except Exception as e:
                    result = f"Error executing command: {e}"

            elif name == "Write":
                try:
                    with open(args_dict["file_path"], "w", encoding="utf-8") as f:
                        f.write(args_dict["content"])
                    result = f"Successfully wrote to file: {args_dict['file_path']}"
                except Exception as e:
                    result = f"Error writing file: {e}"

            else:
                result = f"Unknown tool: {name}"

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "name": name,
                "content": result,
            })


if __name__ == "__main__":
    main()

