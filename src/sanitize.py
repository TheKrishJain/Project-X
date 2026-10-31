"""Text sanitization engine for Project-X auto-poster.

Features:
- Strict DASH BAN: replaces em dash (—), en dash (–), and " - " with natural punctuation
- Hashtag stripping from body text
- Double/multiple whitespace cleanup
- Leading and trailing quote removal
- Hard assertions: publishing hard-fails if any dash survives
"""

from __future__ import annotations

import re


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
    # e.g., "The ERP didn't come to the kirana — it disguised itself" -> "The ERP didn't come to the kirana. It disguised itself"
    def _capitalize_clause(match: re.Match[str]) -> str:
        word = match.group(1)
        # Pronouns and conjunctions starting new clauses
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
            # If word is a clause starter
            return f", {next_char}"
        return f". {next_char}"

    text = re.sub(r"\s+-\s+([a-zA-Z])", _replace_spaced_hyphen, text)
    text = re.sub(r"\s+-\s+", ". ", text)

    return text


def sanitize_post(text: str) -> str:
    """Apply complete sanitization pipeline to a social post.
    
    Steps:
    1. Strip leading and trailing quotes (", ', “, ”, ‘, ’)
    2. Replace em dash, en dash, and ' - ' with natural punctuation
    3. Strip all hashtags (#tag) from body text
    4. Normalize whitespace (no double spaces)
    5. Clean up redundant punctuation (e.g. '.. ', ',.')
    6. Verify no em/en dashes survive
    """
    if not text:
        return ""

    cleaned = text.strip()

    # 1. Strip leading and trailing quotation marks
    cleaned = re.sub(r"^[\"\'“”‘’]+|[\"\'“”‘’]+$", "", cleaned).strip()

    # 2. Replace dashes
    cleaned = replace_dashes_naturally(cleaned)

    # 3. Strip hashtags in body
    cleaned = re.sub(r"#\w+", "", cleaned)

    # 4. Clean up any weird punctuation resulting from replacements
    cleaned = re.sub(r"\s*,\s*\.", ".", cleaned)
    cleaned = re.sub(r"\s*\.\s*,", ".", cleaned)
    cleaned = re.sub(r"\.{2,}", ".", cleaned)
    cleaned = re.sub(r",{2,}", ",", cleaned)
    cleaned = re.sub(r"\s+([.,!?;:])", r"\1", cleaned)

    # 5. Normalize whitespace (convert multiple spaces/tabs to single space, preserve newlines)
    lines = cleaned.splitlines()
    sanitized_lines = [re.sub(r"[ \t]+", " ", line).strip() for line in lines]
    cleaned = "\n".join(sanitized_lines).strip()

    # Final cleanup of quotes in case stripping revealed them
    cleaned = re.sub(r"^[\"\'“”‘’]+|[\"\'“”‘’]+$", "", cleaned).strip()

    # 6. Hard safety assertion
    assert_no_dashes(cleaned)

    return cleaned


def assert_no_dashes(text: str) -> None:
    """Hard-fail validation: raise ValueError if any em dash or en dash survives."""
    if "—" in text:
        raise ValueError(f"CRITICAL SANITIZATION FAILURE: Em dash ('—') survived in post: {text[:80]}...")
    if "–" in text:
        raise ValueError(f"CRITICAL SANITIZATION FAILURE: En dash ('–') survived in post: {text[:80]}...")


def run_unit_tests() -> bool:
    """Unit test suite verifying dash elimination and post sanitization."""
    test_cases = [
        # (Input, Expected property: no dash, no hashtags, trimmed)
        ("The ERP didn't come to the kirana — it disguised itself as a calculator.", "kirana. It disguised itself"),
        ("Apple launched the M1 – Intel was left behind.", "M1. Intel was left behind"),
        ("They didn't sell coffee - they planted a habit.", "coffee, they planted a habit"),
        ('"Great founders focus on distribution — not just code."', "distribution, not just code"),
        ("Check out this #startup #marketing trick!", "Check out this trick!"),
        ("Multiple    spaces    should    collapse.", "Multiple spaces should collapse."),
        ("Amateurs talk strategy — pros talk logistics — legends talk supply chains.", "Amateurs talk strategy"),
    ]

    for inp, expected_snippet in test_cases:
        out = sanitize_post(inp)
        assert "—" not in out, f"Em dash survived in '{inp}' -> '{out}'"
        assert "–" not in out, f"En dash survived in '{inp}' -> '{out}'"
        assert "#" not in out, f"Hashtag survived in '{inp}' -> '{out}'"
        assert "  " not in out, f"Double space survived in '{inp}' -> '{out}'"
        assert not out.startswith('"') and not out.endswith('"'), f"Quotes survived in '{inp}' -> '{out}'"
        assert expected_snippet.lower() in out.lower(), f"Expected '{expected_snippet}' in '{out}'"

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
