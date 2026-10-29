import re


def normalize_phone(raw):
    """Turn 0712345678, 712345678 or +254712345678 into +254712345678."""
    cleaned = re.sub(r"[^\d+]", "", raw or "")
    digits = cleaned[1:] if cleaned.startswith("+") else cleaned
    if digits.startswith("254") and len(digits) == 12:
        pass
    elif digits.startswith("0") and len(digits) == 10:
        digits = "254" + digits[1:]
    elif len(digits) == 9 and digits[0] in "17":
        digits = "254" + digits
    else:
        raise ValueError("Enter a valid Kenyan phone number, for example 0712 345 678.")
    return "+" + digits
