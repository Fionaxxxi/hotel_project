"""Explain the completed soak using retained samples and current read-only facts."""

import json
from datetime import UTC, datetime

from infra.manage import ROOT, uid
from services.core.config import Settings
from services.core.db import Database


def main():
    target = ROOT / 'docs/acceptance'
    soak = json.loads((target / 'g5-soak.json').read_text(encoding='utf-8'))
    samples = soak['samples']
    database = Database(Settings.load().database_url)
    database.pool.open()
    try:
        with database.transaction(uid('tenant-A')) as c:
            listings = c.execute("SELECT id,source_key,state,desired_revision,observed_revision FROM g3_listing WHERE source_key LIKE %s", ('%/' + soak['source'],)).fetchall()
            added = c.execute("SELECT d.id,d.code,d.created_at,l.id AS listing_id,l.source_key,l.state FROM g3_dead_letter d JOIN g3_task t ON t.id=d.task_id JOIN g3_intent i ON i.id=t.intent_id JOIN g3_listing l ON l.id=i.listing_id WHERE d.created_at>=%s AND d.created_at<=%s ORDER BY d.created_at", (soak['started_at'],soak['completed_at'])).fetchall()
            lost = c.execute("SELECT j.id,j.kind,j.state,j.lease_until FROM execution_job j WHERE j.state IN ('READY','RUNNING','RETRY') AND (j.resource_id IN (SELECT t.id FROM g3_task t JOIN g3_intent i ON i.id=t.intent_id JOIN g3_listing l ON l.id=i.listing_id WHERE l.source_key LIKE %s) OR j.resource_id IN (SELECT id FROM g3_listing WHERE source_key LIKE %s))", ('%/' + soak['source'],'%/' + soak['source'])).fetchall()
    finally:
        database.pool.close()
    rss = [s['resources']['rss_sum_bytes']/1048576 for s in samples]
    own_added = [x for x in added if x['source_key'].endswith('/'+soak['source'])]
    closed = len(listings)==2 and all(x['state']=='STOPPED' and x['desired_revision']==x['observed_revision'] for x in listings)
    result = {
        'at':datetime.now(UTC).isoformat(),
        'status':'PASSED' if soak['status']=='PASSED' and closed and not lost and not own_added else 'FAILED',
        'source':soak['source'],'updates':len(soak['updates']),'samples':len(samples),
        'rss_mib':{'first':rss[0],'last':rss[-1],'maximum':max(rss)},
        'rss_note':'Sum of unique process RSS, including PostgreSQL and model; shared pages can count across processes. Bounded over this one-hour run, not a long-term memory guarantee.',
        'queue_due_max':max(x['metrics']['queue']['due'] for x in samples),
        'expired_lease_max':max(x['metrics']['queue']['expired_lease'] for x in samples),
        'global_dead_letters_first':samples[0]['metrics']['dead_letters'],
        'global_dead_letters_last':samples[-1]['metrics']['dead_letters'],
        'dead_letters_added_in_window':added,
        'interpretation':'Four global dead letters belong to older G5-3fe0d250, not this soak source: two overdue stop risks and two send-budget exhaustion. Retained unresolved; global queue is not described as clean. Do not change or forge their remote state.',
        'soak_source_new_dead_letters':own_added,'current_source_pending_jobs':lost,
        'current_source_listings':listings,'both_closed_with_observation':closed,
    }
    (target/'g5-soak-closure.json').write_text(json.dumps(result,default=str,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':result['status'],'updates':result['updates'],'rss_mib':result['rss_mib'],'global_new_dead_letters':len(added),'source_pending_jobs':len(lost)}))


if __name__=='__main__':
    main()
