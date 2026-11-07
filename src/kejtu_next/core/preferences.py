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
# Meaning checks. A note body mostly explains a preference, so a short request
# scores low against it (about .62 for a plain "what are the news?" against a
# news-source note with the local encoder). The scope names the activity where
# the preference applies; asking for that activity scores .76-.93 against it,
# while talking about the topic ("the BBC news app crashed", "a news
# documentary") stays near .63-.67. Tuning values for the current encoder, not
# calibrated probabilities; re-measure when the embedding contract changes.
BODY_MATCH = .65
SCOPE_MATCH = .74


def terms(text):
    return {word for word in re.findall(r'[^\W_]+', text.casefold())
            if len(word) > 2 and word not in CONTEXT_STOP}


def applies(question, scope, strength, semantic_score=None, scope_score=None):
    """`semantic_score`: request against the note body. `scope_score`: request
    against the scope phrase itself, when its embedding is available."""
    if strength == 'off' or not scope.strip():
        return False
    overlap = len(terms(question) & terms(scope))
    meaning = (semantic_score is not None and semantic_score >= BODY_MATCH
               or scope_score is not None and scope_score >= SCOPE_MATCH)
    # A topical word alone does not distinguish asking for news from talking
    # about news. Subtle deliberately misses such ambiguous short requests.
    if strength == 'subtle':
        return overlap >= 2 and (semantic_score is None or meaning)
    if strength == 'balanced':
        # One shared word is enough only when the request means the activity.
        return overlap >= 2 or overlap >= 1 and meaning
    if strength == 'broader':
        return overlap >= 1
    return False
