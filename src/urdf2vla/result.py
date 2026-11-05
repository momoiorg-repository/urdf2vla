"""Skill の実行結果（Agent と CLI が共通で読む JSON 契約）。"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

Status = Literal["ok", "needs_input", "error"]


class Question(BaseModel):
    """ユーザにしか決められない設定。Agent は候補をそのまま提示し、自分で選ばない。"""

    key: str  # project.yaml の answers に保存されるキー（例: "arms.0.ee_link"）
    prompt: str
    candidates: list[Any] = Field(default_factory=list)
    reason: str = ""
    suggestion: Any | None = None  # 参考値。回答として自動採用はしない


class Inferred(BaseModel):
    """URDF から一意に決まった値。Agent は import 前にまとめてユーザに確認させる。"""

    key: str
    value: Any
    evidence: str


class SkillResult(BaseModel):
    skill: str
    status: Status = "ok"
    outputs: dict[str, str] = Field(default_factory=dict)
    questions: list[Question] = Field(default_factory=list)
    inferred: list[Inferred] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)
    log: str | None = None
    next: list[str] = Field(default_factory=list)  # 次に実行できる Skill

    def ask(self, question: Question) -> None:
        self.questions.append(question)
        if self.status == "ok":
            self.status = "needs_input"

    def fail(self, message: str) -> None:
        self.errors.append(message)
        self.status = "error"

    def to_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), ensure_ascii=False, indent=2)
