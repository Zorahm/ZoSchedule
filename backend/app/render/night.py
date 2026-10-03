"""Ночная тема: переопределения светлого CSS, действуют под `.frame.night`.

Вёрстка и шрифты те же, меняются только цвета. Выходной остаётся зелёным, а
пересдача красной, чтобы «выходной», «не опубликовано» и «пересдача» не слились.
"""

from __future__ import annotations

_RULES = """
.eyebrow,.sub,.time .e,.tags,.meta,.upd,.dat,.ln .r,.kinds,.none,.legend,
.place .lbl,.place .bld,.time .pn,.empty{color:#8d96a8}
.sub b,.tag{color:#eef1f7}
.pill{border-color:#3a4256;color:#eef1f7;background:#151a23}
h1{color:#fff;letter-spacing:-.03em}
.chip{border-color:#2b3243;color:#aab2c3;background:#141922}
.chip.on{background:#c6ff3d;border-color:#c6ff3d;color:#0e1117}
.chip.off{background:#17382a;border-color:#2f8a5f;color:#7be0a0}
.card,.row,.empty{background:#161b25;border-color:#252c3b}
.empty.off{color:#7be0a0}
.time,.place,.who,.foot{border-color:#252c3b}
.tag{border-color:#46506a}
.badge.blue{background:#1c2c4e;color:#9bbcff}
.badge.green{background:#16372a;color:#86e5a8}
.badge.red{background:#4a1f18;color:#ff9d88}
.badge.gray{background:#252b38;color:#a0a9bb}
.k-blue{--dot:#6b9bff}.k-green{--dot:#5fd48a}.k-red{--dot:#ff6a4d}.k-gray{--dot:#7a8397}
.tag.retake{background:#ff6a4d;border-color:#ff6a4d;color:#1a0b08}
.card.retake{background:#2a1713;border-color:#ff6a4d}
.ln.retake{background:#2a1713}
.ln.retake .t,.ln.exam .t{color:#ff8a70}
.row.off .wd{background:#17382a;border:1px solid #2f8a5f}
.row.off .dow,.row.off .dat{color:#86e5a8}
.logo{color:#fff}
.logo span{color:#c6ff3d}
"""

_FRAME = ".frame.night{background:#0e1117;color:#eef1f7}"


def _scoped(rules: str) -> str:
    """Дописывает `.night ` к каждому селектору: специфичность выше светлых правил."""
    result: list[str] = []
    for rule in rules.split("}"):
        if not rule.strip():
            continue
        selectors, body = rule.split("{", 1)
        scoped = ",".join(f".night {selector.strip()}" for selector in selectors.split(","))
        result.append(f"{scoped}{{{body}}}")
    return "".join(result)


NIGHT_CSS = _FRAME + _scoped(_RULES)
