import argparse
import os
import sys
import json
import subprocess
from openai import OpenAI

API_KEY = os.getenv("OPENROUTER_API_KEY")
BASE_URL = os.getenv("OPENROUTER_BASE_URL", default="https://openrouter.ai/api/v1")
ENV = os.getenv("env")

def main():#main函数
    p = argparse.ArgumentParser()
    p.add_argument("-p", required=True)
    args = p.parse_args()

    if not API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not set")#核验apikey

    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    # 全局对话历史，全程复用
    messages = [{"role": "user", "content": args.p}]
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
