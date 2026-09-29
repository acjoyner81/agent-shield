import re
from typing import List, Tuple
from fastapi import HTTPException, status

# Patterns for identifying prompt injection attempts
PROMPT_INJECTION_PATTERNS = [
    r"(?i)\bignore\s+(?:all\s+|any\s+)?previous\s+instructions\b",
    r"(?i)\b(?:disregard|ignore)\s+\w+\s+(?:instructions|guidelines|safety|policy|context)\b",
    r"(?i)disregard\s+all\s+prior\s+context",
    r"(?i)system\s*:\s*you\s+are\s+now",
    r"(?i)system\s+override",
    r"(?i)\breveal\s+(?:your|the)\s+system\s+prompt\b",
    r"(?i)\bdump\s+(?:environment\s+variables|env\s+(?:variables?|vars?)|secrets|credentials|api\s+keys?)\b",
    r"(?i)override\s+system\s+prompt",
    r"(?i)\bgenerate\s+(?:malicious\s+|exploit\s+|working\s+)?(?:exploit\s+)?code\b",
    # "Jailbreak" is an ordinary English word (a video game genre, a DRM term), so
    # matching it bare refused benign traffic. Require it in an attacking sense:
    # aimed at the model or assistant, or named as a scenario or attempt.
    r"(?i)\bjailbreak\s+(?:the\s+|this\s+|your\s+|my\s+)?(?:model|assistant|ai|llm|chatbot|system|prompt|guardrails?)\b",
    r"(?i)\b(?:jailbreak|jail\s?break)\s+(?:attempt|prompt|scenario|mode|style|technique|instructions?)\b",
    r"(?i)\b(?:this|that|is)\s+(?:is\s+)?(?:a\s+|an\s+)?(?:jailbreak|jail\s?break)\b",
    r"(?i)^\s*(?:jailbreak|jail\s?break)\b",
]

# Regex patterns for detecting sensitive PII
PII_PATTERNS = {
    "SSN": r"\b\d{3}-\d{2}-\d{4}\b",
    "CREDIT_CARD": r"\b(?:\d{4}[- ]?){3}\d{4}\b",
    "EMAIL": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
}


def scan_prompt_injection(messages: List[dict]) -> None:
    """
    Scans incoming message history for prompt injection patterns.
    Raises HTTPException HTTP 400 if a malicious pattern is detected.
    """
    for msg in messages:
        content = msg.get("content", "")
        if not isinstance(content, str):
            continue

        for pattern in PROMPT_INJECTION_PATTERNS:
            if re.search(pattern, content):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Potential prompt injection detected in input.",
                )


def redact_pii(text: str) -> Tuple[str, bool]:
    """
    Scans text for PII patterns and replaces them with [REDACTED_<TYPE>].
    Returns tuple of (redacted_text, had_pii).
    """
    if not isinstance(text, str):
        return text, False

    modified = False
    redacted_text = text

    for pii_type, pattern in PII_PATTERNS.items():
        if re.search(pattern, redacted_text):
            modified = True
            redacted_text = re.sub(pattern, f"[REDACTED_{pii_type}]", redacted_text)

    return redacted_text, modified


def sanitize_llm_response(response_data: dict) -> dict:
    """
    Traverses LLM completion choice contents and redacts PII before returning.
    """
    choices = response_data.get("choices", [])
    for choice in choices:
        message = choice.get("message", {})
        content = message.get("content", "")
        if content:
            redacted_content, _ = redact_pii(content)
            message["content"] = redacted_content
    return response_data