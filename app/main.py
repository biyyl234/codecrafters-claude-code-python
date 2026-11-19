


import argparse
import os
import sys
import json
from openai import OpenAI

API_KEY = os.getenv("OPENROUTER_API_KEY")
BASE_URL = os.getenv("OPENROUTER_BASE_URL", default="https://openrouter.ai/api/v1")
ENV = os.getenv("env")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("-p", required=True)
    args = p.parse_args()

    if not API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not set")

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
    }]

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

    print("Logs from your program will appear here!", file=sys.stderr)

if __name__ == "__main__":
    main()
