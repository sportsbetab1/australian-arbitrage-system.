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

def add_draft_leg(con, actor, tenant_id, position_id, account_id, selection, outcome_id, odds_milli, stake_cents):
    require_member(con,actor,tenant_id,("tenant_admin","operator"))
    position=con.execute("""SELECT status FROM commercial_customer_positions
        WHERE id=? AND tenant_id=?""",(position_id,tenant_id)).fetchone()
    if not position or position["status"]!="draft":
        raise ValueError("Draft position not found")
    account=con.execute("SELECT status FROM commercial_customer_accounts WHERE tenant_id=? AND id=?", (tenant_id,account_id)).fetchone()
    if not account or account["status"]!="active":
        raise ValueError("Active account not found in workspace")
    if not selection.strip() or not outcome_id.strip() or odds_milli<=1000 or stake_cents<=0:
        raise ValueError("Invalid leg")
    lid=new_id()
    con.execute("""INSERT INTO commercial_customer_position_legs
      (id,tenant_id,position_id,account_id,selection,outcome_id,odds_milli,stake_cents)
      VALUES (?,?,?,?,?,?,?,?)""",
      (lid,tenant_id,position_id,account_id,selection.strip(),outcome_id.strip(),odds_milli,stake_cents))
    audit(con,tenant_id,actor,"customer.draft_leg_added","position_leg",lid)
    return lid

def draft_detail(con, actor, tenant_id, position_id):
    require_member(con,actor,tenant_id)
    row=con.execute("""SELECT * FROM commercial_customer_positions
       WHERE tenant_id=? AND id=?""",(tenant_id,position_id)).fetchone()
    if row is None:
        raise AccessDenied("Position not found")
    legs=con.execute("""SELECT id,account_id,selection,outcome_id,odds_milli,stake_cents,status
       FROM commercial_customer_position_legs
       WHERE tenant_id=? AND position_id=? ORDER BY id""",(tenant_id,position_id)).fetchall()
    return {"position":dict(row),"legs":[dict(x) for x in legs]}

def validate_draft(con, actor, tenant_id, position_id, minimum_roi_bps=150):
    """Calculate a two-way arbitrage draft; does not approve or place bets."""
    require_member(con,actor,tenant_id,("tenant_admin","operator"))
    position=con.execute("""SELECT * FROM commercial_customer_positions
      WHERE tenant_id=? AND id=? AND status='draft'""",(tenant_id,position_id)).fetchone()
    if not position:
        raise ValueError("Draft not found")
    legs=con.execute("""SELECT * FROM commercial_customer_position_legs
      WHERE tenant_id=? AND position_id=? ORDER BY id""",(tenant_id,position_id)).fetchall()
    if len(legs)!=2 or len({leg["outcome_id"] for leg in legs})!=2:
        raise ValueError("Exactly two distinct outcomes required")
    if len({leg["account_id"] for leg in legs})!=2:
        raise ValueError("Two distinct bookmaker accounts required")
    total=sum(leg["stake_cents"] for leg in legs)
    if total!=position["total_stake_cents"]:
        raise ValueError("Leg stakes must equal draft total")
    payouts=[leg["stake_cents"]*leg["odds_milli"]//1000 for leg in legs]
    worst_profit=min(payouts)-total
    roi_bps=worst_profit*10000//total if total else -10000
    return {"total_stake_cents":total,"worst_profit_cents":worst_profit,
            "worst_roi_bps":roi_bps,"meets_minimum":roi_bps>=minimum_roi_bps,
            "note":"Commission, market rules and live odds are NOT verified; not ready for betting"}

def cancel_draft(con, actor, tenant_id, position_id):
    require_member(con,actor,tenant_id,("tenant_admin","operator"))
    row=con.execute("""UPDATE commercial_customer_positions SET status='cancelled'
      WHERE tenant_id=? AND id=? AND status='draft' RETURNING id""",(tenant_id,position_id)).fetchone()
    if not row:
        raise ValueError("Active draft not found")
    audit(con,tenant_id,actor,"customer.draft_cancelled","position",position_id)
    return position_id

def restrict_account(con, actor, tenant_id, account_id):
    require_member(con,actor,tenant_id,("tenant_admin",))
    row=con.execute("""UPDATE commercial_customer_accounts SET status='restricted'
      WHERE tenant_id=? AND id=? AND status='active' RETURNING id""",(tenant_id,account_id)).fetchone()
    if not row:
        raise ValueError("Active account not found")
    audit(con,tenant_id,actor,"customer.account_restricted","account",account_id)
    return account_id
