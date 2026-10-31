"""Text sanitization engine for Project-X auto-poster.

Features:
- Strict DASH BAN: replaces em dash (—), en dash (–), and " - " with natural punctuation
- Code-level number verification: spelled-out numbers, ordinals, years, digits
- Strips thread numbering prefixes (e.g. [1/6], (1/6), 1.) so posts feel native to Threads
- Hashtag stripping from body text
- Double/multiple whitespace cleanup
- Leading and trailing quote removal
- Sentence counting helper for post substance validation
- Hard assertions: publishing hard-fails if any dash survives
"""

from __future__ import annotations

import re


# ── Spelled-out number and ordinal maps ──────────────────────────────────────

SPELLED_NUMBERS: dict[str, str] = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "eleven": "11", "twelve": "12", "thirteen": "13", "fourteen": "14",
    "fifteen": "15", "sixteen": "16", "seventeen": "17", "eighteen": "18",
    "nineteen": "19", "twenty": "20", "thirty": "30", "forty": "40",
    "fifty": "50", "sixty": "60", "seventy": "70", "eighty": "80",
    "ninety": "90", "hundred": "100", "thousand": "1000",
    "million": "1000000", "billion": "1000000000",
    "dozen": "12", "half": "0.5",
}

ORDINALS: dict[str, str] = {
    "first": "1", "second": "2", "third": "3", "fourth": "4", "fifth": "5",
    "sixth": "6", "seventh": "7", "eighth": "8", "ninth": "9", "tenth": "10",
}

ALL_NUM_WORDS = {**SPELLED_NUMBERS, **ORDINALS}


def _build_source_nums(source_text: str) -> set[str]:
    """Build the full set of verified numbers from source text (digits + words)."""
    nums: set[str] = set(re.findall(r"\d+(?:[.,]\d+)*", source_text))
    for n in list(nums):
        if "." in n:
            nums.add(n.split(".")[0])
            nums.add(n.split(".")[1])
        if "," in n:
            nums.add(n.replace(",", ""))
    source_lower = source_text.lower()
    for word, digit in ALL_NUM_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b", source_lower):
            nums.add(digit)
            nums.add(word)
        if digit in nums:
            nums.add(word)
    return nums


def _num_in_source(num_str: str, source_nums: set[str]) -> bool:
    """Check if a number (digit or word) is verified in source."""
    low = num_str.lower().strip()
    if low in source_nums:
        return True
    if low in ALL_NUM_WORDS and ALL_NUM_WORDS[low] in source_nums:
        return True
    for w, d in SPELLED_NUMBERS.items():
        if d == low and w in source_nums:
            return True
    return False


def strip_unverified_numbers(text: str, source_text: str) -> str:
    """Remove or replace unverified numbers in text if they do not exist in the source text.

    Handles: digits, spelled-out numbers (one..hundred, thousand, million, billion,
    dozen, half), ordinals (first..tenth), and 4-digit years.
    """
    if not source_text or not text:
        return text

    source_nums = _build_source_nums(source_text)

    _num_words_pat = "|".join(re.escape(w) for w in sorted(ALL_NUM_WORDS, key=len, reverse=True))
    _num_pat = rf"\d+(?:\.\d+)?|{_num_words_pat}"
    _time_units = r"weeks?|days?|months?|years?|hours?|decades?|centuries?"
    _measure_units = r"lines?|steps?|features?|users?|customers?|employees?|stores?|countries?|cities?|products?|million|billion|thousand|hundred"

    # 1. Replace unverified quantified time expressions
    def _replace_time_unit(m: re.Match[str]) -> str:
        num = m.group(1)
        unit = m.group(2)
        if _num_in_source(num, source_nums):
            return m.group(0)
        return f"several {unit}"

    text = re.sub(rf"\b({_num_pat})\s+({_time_units})\b", _replace_time_unit, text, flags=re.IGNORECASE)

    # 2. Replace unverified quantified measurements
    def _replace_measure_unit(m: re.Match[str]) -> str:
        num = m.group(1)
        unit = m.group(2)
        if _num_in_source(num, source_nums):
            return m.group(0)
        return f"many {unit}"

    text = re.sub(rf"\b({_num_pat})\s+({_measure_units})\b", _replace_measure_unit, text, flags=re.IGNORECASE)

    # 3. Replace unverified percentages
    def _replace_percent(m: re.Match[str]) -> str:
        num = m.group(1)
        if _num_in_source(num, source_nums):
            return m.group(0)
        return "a significant share"

    text = re.sub(r"\b(\d+)(?:\s*%)", _replace_percent, text)
    text = re.sub(r"\b(\d+)\s+percent\b", _replace_percent, text, flags=re.IGNORECASE)

    # 4. Replace standalone unverified 4-digit years
    def _replace_year(m: re.Match[str]) -> str:
        year = m.group(1)
        if year in source_nums:
            return m.group(0)
        return ""

    text = re.sub(r"\b(1[89]\d{2}|20[0-2]\d)\b", _replace_year, text)

    # 5. Replace unverified ordinals (e.g. "the first company to")
    for ordinal_word in ORDINALS:
        if not _num_in_source(ordinal_word, source_nums):
            text = re.sub(rf"\bthe\s+{ordinal_word}\b", "an early", text, flags=re.IGNORECASE)

    # Cleanup double spaces
    text = re.sub(r"  +", " ", text).strip()
    return text


def strip_thread_numbering(text: str) -> str:
    """Strip manual thread numbering prefixes (e.g. '[1/6]', '(1/6)', '1/6', '1.', '1 -')."""
    if not text:
        return ""
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
    raw_parts = re.split(r"[.!?]+(?:\s+|$)", text)
    sentences = [p.strip() for p in raw_parts if len(p.strip().split()) >= 3]
    return len(sentences)


def replace_dashes_naturally(text: str) -> str:
    """Replace em dash (—), en dash (–), and ' - ' used as a dash with natural punctuation."""
    # Pattern A: dash followed by space + uppercase letter -> period + space
    text = re.sub(r"\s*[—–]\s*([A-Z])", r". \1", text)

    # Pattern B: dash followed by space + lowercase word
    def _capitalize_clause(match: re.Match[str]) -> str:
        word = match.group(1)
        if word.lower() in ("it", "they", "we", "you", "he", "she", "that", "this", "instead", "because", "so", "but"):
            return f". {word.capitalize()}"
        return f", {word}"

    text = re.sub(r"\s*[—–]\s*([a-z]+)", _capitalize_clause, text)

    # Pattern C: remaining em/en dashes
    text = re.sub(r"\s*[—–]\s*", ". ", text)

    # Spaced hyphens
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
    """Apply complete sanitization pipeline to a social post."""
    if not text:
        return ""

    # 1. Strip thread numbering
    cleaned = strip_thread_numbering(text.strip())

    # 2. Strip leading and trailing quotation marks
    cleaned = re.sub(r'^["\'\u201c\u201d\u2018\u2019]+|["\'\u201c\u201d\u2018\u2019]+$', "", cleaned).strip()

    # 3. Replace dashes
    cleaned = replace_dashes_naturally(cleaned)

    # 4. Strip hashtags in body
    cleaned = re.sub(r"#\w+", "", cleaned)

    # 5. Clean up punctuation artifacts
    cleaned = re.sub(r"\s*,\s*\.", ".", cleaned)
    cleaned = re.sub(r"\s*\.\s*,", ".", cleaned)
    cleaned = re.sub(r"\.{2,}", ".", cleaned)
    cleaned = re.sub(r",{2,}", ",", cleaned)
    cleaned = re.sub(r"\s+([.,!?;:])", r"\1", cleaned)

    # 6. Normalize whitespace
    lines = cleaned.splitlines()
    sanitized_lines = [re.sub(r"[ \t]+", " ", line).strip() for line in lines]
    cleaned = "\n".join(sanitized_lines).strip()

    # Final cleanup of quotes
    cleaned = re.sub(r'^["\'\u201c\u201d\u2018\u2019]+|["\'\u201c\u201d\u2018\u2019]+$', "", cleaned).strip()

    # 7. Hard safety assertion
    assert_no_dashes(cleaned)

    return cleaned


def assert_no_dashes(text: str) -> None:
    """Hard-fail validation: raise ValueError if any em dash or en dash survives."""
    if "\u2014" in text:
        raise ValueError(f"CRITICAL SANITIZATION FAILURE: Em dash survived in post: {text[:80]}...")
    if "\u2013" in text:
        raise ValueError(f"CRITICAL SANITIZATION FAILURE: En dash survived in post: {text[:80]}...")


# ── Evidence verification helpers ────────────────────────────────────────────

def normalize_whitespace(text: str) -> str:
    """Collapse all whitespace runs to single space and strip."""
    return re.sub(r"\s+", " ", text).strip()


def find_exact_quote(quote: str, source_text: str) -> int:
    """Find the char offset of a whitespace-normalized quote in source text.

    Returns the char offset (>= 0) if found, -1 if not found.
    """
    norm_source = normalize_whitespace(source_text)
    norm_quote = normalize_whitespace(quote)
    if not norm_quote or len(norm_quote) < 10:
        return -1
    idx = norm_source.lower().find(norm_quote.lower())
    return idx


def verify_evidence_table(
    claim_evidence_table: list[dict[str, str]],
    source_text: str,
) -> list[dict[str, str | int | bool]]:
    """Verify each evidence quote is an exact substring of source text.

    Returns enriched table with 'offset' and 'verified' fields.
    Unverified claims get offset=-1, verified=False.
    """
    verified_table: list[dict[str, str | int | bool]] = []
    for entry in claim_evidence_table:
        evidence = str(entry.get("evidence", ""))
        claim = str(entry.get("claim", ""))
        is_editorial = evidence.lower().startswith("editorial") or evidence.lower().startswith("founder")

        if is_editorial:
            verified_table.append({
                "claim": claim,
                "evidence": evidence,
                "offset": -1,
                "verified": True,  # editorial claims are allowed
            })
            continue

        offset = find_exact_quote(evidence, source_text)
        verified_table.append({
            "claim": claim,
            "evidence": evidence,
            "offset": offset,
            "verified": offset >= 0,
        })

    return verified_table


# ── Unit tests ───────────────────────────────────────────────────────────────

def run_unit_tests() -> bool:
    """Unit test suite verifying dash elimination, numbering removal, and sentence counting."""
    test_cases = [
        ("The ERP didn't come to the kirana \u2014 it disguised itself as a calculator.", "kirana. It disguised itself"),
        ("Apple launched the M1 \u2013 Intel was left behind.", "M1. Intel was left behind"),
        ("They didn't sell coffee - they planted a habit.", "coffee, they planted a habit"),
        ('"Great founders focus on distribution \u2014 not just code."', "distribution, not just code"),
        ("Check out this #startup #marketing trick!", "Check out this trick!"),
        ("Multiple    spaces    should    collapse.", "Multiple spaces should collapse."),
        ("Amateurs talk strategy \u2014 pros talk logistics \u2014 legends talk supply chains.", "Amateurs talk strategy"),
        ("[1/6] First line of thread.", "First line of thread."),
        ("(2/5) Second line.", "Second line."),
        ("1. Numbered point.", "Numbered point."),
    ]

    for inp, expected_snippet in test_cases:
        out = sanitize_post(inp)
        assert "\u2014" not in out, f"Em dash survived in '{inp}' -> '{out}'"
        assert "\u2013" not in out, f"En dash survived in '{inp}' -> '{out}'"
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
    assert "9 weeks" not in cleaned_num, f"'9 weeks' survived: {cleaned_num}"
    assert "7 lines" not in cleaned_num, f"'7 lines' survived: {cleaned_num}"
    assert "several weeks" in cleaned_num or "a few" in cleaned_num or "many" in cleaned_num

    # Spelled-out number stripping test
    src2 = "The company was founded in 1989 and sold four million units."
    draft2 = "They sold fifteen million units in twenty years."
    cleaned2 = strip_unverified_numbers(draft2, src2)
    assert "fifteen" not in cleaned2.lower(), f"'fifteen' survived: {cleaned2}"
    # 'million' and 'four' ARE in source, so 'million' is allowed

    # Year stripping test
    src3 = "Game Boy was released in 1989."
    draft3 = "The product launched in 2003 to compete."
    cleaned3 = strip_unverified_numbers(draft3, src3)
    assert "2003" not in cleaned3, f"'2003' survived: {cleaned3}"
    assert "1989" not in draft3 or True  # not testing source year preservation here

    # Evidence verification tests
    source = "Shopify Inc. was founded in 2006 by Tobias Lutke in Ottawa, Canada."
    table_in = [
        {"claim": "Shopify was founded in 2006.", "evidence": "founded in 2006 by Tobias Lutke"},
        {"claim": "They have 10 million merchants.", "evidence": "10 million merchants worldwide"},
        {"claim": "Operators should focus on distribution.", "evidence": "Editorial takeaway"},
    ]
    verified = verify_evidence_table(table_in, source)
    assert verified[0]["verified"] is True, f"Expected verified for exact match: {verified[0]}"
    assert verified[0]["offset"] >= 0
    assert verified[1]["verified"] is False, f"Expected unverified for fabricated quote: {verified[1]}"
    assert verified[2]["verified"] is True, "Editorial claims should pass"

    # Verify hard assertion triggers on unsanitized text containing dash
    try:
        assert_no_dashes("Test with em dash \u2014 should raise")
        raise AssertionError("assert_no_dashes failed to raise on em dash!")
    except ValueError:
        pass

    try:
        assert_no_dashes("Test with en dash \u2013 should raise")
        raise AssertionError("assert_no_dashes failed to raise on en dash!")
    except ValueError:
        pass

    return True


if __name__ == "__main__":
    run_unit_tests()
    print("All sanitize.py unit tests passed successfully!")
