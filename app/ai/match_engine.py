import re
from difflib import SequenceMatcher
from typing import Any, Optional, TypedDict

class MatchCandidate(TypedDict):
    vendor_name: str
    quotation_date: str
    original_item_name: str
    brand: Optional[str]
    unit: Optional[str]
    quoted_rate: float
    confidence: float

def get_similarity_score(str1: str, str2: str) -> float:
    """
    Computes a high-fidelity hybrid similarity score using:
    1. Token-level overlap (intersection / union)
    2. Character-level SequenceMatcher ratio
    """
    s1 = (str1 or "").lower().strip()
    s2 = (str2 or "").lower().strip()
    
    if not s1 or not s2:
        return 0.0
        
    # Exact match
    if s1 == s2:
        return 1.0
        
    # Character SequenceMatcher ratio (char-level alignment)
    char_ratio = SequenceMatcher(None, s1, s2).ratio()
    
    # Token matching
    t1 = set(re.findall(r"\w+", s1))
    t2 = set(re.findall(r"\w+", s2))
    
    if not t1 or not t2:
        return char_ratio
        
    overlap = len(t1.intersection(t2))
    union = len(t1.union(t2))
    token_ratio = overlap / union if union > 0 else 0.0
    
    # Hybrid score (weighted: 40% char-ratio, 60% token-ratio)
    hybrid = (char_ratio * 0.4) + (token_ratio * 0.6)
    
    # Boost if high overlap of key technical descriptors (numbers like "25", "16", "4c")
    n1 = set(re.findall(r"\b\d+\b|\b\d+c\b|\b\d+sq\.?mm\b", s1))
    n2 = set(re.findall(r"\b\d+\b|\b\d+c\b|\b\d+sq\.?mm\b", s2))
    if n1 and n2:
        num_overlap = len(n1.intersection(n2))
        num_union = len(n1.union(n2))
        if num_overlap == 0 and num_union > 0:
            # Penalty if technical numbers are present in both but completely different!
            hybrid *= 0.5
        elif num_overlap > 0:
            # Boost if technical dimensions match perfectly
            hybrid = min(1.0, hybrid + 0.15)
            
    return round(hybrid, 2)

def find_best_vendor_matches(
    boq_description: str,
    boq_category: Optional[str],
    all_quotations: list[dict[str, Any]],
    threshold: float = 0.55
) -> list[MatchCandidate]:
    """
    Scans all quotations and extracts the best competing supplier rates for a given BOQ description.
    Returns matches sorted by lowest rate (cheapest first).
    """
    candidates: list[MatchCandidate] = []
    
    for q in all_quotations:
        vendor = q.get("vendor_name", "Unknown")
        date = q.get("quotation_date", "")
        
        for item in q.get("items", []):
            item_name = item.get("item_name") or item.get("normalized_item_name", "")
            brand = item.get("brand")
            unit = item.get("unit")
            rate = item.get("quoted_rate", 0.0)
            
            # Skip invalid rates
            if rate <= 0.0:
                continue
                
            # Score against description and normalized name
            score = get_similarity_score(boq_description, item_name)
            
            # Also compare category if available to prevent cross-trade mismatching
            item_cat = (item.get("category") or "").lower()
            boq_cat_lower = (boq_category or "").lower()
            if boq_cat_lower and item_cat and boq_cat_lower != item_cat:
                # Heavy penalty for category mismatch
                score *= 0.3
                
            if score >= threshold:
                candidates.append({
                    "vendor_name": vendor,
                    "quotation_date": date,
                    "original_item_name": item_name,
                    "brand": brand,
                    "unit": unit,
                    "quoted_rate": rate,
                    "confidence": score
                })
                
    # Sort candidates: primary by rate (cheapest first)
    candidates.sort(key=lambda x: x["quoted_rate"])
    return candidates
