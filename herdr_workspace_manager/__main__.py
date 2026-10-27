import argparse
import curses
import json
import sys
import time
import uuid

from .api import Herdr, HerdrError
from .model import Store
from .tui import Picker


def main():
    parser = argparse.ArgumentParser(description="Keyboard-driven picker for the attached Herdr session")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--dump", action="store_true", help="Print the grouped live view as JSON")
    action.add_argument("--watch", action="store_true", help="Observe activity continuously without a UI")
    action.add_argument("--report-failure", metavar="PANE_ID", help="Explicitly report a failure via native pane metadata")
    action.add_argument("--clear-failure", metavar="PANE_ID", help="Resolve an explicitly reported failure")
    parser.add_argument("--message", default="Reported failure", help="Failure description for --report-failure")
    args = parser.parse_args()
    try:
        api = Herdr()
        store = Store(api.path)
        if args.report_failure or args.clear_failure:
            reporting = bool(args.report_failure)
            api.call("pane.report_metadata", pane_id=args.report_failure or args.clear_failure,
                     source="herdr-workspace-manager:failure", tokens={
                         "hwm_failure": uuid.uuid4().hex if reporting else None,
                         "hwm_failure_message": args.message if reporting else None,
                     })
        elif args.watch:
            while True:
                try:
                    store.observe(api.snapshot)
                except (HerdrError, OSError, ValueError) as error:
                    print(error, file=sys.stderr, flush=True)
                    time.sleep(5)
                time.sleep(0.6)
        elif args.dump:
            workspaces, _ = store.observe(api.snapshot)
            print(json.dumps(workspaces, indent=2, ensure_ascii=True))
        else:
            api.snapshot()
            curses.wrapper(Picker(api, store).run)
    except KeyboardInterrupt:
        pass
    except (HerdrError, OSError, ValueError, curses.error) as error:
        print(f"herdr-workspace-manager: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
