import json
from datetime import UTC, datetime, timedelta

from services.core.g5.backup_status import status


def write(base,stamp,complete=True):
    folder=base/'sample'
    folder.mkdir(exist_ok=True)
    (folder/'manifest.json').write_text(json.dumps({'schema':'g5-backup-v1','status':'COMPLETE' if complete else 'INCOMPLETE','completed_at':stamp.isoformat()}),encoding='utf-8')


def test_no_backup_and_incomplete_are_stale(tmp_path):
    clock=datetime(2026,10,28,tzinfo=UTC)
    assert status(tmp_path,clock)['state']=='STALE'
    write(tmp_path,clock,False)
    assert status(tmp_path,clock)['state']=='STALE'


def test_backup_age_boundary_and_future_rejected(tmp_path):
    clock=datetime(2026,10,28,tzinfo=UTC)
    write(tmp_path,clock-timedelta(hours=24))
    assert status(tmp_path,clock)['state']=='FRESH'
    assert status(tmp_path,clock+timedelta(seconds=1))['state']=='STALE'
    write(tmp_path,clock+timedelta(hours=1))
    assert status(tmp_path,clock)['state']=='STALE'
