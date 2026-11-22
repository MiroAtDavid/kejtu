"""Read-only compatibility for answers rendered before replay text was updated."""
from ...core.interests import assemble_reply
from ...core.reminder_conversation import assemble
from ...core.reply_presentation import response_text, present
from ...core.providers import provider
from ...core.records import Failure, Reply


def restore(store, turn, conversation, state, answer, maximum, allowance, ledger=None):
    if provider(conversation.profile.provider).transport != 'responses':
        return state
    # Only presentation-bearing turns need this second parser. Ordinary history
    # still passes the adapter's full replay validator before provider IO.
    admissions = []
    for owner, render in ((store.reminder_attention, assemble), (store.interests, assemble_reply)):
        if ledger is not None:
            ledger.spend_sql()
        admitted = owner.admission(turn['id'])
        if admitted is not None and admitted.get('presentation') != 'context':
            admissions.append((admitted, render))
    if not admissions:
        return state
    parsed = response_text(state, conversation.profile)
    if parsed is None or parsed[2] == answer:
        return state
    original = parsed[2]
    # Require the original turn's recorded admission and an exact deterministic
    # presentation match. Arbitrary mismatches remain errors. Do not re-evaluate
    # today's reminder status or rewrite historical artifacts/receipts.
    for admitted, render in admissions:
        for eligible in (True, False):
            rendered, _ = render(original, admitted, eligible, maximum)
            if rendered == answer:
                repaired = present(Reply(original, 'stop', state), answer, conversation.profile, maximum).reasoning
                if len(repaired.encode()) > allowance:
                    raise Failure('context_limit', 'The exact presented history and provider state do not fit; no model call was sent.')
                return repaired
    raise Failure('provider_state_integrity', 'The saved Responses state does not match its answer or a recorded presentation; no model call was sent.')
