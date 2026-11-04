"""Anonymous local search attempts, separate from exact conversation sources."""
import uuid
from datetime import datetime, timezone

WEB_USAGE_SCHEMA = '''
CREATE TABLE web_search_usage(id TEXT PRIMARY KEY,provider TEXT NOT NULL,started_at TEXT NOT NULL,
 outcome TEXT NOT NULL CHECK(outcome IN ('pending','success','failed','cancelled')),
 elapsed_ms INTEGER NOT NULL,result_count INTEGER NOT NULL);
CREATE INDEX web_usage_month ON web_search_usage(provider,started_at);
'''


class WebUsage:
    def __init__(self, db): self.db = db

    def begin(self, provider):
        identity = uuid.uuid4().hex
        with self.db:
            self.db.execute('INSERT INTO web_search_usage VALUES(?,?,?,\'pending\',0,0)',
                            (identity, provider, datetime.now(timezone.utc).isoformat()))
        return identity

    def finish(self, identity, outcome, elapsed_ms, result_count=0):
        with self.db:
            self.db.execute("UPDATE web_search_usage SET outcome=?,elapsed_ms=?,result_count=? WHERE id=? AND outcome='pending'",
                            (outcome, max(0, elapsed_ms), result_count, identity))

    def status(self, provider):
        month = datetime.now(timezone.utc).strftime('%Y-%m')
        rows = self.db.execute('''SELECT outcome,count(*) AS n,sum(elapsed_ms) AS ms,sum(result_count) AS results
            FROM web_search_usage WHERE provider=? AND started_at>=? AND started_at<? GROUP BY outcome''',
            (provider, month+'-01', month+'-32')).fetchall()
        # Unfinished records survive a crash as unknown, never as billed success.
        groups = {r['outcome']: r for r in rows}
        count = lambda outcome: groups[outcome]['n'] if outcome in groups else 0
        success = groups.get('success')
        return dict(month=month, attempts=sum(r['n'] for r in rows), successful=count('success'),
                    failed=count('failed'), cancelled=count('cancelled'), unknown=count('pending'),
                    results=success['results'] if success else 0,
                    average_ms=round(success['ms']/success['n']) if success else None)
