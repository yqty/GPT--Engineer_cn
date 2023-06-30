import json
import random
import tempfile

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from dataclasses_json import dataclass_json
from termcolor import colored

from gpt_engineer.db import DB, DBs
from gpt_engineer.domain import Step


@dataclass_json
@dataclass
class Review:
    ran: Optional[bool]  # 代码是否运行
    perfect: Optional[bool]  # 代码是否完美
    works: Optional[bool]  # 代码是否有用
    comments: str  # 评论
    raw: str  # 原始评论


@dataclass_json
@dataclass
class Learning:
    model: str  # 模型
    temperature: float  # 温度
    steps: str  # 步骤
    steps_file_hash: str  # 步骤文件哈希值
    prompt: str  # 提示
    logs: str  # 日志
    workspace: str  # 工作空间
    feedback: Optional[str]  # 反馈
    session: str  # 会话
    review: Optional[Review]  # 评论
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())  # 时间戳
    version: str = "0.3"  # 版本号


TERM_CHOICES = (
    colored("y", "green")
    + "/"
    + colored("n", "red")
    + "/"
    + colored("u", "yellow")
    + "(不确定): "
)


def human_input() -> Review:
    print()
    print(colored("为了帮助gpt-engineer学习，请回答以下3个问题:", "light_green"))
    print()

    ran = input("生成的代码是否运行？" + TERM_CHOICES)
    while ran not in ("y", "n", "u"):
        ran = input("无效的输入，请输入y、n或u: ")

    perfect = ""
    useful = ""

    if ran == "y":
        perfect = input("生成的代码是否完美？" + TERM_CHOICES)
        while perfect not in ("y", "n", "u"):
            perfect = input("无效的输入，请输入y、n或u: ")

        if perfect != "y":
            useful = input("生成的代码是否有用？" + TERM_CHOICES)
            while useful not in ("y", "n", "u"):
                useful = input("无效的输入，请输入y、n或u: ")

    comments = ""
    if perfect != "y":
        comments = input("如果有时间，请解释哪些部分不起作用 " + colored("(可以留空)\n", "light_green"))
    print(colored("谢谢", "light_green"))
    return Review(
        raw=", ".join([ran, perfect, useful]),
        ran={"y": True, "n": False, "u": None, "": None}[ran],
        works={"y": True, "n": False, "u": None, "": None}[useful],
        perfect={"y": True, "n": False, "u": None, "": None}[perfect],
        comments=comments,
    )


def logs_to_string(steps: List[Step], logs: DB):
    chunks = []
    for step in steps:
        chunks.append(f"--- {step.__name__} ---\n")
        messages = json.loads(logs[step.__name__])
        chunks.append(format_messages(messages))
    return "\n".join(chunks)


def format_messages(messages: List[dict]) -> str:
    return "\n".join(
        [f"{message['role']}:\n\n{message['content']}" for message in messages]
    )


def extract_learning(
    model: str, temperature: float, steps: List[Step], dbs: DBs, steps_file_hash
) -> Learning:
    review = None
    if "review" in dbs.memory:
        review = Review.from_json(dbs.memory["review"])  # type: ignore
    learning = Learning(
        prompt=dbs.input["prompt"],
        model=model,
        temperature=temperature,
        steps=json.dumps([step.__name__ for step in steps]),
        steps_file_hash=steps_file_hash,
        feedback=dbs.input.get("feedback"),
        session=get_session(),
        logs=logs_to_string(steps, dbs.logs),
        workspace=dbs.workspace["all_output.txt"],
        review=review,
    )
    return learning


def get_session():
    path = Path(tempfile.gettempdir()) / "gpt_engineer_user_id.txt"

    try:
        if path.exists():
            user_id = path.read_text()
        else:
            # 随机生成的UUID:
            user_id = str(random.randint(0, 2**32))
            path.write_text(user_id)
        return user_id
    except IOError:
        return "ephemeral_" + str(random.randint(0, 2**32))
