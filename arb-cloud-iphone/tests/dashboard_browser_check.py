"""Manual browser regression for the private dashboard; disposable data only.

Requires Playwright and Chromium. Run from arb-cloud-iphone:
  python tests/dashboard_browser_check.py --server-python ../.venv/bin/python
This is separate from pytest so standard CI does not need a browser install.
"""
import argparse
import os
from pathlib import Path
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

from playwright.sync_api import expect, sync_playwright


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument('--server-python', default=sys.executable)
    parser.add_argument('--chromium', default=shutil.which('chromium'))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    artifacts = root.parent / 'artifacts'
    artifacts.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='arb-dashboard-') as temp:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        origin = f'http://127.0.0.1:{port}'
        token, password = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        db_path = str(Path(temp) / 'browser.sqlite')
        env = dict(os.environ, DB_PATH=db_path, BACKUP_DIR=f'{temp}/backups',
                   AUTH_SETUP_TOKEN=token, AUTH_COOKIE_SECURE='false',
                   ODDS_API_KEY='', APP_PREVIEW_MODE='true')
        process = subprocess.Popen([os.path.abspath(args.server_python), '-m', 'uvicorn',
                                    'app.main:app', '--host', '127.0.0.1', '--port', str(port),
                                    '--no-proxy-headers'], cwd=root, env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=args.chromium,
                                                     headless=True, args=['--no-sandbox'])
                page = browser.new_page(viewport={'width': 1440, 'height': 1100})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                for _ in range(100):
                    if process.poll() is not None:
                        raise RuntimeError('Disposable app failed to start')
                    try:
                        page.goto(origin)
                        break
                    except Exception:
                        time.sleep(.1)
                expect(page.locator('#authTitle')).to_have_text('Set up Hunta login')
                page.locator('#setupToken').fill(token)
                page.locator('#setupHuntaPassword').fill(password)
                page.locator('#setupForm button').click()
                page.locator('#loginPassword').fill(password)
                page.locator('#loginForm button[type=submit]').click()
                expect(page.locator('#lastUpdated')).to_contain_text('Last dashboard update:')
                expect(page.locator('#bookmakerAccountCount')).to_have_text('14')
                expect(page.locator('#performanceChart')).to_contain_text('Your performance starts here')
                page.screenshot(path=str(artifacts / 'dashboard-empty-desktop.png'), full_page=True)

                # Fixture-only records provide an independently calculated chart
                # and table snapshot. This database is removed at test completion.
                with sqlite3.connect(db_path) as con:
                    for month, pnl in [('2026-06', 100), ('2026-07', 150), ('2026-08', -50), ('2026-09', 200), ('2026-10', 150), ('2026-11', 250)]:
                        con.execute('INSERT INTO positions(event,sport,market,status,total_stake,actual_pnl,settled_at) VALUES (?,?,?,?,?,?,?)',
                                    ('Fictional settled match', 'Tennis', 'Winner', 'Settled', 500, pnl, month + '-05T12:00:00+00:00'))
                    accounts = con.execute("SELECT id FROM accounts WHERE bookmaker='Sportsbet' ORDER BY owner").fetchall()
                    for name, profit in [('Fictional Melbourne v Sydney', 42.30), ('Fictional Brisbane v Perth', 28.75)]:
                        cursor = con.execute('INSERT INTO positions(event,sport,market,status,total_stake,expected_profit,expected_roi) VALUES (?,?,?,?,?,?,?)',
                                             (name, 'Tennis', 'Winner', 'Open', 500, profit, profit / 500))
                        for index, account in enumerate(accounts):
                            con.execute('INSERT INTO position_legs(position_id,account_id,selection,odds,stake,accepted,settled) VALUES (?,?,?,?,?,?,?)',
                                        (cursor.lastrowid, account[0], 'Home' if index == 0 else 'Away', 2.2, 250, 1, 0))
                    con.execute("INSERT INTO user_notifications(user_id,event_type,title,message) SELECT id,'fixture','Fictional position settled','Test-only notification for visual verification.' FROM auth_users WHERE username='hunta'")
                page.locator('#refreshButton').click()
                expect(page.locator('#refreshButton')).to_be_enabled()
                expect(page.locator('#realisedProfit')).to_have_text('$800.00')
                expect(page.locator('#performanceChart svg')).to_be_visible()
                expect(page.locator('#performanceChart circle')).to_have_count(6)
                expect(page.locator('#performanceChart circle').last.locator('title')).to_contain_text('$800.00')
                expect(page.locator('#positionsPreview tbody tr')).to_have_count(2)
                expect(page.locator('#notificationsPreview')).to_contain_text('Fictional position settled')
                expect(page.locator('#scannerBadge')).to_have_text('Not configured')
                assert not page.locator('.radar').evaluate("el=>el.classList.contains('scanning')")

                for width, height in [(1440, 1100), (1024, 1000), (768, 1024), (390, 844), (320, 740)]:
                    page.set_viewport_size({'width': width, 'height': height})
                    page.evaluate("showPage('overview')")
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
                    page.screenshot(path=str(artifacts / f'dashboard-overhaul-{width}.png'), full_page=True)
                    for view in ['positions', 'settlements', 'accounts', 'scanner', 'analytics', 'notifications', 'settings']:
                        page.evaluate('(view)=>showPage(view)', view)
                        expect(page.locator(f'[data-view={view}]')).to_be_visible()
                        assert page.locator('[data-view]:visible').count() == 1
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (width, view)

                page.set_viewport_size({'width': 390, 'height': 844})
                page.locator('.nav-accounts').click()
                page.locator('#accountOwner').select_option('Hunta')
                expect(page.locator('#accountBalances > .item')).to_have_count(7)
                page.locator('.nav-scanner').click()
                page.locator('#event').fill('Preserve draft across navigation')
                page.locator('.nav-overview').click()
                page.locator('#refreshButton').click()
                expect(page.locator('#refreshButton')).to_be_enabled()
                page.locator('.nav-scanner').click()
                expect(page.locator('#event')).to_have_value('Preserve draft across navigation')
                expect(page.locator('#loadSportsButton')).to_be_disabled()
                page.evaluate("showPage('positions')")
                page.locator('#sets button').filter(has_text='Record full cashout').first.click()
                expect(page.locator('#cashoutDialog')).to_be_visible()
                page.locator('#cashoutAmount').fill('20.00')
                page.evaluate('refresh()')  # Simulate the background polling timer while the modal is open.
                expect(page.locator('#refreshButton')).to_be_enabled()
                expect(page.locator('#cashoutAmount')).to_have_value('20.00')
                page.get_by_role('button', name='Cancel', exact=True).click()
                # Editing an unmatched hedge must survive a polling refresh.
                with sqlite3.connect(db_path) as con:
                    position_id = con.execute("SELECT MAX(id) FROM positions WHERE status='Open'").fetchone()[0]
                    con.execute("UPDATE positions SET status='Unmatched', possible_outcomes_json=? WHERE id=?",
                                ('["home","away"]', position_id))
                page.evaluate('refresh()')
                page.locator('#sets button').filter(has_text='Record manually accepted hedge').click()
                page.locator(f'#hedge-odds-{position_id}').fill('2.25')
                page.locator(f'#hedge-stake-{position_id}').fill('125.00')
                page.evaluate('refresh()')
                expect(page.locator(f'#hedge-odds-{position_id}')).to_have_value('2.25')
                expect(page.locator(f'#hedge-stake-{position_id}')).to_have_value('125.00')
                expect(page.locator(f'#hedge-stake-{position_id}')).to_be_focused()

                page.route('**/api/dashboard', lambda route: route.fulfill(status=503, json={'detail': 'Test outage'}))
                page.locator('#refreshButton').click()
                expect(page.locator('#dashboardError')).to_contain_text('out of date')
                expect(page.locator('#refreshButton')).to_be_enabled()
                expect(page.locator('#connectionLabel')).to_have_text('API unavailable')
                page.unroute('**/api/dashboard')
                page.route('**/api/reports/summary', lambda route: route.fulfill(status=503, json={'detail': 'Test outage'}))
                page.locator('#refreshButton').click()
                expect(page.locator('#refreshButton')).to_be_enabled()
                expect(page.locator('#dashboardError')).to_be_hidden()
                expect(page.locator('#performanceChart')).to_contain_text('Performance unavailable')
                expect(page.locator('#realisedProfit')).to_have_text('$800.00')

                page.route('**/api/config', lambda route: route.fulfill(json={'odds_feed': {'configured': True}, 'preview_mode': True}))
                page.locator('#refreshButton').click()
                expect(page.locator('#refreshButton')).to_be_enabled()
                page.locator('.nav-scanner').click()
                expect(page.locator('#loadSportsButton')).to_be_enabled()
                page.route('**/api/odds/sports', lambda route: route.fulfill(json={'sports': [{'key': 'tennis_test', 'title': 'Test Tennis', 'active': True}]}))
                page.locator('#loadSportsButton').click()
                expect(page.locator('#liveScanButton')).to_be_enabled()
                held_requests = []
                page.route('**/api/odds/scan/*', lambda route: held_requests.append(route))
                page.locator('#liveScanButton').click()
                expect(page.locator('#liveScanButton')).to_be_disabled()
                expect(page.locator('.radar')).to_have_class('radar scanning')
                page.evaluate('scanLiveOdds()')
                assert len(held_requests) == 1
                held_requests[0].fulfill(json={'events_received': 2, 'qualified_count': 0, 'candidates': [], 'quota': {'requests_remaining': 100}})
                expect(page.locator('#liveOddsStatus')).to_contain_text('credits remaining: 100')
                expect(page.locator('#scannerBadge')).to_have_text('On demand')
                page.locator('.mobile-more').click()
                page.get_by_role('button', name='Sign out', exact=True).click()
                expect(page.locator('#authGate')).to_be_visible()
                assert page.locator('#dashboardContent').evaluate('(el)=>el.inert')
                assert not errors, errors
                browser.close()
                print('PASS: staged sign-in, five viewport sizes, all pages, real fixture totals/chart, account filters, draft preservation, cashout dialog, outages, mocked odds and logout.')
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == '__main__':
    run()
