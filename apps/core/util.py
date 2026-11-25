"""Мелкие общие помощники."""


def parse_int(value, max_digits=9):
    """Целое число из строки запроса или None. Принимает только ASCII-цифры ограниченной длины:
    str.isdigit() пропускает и «²», и очень длинные строки, а int() на них падает или тратит время."""
    if not isinstance(value, str) or not value.isascii() or not value.isdigit() or len(value) > max_digits:
        return None
    return int(value)
