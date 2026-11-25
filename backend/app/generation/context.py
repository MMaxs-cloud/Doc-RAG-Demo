import json
from pathlib import Path

from app.citations import source_label
from app.core.errors import AppError

SYSTEM = Path(__file__).with_name("system-v1.md").read_text(encoding="utf-8")


def uncovered(start, end, previous):
    ranges = [(start, end)]
    for left, right in previous:
        remaining = []
        for a, b in ranges:
            if right <= a or b <= left:
                remaining.append((a, b))
            else:
                if a < left:
                    remaining.append((a, left))
                if right < b:
                    remaining.append((right, b))
        ranges = remaining
    return ranges


def messages(question, history, entries):
    if entries is None:
        return [{"role": "system", "content": "Отвечай на языке пользователя. Эта беседа использует историю чата без поиска по документам. Не выдумывай ссылки, цитаты или проверенные значения."},
                *history, {"role": "user", "content": question}]
    context = json.dumps(entries, ensure_ascii=False)
    return [{"role": "system", "content": SYSTEM}, *history,
            {"role": "user", "content": f"Вопрос: {question}\n\nИсточники в JSON:\n{context}"}]


async def fit_history(provider, question, history, entries, options):
    available = provider.window - options.max_output_tokens - 64
    history = list(history)
    base = await provider.count(messages(question, history, entries))
    while history and base > available:
        history = history[2:]
        base = await provider.count(messages(question, history, entries))
    if base > available:
        raise AppError("generation_budget", "Вопрос и выходной лимит превышают окно модели", 422)
    return history, base, available


async def build_context(provider, question, history, retrieved, options, max_sources):
    history, base, available = await fit_history(provider, question, history, [], options)
    budget = min(available, base + options.context_tokens)
    chosen, entries, used = [], [], {}
    for source in retrieved:
        if len(entries) >= max_sources:
            break
        previous = used.setdefault((source["document_id"], source["document_version"]), [])
        start, end = source["start_offset"], source["end_offset"]
        ranges = uncovered(start, end, previous)
        if not ranges:
            continue
        fragments = [source["text"][a-start:b-start] for a, b in ranges]
        entry = {"id": source["source_id"], "document": source["title"],
                 "location": source_label(source["source_location"]), "text": fragments}
        if await provider.count(messages(question, history, [*entries, entry])) <= budget:
            entries.append(entry)
            chosen.append({**source, "context_ranges": ranges, "context_text": "\n…\n".join(fragments)})
            previous.extend(ranges)
            continue
        if entries:
            continue
        text = fragments[0]
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            sample = {**entry, "text": [text[:middle]]}
            if await provider.count(messages(question, history, [sample])) <= budget:
                low = middle
            else:
                high = middle - 1
        if low >= min(30, len(text)):
            entries.append({**entry, "text": [text[:low]]})
            span = (ranges[0][0], ranges[0][0] + low)
            chosen.append({**source, "context_ranges": [span], "context_text": text[:low]})
            previous.append(span)
    return messages(question, history, entries), chosen
