import re

VERSION = "parser-2025-08-08-d"

# Bullets: •, -, –, —, *
BULLET_RE = re.compile(r'^[\u2022\-\–\—\*]\s+')
NUM_BULLET_RE = re.compile(r'^\(?\d+[\.\)\]]\s+')

# Section headers (forgiving)
EXPERIENCE_HEADERS = re.compile(r'^\s*(experience|work experience|professional experience)\b.*$', re.I)
PROJECTS_HEADERS   = re.compile(r'^\s*(projects|project experience|personal projects)\b.*$', re.I)
OTHER_HEADERS      = re.compile(r'^\s*(education|skills|certifications?|awards?|publications?|summary|contact|achievements?)\b.*$', re.I)

# Job header / date / location / role heuristics
MONTH_RE    = r'(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
DATE_RE     = re.compile(rf'\b{MONTH_RE}\b.*?\b(19|20)\d{{2}}\b')
RANGE_RE    = re.compile(r'\s-\s')
ROLE_RE     = re.compile(r'\b(Engineer|Developer|Scientist|Analyst|Manager|Consultant|Architect|Administrator)\b', re.I)
LOCATION_RE = re.compile(r',\s*[A-Z]{2}(?:\b|,|\s)')
ALLCAPS_RE  = re.compile(r'^[A-Z][A-Z &\-\.,/()]+$')

SMALL_WORDS = {"and","for","of","to","in","with","on","the","a","an","&"}

def _is_bullet(text: str) -> bool:
    return bool(BULLET_RE.match(text) or NUM_BULLET_RE.match(text))

def _looks_like_job_header(text: str) -> bool:
    return bool(
        DATE_RE.search(text) or RANGE_RE.search(text) or
        ROLE_RE.search(text) or LOCATION_RE.search(text) or
        ALLCAPS_RE.match(text)
    )

def _looks_like_project_title(text: str) -> bool:
    t = text.strip()
    if len(t) < 2 or len(t) > 120:
        return False
    if t.endswith(('.', '!', '?', ':', ';', ',')):
        return False
    words = [w for w in re.split(r'\s+', t) if w]
    if not (1 <= len(words) <= 10):
        return False
    titleish = 0
    sig = 0
    for w in words:
        wl = w.lower().strip(".,:;()[]")
        if wl in SMALL_WORDS:
            continue
        sig += 1
        if w.isupper() or (w[:1].isupper() and w[1:].islower()) or re.match(r'[A-Z][\w&\-/]+$', w):
            titleish += 1
    return sig and (titleish / sig) >= 0.6

def _word_count(text: str) -> int:
    return len([w for w in re.split(r'\s+', text.strip()) if w])

def _split_trailing_title_if_any(text: str):
    """Split a trailing title-like chunk off the END of a line."""
    tokens = re.split(r'\s+', text.strip())
    for start in range(max(0, len(tokens) - 10), len(tokens) - 1):
        candidate = " ".join(tokens[start:])
        prefix    = " ".join(tokens[:start]).rstrip()
        if prefix and _looks_like_project_title(candidate):
            return prefix, candidate
    return text, None

def split_and_merge(lines):
    """
    Section-aware merge/split:
      • Bullets start new items.
      • PROJECTS:
          - First non-bullet after header → title
          - Non-bullet after a bullet → continuation (unless it carries a trailing title; then split)
          - Short consecutive titles merge (e.g., "Suspicious" + "Activity Detection")
      • EXPERIENCE:
          - Job-header-ish non-bullets start new items; others are continuations
    Returns: (experience_lines, project_lines)
    """
    merged = []
    section = None  # 'exp' | 'proj' | None

    for raw in lines:
        if not raw:
            continue
        text = raw.strip()
        if not text:
            continue

        # Section switches (keep markers so we can split later)
        if EXPERIENCE_HEADERS.match(text):
            section = 'exp'
            merged.append(text); continue
        if PROJECTS_HEADERS.match(text):
            section = 'proj'
            merged.append(text); continue
        if OTHER_HEADERS.match(text):
            section = None
            merged.append(text); continue

        # Bullets always start new items
        if _is_bullet(text):
            if section == 'proj':
                main, maybe_title = _split_trailing_title_if_any(text)
                merged.append(main)
                if maybe_title:
                    merged.append(maybe_title)
            else:
                merged.append(text)
            continue

        # Section-specific handling
        if section == 'proj':
            last = merged[-1] if merged else ''
            if not merged or PROJECTS_HEADERS.match(last):
                merged.append(text); continue  # first line after header = title
            if _is_bullet(last):
                # wrapped bullet, but also check if it ends with a title
                prefix, title = _split_trailing_title_if_any(text)
                if title:
                    merged[-1] = merged[-1].rstrip() + (' ' + prefix if prefix else '')
                    merged.append(title)
                else:
                    merged[-1] = merged[-1].rstrip() + ' ' + text
                continue
            # title vs continuation
            if _looks_like_project_title(text):
                merged.append(text)
            else:
                merged[-1] = merged[-1].rstrip() + ' ' + text
            continue

        if section == 'exp':
            if _looks_like_job_header(text):
                merged.append(text)
            else:
                merged[-1] = merged[-1].rstrip() + ' ' + text if merged else text
            continue

        merged.append(text)

    # Split into sections
    exp, proj = [], []
    current = None
    for line in merged:
        if EXPERIENCE_HEADERS.match(line):
            current = exp; continue
        if PROJECTS_HEADERS.match(line):
            current = proj; continue
        if OTHER_HEADERS.match(line):
            current = None; continue
        if current is not None:
            current.append(line)

    # --- Post-pass 1: split any trailing titles from ANY project item ---
    fixed = []
    for item in proj:
        main, maybe_title = _split_trailing_title_if_any(item)
        fixed.append(main)
        if maybe_title:
            fixed.append(maybe_title)
    proj = fixed

    # --- Post-pass 2: merge consecutive short titles (e.g., "Suspicious" + "Activity Detection") ---
    merged_titles = []
    i = 0
    while i < len(proj):
        cur = proj[i]
        if i + 1 < len(proj) and _looks_like_project_title(cur) and _looks_like_project_title(proj[i+1]):
            if _word_count(cur) <= 4 and _word_count(proj[i+1]) <= 4 and _word_count(cur + ' ' + proj[i+1]) <= 8:
                merged_titles.append((cur + ' ' + proj[i+1]).strip())
                i += 2
                continue
        merged_titles.append(cur)
        i += 1
    proj = merged_titles

    # --- Post-pass 3: if a BULLET ends with a short TitleCase/ALLCAPS fragment and next is a title, move fragment to the title ---
    final_proj = []
    i = 0
    FRAG_RE = re.compile(r'(?:[A-Z]{2,}|[A-Z][a-z]+)(?:\s+(?:[A-Z]{2,}|[A-Z][a-z]+))?$')  # 1–2 title-ish words
    while i < len(proj):
        item = proj[i]
        if i + 1 < len(proj) and _is_bullet(item) and _looks_like_project_title(proj[i+1]):
            # check trailing fragment on the bullet
            m = FRAG_RE.search(item.rstrip())
            if m:
                frag = m.group(0)
                # don't "borrow" if it already ends with punctuation like '.' etc.
                if frag and not item.strip().endswith(('.', '!', '?', ':', ';', ',')):
                    # cut fragment from bullet
                    trimmed = item[: item.rfind(frag)].rstrip()
                    if trimmed.endswith((' -', '–', '—')):  # tiny cleanup
                        trimmed = trimmed[:-1].rstrip()
                    final_proj.append(trimmed)
                    # prepend frag to the next title
                    proj[i+1] = (frag + ' ' + proj[i+1]).strip()
                    i += 1
                    continue
        final_proj.append(item)
        i += 1
    proj = final_proj

    return exp, proj
