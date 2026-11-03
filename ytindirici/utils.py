"""Small helpers that don't depend on the UI or yt-dlp."""


def format_ranges(numbers):
    """[1, 2, 3, 7, 9, 10] -> '1-3,7,9-10' (yt-dlp playlist_items syntax)."""
    numbers = sorted(set(numbers))
    parts, i = [], 0
    while i < len(numbers):
        j = i
        while j + 1 < len(numbers) and numbers[j + 1] == numbers[j] + 1:
            j += 1
        parts.append(str(numbers[i]) if i == j else f"{numbers[i]}-{numbers[j]}")
        i = j + 1
    return ",".join(parts)


def parse_ranges(text):
    """'1-3, 7, 10-9' -> {1, 2, 3, 7, 9, 10}. Raises ValueError on bad input."""
    result = set()
    for part in text.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = map(int, part.split("-", 1))
            result.update(range(min(a, b), max(a, b) + 1))
        else:
            result.add(int(part))
    return result


def format_duration(seconds):
    """3725 -> '1:02:05', 65 -> '1:05', None -> ''."""
    if not seconds:
        return ""
    seconds = int(seconds)
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
