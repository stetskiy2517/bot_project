"""Real-browser regressions for approved search, paging and commitment actions."""
import argparse
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='evidence/approved-changes');args=parser.parse_args()
    output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    temporary=tempfile.TemporaryDirectory()
    os.environ.update(DB_PATH=str(Path(temporary.name)/'browser.db'),WEB_PUSH_WORKER_ENABLED='0',EMAIL_AUTO_WORKER_ENABLED='0',AI_MEMORY_WORKER_ENABLED='0',BASE_URL='',WEB_SESSION_SECRET=secrets.token_hex(32))
    import web_app
    from core.db import get_or_create_google_user,save_user_timezone
    from core.note_store import create_note
    from core.memory_store import create_commitment,get_commitment,upsert_memory
    from core.task_planner_store import create_planner_task,list_planner_tasks
    from playwright.sync_api import sync_playwright,expect
    from werkzeug.serving import make_server

    app=web_app.create_web_app();server=make_server('127.0.0.1',0,app,threaded=True)
    base=f'http://127.0.0.1:{server.server_port}'
    threading.Thread(target=server.serve_forever,daemon=True).start()
    results=[]
    try:
        with ExitStack() as stack:
            for module in ('calendar_actions','calendar_availability','calendar_user','daily_review'):
                stack.enter_context(patch(f'modules.{module}._list_events',return_value=[]))
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True,executable_path=os.environ.get('CHROMIUM_PATH') or None,args=['--no-sandbox'])
                try:
                    for width,height in ((1280,900),(390,844)):
                        for case in ('search','commitment','pagination'):
                            tag=secrets.token_hex(16);user=get_or_create_google_user(tag,tag+'@example.test','Browser review');save_user_timezone(user,'Europe/Moscow')
                            context=browser.new_context(viewport={'width':width,'height':height},service_workers='block')
                            cookie=app.session_interface.get_signing_serializer(app).dumps({'user_id':user,'csrf_token':secrets.token_urlsafe(32)})
                            context.add_cookies([{'name':'session','value':cookie,'url':base}])
                            page=context.new_page();page.set_default_timeout(8000);errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                            if case=='search':
                                note=create_note(user,'NeedleNote proposal source');task=create_planner_task(user,'NeedleTask proposal')
                            elif case=='commitment':
                                commitment=create_commitment(user,'Send client proposal')
                            else:
                                for number in range(101):upsert_memory(user,'fact',f'page.{number}',f'Fact {number}',1,source_type='user_correction',source_id=number)
                            page.goto(base,wait_until='domcontentloaded')
                            page.wait_for_function("window.PlannerMemory && window.PlannerSearch && document.getElementById('accountEmail').textContent.includes('@example.test')")
                            if case=='search':
                                page.locator('#globalSearchOpen').click();page.locator('#globalSearchQuery').fill('Needle');page.locator('#globalSearchForm button').click()
                                expect(page.locator('.secretary-search-result').filter(has_text='NeedleNote')).to_have_count(1)
                                expect(page.locator('.secretary-search-result').filter(has_text='NeedleTask')).to_have_count(1)
                                with page.expect_response(lambda response:f'/api/note-tools/{note["note_id"]}' in response.url and response.status==200):
                                    page.locator('.secretary-search-result').filter(has_text='NeedleNote').click()
                                page.evaluate('window.PlannerNotes.close()')
                                page.locator('#globalSearchOpen').click();page.locator('.secretary-search-result').filter(has_text='NeedleTask').click()
                                expect(page.locator('#memoryEntityDialog h2')).to_have_text('NeedleTask proposal')
                                expect(page.locator('#memoryEntityDialog').get_by_role('button',name='Изменить',exact=True)).to_be_visible()
                            elif case=='commitment':
                                page.evaluate('id=>window.PlannerMemory.openEntity("commitment",id)',commitment['commitment_id'])
                                page.locator('#memoryEntityDialog').get_by_role('button',name='Создать задачу',exact=True).click()
                                page.locator('#commitmentActionDue').fill((datetime.now(timezone.utc)+timedelta(days=2)).strftime('%Y-%m-%dT%H:%M'))
                                page.locator('#commitmentActionConfirm').click()
                                page.locator('#memoryEntityDialog').get_by_role('button',name='Открыть задачу',exact=True).click()
                                expect(page.locator('#memoryEntityDialog').get_by_role('button',name='Исходная договорённость',exact=True)).to_be_visible()
                                page.locator('#memoryEntityDialog').get_by_role('button',name='Выполнить',exact=True).click()
                                expect(page.locator('#memoryEntityDialog').get_by_role('button',name='Вернуть',exact=True)).to_be_visible()
                                assert len(list_planner_tasks(user,status=None))==1
                                assert get_commitment(user,commitment['commitment_id'])['status']=='done'
                            else:
                                page.evaluate('window.PlannerMemory.open()')
                                expect(page.locator('#memoryPersonalList .memory-card')).to_have_count(100)
                                page.locator('[data-memory-more-personal]').click()
                                expect(page.locator('#memoryPersonalList .memory-card')).to_have_count(101)
                                expect(page.locator('#memoryState')).to_contain_text('101')
                            assert not errors,errors
                            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
                            page.screenshot(path=str(output/f'{case}-{width}.png'))
                            results.append({'case':case,'width':width,'passed':True});print(results[-1],flush=True)
                            context.close()
                finally:browser.close()
    finally:
        (output/'results.json').write_text(json.dumps(results,indent=2));server.shutdown();server.server_close();temporary.cleanup()
    print(f'APPROVED_BROWSER_RESULTS {len(results)}/6')
    return 0


if __name__=='__main__':raise SystemExit(main())
