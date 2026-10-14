"""Audit regressions that could falsely certify scheduled evidence or freshness."""
import unittest
import copy

from scripts.verify_scheduled_collection import TABLES, HOST, assess, digest


def evidence(status='partial', observations=1, errors=1):
    tables = {t:[] for t in TABLES}
    run = dict(id=17, session_id='direct-real', started_at='2026-10-13T17:49:23',
               finished_at='2026-10-13T17:54:09',status=status, observations=observations,
               new_listings=0, errors=errors, detail_attempted=1, search_attempted=0,
               details_completed=1, discovery_complete=int(status=='completed'))
    tables['website_collection_runs'] = [run]
    tables['scraping_logs'] = [dict(id=1,session_id='old',status='completed',errors_count=0,
        end_time='2026-10-01T17:31:22'), dict(id=2,session_id='direct-real',status=status,
        errors_count=errors,total_listings_found=observations,end_time=run['finished_at'])]
    if observations:
        tables['listing_snapshots'] = [dict(id=1,observed_at='2026-10-13T17:50:00',asking_price=22000)]
    tables['website_listing_checks'] = [dict(id=1,run_id=17,outcome='listed')]
    tables['website_collection_budget'] = [dict(day='2026-10-13',search_visits=3,detail_visits=18)]
    checksums = {t:digest(v) for t,v in tables.items()}
    attempt = dict(architecture='direct_supabase',verify_only=False,started_at='2026-10-13T17:49:13Z',
        finished_at='2026-10-13T17:54:19Z',exit_code=0 if status=='completed' else 1,
        collection=run.copy(),backup_verified=True,before={'checksums':dict(checksums)},after={'checksums':dict(checksums)})
    scheduler = dict(service=dict(direct_exec=True,no_post_sync=True,
        ExecMainStartTimestamp='2026-10-13T17:49:11Z',ActiveState='failed' if status!='completed' else 'inactive',
        ExecMainStatus='1' if status!='completed' else '0',Result='exit-code' if status!='completed' else 'success'),
        timer=dict(ActiveState='active',UnitFileState='enabled',TimersCalendar='07:17:00 UTC',
        LastTriggerUSec='2026-10-13T07:17:09Z'),configuration={k:True for k in
        ('release_is_managed','uses_standalone_docker','collector_secret','no_local_or_sync','runtime_manifest_matches')})
    grants = [dict(rolname=role,tablename=t,rolsuper=False,rolbypassrls=False,schema_create=False,
                   rowsecurity=True,collector_member=role=='car_analyzer_collector',can_select=False,
                   can_insert=False,can_delete=False,any_update=False,baseline_update=False)
              for role in ('car_analyzer_app','car_analyzer_collector','car_analyzer_backup','anon','authenticated') for t in TABLES]
    overview = dict(status=200,url='https://'+HOST+'/api/overview',captured_at='2026-10-13T18:00:00Z',
                    cache_control='no-store',body=dict(listings=0,snapshots=observations,
                    as_of='2026-10-01T17:31:22Z' if status!='completed' else run['finished_at'],
                    website_collection=run,website_observation_collection=run if observations else None))
    browser = dict(url='https://'+HOST+'/market',captured_at='2026-10-13T18:00:00Z',
                   freshness_text_matches=True,screenshot='market.png')
    access = dict(html_status=302,api_status=302,redirect_is_vercel_login=True)
    scheduler['journal']={'last_real_report':{k:attempt[k] for k in ('started_at','finished_at','exit_code','collection')}}
    return dict(tables=tables,grants=grants,attempt=attempt,scheduler=scheduler,
                overview=overview,browser=browser,access=access,pre_run_tables=copy.deepcopy(tables))


class AuditTests(unittest.TestCase):
    def test_honest_partial_can_pass_audit_without_becoming_clean(self):
        result = assess(**evidence())
        self.assertEqual(result['result'],'pass')
        self.assertEqual(result['run']['status'],'partial')
        self.assertEqual(result['clean_collection_at'],'2026-10-01T17:31:22')

    def test_future_scheduled_run_remains_pending(self):
        result = assess(**evidence(),expected_start='2026-10-14T07:17:00Z',scheduled=True,
                        evaluated_at='2026-10-13T19:00:00Z')
        self.assertEqual(result['result'],'pending')
        self.assertIsNone(result['run'])

    def test_manual_run_cannot_certify_timer_trigger(self):
        result = assess(**evidence(),expected_start='2026-10-13T07:17:00Z',scheduled=True)
        self.assertEqual(result['result'],'fail')
        self.assertEqual(next(c['result'] for c in result['checks'] if c['name']=='normal_timer_trigger'),'fail')

    def test_verification_marker_cannot_hide_real_run(self):
        data=evidence();data['attempt']['verify_only']=True
        self.assertEqual(assess(**data)['result'],'fail')

    def test_missing_job_report_does_not_certify_canonical_run(self):
        data=evidence();data['attempt']=None
        self.assertEqual(assess(**data)['result'],'fail')

    def test_zero_new_ads_and_zero_observations_are_not_automatically_failure(self):
        data=evidence(status='completed',observations=0,errors=0)
        data['tables']['scraping_logs'][1]['status']='partial'
        data['overview']['body']['as_of']='2026-10-01T17:31:22Z'
        data['attempt']['after']['checksums']['scraping_logs']=digest(data['tables']['scraping_logs'])
        data['pre_run_tables']['scraping_logs'][1]['status']='completed'
        self.assertEqual(assess(**data)['result'],'pass')

    def test_false_api_freshness_is_rejected(self):
        data=evidence();data['overview']['body']['as_of']='2026-10-13T18:00:00Z'
        self.assertEqual(assess(**data)['result'],'fail')

    def test_app_column_grant_can_violate_baseline_boundary(self):
        data=evidence()
        next(g for g in data['grants'] if g['rolname']=='car_analyzer_app' and
             g['tablename']=='dealer_model_searches')['baseline_update']=True
        self.assertEqual(assess(**data)['result'],'fail')

    def test_old_immutable_observation_must_not_change(self):
        data=evidence();baseline=assess(**data)
        data['tables']['listing_snapshots'][0]['asking_price']=1
        self.assertEqual(assess(**data,baseline=baseline)['result'],'fail')

    def test_private_search_filter_or_baseline_change_requires_attribution(self):
        data=evidence();baseline=assess(**data)
        data['tables']['dealer_model_searches']=[{'id':'saved','filters':{'price_max':50000},'baseline':{'ads':7}}]
        result=assess(**data,baseline=baseline)
        self.assertEqual(result['result'],'fail')

    def test_missing_scheduled_run_after_timeout_is_failure(self):
        result=assess(**evidence(),expected_start='2026-10-14T07:17:00Z',scheduled=True,
                      evaluated_at='2026-10-14T08:00:00Z')
        self.assertEqual(result['result'],'fail')

    def test_priority_queue_cannot_pass_with_backup_only_read_permissions(self):
        data=evidence();data['scheduler']['configuration']['priority_installed']=True
        result=assess(**data)
        failure=next(c for c in result['checks'] if c['name']=='priority_collector_read_access')
        self.assertEqual(failure['result'],'fail')
        self.assertEqual(set(failure['detail']),{'dealer_listing_details','dealer_candidates','dealer_model_searches'})

    def test_stale_successful_restore_does_not_verify_new_archive(self):
        data=evidence();scheduler=data['scheduler']
        scheduler['service'].update(canonical_backup_pre=True,canonical_backup_post=True,canonical_backup_hook_exits_ok=True)
        manifest=dict(path='/backup/current',created_at='2026-10-13T17:55:00Z',complete=True,
            stage='after',checksums={t:digest(v) for t,v in data['tables'].items()})
        scheduler['full_backup']=dict(installation_matches=True,archive_files_match=True,
            snapshot_checksums_match=True,manifest=manifest,
            export_attempt=dict(status='success',path='/backup/current'),
            restore=dict(bundle='/backup/previous',verified_at='2026-10-12T18:00:00Z',scratch_network='none',
                content_checksums_match=True,schema_access_match=True,sequence_state_match=True,scratch_removed=True),
            restore_attempt=dict(bundle='/backup/current',status='failed'))
        result=assess(**data)
        self.assertEqual(next(c['result'] for c in result['checks'] if c['name']=='full_backup_current_restore_verified'),'fail')


if __name__ == '__main__':
    unittest.main()
