"""The task both sides run: the 21-file migration from the resume gate
(python/recall-langgraph/bench/gate.py), copied so the demo image needs
nothing but this folder."""

from pathlib import Path


def _files() -> dict[str, str]:
    """A small shop codebase: 16 files with calls to migrate, and decoys that
    must be left alone (another function named like it, an already migrated
    call, docs and comments that mention it)."""
    files = {
        "README.md": "# shop\n\nPayments go through `billing`. `billing.charge(amount)` is deprecated;\n"
                     "use `billing.charge_v2(amount, currency=...)` instead.\n",
        "api/refunds.py": "import billing\n\n\ndef refund(order):\n    # refunds go through billing.refund, not billing.charge(...)\n"
                          "    return billing.refund(order.total)\n",
        "worker/reports.py": "def summarize(rows):\n    return {\"count\": len(rows), \"sum\": sum(r.amount for r in rows)}\n",
        "wallet/topup.py": "import wallet\n\n\ndef top_up(user, amount):\n    return wallet.recharge(user, amount)\n",
        "api/tax.py": "import billing\n\n\ndef charge_with_tax(amount, rate):\n"
                      "    return billing.charge_v2(amount * (1 + rate), currency=\"USD\")\n",
    }
    names = ["checkout", "subscriptions", "invoices", "retries", "marketplace", "gift_cards", "shipping",
             "donations", "late_fees", "trials", "addons", "installments", "tips", "deposits", "penalties",
             "overages"]
    bodies = [
        "    total = sum(i.price for i in cart)\n    return billing.charge(total)\n",
        "    fee = item.plan.monthly_fee\n    receipt = billing.charge(fee)\n    item.last_receipt = receipt\n    return receipt\n",
        "    for line in item.lines:\n        billing.charge(line.amount)\n    item.settled = True\n",
        "    done = []\n    for p in item:\n        if p.failed:\n            done.append(billing.charge(p.amount))\n    return done\n",
    ]
    for i, name in enumerate(names):
        folder = ("api", "worker", "admin")[i % 3]
        doc = '    """Takes payment. See billing.charge() in the README."""\n' if i % 5 == 0 else ""
        files[f"{folder}/{name}.py"] = f"import billing\n\n\ndef {name}(item, cart=()):\n{doc}{bodies[i % len(bodies)]}"
    return files


FILES = _files()

TASK = (
    "In this repository, every call `billing.charge(X)` must become "
    '`billing.charge_v2(X, currency="USD")`, keeping X exactly as written. Do not change '
    "anything else: not comments, not docs, not other billing functions. Work file by file. "
    "When run_tests passes, reply with the single word DONE."
)

SYSTEM = (
    "You are a careful coding agent. Keep your working state current with update_working_state "
    "(goal, plan, progress, focus) when your plan or progress changes, and call milestone when a "
    "group of files is done, so that if this process dies, the next one can continue from your "
    "notes. Files already on disk are real: if you are resuming, do not redo work they show is done."
)


def expected(text: str) -> str:
    """The file after a correct migration: every call rewritten, and docs,
    comments and docstrings that only mention billing.charge left alone."""
    import re
    out = []
    for line in text.splitlines(keepends=True):
        prose = line.lstrip().startswith(("#", '"""')) or "`" in line
        out.append(line if prose else re.sub(
            r"billing\.charge\(([^()]*(?:\([^()]*\))?[^()]*)\)", r'billing.charge_v2(\1, currency="USD")', line))
    return "".join(out)


def check(workspace: Path) -> list[str]:
    """Paths that are not exactly as the migration should leave them."""
    bad = []
    for rel, original in FILES.items():
        path = workspace / rel
        want = original if rel.endswith(".md") else expected(original)
        if not path.exists() or path.read_text() != want:
            bad.append(rel)
    return bad


