import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch,Mock

from social_read.connection import BrowserConnection,EXTENSION
from social_read.model import ReadError

class ConnectionTests(unittest.TestCase):
    def manager(self,td):
        root=Path(td);state=root/'state';state.mkdir()
        package=root/'package';entry=package/'dist/src/main.js';entry.parent.mkdir(parents=True);entry.write_text('')
        (package/'package.json').write_text(json.dumps({'version':'1.8.6'}))
        chrome=root/'Chrome';chrome.mkdir()
        return BrowserConnection(state,'selected-context',entry,chrome)

    def add_profile(self,m,directory='Profile 2',context='selected-context'):
        storage=m.chrome_root/directory/'Local Extension Settings'/EXTENSION
        storage.mkdir(parents=True);(storage/'000003.log').write_bytes(context.encode())
        (m.chrome_root/'Local State').write_text(json.dumps({'profile':{'info_cache':{directory:{'name':'Social Read'}}}}))

    def test_offline_profile_launches_only_verified_original_once(self):
        with tempfile.TemporaryDirectory() as td:
            m=self.manager(td);self.add_profile(m)
            # An onboarding marker for a different blank profile must be ignored.
            (m.state/'onboarding-browser.json').write_text(json.dumps({'profile_directory':'SocialRead-blank'}))
            with patch.object(m,'status',side_effect=['profile_disconnected','ready']),patch.object(m,'launch') as launch,patch('social_read.connection._pause'):
                result=m.ensure(threading.Event())
            launch.assert_called_once_with('Profile 2')
            self.assertEqual(result['browser_launches'],1);self.assertEqual(result['daemon_starts'],0)
            self.assertEqual(json.loads((m.state/'browser-launch.json').read_text())['context_id'],'selected-context')
            self.assertNotIn('selected-context',(m.state/'connection-recovery.jsonl').read_text())

    def test_ready_does_not_open_browser_or_restart_service(self):
        with tempfile.TemporaryDirectory() as td:
            m=self.manager(td)
            with patch.object(m,'status',return_value='ready'),patch.object(m,'launch') as launch,patch.object(m,'start_daemon') as start:
                self.assertEqual(m.ensure()['initial_status'],'ready')
            launch.assert_not_called();start.assert_not_called()

    def test_stopped_service_starts_once_then_opens_saved_profile(self):
        with tempfile.TemporaryDirectory() as td:
            m=self.manager(td);self.add_profile(m)
            with patch.object(m,'status',side_effect=['daemon_stopped','profile_disconnected','ready']),patch.object(m,'start_daemon') as start,patch.object(m,'launch') as launch,patch('social_read.connection._pause'):
                result=m.ensure()
            start.assert_called_once();launch.assert_called_once_with('Profile 2')
            self.assertEqual(result['daemon_starts'],1)

    def test_unconfirmed_service_never_restarts_or_opens_browser(self):
        for state in ['service_incompatible','service_unavailable']:
            with self.subTest(state=state),tempfile.TemporaryDirectory() as td:
                m=self.manager(td)
                with patch.object(m,'status',return_value=state),patch.object(m,'launch') as launch,patch.object(m,'start_daemon') as start,self.assertRaises(ReadError):m.ensure()
                launch.assert_not_called();start.assert_not_called()

    def test_ambiguous_or_unmatched_context_does_not_choose_another_profile(self):
        with tempfile.TemporaryDirectory() as td:
            m=self.manager(td);self.add_profile(m,context='another-account')
            with self.assertRaises(ReadError) as caught:m.binding()
            self.assertEqual(caught.exception.code,'browser_binding_required')
            self.add_profile(m,'Profile 3');self.add_profile(m,'Profile 4')
            (m.chrome_root/'Local State').write_text(json.dumps({'profile':{'info_cache':{'Profile 3':{},'Profile 4':{}}}}))
            with self.assertRaises(ReadError):m.binding()

    def test_cancel_before_recovery_has_no_side_effects(self):
        with tempfile.TemporaryDirectory() as td:
            m=self.manager(td);event=threading.Event();event.set()
            with patch.object(m,'status') as status,self.assertRaises(ReadError) as error:m.ensure(event)
            self.assertEqual(error.exception.code,'cancelled');status.assert_not_called()

    def test_status_uses_loopback_and_exact_connected_profile(self):
        with tempfile.TemporaryDirectory() as td:
            m=self.manager(td);response=Mock();response.status=200
            connection=Mock();connection.getresponse.return_value=response
            def body(context):return json.dumps({'ok':True,'daemonVersion':'1.8.6','profiles':[{'contextId':context,'extensionConnected':True}]}).encode()
            with patch('social_read.connection.http.client.HTTPConnection',return_value=connection) as http:
                response.read.return_value=body('other-profile');self.assertEqual(m.status(),'profile_disconnected')
                response.read.return_value=body('selected-context');self.assertEqual(m.status(),'ready')
                self.assertEqual(http.call_args.args,('127.0.0.1',19825))
                self.assertIn('contextId=selected-context',connection.request.call_args.args[1])
                response.read.return_value=b'[]';self.assertEqual(m.status(),'service_incompatible')

    def test_failed_reconnection_is_bounded_and_not_relaunched(self):
        with tempfile.TemporaryDirectory() as td:
            m=self.manager(td);self.add_profile(m);clock=[0.]
            def pause(*args):clock[0]+=1
            with patch.object(m,'status',return_value='profile_disconnected'),patch.object(m,'launch') as launch,patch('social_read.connection.time.monotonic',side_effect=lambda:clock[0]),patch('social_read.connection._pause',side_effect=pause),self.assertRaises(ReadError):m.ensure()
            launch.assert_called_once();self.assertLessEqual(clock[0],16)

    def test_profile_path_escape_and_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            m=self.manager(td);self.add_profile(m)
            self.assertFalse(m.matches('../Profile 2'))
            (m.chrome_root/'Profile 3').symlink_to(m.chrome_root/'Profile 2',target_is_directory=True)
            self.assertFalse(m.matches('Profile 3'))

    def test_concurrent_agents_do_not_duplicate_browser_launch(self):
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as td:
            m=self.manager(td);self.add_profile(m);ready=[False]
            def launch(directory):ready[0]=True
            with patch.object(m,'status',side_effect=lambda:'ready' if ready[0] else 'profile_disconnected'),patch.object(m,'launch',side_effect=launch) as opened:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    reports=list(pool.map(lambda _:m.ensure(),[1,2]))
            opened.assert_called_once();self.assertEqual(sum(r['browser_launches'] for r in reports),1)

    def test_preflight_only_before_new_dispatch_not_eval_or_close(self):
        from social_read.adapter import Runner
        with tempfile.TemporaryDirectory() as td:
            runner=Runner(td,executable='/fixture/opencli',profile='selected-context')
            process=Mock();process.poll.return_value=0;process.returncode=0
            with patch.object(runner,'ensure_connection') as ready,patch('social_read.adapter.subprocess.Popen',return_value=process):
                runner.run(['browser','owned','tab','new','https://www.zhihu.com'])
                runner.run(['browser','owned','eval','fixed_script','--tab','owned'])
                runner.run(['browser','owned','tab','close','owned'])
                self.assertEqual(ready.call_count,1)

    def test_explicit_alias_resolves_but_default_is_never_adopted(self):
        with tempfile.TemporaryDirectory() as td,patch.dict('os.environ',{'OPENCLI_CONFIG_DIR':td}):
            (Path(td)/'browser-profiles.json').write_text(json.dumps({'aliases':{'chosen':'selected-context'},'defaultContextId':'daily'}))
            self.assertEqual(BrowserConnection(td,'chosen','/fixture').profile,'selected-context')
            self.assertEqual(BrowserConnection(td,'unknown','/fixture').profile,'unknown')
