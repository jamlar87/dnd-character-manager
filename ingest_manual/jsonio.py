from __future__ import annotations
from typing import Any
from pathlib import Path
import json
import re


# ═══════════════════════════════════════════════════════════════════════════════
# JSON helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_json(text: str) -> dict | None:
    """Extract JSON from LLM response, stripping markdown wrappers.
    Includes repair logic for common LLM JSON errors: control chars,
    trailing commas, truncated output, unclosed brackets."""
    if not text:
        return None

    # Strip markdown code fences
    candidates = []
    if "```" in text:
        parts = text.split("```")
        for i, part in enumerate(parts):
            if i % 2 == 1:  # inside code block
                if part.startswith("json"):
                    part = part[4:]
                candidates.append(part.strip())
    else:
        candidates.append(text.strip())

    for candidate in candidates:
        result = _try_parse_json(candidate)
        if result is not None:
            return result

    # Last resort: regex for JSON object anywhere in raw text
    m = re.search(r'\{[\s\S]*\}', text)
    if m:
        result = _try_parse_json(m.group())
        if result is not None:
            return result

    return None


def _try_parse_json(raw: str) -> dict | None:
    """Try to parse JSON with progressive repair attempts."""
    # 0. Fix OCR artifacts BEFORE parsing — prevents corrupted text from
    #    surviving into the data at all. Applied to raw LLM output.
    raw = _fix_ocr_artifacts(raw)

    # 1. Straight parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 1b. Strip a markdown code fence. DeepSeek returns bare JSON; free/other models
    #     (Gemini, OpenRouter) wrap it in ```json … ```, and the later repair attempts
    #     cannot see through it — their last-ditch regex only matches two levels deep,
    #     while an extraction response is three or more. Without this, a perfectly good
    #     response parses to nothing and the run looks like the model found no items.
    fenced = re.search(r"```(?:json)?\s*(.+?)\s*```", raw, re.S)
    if fenced:
        inner = fenced.group(1)
        try:
            return json.loads(inner)
        except json.JSONDecodeError:
            raw = inner

    # 2. Remove control characters (0x00-0x1F except \t, \n, \r)
    cleaned = _clean_json_controls(raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 3. Fix trailing commas before } or ]
    fixed = re.sub(r',\s*([}\]])', r'\1', cleaned)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    # 4. Try to close truncated JSON (add missing brackets/quotes)
    truncated = _close_truncated_json(fixed)
    if truncated != fixed:
        try:
            return json.loads(truncated)
        except json.JSONDecodeError:
            pass

    # 5. Aggressive: extract the largest valid JSON sub-object
    for m in re.finditer(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', raw):
        try:
            candidate = _clean_json_controls(m.group())
            candidate = re.sub(r',\s*([}\]])', r'\1', candidate)
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue

    return None


def _clean_json_controls(text: str) -> str:
    """Remove control characters that break JSON, preserving \t, \n, \r in strings.
    Strategy: replace raw control chars inside string values with spaces."""
    result = []
    in_string = False
    escape = False
    for ch in text:
        if escape:
            result.append(ch)
            escape = False
            continue
        if ch == '\\':
            result.append(ch)
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            result.append(ch)
            continue
        if in_string:
            # Inside a string value: replace raw control chars with space
            if ord(ch) < 0x20 and ch not in ('\t', '\n', '\r'):
                result.append(' ')
            else:
                result.append(ch)
        else:
            result.append(ch)
    return ''.join(result)


# ── OCR artifact correction ──
# These patterns come from real PDF→text extraction where serif fonts
# cause systematic misreads: Y→V, t→l, m→rn, etc.
_OCR_FIXES: list[tuple[str, str]] = [
    # V↔Y confusion (serif capital Y reads as V)
    (r'\bVou\b', 'You'), (r'\bvou\b', 'you'), (r'\bVour\b', 'Your'),
    # l↔t confusion (lowercase t reads as l)
    (r'\blhe\b', 'the'), (r'\blhal\b', 'that'), (r'\blhis\b', 'this'),
    (r'\blhan\b', 'than'), (r'\blhen\b', 'then'), (r'\blhere\b', 'there'),
    (r'\blhose\b', 'those'), (r'\blhing\b', 'thing'), (r'\blhink\b', 'think'),
    (r'\blhree\b', 'three'), (r'\blhrough\b', 'through'), (r'\blhrow\b', 'throw'),
    (r'\blhrone\b', 'throne'), (r'\blurn\b', 'turn'),
    # l↔1 confusion in dice notation
    (r'\bld(\d+)\b', r'1d\1'), (r'\bId(\d+)\b', r'1d\1'),
    # rn→m / ll→m (OCR misreads)
    (r'\bllaximum\b', 'maximum'), (r'\bllinimum\b', 'minimum'),
    (r'\bllove(s|d|ment)?\b', r'move\1'), (r'\bllagic(al)?\b', r'magic\1'),
    (r'\bllodifier\b', 'modifier'), (r'\bllake\b', 'make'),
    (r'\bllay\b', 'may'), (r'\bllust\b', 'must'), (r'\bllore\b', 'more'),
    (r'\bllonth\b', 'month'), (r'\bllinute\b', 'minute'),
    (r'\bllaterial\b', 'material'), (r'\bllaster\b', 'master'),
    (r'\bIllaster\b', 'master'),
    (r'\brnaximum\b', 'maximum'), (r'\brnagic\b', 'magic'), (r'\brnake\b', 'make'),
    # Common mangled words
    (r'\bcrealure\b', 'creature'), (r'\bcrealures\b', 'creatures'),
    (r'\bdalllage\b', 'damage'), (r'\baclion\b', 'action'),
    (r'\bbeffecl\b', 'effect'), (r'\bbeffecls\b', 'effects'),
    (r'\breaclion\b', 'reaction'), (r'\bbenelils?\b', 'benefits'),
    (r'\bproleclion\b', 'protection'), (r'\bRolI\b', 'Roll'),
    (r'\bnotjust\b', 'not just'), (r'\bbdore\b', 'before'),
    (r'\bWhcn\b', 'When'), (r'\bbeeornes\b', 'becomes'),
    (r'\bdiscordam\b', 'discordant'), (r'\bfillthe\b', 'fill the'),
    (r'\blevei\b', 'level'), (r'\bleveI\b', 'level'),
    (r'\bproliciency\b', 'proficiency'), (r'\bproticiency\b', 'proficiency'),
    (r"olheI'", "other"), (r"ralheI'", 'rather'),
    (r'\blry\b', 'try'), (r'\bwilhin\b', 'within'), (r'\bwilh\b', 'with'),
    (r'\bvalnerable\b', 'vulnerable'),
    # Mixed case / spacing
    (r'lesse r\b', 'lesser'), (r'\bof20\b', 'of 20'),
]


def _fix_ocr_artifacts(text: str) -> str:
    """Apply OCR correction patterns to a string. Idempotent — safe to call
    on already-clean text (no double-correction)."""
    if not text:
        return text
    for pattern, replacement in _OCR_FIXES:
        text = re.sub(pattern, replacement, text)
    return text


def _close_truncated_json(text: str) -> str:
    """Close truncated JSON by adding missing closing brackets and quotes.
    Uses a stack to track nesting so inner brackets close before outer ones."""
    stack = []
    in_string = False
    escape = False

    for ch in text:
        if escape:
            escape = False
            continue
        if ch == '\\':
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            stack.append('}')
        elif ch == '[':
            stack.append(']')
        elif ch == '}' and stack and stack[-1] == '}':
            stack.pop()
        elif ch == ']' and stack and stack[-1] == ']':
            stack.pop()

    result = text
    if in_string:
        result += '"'
    # Close in reverse order (inner-most first)
    result += ''.join(reversed(stack))
    return result


def _load_json(path: Path) -> Any:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _load_json_list(path: Path) -> list:
    """Load a JSON array, returning [] on failure."""
    data = _load_json(path)
    return data if isinstance(data, list) else []


def _save_json(path: Path, data: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
