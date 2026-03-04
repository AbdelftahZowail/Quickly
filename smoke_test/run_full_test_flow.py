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

import os
import sys
import asyncio

# Ensure the project root is on sys.path when the script is executed directly
# (consistent with other smoke_test helpers such as populate_test_data.py).
# When running via `python smoke_test/run_full_test_flow.py`, the interpreter
# adds `smoke_test` to sys.path which hides the `app` package.  Prepend the
# workspace root so imports like ``from app...`` succeed.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import AsyncSessionLocal, init_db
from app import time as time_provider

# We'll call endpoint logic directly instead of using TestClient to avoid
# async loop conflicts.  Import the route handlers we need.
from app.routers.schedule import recalculate_all_campaigns, validate_queue

# TestClient is no longer required; we invoke handlers directly

# import the helpers from the existing scripts
import populate_test_data
from simulate_queue_2_days import QueueSimulator


async def _reset_time_offset() -> None:
    """Clear any persisted time offset so ``time_provider.now()`` returns real time."""
    async with AsyncSessionLocal() as session:
        await time_provider.clear_persisted_offset(session)
        print("-> time offset reset to real now")


async def _recalculate_all() -> None:
    """Invoke the recalc handler directly against a DB session.

    This skips HTTP and avoids any loop/thread issues.  Output mirrors the
    JSON returned by the API.
    """
    print("-> performing global recalculation")
    async with AsyncSessionLocal() as session:
        result = await recalculate_all_campaigns(session)

    print(f"-> recalculation complete: strategy={result.get('strategy')} "
          f"processed={result.get('campaigns_processed')} "
          f"slots={result.get('total_slots')} (initial {result.get('initial_slots')})")


async def _validate() -> bool:
    """Run the queue validator directly and print results."""
    print("-> running scheduled-queue validation")
    async with AsyncSessionLocal() as session:
        data = await validate_queue(session)

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
    # make sure database is initialized (tables/migrations)
    await init_db()

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
