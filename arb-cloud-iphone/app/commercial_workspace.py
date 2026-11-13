"""Tenant-safe customer workspace mutations, not bookmaker execution.

Every mutation authorizes the tenant and uses BEGIN IMMEDIATE. Monetary
amounts are integer AUD cents; no funds move and no bets are placed.
"""
import json
from app.commercial_security import AccessDenied, new_id, require_member

def audit(con, tenant_id, actor, action, target_type, target_id):
    con.execute("""INSERT INTO commercial_audit_events
       (id,tenant_id,actor_user_id,action,target_type,target_id)
       VALUES (?,?,?,?,?,?)""",
       (new_id(),tenant_id,actor,action,target_type,target_id))

def create_account(con, actor, tenant_id, label, bookmaker, opening_balance_cents=0):
    require_member(con,actor,tenant_id,("tenant_admin","operator"))
    if not label.strip() or not bookmaker.strip() or opening_balance_cents < 0:
        raise ValueError("Invalid account")
    aid=new_id()
    con.execute("""INSERT INTO commercial_customer_accounts
      (id,tenant_id,label,bookmaker,opening_balance_cents)
      VALUES (?,?,?,?,?)""",(aid,tenant_id,label.strip(),bookmaker.strip(),opening_balance_cents))
    if opening_balance_cents:
        con.execute("""INSERT INTO commercial_customer_bankroll_ledger
          (id,tenant_id,entry_type,amount_cents,description)
          VALUES (?,?,'opening',?,?)""",(new_id(),tenant_id,opening_balance_cents,"Opening balance: "+aid))
    audit(con,tenant_id,actor,"customer.account_created","account",aid)
    return aid

def create_draft(con, actor, tenant_id, event, sport, market, total_stake_cents):
    require_member(con,actor,tenant_id,("tenant_admin","operator"))
    if not all(x.strip() for x in (event,sport,market)) or total_stake_cents < 0:
        raise ValueError("Invalid draft")
    pid=new_id()
    con.execute("""INSERT INTO commercial_customer_positions
       (id,tenant_id,event,sport,market,total_stake_cents)
       VALUES (?,?,?,?,?,?)""",
       (pid,tenant_id,event.strip(),sport.strip(),market.strip(),total_stake_cents))
    audit(con,tenant_id,actor,"customer.draft_created","position",pid)
    return pid

def record_bankroll_entry(con, actor, tenant_id, entry_type, amount_cents, description):
    require_member(con,actor,tenant_id,("tenant_admin",))
    if entry_type not in ("deposit","withdrawal","adjustment") or amount_cents == 0:
        raise ValueError("Invalid ledger entry")
    if entry_type=="deposit" and amount_cents < 0:
        raise ValueError("Deposit must be positive")
    if entry_type=="withdrawal" and amount_cents > 0:
        raise ValueError("Withdrawal must be negative")
    if len(description.strip()) < 4:
        raise ValueError("Description required")
    lid=new_id()
    con.execute("""INSERT INTO commercial_customer_bankroll_ledger
      (id,tenant_id,entry_type,amount_cents,description)
      VALUES (?,?,?,?,?)""",(lid,tenant_id,entry_type,amount_cents,description.strip()))
    audit(con,tenant_id,actor,"customer.ledger_entry","bankroll_entry",lid)
    return lid

def workspace_summary(con, actor, tenant_id):
    require_member(con,actor,tenant_id)
    accounts=con.execute("SELECT COUNT(*) FROM commercial_customer_accounts WHERE tenant_id=?",(tenant_id,)).fetchone()[0]
    positions=con.execute("""SELECT status,COUNT(*) AS count FROM commercial_customer_positions
      WHERE tenant_id=? GROUP BY status""",(tenant_id,)).fetchall()
    balance=con.execute("""SELECT COALESCE(SUM(amount_cents),0) FROM commercial_customer_bankroll_ledger
      WHERE tenant_id=?""",(tenant_id,)).fetchone()[0]
    return {"accounts":accounts,"positions_by_status":{r["status"]:r["count"] for r in positions},
            "bankroll_ledger_total_cents":balance,"currency":"AUD",
            "note":"Recorded ledger total; not verified bookmaker balance"}
