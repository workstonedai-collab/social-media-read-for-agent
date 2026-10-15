import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, MagicMock, patch

ROOT=Path(__file__).resolve().parent.parent
spec=importlib.util.spec_from_file_location('bootstrap_onboarding',ROOT/'bootstrap.py')
setup=importlib.util.module_from_spec(spec);spec.loader.exec_module(setup)

class NavigationTests(unittest.TestCase):
    def test_first_blank_page_fails_early_without_evaluating_or_replaying_navigation(self):
        from social_read.browser_page import wait_navigation
        from social_read.model import ReadError
        clock=[0.0];runner=Mock();runner.workdir=None
        def run(args,cancel=None,timeout=None):
            clock[0]+=1
            return 0,'[{"page":"owned","url":"about:blank"}]',''
        runner.run.side_effect=run
        with patch('social_read.browser_page.time.monotonic',side_effect=lambda:clock[0]),patch('social_read.browser_page._pause'):
            with self.assertRaises(ReadError) as error:wait_navigation(runner,'session','owned',threading.Event(),25,4)
        self.assertEqual(error.exception.code,'navigation_not_started');self.assertEqual(clock[0],4)
        self.assertTrue(all(c.args[0]==['browser','session','tab','list'] for c in runner.run.call_args_list))

    def test_early_recovery_gives_second_page_full_navigation_budget(self):
        from social_read.xiaohongshu_search import search_notes
        from social_read.model import ReadError
        first=MagicMock();first.__enter__.side_effect=ReadError('navigation_not_started','blank')
        second=MagicMock();second.__enter__.return_value=second
        second.evaluate.return_value={'items':[{'title':'result','url':'https://www.xiaohongshu.com/explore/6a99838a000000002b0133c8?xsec_token=test'}]}
        with patch('social_read.xiaohongshu_search.ReadPage',side_effect=[first,second]),patch('social_read.xiaohongshu_search._pause'):
            result=search_notes(None,'query',1,threading.Event())
        self.assertEqual(first.blank_navigation_grace,4);self.assertIsNone(second.blank_navigation_grace)
        self.assertFalse(first.foreground);self.assertTrue(second.foreground)
        self.assertEqual(result[0]['recovery']['previous_status'],'navigation_not_started')

    def test_delayed_title_or_signed_link_waits_in_same_page(self):
        from social_read.xiaohongshu_search import search_notes
        url='https://www.xiaohongshu.com/explore/6a99838a000000002b0133c8?xsec_token=test'
        page=MagicMock();page.__enter__.return_value=page
        page.evaluate.side_effect=[{'failure':'adapter_incompatible','items':[]},{'failure':'','items':[{'title':'ready title','url':url}]}]
        with patch('social_read.xiaohongshu_search.ReadPage',return_value=page) as pages,patch('social_read.xiaohongshu_search._pause'):
            result=search_notes(None,'query',1,threading.Event())
        self.assertEqual(len(result),1);self.assertEqual(page.evaluate.call_count,2);self.assertEqual(pages.call_count,1)
        self.assertNotIn('recovery',result[0])

    def test_rate_limit_document_is_not_reopened(self):
        from social_read.xiaohongshu_search import search_notes
        from social_read.model import ReadError
        page=MagicMock();page.__enter__.return_value=page;page.evaluate.return_value={'failure':'rate_limited','items':[]}
        with patch('social_read.xiaohongshu_search.ReadPage',return_value=page) as pages:
            with self.assertRaises(ReadError) as error:search_notes(None,'query',1,threading.Event())
        self.assertEqual(error.exception.code,'rate_limited');self.assertEqual(pages.call_count,1)

    def test_comments_only_recover_initial_loading(self):
        from social_read.comments import xhs_comments
        from social_read.model import ReadError
        first=MagicMock();first.__enter__.return_value=first;first.wait.side_effect=ReadError('content_loading_timeout','loading')
        second=MagicMock();second.__enter__.return_value=second;second.evaluate.return_value={'items':[],'is_end':True,'backend':'test'}
        with patch('social_read.comments.ReadPage',side_effect=[first,second]),patch('social_read.comments._pause'):
            result=xhs_comments(None,'https://www.xiaohongshu.com/explore/6a99838a000000002b0133c8?xsec_token=test',2,1,threading.Event())
        self.assertEqual(result['coverage']['loading_recovery']['previous_status'],'content_loading_timeout')
        self.assertTrue(second.foreground);first.evaluate.assert_not_called()
        page=MagicMock();page.__enter__.return_value=page;page.evaluate.side_effect=ReadError('timeout','walk outcome unknown')
        with patch('social_read.comments.ReadPage',return_value=page) as pages,patch('social_read.comments._pause') as pause:
            with self.assertRaises(ReadError):xhs_comments(None,'https://www.xiaohongshu.com/explore/6a99838a000000002b0133c8?xsec_token=test',2,1,threading.Event())
        self.assertEqual(pages.call_count,1);pause.assert_not_called()


class OnboardingTests(unittest.TestCase):
    def test_new_browser_plan_is_repeatable_and_legacy_layout_is_preserved(self):
        with tempfile.TemporaryDirectory() as td,patch.object(setup,'STATE',Path(td)):
            marker=Path(td)/'plan.json'
            first=setup.browser_plan(marker,['daily'])
            self.assertEqual(first['layout'],'chrome-profile')
            self.assertTrue(first['profile_directory'].startswith('SocialRead-'))
            self.assertEqual(setup.browser_plan(marker,['unrelated']),first)
            with self.assertRaises(RuntimeError):setup.browser_plan(marker,['daily'],'isolated')
            marker.write_text('{"before":["daily"]}')
            self.assertEqual(setup.browser_plan(marker,['daily'])['layout'],'isolated')

    def test_standard_profile_launch_never_registers_global_extension(self):
        process=Mock();process.wait.return_value=0
        with patch.object(setup.Path,'is_file',return_value=True),patch.object(setup.subprocess,'Popen',return_value=process) as launch,patch.object(setup,'prepare_extension') as prepare:
            result=setup.launch_browser({'layout':'chrome-profile','profile_directory':'SocialRead-owned'})
        prepare.assert_not_called();args=launch.call_args.args[0]
        self.assertIn('--profile-directory=SocialRead-owned',args)
        self.assertFalse(any(str(a).startswith('--user-data-dir') for a in args))
        self.assertFalse(result['prepared'])
        with self.assertRaises(RuntimeError):setup.launch_browser({'layout':'chrome-profile','profile_directory':'Default'})

    def test_deferred_login_does_not_visit_platform_or_pass_acceptance(self):
        engine=Mock()
        result=setup.verify_with_pending_logins(engine,['xiaohongshu'],'query',['xiaohongshu'])
        engine.submit.assert_not_called();self.assertFalse(result['accepted'])
        self.assertEqual(result['errors'][0]['status'],'login_pending');self.assertEqual(result['search_status'],'not_run')
        self.assertEqual(result['awaiting_login'],['xiaohongshu'])

    def test_registration_is_store_only_owned_and_repeatable(self):
        with tempfile.TemporaryDirectory() as td,patch.object(setup,'STATE',Path(td)):
            browser=Path(td)/'chrome-profile'
            self.assertTrue(setup.prepare_extension(browser));self.assertFalse(setup.prepare_extension(browser))
            target=browser/'External Extensions'/(setup.EXTENSION_ID+'.json')
            self.assertEqual(json.loads(target.read_text()),{'external_update_url':'https://clients2.google.com/service/update2/crx'})
            self.assertEqual(target.stat().st_mode&0o777,0o600)
            with self.assertRaises(RuntimeError):setup.prepare_extension(Path(td)/'daily')

    def test_registration_does_not_overwrite_conflicts_or_follow_symlink(self):
        with tempfile.TemporaryDirectory() as td,patch.object(setup,'STATE',Path(td)):
            browser=Path(td)/'chrome-profile';parent=browser/'External Extensions';parent.mkdir(parents=True)
            target=parent/(setup.EXTENSION_ID+'.json');target.write_text('different')
            with self.assertRaises(RuntimeError):setup.prepare_extension(browser)
            self.assertEqual(target.read_text(),'different');target.unlink();target.symlink_to(Path(td)/'elsewhere')
            with self.assertRaises(RuntimeError):setup.prepare_extension(browser)

    def test_connection_wait_ignores_daily_and_picks_only_new_owned_connection(self):
        with patch.object(setup,'connected_profiles',side_effect=[['daily'],['daily','new']]),patch.object(setup.time,'sleep'):
            profiles,selected=setup.wait_for_profile('cli',None,['daily'],10)
        self.assertEqual(selected,'new')
        with patch.object(setup,'connected_profiles',return_value=['daily','new','other']):
            with self.assertRaises(RuntimeError):setup.wait_for_profile('cli',None,['daily'],10)
        with patch.object(setup,'connected_profiles',return_value=['daily']):
            self.assertEqual(setup.wait_for_profile('cli',None,['daily'],0),(['daily'],None))

if __name__=='__main__':unittest.main()
