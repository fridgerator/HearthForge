"""
CLI entrypoint for the HearthForge orchestrator.

Usage:
  python main.py "your goal here"
  python main.py --user myuser "analyze BTC"
  python main.py --interactive --user myuser
  python main.py --user myuser --set-pref "output_style=concise, no fluff"
  python main.py --user myuser --show-memory
"""

import argparse
import asyncio
import logging
import sys

from config import OLLAMA_HOST, OLLAMA_MODEL
from memory import MemoryManager
from ollama_client import OllamaClient
from orchestrator import Orchestrator


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


async def run_goal(goal: str, user_id: str) -> None:
    client = OllamaClient()
    orchestrator = Orchestrator(client, user_id=user_id)

    try:
        result = await orchestrator.run(goal)

        print("\n" + "=" * 60)
        print("FINAL OUTPUT")
        print("=" * 60)
        print(result.final_output)
        print("=" * 60)
        print(f"Tasks: {result.completed_tasks}/{result.total_tasks} completed")
        if result.failed_tasks:
            print(f"Failed: {result.failed_tasks}")
        print(f"Time: {result.elapsed_seconds:.1f}s")
        print(f"User: {user_id}")
    finally:
        await client.close()


async def interactive_mode(user_id: str) -> None:
    print(f"HearthForge Orchestrator")
    print(f"Model: {OLLAMA_MODEL} @ {OLLAMA_HOST}")
    print(f"User: {user_id}")
    print(f"Type 'quit' to exit, '/memory' to view memory, '/pref key=value' to set a preference\n")

    client = OllamaClient()
    orchestrator = Orchestrator(client, user_id=user_id)

    try:
        while True:
            goal = input("\n🎯 Goal: ").strip()
            if goal.lower() in ("quit", "exit", "q"):
                break
            if not goal:
                continue

            # Meta-commands
            if goal == "/memory":
                show_memory(user_id)
                continue
            if goal.startswith("/pref "):
                handle_pref_command(goal[6:], user_id)
                continue
            if goal.startswith("/forget "):
                handle_forget_command(goal[8:], user_id)
                continue

            result = await orchestrator.run(goal)

            print("\n" + "=" * 60)
            print("FINAL OUTPUT")
            print("=" * 60)
            print(result.final_output)
    finally:
        await client.close()


def show_memory(user_id: str) -> None:
    """Display the current memory state for a user."""
    memory = MemoryManager(user_id=user_id)
    mem = memory.load()

    print(f"\n📝 Memory for user '{user_id}':")
    print(f"   File: {memory.file_path}")

    print(f"\n   History ({len(mem.history)} entries):")
    if mem.history:
        for run in reversed(mem.history[-10:]):  # Show last 10
            print(f"   • [{run.timestamp[:16]}] \"{run.goal}\" → {run.outcome}")
    else:
        print("   (empty)")

    print(f"\n   Preferences ({len(mem.preferences)}):")
    if mem.preferences:
        for pref in mem.preferences:
            print(f"   • {pref.key}: {pref.value}")
    else:
        print("   (empty)")

    print(f"\n   Tool Knowledge ({len(mem.tool_knowledge)}):")
    if mem.tool_knowledge:
        for tk in mem.tool_knowledge:
            print(f"   • [{tk.source}] {tk.fact[:100]}")
    else:
        print("   (empty)")
    print()


def handle_pref_command(args: str, user_id: str) -> None:
    """Set a user preference. Format: key=value"""
    if "=" not in args:
        print("   Usage: /pref key=value")
        print("   Example: /pref output_style=concise, no fluff")
        return
    key, value = args.split("=", 1)
    memory = MemoryManager(user_id=user_id)
    memory.load()
    memory.set_preference(key.strip(), value.strip(), source="explicit")
    print(f"   ✓ Set preference: {key.strip()} = {value.strip()}")


def handle_forget_command(args: str, user_id: str) -> None:
    """Remove a user preference. Format: /forget key"""
    memory = MemoryManager(user_id=user_id)
    memory.load()
    if memory.remove_preference(args.strip()):
        print(f"   ✓ Removed preference: {args.strip()}")
    else:
        print(f"   Preference '{args.strip()}' not found")


def main():
    parser = argparse.ArgumentParser(description="HearthForge Agent Orchestrator")
    parser.add_argument("goal", nargs="?", help="Goal to accomplish")
    parser.add_argument("--user", "-u", default="default", help="User ID for memory (default: 'default')")
    parser.add_argument("--interactive", "-i", action="store_true", help="Interactive mode")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    parser.add_argument("--show-memory", action="store_true", help="Display current memory state")
    parser.add_argument("--set-pref", metavar="KEY=VALUE", help="Set a user preference")
    args = parser.parse_args()

    setup_logging(args.verbose)

    if args.show_memory:
        show_memory(args.user)
        return

    if args.set_pref:
        handle_pref_command(args.set_pref, args.user)
        return

    if args.interactive:
        asyncio.run(interactive_mode(args.user))
    elif args.goal:
        asyncio.run(run_goal(args.goal, args.user))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
