"""Bounded local failure diagnostics without arguments, bodies or credentials."""
import logging
from pathlib import Path
import traceback

from ..core.records import Failure

LOG = logging.getLogger(__name__)


def record_failure(store, turn_id, error, stage):
    # Exception messages/locals can contain credentials, tool bodies and private
    # URLs. Keep only code locations and fixed application/type metadata.
    frames = [dict(file=Path(frame.filename).name, function=frame.name, line=frame.lineno)
              for frame in traceback.extract_tb(error.__traceback__)[-12:]]
    record = dict(kind='turn_failure', stage=stage,
                  error_type=type(error).__name__[:80], frames=frames)
    if isinstance(error, Failure):
        record['reason'] = error.code
    try:
        store.context_event(turn_id, record)
    except Exception:
        # A failed diagnostic write must not prevent terminal-state recovery.
        LOG.error('Turn %s diagnostic unavailable: %s', turn_id, record)
    else:
        LOG.error('Turn %s stopped: %s', turn_id, record)
