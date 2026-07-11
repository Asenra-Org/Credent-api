# =============================================================================
# CREDENT — Text Processing Utilities
# A product of Asenra | https://asenra.in
# Copyright (c) 2026 Asenra. All rights reserved.
# Unauthorized use, reproduction, or distribution is strictly prohibited.
# =============================================================================

import nltk
from nltk.tokenize import word_tokenize

# High-risk credit keywords to monitor in raw documents
HIGH_RISK_KEYWORDS = {
    "default",
    "restructuring",
    "npa",
    "overdue",
    "bankruptcy",
    "liquidation",
    "insolvency",
    "litigation",
    "defaulter",
    "arrears",
    "write-off"
}

def download_nltk_data():
    """Ensure required NLTK tokenizers are downloaded."""
    try:
        nltk.data.find('tokenizers/punkt')
    except LookupError:
        nltk.download('punkt', quiet=True)
    try:
        nltk.data.find('tokenizers/punkt_tab')
    except LookupError:
        nltk.download('punkt_tab', quiet=True)


def extract_high_risk_keywords(text: str) -> list[str]:
    """
    Scans raw document text and extracts any high-risk industry terms.
    Uses NLTK for accurate tokenization.
    
    Args:
        text (str): The raw text extracted from the document.
        
    Returns:
        list[str]: A list of matched high-risk keywords found in the text.
    """
    if not text:
        return []

    # Ensure dependencies are available before tokenizing
    download_nltk_data()

    # Tokenize the text into words and convert to lowercase for matching
    tokens = word_tokenize(text.lower())
    
    # Find intersection of tokens and our high-risk keywords set
    matched_keywords = set(tokens).intersection(HIGH_RISK_KEYWORDS)
    
    return list(matched_keywords)
