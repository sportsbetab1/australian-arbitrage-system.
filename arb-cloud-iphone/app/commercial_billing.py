"""Read-only commercial subscription and fee reporting.

No charging, billing-provider calls, or money transfers are performed here.
"""
from app.commercial_security import require_member, AccessDenied

def subscription_summary(con, user_id, tenant_id):
    require_member(con,user_id,tenant_id)
    rows=con.execute("""SELECT s.id,s.status,s.current_period_end,
        p.name,p.monthly_price_cents,p.currency
        FROM commercial_subscriptions s
        JOIN commercial_plans p ON p.id=s.plan_id
        WHERE s.tenant_id=? ORDER BY s.created_at DESC""",(tenant_id,)).fetchall()
    return [dict(row) for row in rows]

def fee_summary(con, user_id, tenant_id):
    require_member(con,user_id,tenant_id)
    rows=con.execute("""SELECT id,position_external_id,realised_profit_cents,
        fee_basis_cents,fee_rate_bps,fee_amount_cents,status,created_at
        FROM commercial_profit_fee_ledger WHERE tenant_id=?
        ORDER BY created_at DESC""",(tenant_id,)).fetchall()
    return [dict(row) for row in rows]
