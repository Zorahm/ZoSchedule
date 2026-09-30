"""События изменений — размеченное объединение по полю ``kind``.

Черновик (``*Draft``) — то, что вычислил дифф. Событие (``Moved`` и прочие) — то
же самое плюс идентификатор и время обнаружения, проставленные хранилищем.
Классы событий наследуют черновики, чтобы список полей существовал в одном
месте: добавленное в черновик поле автоматически попадает в API.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, Field

ChangeKind = Literal["moved", "cancelled", "added", "teacher_changed"]


class ChangeCore(BaseModel):
    """Общее у всех событий: к какой паре относится."""

    date: dt.date
    discipline: str
    retake: bool = False
    """Событие о пересдаче: в ленте она называется пересдачей, а не парой."""


class Identified(BaseModel):
    id: int
    detected_at: dt.datetime


class MovedDraft(ChangeCore):
    """Пара сменила время, аудиторию или и то и другое.

    Несёт и старое, и новое значение: «перенесена» без «откуда» ничего не
    говорит, а лента изменений существует именно ради этого.
    """

    kind: Literal["moved"] = "moved"
    from_time: str
    to_time: str
    from_room: str | None = None
    to_room: str | None = None


class CancelledDraft(ChangeCore):
    kind: Literal["cancelled"] = "cancelled"
    at_time: str
    room: str | None = None


class AddedDraft(ChangeCore):
    kind: Literal["added"] = "added"
    at_time: str
    room: str | None = None
    teacher: str | None = None


class TeacherChangedDraft(ChangeCore):
    kind: Literal["teacher_changed"] = "teacher_changed"
    at_time: str
    from_teacher: str | None = None
    to_teacher: str | None = None


ChangeDraft: TypeAlias = Annotated[
    MovedDraft | CancelledDraft | AddedDraft | TeacherChangedDraft,
    Field(discriminator="kind"),
]


class Moved(MovedDraft, Identified):
    pass


class Cancelled(CancelledDraft, Identified):
    pass


class Added(AddedDraft, Identified):
    pass


class TeacherChanged(TeacherChangedDraft, Identified):
    pass


ChangeEvent: TypeAlias = Annotated[
    Moved | Cancelled | Added | TeacherChanged,
    Field(discriminator="kind"),
]

_EVENT_BY_KIND: dict[str, type[Moved] | type[Cancelled] | type[Added] | type[TeacherChanged]] = {
    "moved": Moved,
    "cancelled": Cancelled,
    "added": Added,
    "teacher_changed": TeacherChanged,
}


def build_event(draft_payload: dict[str, object], *, id: int, detected_at: dt.datetime) -> ChangeEvent:
    """Собирает событие из сохранённого payload и служебных полей."""
    kind = draft_payload.get("kind")
    if not isinstance(kind, str) or kind not in _EVENT_BY_KIND:
        raise ValueError(f"Неизвестный вид события изменения: {kind!r}")
    model = _EVENT_BY_KIND[kind]
    return model.model_validate({**draft_payload, "id": id, "detected_at": detected_at})
