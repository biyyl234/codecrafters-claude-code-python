'''
import argparse
import os
import sys
import json
import subprocess
import re
import frontmatter
from pathlib import Path
from openai import OpenAI
import asyncio
from claude_agent_sdk import query, ClaudeAgentOptions

API_KEY = os.getenv("OPENROUTER_API_KEY")
BASE_URL = os.getenv("OPENROUTER_BASE_URL", default="https://openrouter.ai/api/v1")
ENV = os.getenv("env")

def main():#main函数
    p = argparse.ArgumentParser()#意思是创建一个ArgumentParser对象，用于解析命令行参数。ArgumentParser是argparse模块中的一个类，它提供了处理命令行参数的功能。
    p.add_argument("-p", required=True)#意思是添加一个命令行参数-p，并且这个参数是必须的（required=True），也就是说在运行脚本时必须提供这个参数，否则会报错。
    args = p.parse_args()#作用是解析命令行参数，并将解析结果存储在args对象中。args对象的属性对应于定义的命令行参数，这样你就可以通过args.p来访问-p参数的值。

    if not API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not set")#核验apikey

    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    # 全局对话历史，全程复用
#以下用来告知大模型skill信息
    system_prompt = ""
    skills_path = Path(".claude/skills/")
    if skills_path.exists():
        system_prompt += "You have access to the following skills:\n\n"
        for skill in skills_path.iterdir():
            skill_text = (skill / "SKILL.md").read_text()
            system_prompt += "- " + ": ".join(
                re.search(r"--\nname: (.*)\ndescription: (.*)\n--", skill_text).groups()#原理是使用正则表达式从SKILL.md文件中提取技能的名称和描述，并将其格式化为一个字符串，添加到system_prompt中。具体来说，re.search函数会在skill_text中查找匹配模式"--\nname: (.*)\ndescription: (.*)\n--"的内容，并返回一个匹配对象。然后，使用groups()方法获取匹配的组，即技能的名称和描述，并将它们用": "连接起来，最后添加到system_prompt字符串中。
            )

#以上用来告知大模型skill信息
            user_prompt = args.p
system_prompt = advertised_skills()

# 处理 / 开头的显式技能调用
if user_prompt.startswith("/"):
    # 拆分技能名和用户实际问题，最多拆一次
    parts = user_prompt[1:].split(maxsplit=1)
    skill_name = parts[0]
    user_query = parts[1] if len(parts) > 1 else ""
    
    # 拼接路径并校验存在性
    skill_md_path = Path(".claude/skills") / skill_name / "SKILL.md"
    if not skill_md_path.exists():
        print(f"错误：未找到技能 [{skill_name}]", file=sys.stderr)
        sys.exit(1)
    
    # 解析技能，把完整指令追加到系统提示（给模型的操作指南）
    metadata, skill_content = parse_skill_md(skill_md_path)
    system_prompt += f"\n## 已激活技能：{metadata.get('name', skill_name)}\n{skill_content}\n"
    
    # 保留用户的真实问题作为 user_prompt
    user_prompt = user_query

#以上用来加载指定skill的正文部分
    messages = [
      { "role": "system", "content": "You have access to the following skills:" },
       { "role": "user", "content": args.p }
 ]
    # 根据环境选择模型
    model_name = "anthropic/claude-haiku-4.5" if ENV != "local" else "openrouter/free"

    # 工具定义抽离，避免重复编写
    tools = [{
        "type": "function",
        "function": {
            "name": "Read",
            "description": "Read and return the contents of a file",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "The path to the file to read"
                    }
                },
                "required": ["file_path"]
            }
        }
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
          "description": "The command to execute"
        }
      }
        }
  }
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
                         "description": "The path of the file to write to"
                       },
                      "content": {
                            "type": "string",
                       "description": "The content to write to the file"
        }
      }
    }
  }
}
    ]

    while True:
        # 修复：使用对话历史变量，不再写死
        chat = client.chat.completions.create(
            model=model_name,
            messages=messages,
            tools=tools
        )

        if not chat.choices or len(chat.choices) == 0:
            raise RuntimeError("no choices in response")

        response_message = chat.choices[0].message
        # 把模型回复加入对话历史
        messages.append(response_message)

        # 没有工具调用 = 模型给出最终回复，打印并退出循环
        if not response_message.tool_calls:
            if response_message.content is not None:
                print(response_message.content)
            break  # 修复：添加退出条件，结束循环

        # 有工具调用，逐个执行并返回结果
        for tc in response_message.tool_calls:
            args_dict = json.loads(tc.function.arguments)
            if tc.function.name == "Read":
                try:
                    with open(args_dict["file_path"]) as f:
                        result = f.read()
                except Exception as e:
                    # 读文件异常也要返回给模型，避免对话卡住
                    result = f"Error reading file: {str(e)}"
                
                # 工具结果加入对话历史
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.function.name,
                    "content": result,
                })

            if tc.function.name == "Bash":
                try:
                    command = args_dict["command"]
        
        # 执行 bash 命令，捕获输出、设置超时
                    proc = subprocess.run(
                        command,
                        shell=True,          # 支持 bash 语法（管道、重定向等）
                        capture_output=True, # 捕获 stdout 和 stderr
                        text=True,           # 返回字符串而非字节
                        encoding="utf-8",
                        timeout=30           # 超时时间，单位秒，防止命令卡死
                        )

        # 组装返回结果，把退出码、标准输出、错误输出都返回给模型
                    if proc.returncode == 0:
                        result = f"[Exit code: 0] Command executed successfully\nOutput:\n{proc.stdout}"
                    else:
                        result = f"[Exit code: {proc.returncode}] Command failed\nSTDERR:\n{proc.stderr}\nSTDOUT:\n{proc.stdout}"

                except subprocess.TimeoutExpired:
                    result = f"Error: Command timed out after 30 seconds"
                except Exception as e:
                    result = f"Error executing command: {str(e)}"
                messages.append({
                                                "role": "tool",
                                                "tool_call_id": tc.id,
                                                "name": tc.function.name,
                                                "content": result,
                                            })
                
            if tc.function.name == "Write":
                try:
                    file_path = args_dict["file_path"]
        # 取出模型传入的待写入内容
                    content = args_dict["content"]
        
        # "w" 表示覆盖写入模式；文件不存在会自动创建，已存在会清空原有内容
        # 指定 encoding="utf-8" 避免中文乱码
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(content)
        
        # 执行成功，返回结果给模型
                    result = f"Successfully wrote to file: {file_path}"
                except Exception as e:
                                # 写入文件异常也要返回给模型，避免对话卡住
                    result = f"Error writing file: {str(e)}"
                            
                            # 工具结果加入对话历史
                messages.append({
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "name": tc.function.name,
                                "content": result,
                            })
                

    print("Logs from your program will appear here!", file=sys.stderr)

if __name__ == "__main__":
    main()
'''


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

