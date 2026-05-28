import re
from enum import Enum

class QueryType(str, Enum):
    CONFIG = "config"
    TROUBLESHOOT = "troubleshoot"
    INVENTORY = "inventory"
    IMPACT = "impact"

class IntentClassification:
    def __init__(self, query_type: QueryType, target_device: str = None):
        self.query_type = query_type
        self.target_device = target_device

def classify_intent(prompt: str) -> QueryType:
    prompt_lower = prompt.lower()
    
    # Check for TROUBLESHOOT keywords using word boundaries
    troubleshoot_keywords = ["unstable", "slow", "why", "loss", "flap", "error", "troubleshoot", "status"]
    if any(re.search(rf"\b{kw}\w*", prompt_lower) for kw in troubleshoot_keywords):
        return QueryType.TROUBLESHOOT
        
    # Check for CONFIG keywords using word boundaries
    config_keywords = ["vlan", "ospf", "playbook", "config", "route", "acl", "generate"]
    if any(re.search(rf"\b{kw}\b", prompt_lower) for kw in config_keywords):
        return QueryType.CONFIG
        
    # Check for IMPACT keywords using word boundaries
    impact_keywords = ["fail", "impact", "blast", "down", "disconnect", "reachability"]
    if any(re.search(rf"\b{kw}\w*", prompt_lower) for kw in impact_keywords):
        return QueryType.IMPACT
        
    return QueryType.INVENTORY

def extract_device_name(prompt: str) -> str | None:
    # List of excluded/ignored words that should never be classified as device names:
    excluded = {
        "show", "get", "post", "delete", "put", "generate", "playbook", "for", "the", "device", 
        "this", "status", "unstable", "why", "now", "ospf", "bgp", "vlan", "route", "acl", 
        "logs", "log", "config", "configuration", "fail", "impact", "blast", "down", 
        "disconnect", "reachability", "is", "flapping", "active", "down", "up", "a", "an", "of"
    }
    
    # 1. First, search for words containing a hyphen, which is extremely common for hostnames:
    # e.g., edge-r1, core-1, spine-2, leaf-1
    hyphen_matches = re.findall(r'\b([a-zA-Z0-9_]+-[a-zA-Z0-9_\-]+)\b', prompt)
    for match in hyphen_matches:
        if match.lower() not in excluded:
            return match

    # 2. If no hyphenated word, look for words ending with a digit (e.g. core1, switch2)
    digit_matches = re.findall(r'\b([a-zA-Z0-9_]+\d+)\b', prompt)
    for match in digit_matches:
        if match.lower() not in excluded and not match.isdigit():
            return match

    # 3. Fallback: match any word that isn't in the excluded list
    # but has some length (e.g., at least 3 chars) and is not purely numbers
    all_words = re.findall(r'\b([a-zA-Z0-9_\-]+)\b', prompt)
    for word in all_words:
        if word.lower() not in excluded and not word.isdigit() and len(word) >= 3:
            return word

    return None
