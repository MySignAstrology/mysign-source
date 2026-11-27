"""
The human in the loop.

  python -m rig.operator

Shows each pending draft with the context the model was given, so you can tell a
good answer from a lucky one. Approve, edit, or kill. Nothing sends otherwise.
"""
import os
import sys

import requests

from engine.store import load

API = os.environ.get("RIG_API", "http://127.0.0.1:8000")
BOLD, DIM, WARN, CRIT, OFF = "\033[1m", "\033[2m", "\033[33m", "\033[31m", "\033[0m"


def show(item, participant):
    tier = item["kind"].split("/")[-1]
    color = CRIT if tier == "red" else WARN if tier == "amber" else ""
    print(f"\n{'=' * 72}")
    print(f"{BOLD}{participant['name']}{OFF}  ·  {item['kind']}  ·  "
          f"salience {item['verdict']['band']} ({item['verdict']['score']})"
          f"  ·  queued {item['queued_at'][11:]}")
    print(f"{'=' * 72}")
    if item.get("context_block"):
        print(f"{DIM}{item['context_block']}{OFF}")
        print("-" * 72)
    print(f"{color}{item['draft']}{OFF}\n")


def main():
    q = [i for i in load("_study", "queue", []) if i["status"] == "pending"]
    people = {p["user_id"]: p for p in load("_study", "participants", [])}
    if not q:
        print("Queue empty.")
        return
    print(f"{len(q)} pending.")
    for item in q:
        show(item, people[item["user_id"]])
        while True:
            a = input("[a]pprove  [e]dit  [k]ill  [s]kip  [q]uit > ").strip().lower()
            if a == "a":
                requests.post(f"{API}/approve/{item['id']}"); print("sent."); break
            if a == "e":
                print("New text (end with a blank line):")
                lines = iter(input, "")
                requests.post(f"{API}/approve/{item['id']}",
                              data={"text": "\n".join(lines)})
                print("edited + sent."); break
            if a == "k":
                print("killed (left pending — mark it in the queue file)."); break
            if a == "s":
                break
            if a == "q":
                sys.exit(0)


if __name__ == "__main__":
    main()
