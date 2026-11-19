import argparse
import os
import sys
import openai#大模型库
import json

from openai import OpenAI

API_KEY = os.getenv("OPENROUTER_API_KEY")
BASE_URL = os.getenv("OPENROUTER_BASE_URL", default="https://openrouter.ai/api/v1")#配置LLM基本信息


def main():
    p = argparse.ArgumentParser()
    p.add_argument("-p", required=True)
    args = p.parse_args()

    if not API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not set")#检测apikey是否有效

    client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

    chat = client.chat.completions.create(
        model="anthropic/claude-haiku-4.5",#选择模型
        messages=[{"role": "user", "content": args.p}],
        tools=[{#告诉模型这里有工具可以使用，如果模型想要使用的话，会在response里面说的；同时因为要读文件了，所以对话流会随之终止
        "type": "function",
        "function": {
            "name": "Read",#读文件的工具
            "description": "Read and return the contents of a file",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "The path to the file to read"
                    }
                },
                "required": ["file_path"]#需要文件路径参数
            }
        }
    }])

    if not chat.choices or len(chat.choices) == 0:
        raise RuntimeError("no choices in response")

    # You can use print statements as follows for debugging, they'll be visible when running tests.
    print("Logs from your program will appear here!", file=sys.stderr)
'''
 #   print(chat.choices[0].message.content, 'abc')#作用是将模型生成的响应内容打印到标准输出
#调用本地工具并返回给模型结果
    for tc in chat.choices[0].message.tool_calls or []:#功能调用
        args = json.loads(tc.function.arguments)#作用是将模型返回的参数字符串解析为字典
        if tc.function.name == "Read":#作用是判断模型调用的工具是否是Read
            with open(args["file_path"]) as f:#作用是根据模型提供的文件路径参数，打开指定的文件，并将文件对象赋值给变量f
                print(f.read())#作用是将打开的文件内容读取出来，并打印到标准输出
'''


    if chat.choices[0].message.tool_calls:
        tool = chat.choices[0].message.tool_calls[0].function
        tool_name = tool.name
        tool_args = json.loads(tool.arguments)
        file_path = tool_args["file_path"]

        # Check the tool name
        if tool_name == "read_file":
            with open(file_path, "r") as file:
                print(file.read())
    else:
        print(chat.choices[0].message.content)


if __name__ == "__main__":#运行主程序时走这个逻辑
    main()



#工具调用怎么理解：模型在对话中可以使用工具（functions）来扩展其能力。通过在请求中提供工具的定义，模型可以选择调用这些工具来获取
# 额外的信息或执行特定的操作。在这个例子中，定义了一个名为 "Read" 的工具，它允许模型读取指定路径的文件内容。
#作用是提供给模型真实的文件读取能力，让模型在生成响应时可以访问文件系统中的数据。
#流程：
# 1. 模型接收到用户的输入（args.p）。
# 2. 模型在生成响应时，可以选择调用 "Read" 工具来读取文件内容。
# 3. 如果模型决定使用该工具，它会在响应中指示调用 "Read" 工具，并提供所需的参数（文件路径）。
# 4. 你的程序可以根据模型的指示，实际执行文件读取操作
