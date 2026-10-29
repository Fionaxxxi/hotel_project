"""Read-only final normal-service and disabled-fault proof."""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime

import httpx

from infra.manage import ROOT
from services.core.config import Settings


def main():
    Settings.load()
    check = subprocess.run([sys.executable, '-s', '-m', 'infra.g5_health', 'all'], capture_output=True, text=True, encoding='utf-8', errors='replace')
    profile = json.loads((ROOT/'.runtime/g5-service-profile.json').read_text(encoding='utf-8-sig'))
    probes = []
    with httpx.Client(trust_env=False, timeout=5) as client:
        for channel, port in [('SIM-A',8001),('SIM-B',8002)]:
            response = client.get(f'http://127.0.0.1:{port}/g3/control/g5-final-readonly-probe', headers={'Authorization':'Bearer '+os.environ[f"G3_{channel.replace('-', '_')}_TENANT_A_TOKEN"], 'X-Control-Token':os.environ['G3_CONTROL_TOKEN']})
            probes.append({'channel':channel,'status':response.status_code,'disabled':response.status_code==404})
    result = {'at':datetime.now(UTC).isoformat(),'status':'PASSED' if check.returncode==0 and profile['offline_only'] and profile['simulator_profile']=='normal' and all(p['disabled'] for p in probes) else 'FAILED','process_health_exit':check.returncode,'process_health':check.stdout.strip().splitlines(),'profile':profile,'readonly_fault_probes':probes}
    (ROOT/'docs/acceptance/g5-final-health.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result))
    if result['status']!='PASSED':
        raise RuntimeError('FINAL_NORMAL_SERVICE_HEALTH_FAILED')


if __name__=='__main__':
    main()
