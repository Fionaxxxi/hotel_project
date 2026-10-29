# G4 workflow definition g4-flow-v1

Opt-in subscriptions own one tenant/supplier/property source key. G2 checks this ownership before its legacy activation/publish flow, marks old flow G4_MANAGED and pauses it. G4 uses existing G2 parser and activation commands, existing G3 allocation/compiler/plan commands; it does not write around their business invariants.

Independent G4 inbox entries consume related processing/mapping/approval facts idempotently. A 300ms durable reconciler also checks current facts, so missed or reordered notifications do not lose a wait. A source advisory lock and transaction serialize steps. A crash before the automatic decision/plan transaction commits leaves no partial effect; after commit the run references the existing unique G3 plan. Polling is intentional; this is not a new distributed workflow engine or a claim of arbitrary throughput.

PROCESSING → WAIT_SCOPE/WAIT_QUALITY → activation → WAIT_MAPPING → WAIT_ALLOCATION → compilation → WAIT_APPROVAL → RUNNING/WAIT_EXTERNAL → COMPLETED. STALE marks replaced source runs; completed records remain historical. Steps retain reason and resource/hash references. There is no universal bypass/resume command.

Autoactivation requires the exact independently authorized contract/mode/scope/sequence stream, increasing sequence, current partition CAS and no DQ issue. No unknown template or incomplete snapshot auto-deletes data. Manual G2 activation still resolves explicit scope waits. Mapping reuse verifies unchanged normalized facts, parent mapping and current target version, and writes reuse evidence rather than impersonating a reviewer.

Allocation binds the current activation with the same authorized A/B amounts and buffer; current capacity and reserved exposure must permit it. Insufficient capacity waits; no automatic transfer. Human allocation changes force new snapshots and human review if outside original grant. New mappings or property fact changes create advisory tasks and durable human waits.

Normal source updates can temporarily cause safety closure of old targets; no uninterrupted availability promise is made. Policy replacement, expiry and stop recovery remain separate actions. A completed run means its selected branches were VERIFIED at that point, not that its inventory stays valid forever.
