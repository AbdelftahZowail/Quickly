"""High-level script for exercising the full test‑data/queue lifecycle.

This standalone utility runs the sequence described by the user:

1. Delete any existing test data and reset the application's time offset.
2. Populate the database with a fresh set of test leads/campaigns/etc.
3. Perform a full queue recalculation over all campaign leads and validate the
   resulting schedule.
4. Advance the clock by two days (a "simulation") and then run validation again.
5. Re‑run a full recalculation and a final validation pass.

The existing helper modules (``populate_test_data.py``,
``simulate_queue_2_days.py`` and ``validate_scheduled_emails.py``) are imported
and invoked directly so that output is gathered in a single place.  This is just
intended to make it easy to repeat a full end-to-end smoke test while
working on the queue logic.

Usage from the workspace root::

    python run_full_test_flow.py

No arguments are supported; the behaviour is hard‑coded for the steps listed
above, but you are welcome to edit the file if you need alternative timing or
additional logging.
"""

import asyncio

from app.database import AsyncSessionLocal
from app import time as time_provider

# use FastAPI test client for endpoint calls
from fastapi.testclient import TestClient
from app.main import app

# import the helpers from the existing scripts
import populate_test_data
from simulate_queue_2_days import QueueSimulator


async def _reset_time_offset() -> None:
    """Clear any persisted time offset so ``time_provider.now()`` returns real time."""
    async with AsyncSessionLocal() as session:
        await time_provider.clear_persisted_offset(session)
        print("-> time offset reset to real now")


async def _recalculate_all() -> None:
    """Trigger the recalculation endpoint on the running application.

    This uses FastAPI's TestClient so we don't need a live server process; the
    same startup events (DB initialization, etc.) run automatically.
    """
    print("-> requesting /api/scheduler/recalculate-all")

    def sync_call():
        with TestClient(app) as client:
            resp = client.post("/api/scheduler/recalculate-all")
            resp.raise_for_status()
            return resp.json()

    result = await asyncio.to_thread(sync_call)
    # the endpoint already logs details; print a concise summary here
    print(f"-> recalculation complete: strategy={result.get('strategy')} "
          f"processed={result.get('campaigns_processed')} "
          f"slots={result.get('total_slots')} (initial {result.get('initial_slots')})")


async def _validate() -> bool:
    """POST to the validation endpoint and display its result.

    Returns True if the endpoint reported errors (mirrors previous return type).
    """
    print("-> requesting /api/scheduler/validate-queue")

    def sync_call():
        with TestClient(app) as client:
            resp = client.post("/api/scheduler/validate-queue")
            resp.raise_for_status()
            return resp.json()

    data = await asyncio.to_thread(sync_call)
    # emulate validator output for human readability
    total_slots = data.get("total_slots_checked")
    issues = data.get("issues", [])
    print(f"-> validated {total_slots} slots, found {len(issues)} issue(s)")
    if data.get("has_errors"):
        print("-> validation finished with errors")
        return True
    else:
        print("-> validation passed with no errors")
        return False


async def _simulate_two_days() -> None:
    """Advance the clock by two days and log any simulated sends.

    This mirrors ``python simulate_queue_2_days.py --days 2`` behaviour.
    The simulation helper persists the offset for us at the end of the run.
    """
    async with AsyncSessionLocal() as session:
        simulator = QueueSimulator(session)
        start = time_provider.now()
        print(f"-> simulating two days starting at {start.isoformat(sep=' ')}")
        res = await simulator.simulate(start_at=start, days=2, dry_run=False)
        res.print_summary(dry_run=False)
        # ``simulate`` already commits and persists offset; nothing else to do.
        print("-> simulation complete (offset persisted by simulator)")


async def main() -> None:
    validation_failed = False
    # 1. delete existing test data and reset time
    print("STEP 1: cleanup old test data")
    await populate_test_data.delete_test_data()
    await _reset_time_offset()

    # 2. repopulate
    print("\nSTEP 2: populating fresh test data")
    await populate_test_data.main()

    # 3. initial recalc + validate
    print("\nSTEP 3: initial queue recalculation and validation")
    await _recalculate_all()
    validation_failed |= await _validate()

    # 4. simulate two days then validate
    print("\nSTEP 4: simulate two days and re-validate")
    await _simulate_two_days()
    validation_failed |= await _validate()

    # 5. final recalc + validate
    print("\nSTEP 5: final recalculation and validation")
    await _recalculate_all()
    validation_failed |= await _validate()

    print("\nSTEP 6: cleanup test data and reset time")
    await populate_test_data.delete_test_data()
    await _reset_time_offset()

    if validation_failed:
        print("Some validations failed.")
        exit(1)
    else:
        print("All validations passed.")


if __name__ == "__main__":
    asyncio.run(main())
