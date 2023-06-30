import json
import re
import subprocess

from enum import Enum
from typing import List

from termcolor import colored

from gpt_engineer.ai import AI
from gpt_engineer.chat_to_files import to_files
from gpt_engineer.db import DBs
from gpt_engineer.learning import human_input


def setup_sys_prompt(dbs: DBs) -> str:
    return dbs.preprompts["generate"] + "\n有用的信息:\n" + dbs.preprompts["philosophy"]


def get_prompt(dbs: DBs) -> str:
    """迁移期间，我们使用此备用的获取器"""
    assert "prompt" in dbs.input or "main_prompt" in dbs.input, "请将您的提示放在项目目录中的prompt文件中"

    if "prompt" not in dbs.input:
        print(colored("请将提示放在`prompt`文件中，而不是`main_prompt`", "red"))
        print()
        return dbs.input["main_prompt"]

    return dbs.input["prompt"]


# 以下所有步骤的签名均为Step
def simple_gen(ai: AI, dbs: DBs) -> List[dict]:
    """在主提示上运行AI并保存结果"""
    messages = ai.start(setup_sys_prompt(dbs), get_prompt(dbs))
    to_files(messages[-1]["content"], dbs.workspace)
    return messages


def clarify(ai: AI, dbs: DBs) -> List[dict]:
    """
    询问用户是否需要澄清任何内容，并将结果保存到工作区
    """
    messages = [ai.fsystem(dbs.preprompts["qa"])]
    user_input = get_prompt(dbs)
    while True:
        messages = ai.next(messages, user_input)

        if messages[-1]["content"].strip() == "没有更多要澄清的内容。":
            break

        if messages[-1]["content"].strip().lower().startswith("no"):
            print("没有更多要澄清的内容。")
            break

        print()
        user_input = input("(回答文本中的问题，或者选择“c”继续)\n")
        print()

        if not user_input or user_input == "c":
            print("(让gpt-engineer自己做出假设)")
            print()
            messages = ai.next(
                messages,
                "在开始之前，请自行做出假设并明确说明它们",
            )
            print()
            return messages

        user_input += (
            "\n\n"
            "还有其他不清楚的地方吗？如果是，请仅以以下形式回答：\n"
            "{剩余的不清楚的问题} 剩余的问题。\n"
            "{下一个问题}\n"
            "如果一切都足够清楚，请仅回答“没有更多要澄清的内容”。"
        )

    print()
    return messages


def gen_spec(ai: AI, dbs: DBs) -> List[dict]:
    """
    根据主提示+澄清生成规范，并将结果保存到工作区
    """
    messages = [
        ai.fsystem(setup_sys_prompt(dbs)),
        ai.fsystem(f"说明：{dbs.input['prompt']}"),
    ]

    messages = ai.next(messages, dbs.preprompts["spec"])

    dbs.memory["specification"] = messages[-1]["content"]

    return messages


def respec(ai: AI, dbs: DBs) -> List[dict]:
    messages = json.loads(dbs.logs[gen_spec.name])
    messages += [ai.fsystem(dbs.preprompts["respec"])]

    messages = ai.next(messages)
    messages = ai.next(
        messages,
        ("根据目前的对话，请重新阐述程序的规范。" "如果有可以改进的地方，请加以改进。" "如果您对规范满意，请将规范逐字逐句地再次写出。"),
    )

    dbs.memory["specification"] = messages[-1]["content"]
    return messages


def gen_unit_tests(ai: AI, dbs: DBs) -> List[dict]:
    """
    #根据规范生成应该有效的单元测试。
    """
    messages = [
        ai.fsystem(setup_sys_prompt(dbs)),
        ai.fuser(f"说明：{dbs.input['prompt']}"),
        ai.fuser(f"规范：\n\n{dbs.memory['specification']}"),
    ]

    messages = ai.next(messages, dbs.preprompts["unit_tests"])

    dbs.memory["unit_tests"] = messages[-1]["content"]
    to_files(dbs.memory["unit_tests"], dbs.workspace)

    return messages


def gen_clarified_code(ai: AI, dbs: DBs) -> List[dict]:
    """获取澄清并生成代码"""

    messages = json.loads(dbs.logs[clarify.__name__])

    messages = [
        ai.fsystem(setup_sys_prompt(dbs)),
    ] + messages[1:]
    messages = ai.next(messages, dbs.preprompts["use_qa"])

    to_files(messages[-1]["content"], dbs.workspace)
    return messages


def gen_code(ai: AI, dbs: DBs) -> List[dict]:
    # 获取前一步的消息

    messages = [
        ai.fsystem(setup_sys_prompt(dbs)),
        ai.fuser(f"说明：{dbs.input['prompt']}"),
        ai.fuser(f"规范：\n\n{dbs.memory['specification']}"),
        ai.fuser(f"单元测试：\n\n{dbs.memory['unit_tests']}"),
    ]
    messages = ai.next(messages, dbs.preprompts["use_qa"])
    to_files(messages[-1]["content"], dbs.workspace)
    return messages


def execute_entrypoint(ai: AI, dbs: DBs) -> List[dict]:
    command = dbs.workspace["run.sh"]

    print("您是否要执行此代码？")
    print()
    print(command)
    print()
    print('如果是，请按回车键。否则，输入"no"')
    print()
    if input() not in ["", "y", "yes"]:
        print("好的，不执行代码。")
        return []
    print("正在执行代码...")
    print()
    print(
        colored(
            "注意：如果代码的执行结果与预期不符，请考虑以其他方式运行代码，而不是上述方式。",
            "green",
        )
    )
    print()
    print("您可以按ctrl+c *一次*来停止执行。")
    print()

    p = subprocess.Popen("bash run.sh", shell=True, cwd=dbs.workspace.path)
    try:
        p.wait()
    except KeyboardInterrupt:
        print()
        print("停止执行。")
        print("执行已停止。")
        p.kill()
        print()

    return []


def gen_entrypoint(ai: AI, dbs: DBs) -> List[dict]:
    messages = ai.start(
        system=(
            "您将获得当前文件夹中当前磁盘上的代码库的信息。\n"
            "您需要用包含所有必要的Unix终端命令的代码块回答，"
            "这些命令包括："
            "a）安装依赖项"
            "b）运行代码库的所有必要部分（如果有必要，可以并行运行）。\n"
            "不要进行全局安装。不要使用sudo。\n"
            "不要解释代码，只需给出命令。\n"
            "不要使用占位符，如果有必要，请使用示例值（例如.表示文件夹参数）。\n"
        ),
        user="代码库的信息：\n\n" + dbs.workspace["all_output.txt"],
    )
    print()

    regex = r"```\S*\n(.+?)```"
    matches = re.finditer(regex, messages[-1]["content"], re.DOTALL)
    dbs.workspace["run.sh"] = "\n".join(match.group(1) for match in matches)
    return messages


def use_feedback(ai: AI, dbs: DBs):
    messages = [
        ai.fsystem(setup_sys_prompt(dbs)),
        ai.fuser(f"说明：{dbs.input['prompt']}"),
        ai.fassistant(dbs.workspace["all_output.txt"]),
        ai.fsystem(dbs.preprompts["use_feedback"]),
    ]
    messages = ai.next(messages, dbs.input["feedback"])
    to_files(messages[-1]["content"], dbs.workspace)
    return messages


def fix_code(ai: AI, dbs: DBs):
    code_output = json.loads(dbs.logs[gen_code.name])[-1]["content"]
    messages = [
        ai.fsystem(setup_sys_prompt(dbs)),
        ai.fuser(f"说明：{dbs.input['prompt']}"),
        ai.fuser(code_output),
        ai.fsystem(dbs.preprompts["fix_code"]),
    ]
    messages = ai.next(messages, "请修复上面代码中的任何错误。")
    to_files(messages[-1]["content"], dbs.workspace)
    return messages


def human_review(ai: AI, dbs: DBs):
    review = human_input()
    dbs.memory["review"] = review.to_json()  # type: ignore
    return []


class Config(str, Enum):
    DEFAULT = "default"
    BENCHMARK = "benchmark"
    SIMPLE = "simple"
    TDD = "tdd"
    TDD_PLUS = "tdd+"
    CLARIFY = "clarify"
    RESPEC = "respec"
    EXECUTE_ONLY = "execute_only"
    EVALUATE = "evaluate"
    USE_FEEDBACK = "use_feedback"


# 不同的配置决定要运行的步骤
STEPS = {
    Config.DEFAULT: [
        clarify,
        gen_clarified_code,
        gen_entrypoint,
        execute_entrypoint,
        human_review,
    ],
    Config.BENCHMARK: [simple_gen, gen_entrypoint],
    Config.SIMPLE: [simple_gen, gen_entrypoint, execute_entrypoint],
    Config.TDD: [
        gen_spec,
        gen_unit_tests,
        gen_code,
        gen_entrypoint,
        execute_entrypoint,
        human_review,
    ],
    Config.TDD_PLUS: [
        gen_spec,
        gen_unit_tests,
        gen_code,
        fix_code,
        gen_entrypoint,
        execute_entrypoint,
        human_review,
    ],
    Config.CLARIFY: [
        clarify,
        gen_clarified_code,
        gen_entrypoint,
        execute_entrypoint,
        human_review,
    ],
    Config.RESPEC: [
        gen_spec,
        respec,
        gen_unit_tests,
        gen_code,
        fix_code,
        gen_entrypoint,
        execute_entrypoint,
        human_review,
    ],
    Config.USE_FEEDBACK: [
        use_feedback,
        gen_entrypoint,
        execute_entrypoint,
        human_review,
    ],
    Config.EXECUTE_ONLY: [execute_entrypoint],
    Config.EVALUATE: [execute_entrypoint, human_review],
}
