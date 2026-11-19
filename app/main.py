import argparse
import json
import os
import subprocess
import sys
import traceback
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
            print(f"Warning: failed to parse {skill_md}: {e}", file=sys.stderr)
            continue
        name = metadata.get("name", skill_dir.name)
        desc = str(metadata.get("description", "")).strip()
        lines.append(f"- {name}: {desc}")
    return "\n".join(lines) + "\n"


def build_tools():
    return [
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


def execute_tool(name: str, args_dict: dict) -> str:
    """执行单个工具调用，返回结果字符串。任何异常都转成错误信息返回给模型。"""
    try:
        if name == "Read":
            file_path = args_dict["file_path"]
            with open(file_path, encoding="utf-8") as f:
                return f.read()

        if name == "Bash":
            command = args_dict["command"]
            try:
                proc = subprocess.run(
                    command,
                    shell=True,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=30,
                )
            except subprocess.TimeoutExpired:
                return "Error: Command timed out after 30 seconds"
            if proc.returncode == 0:
                return (
                    f"[Exit code: 0] Command executed successfully\n"
                    f"Output:\n{proc.stdout}"
                )
            return (
                f"[Exit code: {proc.returncode}] Command failed\n"
                f"STDERR:\n{proc.stderr}\nSTDOUT:\n{proc.stdout}"
            )

        if name == "Write":
            file_path = args_dict["file_path"]
            content = args_dict["content"]
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            return f"Successfully wrote to file: {file_path}"

        return f"Error: unknown tool '{name}'"

    except KeyError as e:
        return f"Error: missing required argument {e}"
    except Exception as e:
        return f"Error executing tool '{name}': {e}"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("-p", required=True)
    args = p.parse_args()

    if not API_KEY:
        print("Error: OPENROUTER_API_KEY is not set", file=sys.stderr)
        sys.exit(1)

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
            print(
                f"Error: skill '{skill_name}' not found at {skill_md_path}",
                file=sys.stderr,
            )
            sys.exit(1)

        try:
            metadata, skill_content = parse_skill_md(skill_md_path)
        except Exception as e:
            print(f"Error: failed to parse {skill_md_path}: {e}", file=sys.stderr)
            sys.exit(1)

        system_prompt += (
            f"\n## Activated skill: {metadata.get('name', skill_name)}\n"
            f"{skill_content}\n"
        )
        # 关键修复：/skill 后面没有附加问题时，补一个非空占位内容，
        # 否则 Anthropic 等 provider 会报 "messages: at least one message is required"
        user_prompt = user_query or (
            f"Follow the instructions of the activated skill '{skill_name}'."
        )

    # 兜底：任何情况下都不发送空 user 内容
    if not str(user_prompt).strip():
        user_prompt = "Continue."

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    model_name = "anthropic/claude-haiku-4.5" if ENV != "local" else "openrouter/free"
    tools = build_tools()

    while True:
        try:
            chat = client.chat.completions.create(
                model=model_name,
                messages=messages,
                tools=tools,
            )
        except Exception as e:
            print(f"Error calling OpenRouter API: {e}", file=sys.stderr)
            traceback.print_exc()
            sys.exit(1)

        if not chat.choices:
            print("Error: API returned no choices", file=sys.stderr)
            sys.exit(1)

        response_message = chat.choices[0].message
        messages.append(response_message.model_dump(exclude_none=True))

        # 无工具调用 = 最终回复
        if not response_message.tool_calls:
            if response_message.content:
                print(response_message.content)
            sys.exit(0)

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

            result = execute_tool(tc.function.name, args_dict)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "name": tc.function.name,
                "content": result,
            })


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Fatal error: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)