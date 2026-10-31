"""Text sanitization engine for Project-X auto-poster.

Features:
- Strict DASH BAN: replaces em dash (—), en dash (–), and " - " with natural punctuation
- Strips thread numbering prefixes (e.g. [1/6], (1/6), 1.) so posts feel native to Threads
- Hashtag stripping from body text
- Double/multiple whitespace cleanup
- Leading and trailing quote removal
- Sentence counting helper for post substance validation
- Hard assertions: publishing hard-fails if any dash survives
"""

from __future__ import annotations

import re


def strip_unverified_numbers(text: str, source_text: str) -> str:
    """Remove or replace unverified numbers in text if they do not exist in the source text.
    
    Examples:
    - 'In 9 weeks they launched' -> 'In several weeks they launched'
    - 'Built in 7 lines of code' -> 'Built in a few lines of code'
    - 'Over 42 days' -> 'Over several days'
    """
    if not source_text or not text:
        return text

    source_nums = set(re.findall(r"\b\d+(?:[.,]\d+)?\b", source_text))
    word_to_num = {
        "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
        "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    }
    source_lower = source_text.lower()
    for w, n in word_to_num.items():
        if re.search(rf"\b{w}\b", source_lower):
            source_nums.add(n)
            source_nums.add(w)

    # 1. Replace unverified quantified time expressions (e.g. 9 weeks, 42 days)
    def _replace_time_unit(m: re.Match[str]) -> str:
        num = m.group(1)
        unit = m.group(2)
        if num in source_nums or num.lower() in source_nums:
            return m.group(0)
        return f"several {unit}"

    text = re.sub(
        r"\b(\d+|nine|eight|seven|six|five|four|three|two|one)\s+(weeks?|days?|months?|years?|hours?)\b",
        _replace_time_unit,
        text,
        flags=re.IGNORECASE,
    )

    # 2. Replace unverified quantified measurements (e.g. 7 lines, 5 steps)
    def _replace_measure_unit(m: re.Match[str]) -> str:
        num = m.group(1)
        unit = m.group(2)
        if num in source_nums or num.lower() in source_nums:
            return m.group(0)
        return f"a few {unit}"

    text = re.sub(
        r"\b(\d+|nine|eight|seven|six|five|four|three|two|one)\s+(lines?|steps?|features?)\b",
        _replace_measure_unit,
        text,
        flags=re.IGNORECASE,
    )

    # 3. Replace unverified percentages (e.g. 50%, 80 percent)
    def _replace_percent(m: re.Match[str]) -> str:
        num = m.group(1)
        if num in source_nums:
            return m.group(0)
        return "a notable portion"

    text = re.sub(r"\b(\d+)(?:\s*%)", _replace_percent, text)
    text = re.sub(r"\b(\d+)\s+percent\b", _replace_percent, text, flags=re.IGNORECASE)

    return text


def strip_thread_numbering(text: str) -> str:
    """Strip manual thread numbering prefixes (e.g. '[1/6]', '(1/6)', '1/6', '1.', '1 -')."""
    if not text:
        return ""
    # Strip at start of string or start of lines
    lines = text.splitlines()
    cleaned_lines = []
    for line in lines:
        cleaned = re.sub(
            r"^\s*(?:\[\s*\d+\s*(?:/\s*\d+)?\s*\]|\(\s*\d+\s*(?:/\s*\d+)?\s*\)|\d+\s*/\s*\d+|\d+\.|\d+\s*-\s*)\s*",
            "",
            line,
        )
        cleaned_lines.append(cleaned)
    return "\n".join(cleaned_lines)


def count_sentences(text: str) -> int:
    """Count complete sentences in text. A valid sentence contains at least 3 words."""
    if not text:
        return 0
    # Split by terminal punctuation followed by space, newline, or end of string
    raw_parts = re.split(r"[.!?]+(?:\s+|$)", text)
    sentences = [p.strip() for p in raw_parts if len(p.strip().split()) >= 3]
    return len(sentences)


def replace_dashes_naturally(text: str) -> str:
    """Replace em dash (—), en dash (–), and ' - ' used as a dash with natural punctuation.
    
    If the dash is followed by a capital letter or connects full clauses, replace with a period + new sentence.
    If it introduces a short clause or list item, replace with a comma or period depending on context.
    Fallback: '. '
    """
    # 1. Handle em dash (—) and en dash (–)
    # Pattern A: dash followed by space + uppercase letter -> period + space
    text = re.sub(r"\s*[—–]\s*([A-Z])", r". \1", text)
    
    # Pattern B: dash followed by space + lowercase word that looks like a new clause
    def _capitalize_clause(match: re.Match[str]) -> str:
        word = match.group(1)
        if word.lower() in ("it", "they", "we", "you", "he", "she", "that", "this", "instead", "because", "so", "but"):
            return f". {word.capitalize()}"
        return f", {word}"

    text = re.sub(r"\s*[—–]\s*([a-z]+)", _capitalize_clause, text)
    
    # Pattern C: remaining em/en dashes with spaces or standalone
    text = re.sub(r"\s*[—–]\s*", ". ", text)
    
    # 2. Handle ' - ' used as a dash (space hyphen space)
    # Avoid touching hyphenated words like 'well-known' or 'real-time'
    def _replace_spaced_hyphen(match: re.Match[str]) -> str:
        next_char = match.group(1)
        if next_char.isupper():
            return f". {next_char}"
        if next_char.islower():
            return f", {next_char}"
        return f". {next_char}"

    text = re.sub(r"\s+-\s+([a-zA-Z])", _replace_spaced_hyphen, text)
    text = re.sub(r"\s+-\s+", ". ", text)

    return text


def sanitize_post(text: str) -> str:
    """Apply complete sanitization pipeline to a social post.
    
    Steps:
    1. Strip thread numbering prefixes ([1/6], 1., etc.)
    2. Strip leading and trailing quotation marks (", ', “, ”, ‘, ’)
    3. Replace em dash, en dash, and ' - ' with natural punctuation
    4. Strip all hashtags (#tag) from body text
    5. Clean up redundant punctuation (e.g. '.. ', ',.')
    6. Normalize whitespace (no double spaces)
    7. Verify no em/en dashes survive
    """
    if not text:
        return ""

    # 1. Strip thread numbering
    cleaned = strip_thread_numbering(text.strip())

    # 2. Strip leading and trailing quotation marks
    cleaned = re.sub(r"^[\"\'“”‘’]+|[\"\'“”‘’]+$", "", cleaned).strip()

    # 3. Replace dashes
    cleaned = replace_dashes_naturally(cleaned)

    # 4. Strip hashtags in body
    cleaned = re.sub(r"#\w+", "", cleaned)

    # 5. Clean up any weird punctuation resulting from replacements
    cleaned = re.sub(r"\s*,\s*\.", ".", cleaned)
    cleaned = re.sub(r"\s*\.\s*,", ".", cleaned)
    cleaned = re.sub(r"\.{2,}", ".", cleaned)
    cleaned = re.sub(r",{2,}", ",", cleaned)
    cleaned = re.sub(r"\s+([.,!?;:])", r"\1", cleaned)

    # 6. Normalize whitespace (convert multiple spaces/tabs to single space, preserve newlines)
    lines = cleaned.splitlines()
    sanitized_lines = [re.sub(r"[ \t]+", " ", line).strip() for line in lines]
    cleaned = "\n".join(sanitized_lines).strip()

    # Final cleanup of quotes in case stripping revealed them
    cleaned = re.sub(r"^[\"\'“”‘’]+|[\"\'“”‘’]+$", "", cleaned).strip()

    # 7. Hard safety assertion
    assert_no_dashes(cleaned)

    return cleaned


def assert_no_dashes(text: str) -> None:
    """Hard-fail validation: raise ValueError if any em dash or en dash survives."""
    if "—" in text:
        raise ValueError(f"CRITICAL SANITIZATION FAILURE: Em dash ('—') survived in post: {text[:80]}...")
    if "–" in text:
        raise ValueError(f"CRITICAL SANITIZATION FAILURE: En dash ('–') survived in post: {text[:80]}...")


def run_unit_tests() -> bool:
    """Unit test suite verifying dash elimination, numbering removal, and sentence counting."""
    test_cases = [
        ("The ERP didn't come to the kirana — it disguised itself as a calculator.", "kirana. It disguised itself"),
        ("Apple launched the M1 – Intel was left behind.", "M1. Intel was left behind"),
        ("They didn't sell coffee - they planted a habit.", "coffee, they planted a habit"),
        ('"Great founders focus on distribution — not just code."', "distribution, not just code"),
        ("Check out this #startup #marketing trick!", "Check out this trick!"),
        ("Multiple    spaces    should    collapse.", "Multiple spaces should collapse."),
        ("Amateurs talk strategy — pros talk logistics — legends talk supply chains.", "Amateurs talk strategy"),
        ("[1/6] First line of thread.", "First line of thread."),
        ("(2/5) Second line.", "Second line."),
        ("1. Numbered point.", "Numbered point."),
    ]

    for inp, expected_snippet in test_cases:
        out = sanitize_post(inp)
        assert "—" not in out, f"Em dash survived in '{inp}' -> '{out}'"
        assert "–" not in out, f"En dash survived in '{inp}' -> '{out}'"
        assert "#" not in out, f"Hashtag survived in '{inp}' -> '{out}'"
        assert "  " not in out, f"Double space survived in '{inp}' -> '{out}'"
        assert not out.startswith('"') and not out.endswith('"'), f"Quotes survived in '{inp}' -> '{out}'"
        assert expected_snippet.lower() in out.lower(), f"Expected '{expected_snippet}' in '{out}'"

    # Sentence count tests
    s3 = "Stripe made payments simple. They charged 2.9 percent plus 30 cents. Developers loved it."
    assert count_sentences(s3) == 3, f"Expected 3 sentences, got {count_sentences(s3)}"

    s1 = "Only one sentence here."
    assert count_sentences(s1) == 1, f"Expected 1 sentence, got {count_sentences(s1)}"

    # Number verification & stripping tests
    src_sample = "Shopify was founded in 2006 by Tobias Lutke."
    draft_sample = "Tobias built the system in 9 weeks with 7 lines of code."
    cleaned_num = strip_unverified_numbers(draft_sample, src_sample)
    assert "9 weeks" not in cleaned_num
    assert "7 lines" not in cleaned_num
    assert ("several weeks" in cleaned_num or "a few" in cleaned_num)

    # Verify hard assertion triggers on unsanitized text containing dash
    try:
        assert_no_dashes("Test with em dash — should raise")
        raise AssertionError("assert_no_dashes failed to raise on em dash!")
    except ValueError:
        pass

    try:
        assert_no_dashes("Test with en dash – should raise")
        raise AssertionError("assert_no_dashes failed to raise on en dash!")
    except ValueError:
        pass

    return True


if __name__ == "__main__":
    run_unit_tests()
    print("All sanitize.py unit tests passed successfully!")
