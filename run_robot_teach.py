#!/usr/bin/env python3
"""Interactive ReBeL pose teacher: nudge, save, goto, test dump, park.

Teach ``home`` (under trapdoor), ``rest`` (idle park clear of furniture),
and per-bin ``approach`` / ``dump`` poses. Stored in data/robot/poses.json.

Thin REPL over ``TeachSession`` (same backend as the Gradio teach UI).

Close iRC first (one CRI client only). Keep E-stop handy; default override is low.
"""

from __future__ import annotations

import argparse
import logging
import shlex
import sys

from robot.poses import POSES_PATH
from robot.teach_session import TeachSession

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

HELP = """
Commands
  help                         this text
  status                       live joints + TCP + pose summary
  list                         saved pose summary

  nudge x|y|z <+|-> [mm]       Cartesian base nudge (default step from poses.json)
  nudge a1..a6 <+|-> [deg]     Joint nudge (wrist a5/a6 for dump tilt)
  stop                         stop any in-progress move

  save home                    capture current → home (catch under trapdoor)
  save rest                    capture current → rest (idle park — teach this!)
  save bin <id> approach|dump  capture current → that bin waypoint
  goto home|rest
  goto bin <id> approach|dump
  test dump <id>               full home→approach→dump→approach→home @ low speed
  park                         goto rest (or home) then disable + quit

  override <pct>               set speed override (teach default ~20)
  vel <pct>                    joint move velocity for goto/test (default ~20)
  quit / exit                  park if rest taught, then release
""".strip()


def _parse_sign(token: str) -> float:
    t = token.strip().lower()
    if t in ("+", "pos", "plus"):
        return 1.0
    if t in ("-", "neg", "minus"):
        return -1.0
    raise ValueError("expected + or -")


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)

    session = TeachSession()
    exit_code = 0
    parked = False

    try:
        print(HELP)
        print(
            "\nSafety: E-stop is last resort. Teach a clear `rest` pose, keep "
            "override low, and always `park` / quit so drives disable (brakes on).\n"
        )
        st = session.connect()
        print(f"  {st.message}")
        if not session.connected:
            return 1
        print(f"  live: {st.live}")
        print("  saved:")
        for line in st.summary.splitlines():
            print(f"    {line}")

        while True:
            try:
                line = input("teach> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            try:
                parts = shlex.split(line)
            except ValueError as exc:
                print(f"  parse error: {exc}")
                continue
            cmd = parts[0].lower()
            args = parts[1:]

            try:
                if cmd in ("help", "h", "?"):
                    print(HELP)
                    continue
                if cmd in ("quit", "exit", "q"):
                    break
                if cmd == "status":
                    st = session.status()
                    print(f"  live: {st.live}")
                    print("  saved:")
                    for line_s in st.summary.splitlines():
                        print(f"    {line_s}")
                    continue
                if cmd == "list":
                    st = session.status()
                    for line_s in st.summary.splitlines():
                        print(f"  {line_s}")
                    print(f"  file: {POSES_PATH}")
                    continue
                if cmd == "stop":
                    st = session.stop()
                elif cmd == "override":
                    if len(args) != 1:
                        print("  usage: override <pct>")
                        continue
                    st = session.set_override(float(args[0]))
                elif cmd == "vel":
                    if len(args) != 1:
                        print("  usage: vel <pct>")
                        continue
                    st = session.set_velocity(float(args[0]))
                elif cmd == "nudge":
                    if len(args) < 2:
                        print("  usage: nudge x|y|z|a1..a6 <+|-> [amount]")
                        continue
                    axis = args[0].lower()
                    sign = _parse_sign(args[1])
                    amount = float(args[2]) if len(args) > 2 else None
                    if axis in ("x", "y", "z"):
                        st = session.nudge_cart(axis, sign, amount)
                    elif axis in ("a1", "a2", "a3", "a4", "a5", "a6"):
                        st = session.nudge_joint(axis, sign, amount)
                    else:
                        print("  axis must be x|y|z|a1|a2|a3|a4|a5|a6")
                        continue
                elif cmd == "save":
                    if not args:
                        print("  usage: save home|rest | save bin <id> approach|dump")
                        continue
                    what = args[0].lower()
                    if what in ("home", "rest"):
                        st = session.save_named(what)
                    elif what == "bin":
                        if len(args) != 3:
                            print("  usage: save bin <id> approach|dump")
                            continue
                        st = session.save_bin(args[1], args[2])
                    else:
                        print("  usage: save home|rest | save bin <id> approach|dump")
                        continue
                elif cmd == "goto":
                    if not args:
                        print("  usage: goto home|rest | goto bin <id> approach|dump")
                        continue
                    what = args[0].lower()
                    if what in ("home", "rest"):
                        st = session.goto_named(what)
                    elif what == "bin":
                        if len(args) != 3:
                            print("  usage: goto bin <id> approach|dump")
                            continue
                        st = session.goto_bin(args[1], args[2])
                    else:
                        print("  usage: goto home|rest | goto bin <id> approach|dump")
                        continue
                elif cmd == "test":
                    if len(args) != 2 or args[0].lower() != "dump":
                        print("  usage: test dump <id>")
                        continue
                    st = session.test_dump(args[1])
                elif cmd == "park":
                    st = session.park()
                    print(f"  {st.message}")
                    parked = True
                    break
                else:
                    print(f"  unknown command '{cmd}' — try help")
                    continue
                print(f"  {st.message}")
            except (RuntimeError, KeyError, ValueError) as exc:
                logger.error("%s", exc)
                print(f"  error: {exc}")

    except RuntimeError as exc:
        logger.error("%s", exc)
        exit_code = 1
    finally:
        if not parked:
            try:
                if session.connected:
                    print("  parking before exit…")
                    session.disconnect(park=True)
            except Exception as exc:
                logger.warning("cleanup: %s", exc)
                try:
                    if session.connected:
                        session.disconnect(park=False)
                except Exception:
                    pass

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
