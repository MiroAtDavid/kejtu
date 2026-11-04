"""Conservative admission of explicitly scoped, user-attributed preferences.

These gates are tuning policies, not calibrated probabilities. Ordinary recall
and explicit searches retain their own ranking. No topic dictionary or extra
model call guesses a preference's applicability.
"""
import re
from .recall import CONTEXT_STOP

STRENGTHS = ('off', 'subtle', 'balanced', 'broader')
MAX_PREFERENCES = 2
MAX_PREFERENCE_BYTES = 1024


def terms(text):
    return {word for word in re.findall(r'[^\W_]+', text.casefold())
            if len(word) > 2 and word not in CONTEXT_STOP}


def applies(question, scope, strength, semantic_score=None):
    if strength == 'off' or not scope.strip():
        return False
    overlap = len(terms(question) & terms(scope))
    # A topical word alone does not distinguish asking for news from talking
    # about news. Subtle deliberately misses such ambiguous short requests.
    if strength == 'subtle':
        return overlap >= 2 and (semantic_score is None or semantic_score >= .65)
    if strength == 'balanced':
        return overlap >= 2 or overlap >= 1 and semantic_score is not None and semantic_score >= .65
    if strength == 'broader':
        return overlap >= 1
    return False
