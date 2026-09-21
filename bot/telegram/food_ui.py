"""/calories: today's food with the total against the target; tap an item to remove it."""
from __future__ import annotations

from datetime import datetime

from bot.knowledge.views import esc
from bot.scheduler.outbound import Outbound


def day_view(store, now: datetime, message_id: int | None = None, toast: str | None = None) -> Outbound:
    profile = store.profile()
    items = store.food(now.date())
    total = sum(f["kcal"] for f in items)
    prot = sum(f["protein_g"] or 0 for f in items)
    carbs = sum(f["carbs_g"] or 0 for f in items)
    fat = sum(f["fat_g"] or 0 for f in items)
    head = f"<b>{total}</b>" + (f" / {profile.calorie_target}" if profile.calorie_target else "") + " kcal"
    if prot or profile.protein_target:
        head += f" · <b>{prot}</b>" + (f" / {profile.protein_target}" if profile.protein_target else "") + " g protein"
    macros = f"{carbs} g carbs · {fat} g fat" if (carbs or fat) else ""
    sodium = sum(f["sodium_mg"] or 0 for f in items)
    potassium = sum(f["potassium_mg"] or 0 for f in items)
    minerals = " · ".join(x for x in (f"{sodium} mg sodium" if sodium else "", f"{potassium} mg potassium" if potassium else "") if x)
    lines = ["<b>Today</b>", head] + ([macros] if macros else []) + ([minerals] if minerals else [])
    for f in items:
        bits = [f"{f['protein_g']}p" if f["protein_g"] is not None else "", f"{f['carbs_g']}c" if f["carbs_g"] is not None else "",
                f"{f['fat_g']}f" if f["fat_g"] is not None else ""]
        m = " · " + " ".join(b for b in bits if b) if any(bits) else ""
        lines.append(f"• {f['time']} {esc(f['item'])} — {'~' if f['estimate'] else ''}{f['kcal']} kcal{m}")
    if not items:
        lines.append("• Nothing logged. Say what you ate.")
    if not profile.calorie_target:
        lines.append("\nNo target set: \"my calorie target is 2800\".")
    buttons = [(f"{f['time']} {f['item']}"[:40], f"food:e:{i}") for i, f in enumerate(items)]
    return Outbound("\n".join(lines), buttons=buttons, kind="edit" if message_id else "reply", edit_message_id=message_id, toast=toast)


def item_view(store, now: datetime, index: int, message_id: int | None = None) -> Outbound:
    items = store.food(now.date())
    if index < 0 or index >= len(items):
        return day_view(store, now, message_id)
    f = items[index]
    text = f"<b>{esc(f['item'])}</b>\n{'~' if f['estimate'] else ''}{f['kcal']} kcal" + "".join(
        f", {f[k]} {unit} {label}" for k, unit, label in
        (("protein_g", "g", "protein"), ("carbs_g", "g", "carbs"), ("fat_g", "g", "fat"), ("sodium_mg", "mg", "sodium"), ("potassium_mg", "mg", "potassium"))
        if f[k] is not None)
    return Outbound(text, buttons=[("🗑 Remove", f"food:remove:{index}"), ("◀ Back", "food:day")],
                    kind="edit" if message_id else "reply", edit_message_id=message_id)


def handle(arg: str, store, state, now: datetime, message_id: int | None) -> list[Outbound]:
    what, _, idx = arg.partition(":")
    if what == "e" and idx.isdigit():
        return [item_view(store, now, int(idx), message_id)]
    if what == "remove" and idx.isdigit():
        items = store.food(now.date())
        if int(idx) < len(items):
            store.remove_food(now.date(), items[int(idx)]["line"])
            store.commit("food: removed")
            return [day_view(store, now, message_id, toast="Removed.")]
    return [day_view(store, now, message_id)]
