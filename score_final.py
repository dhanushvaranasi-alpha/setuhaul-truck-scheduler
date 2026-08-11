import sqlite3
from datetime import datetime
con=sqlite3.connect('setuhaul_freight_operations.db'); con.row_factory=sqlite3.Row
NOW=datetime.fromisoformat('2026-08-04T10:00:00+05:30'); dt=lambda s: datetime.fromisoformat(s)

PRIORITY_POINTS = {'CRITICAL':9.0,'HIGH':6.0,'NORMAL':3.0,'LOW':0.0}
WAIT_LINEAR, WAIT_QUAD = 1.5, 0.6
FAULT, PERISHABLE, PAST_SLOT = 2.5, 2.0, 0.5
UNCERTAINTY = {'HIGH':0.0,'MEDIUM':-0.75,'LOW':-1.5}
FAIRNESS_PER_WIN = -0.4

wins={r['carrier_id']:r['n'] for r in con.execute(
 """SELECT s.carrier_id,COUNT(*) n FROM appointments a JOIN shipments s ON s.shipment_id=a.shipment_id
    WHERE a.is_current=1 AND a.appointment_status IN ('CONFIRMED','IN_PROGRESS') GROUP BY s.carrier_id""")}

rows=con.execute("""SELECT v.shipment_id,v.priority_code,v.eta_confidence,
  s.temperature_control_required tc,s.carrier_id,c.gate_in_ts,c.queue_state,sl.slot_start_ts
  FROM v_inbound_operational_state v JOIN shipments s ON s.shipment_id=v.shipment_id
  LEFT JOIN facility_checkins c ON c.shipment_id=v.shipment_id
  LEFT JOIN appointments a ON a.shipment_id=v.shipment_id AND a.is_current=1
  LEFT JOIN appointment_slots sl ON sl.slot_id=a.slot_id
  WHERE v.destination_facility_id='FAC-JAI-01'
    AND (c.queue_state LIKE 'WAITING%' OR v.current_status IN ('IN_TRANSIT','ASSIGNED'))
    AND v.required_dock_type IN ('STANDARD','ANY')""").fetchall()

def score(r):
    # Waiting counts from max(gate_in, slot_start): INVOLUNTARY waiting only.
    # Measuring from gate-in pays a truck for arriving early, which contradicts
    # the brief's "an early truck does not automatically win". RULE001 caps
    # early check-in at 60 min, so the excluded portion is bounded anyway.
    if r['gate_in_ts']:
        start = dt(r['gate_in_ts'])
        if r['slot_start_ts']:
            start = max(start, dt(r['slot_start_ts']))
        h = max(0.0, (NOW - start).total_seconds()/3600)
    else:
        h = 0
    fault = 1 if r['queue_state']=='WAITING_DOCK_UNAVAILABLE' else 0
    past = max(0,(NOW-dt(r['slot_start_ts'])).total_seconds()/3600) if r['slot_start_ts'] else 0
    t = {
      'importance': PRIORITY_POINTS[r['priority_code']],
      'waiting':    WAIT_LINEAR*h + WAIT_QUAD*h*h,
      'our fault':  FAULT*fault,
      'perishable': PERISHABLE*(r['tc'] or 0),
      'past slot':  PAST_SLOT*past,
      'bad ETA':    UNCERTAINTY.get(r['eta_confidence'],0.0),
      'fairness':   0.0 if fault else FAIRNESS_PER_WIN*wins.get(r['carrier_id'],0),
    }
    return t, sum(t.values())

print(f"{'shipment':<10}{'importance':>11}{'waiting':>9}{'fault':>7}{'perish':>8}{'past':>6}{'badETA':>8}{'fair':>7}{'TOTAL':>8}")
for r in sorted(rows,key=lambda r:-score(r)[1]):
    t,tot=score(r)
    print(f"{r['shipment_id']:<10}{t['importance']:>11.1f}{t['waiting']:>9.2f}{t['our fault']:>7.1f}"
          f"{t['perishable']:>8.1f}{t['past slot']:>6.1f}{t['bad ETA']:>8.2f}{t['fairness']:>7.1f}{tot:>8.2f}")

print("\n=== waiting points as hours accumulate (1.5h + 0.6h^2) ===")
print(f"{'hours':>6}{'points':>9}   beats a fresh...")
for h in (0.5,1,1.5,2,2.5,3,3.5,4):
    p=WAIT_LINEAR*h+WAIT_QUAD*h*h
    beats = 'CRITICAL (9)' if p>=9 else 'HIGH (6)' if p>=6 else 'NORMAL (3)' if p>=3 else '—'
    print(f"{h:>6}{p:>9.2f}   {beats}")
