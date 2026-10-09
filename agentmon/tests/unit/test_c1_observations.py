from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

import pytest

from agentmon.analysis.c1_observations import cache_config_labels, classify, metric_values, parse_hbm_table, process_epoch_stable, safe_delta, summarize
from agentmon.analysis.c1_reports import write_reports


def load_script():
    path = Path(__file__).resolve().parents[2] / 'scripts/dsv41_c1_observe.py'
    spec = importlib.util.spec_from_file_location('c1_script', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def table():
    text = ''
    for device in range(8):
        text += f'| {device} 910B1 | OK | 100 40 0 / 0 |\n| 0 | 0000:C1:00.0 | 0 0 / 0 {58828-device}/65536 |\n'
    return text + '| NPU | Chip | Process id | Process memory(MB) |\n| 0 | 0 | 944079 | 55435 |\n'


def test_hbm_confirmed_format_excludes_process_and_other_memory():
    rows = parse_hbm_table(table())
    assert len(rows) == 8
    assert rows[0]['hbm_used_mb_reported'] == 58828
    assert rows[0]['hbm_capacity_mb_reported'] == 65536
    assert 'hbm_used_bytes' not in rows[0]
    assert parse_hbm_table('DDR Capacity(MB): 0\nHBM Usage Rate(%): 89') == []


def test_hbm_duplicate_and_invalid_capacity_fail():
    with pytest.raises(ValueError):
        parse_hbm_table(table().replace('| 1 910B1', '| 0 910B1'))
    with pytest.raises(ValueError):
        parse_hbm_table(table().replace('58828/65536','99999/65536'))


def test_metric_scope_duplicate_and_created_handling():
    text = 'vllm:prefix_cache_queries_total{engine="0",model_name="deepseek-v41"} 0\n'
    other = text.replace('engine="0"','engine="1"').replace('} 0','} 9')
    assert metric_values(text+other)['vllm:prefix_cache_queries_total'] == 0
    assert metric_values(text+text)['vllm:prefix_cache_queries_total'] is None
    assert metric_values(text.replace('} 0','} NaN'))['vllm:prefix_cache_queries_total'] is None


@pytest.mark.parametrize('before,after,stable,status', [(0,0,True,'VERIFIED'),(10,9,True,'UNRESOLVED'),
                                                      (None,8,True,'NOT_OBSERVED'),(0,9,False,'NOT_OBSERVED')])
def test_counter_missing_reset_epoch_and_real_zero(before,after,stable,status):
    result = safe_delta(before,after,epoch_stable=stable)
    assert result['status'] == status
    assert (result['delta'] == 0) if before == after == 0 and stable else result['delta'] is None


def row(source, start, end, data, status='VERIFIED'):
    return {'source':source,'started_ns':start,'ended_ns':end,'data':data,'status':status,'utc':'2026-10-09T00:00:00Z'}


def test_overlap_not_during_and_absent_peak_not_zero():
    request = {'started_ns':10,'ended_ns':20,'post_attempts':1,'status':'VERIFIED'}
    assert classify(row('npu',5,15,[]),request) == 'overlap'
    assert classify(row('npu',11,19,[]),request) == 'during'
    result = summarize([row('npu',5,15,parse_hbm_table(table()))],request,epoch_stable=True,disk_device='nvme1n1p1')
    assert result['hbm'][0]['during_sample_peak_mb_reported'] is None
    assert result['status'] == 'PARTIAL'


def test_failed_samples_and_no_request_still_generate_three_reports(tmp_path):
    rows = [row('npu',1,2,None,'NOT_OBSERVED'),row('metrics',1,2,None,'NOT_OBSERVED')]
    result = summarize(rows,{'post_attempts':0},epoch_stable=False,disk_device='nvme1n1p1')
    write_reports(tmp_path,result,{},rows,[])
    for name in ('01_BEGINNER_SUMMARY.md','02_TECHNICAL_REPORT.md','03_RAW_OBSERVATIONS.txt'):
        assert (tmp_path/name).stat().st_size > 0
    assert 'NOT_OBSERVED' in (tmp_path/'01_BEGINNER_SUMMARY.md').read_text(encoding='utf-8')


def test_counter_created_change_and_process_reuse_block_deltas():
    request={'started_ns':10,'ended_ns':20,'post_attempts':1,'status':'VERIFIED'}
    rows=[row('metrics',1,2,{'vllm:prefix_cache_queries_total':0,'vllm:prefix_cache_queries_created':1}),
          row('metrics',21,22,{'vllm:prefix_cache_queries_total':10,'vllm:prefix_cache_queries_created':2}),
          row('process',1,2,[{'pid':1,'starttime_ticks':5,'read_bytes':10}]),
          row('process',21,22,[{'pid':1,'starttime_ticks':6,'read_bytes':20}])]
    result=summarize(rows,request,epoch_stable=True,disk_device='nvme1n1p1')
    assert result['metric_deltas']['prefix_cache_queries_total']['delta'] is None
    assert result['processes'][0]['io_deltas']['read_bytes']['delta'] is None


def test_permission_denied_is_not_zero_and_rss_is_still_available(monkeypatch,tmp_path):
    module=load_script()
    def read(path):
        if path.name=='io':return None,{'status':'NOT_OBSERVED','reason':'PermissionError','errno':13}
        if path.name=='stat':return '123 (worker) '+' '.join(['0']*19+['999']),{'status':'VERIFIED'}
        return 'VmRSS: 16420996 kB\nRssAnon: 15790768 kB',{'status':'VERIFIED'}
    monkeypatch.setattr(module,'read_observed',read)
    value=module.process_sample(123,'worker',proc_root=tmp_path)
    assert value['rss_bytes']==16420996*1024
    assert value['read_bytes'] is None
    assert value['sources']['io']['reason']=='PermissionError'
    assert value['starttime_ticks']==999


class FakeResponse:
    def __init__(self,lines,status=200):self.lines=io.BytesIO(lines);self.status=status
    def getheader(self,key):return 'text/event-stream'
    def readline(self,limit):return self.lines.readline(limit)


def sse(content=True,done=True):
    parts=[{'id':'chatcmpl-test','choices':[{'delta':{'role':'assistant'}}]}]
    if content:parts.append({'id':'chatcmpl-test','choices':[{'delta':{'content':'观测测试'}}]})
    parts += [{'id':'chatcmpl-test','choices':[{'delta':{},'finish_reason':'stop'}]},
              {'id':'chatcmpl-test','choices':[],'usage':{'prompt_tokens':20,'completion_tokens':5}}]
    raw=''.join('data: '+json.dumps(p)+'\n\n' for p in parts)
    return (raw+('data: [DONE]\n\n' if done else '')).encode()


@pytest.mark.parametrize('http_status,done,content,verified',[(200,True,True,True),(302,True,True,False),
                                                           (500,True,True,False),(200,False,True,False),(200,True,False,False)])
def test_one_post_no_retry_on_redirect_error_partial_stream(tmp_path,http_status,done,content,verified):
    module=load_script();calls=[]
    class Connection:
        sock=None
        def __init__(self,*args,**kwargs):pass
        def request(self,method,path,body,headers):calls.append((method,json.loads(body),headers))
        def getresponse(self):return FakeResponse(sse(content,done),http_status)
        def close(self):pass
    result=module.single_request(tmp_path,connection_factory=Connection)
    assert len(calls)==1 and calls[0][0]=='POST'
    assert calls[0][1]['chat_template_kwargs']=={'thinking':False}
    assert result['post_attempts']==1
    assert (result['status']=='VERIFIED')==verified
    assert result.get('content_ttft_seconds') is not None if verified else True
    assert (tmp_path/'request_attempt_started.json').exists()


def test_connection_failure_consumes_attempt_and_is_saved(tmp_path):
    module=load_script();calls=[]
    def failing(*args,**kwargs):calls.append(1);raise OSError('failed')
    result=module.single_request(tmp_path,connection_factory=failing)
    assert calls==[1] and result['post_attempts']==1
    assert result['status']=='NOT_OBSERVED'
    assert json.loads((tmp_path/'response_summary.json').read_text())['post_attempts']==1


def test_single_mode_requires_flags_before_any_network_or_output(tmp_path):
    module=load_script()
    with pytest.raises(SystemExit):module.main(['--run-dir',str(tmp_path/'new'),'--mode','single-request'])
    assert not (tmp_path/'new').exists()


def test_default_dry_run_does_not_call_request(monkeypatch,tmp_path):
    module=load_script()
    class FakeCollector:
        def __init__(self,args):self.rows=[];self.commands=[];self.phase='pre'
        def preflight(self):return {'id':'container','image':'image','state':{'StartedAt':'same','Pid':1}}
        def identity(self):return self.preflight()
        def resources(self,pss=False):pass
        def start(self):pass
        def close(self):return True
    monkeypatch.setattr(module,'Collector',FakeCollector)
    monkeypatch.setattr(module,'ready',lambda rows:True)
    monkeypatch.setattr(module.time,'sleep',lambda seconds:None)
    monkeypatch.setattr(module,'single_request',lambda *a,**k:pytest.fail('dry run posted'))
    out=tmp_path/'dry'
    assert module.main(['--run-dir',str(out)])==0
    assert json.loads((out/'single_request_report.json').read_text())['request']['post_attempts']==0
    with pytest.raises(FileExistsError):module.main(['--run-dir',str(out)])


def test_permission_missing_io_does_not_block_rss_dry_run():
    module=load_script()
    rows=[row(s,1,2,[{'pid':1,'rss_bytes':5,'starttime_ticks':9,'read_bytes':None}] if s=='process' else {'value':1})
          for s in ('npu','host','process','disk')]
    rows.append(row('metrics',1,2,{'vllm:num_requests_running':0,'vllm:num_requests_waiting':0}))
    assert module.ready(rows)
    rows[-1]['data']['vllm:num_requests_running']=1
    assert not module.ready(rows)


def test_wrong_dry_run_identity_blocks_post_and_still_reports(monkeypatch,tmp_path):
    module=load_script()
    class FakeCollector:
        def __init__(self,args):self.rows=[];self.commands=[]
        def preflight(self):return {'id':'new','image':'image','state':{'StartedAt':'now','Pid':1}}
        def close(self):return True
    monkeypatch.setattr(module,'Collector',FakeCollector)
    monkeypatch.setattr(module,'single_request',lambda *a,**k:pytest.fail('identity mismatch posted'))
    prior=tmp_path/'prior';prior.mkdir()
    (prior/'preflight.json').write_text(json.dumps({'status':'DRY_RUN_VERIFIED'}))
    (prior/'runtime_identity.json').write_text(json.dumps({'id':'old','image':'image','state':{'StartedAt':'then'}}))
    out=tmp_path/'new'
    assert module.main(['--run-dir',str(out),'--mode','single-request','--confirm-exclusive-window','--validated-dry-run',str(prior)])==2
    assert json.loads((out/'single_request_report.json').read_text())['request']['post_attempts']==0
    assert (out/'03_RAW_OBSERVATIONS.txt').exists()


def test_visible_reset_between_boundaries_cannot_be_hidden_by_growth():
    request={'started_ns':10,'ended_ns':20,'post_attempts':1,'status':'VERIFIED'}
    key='vllm:prefix_cache_queries_total'
    rows=[row('metrics',1,2,{key:10}), row('metrics',11,12,{key:2}),
          row('metrics',21,22,{key:20})]
    result=summarize(rows,request,epoch_stable=True,disk_device='nvme1n1p1')
    assert result['metric_deltas']['prefix_cache_queries_total']['delta'] is None
    assert result['metric_deltas']['prefix_cache_queries_total']['status']=='UNRESOLVED'


def test_post_tail_includes_late_metric_flush():
    key='vllm:prefix_cache_queries_total'
    rows=[row('metrics',1,2,{key:0}), row('metrics',21,22,{key:0}),
          row('metrics',31,32,{key:20})]
    result=summarize(rows,{'started_ns':10,'ended_ns':20,'post_attempts':1,'status':'VERIFIED'},
                     epoch_stable=True,disk_device='nvme1n1p1')
    assert result['metric_deltas']['prefix_cache_queries_total']['delta']==20


def test_engine_restart_invalidates_epoch_even_if_container_stays_same():
    rows=[row('process',1,2,[{'pid':9,'starttime_ticks':10}]),
          row('process',21,22,[{'pid':9,'starttime_ticks':10}])]
    assert process_epoch_stable(rows,[9])
    rows[-1]['data'][0]['starttime_ticks']=20
    assert not process_epoch_stable(rows,[9])
    assert not process_epoch_stable(rows,[9,99])
    assert not process_epoch_stable([], [9])


def test_config_has_engine_scope_and_gauge_reports_preserve_zero(tmp_path):
    config='vllm:cache_config_info{engine="0",block_size="32",num_gpu_blocks="13448"} 1\n'
    assert cache_config_labels(config)['block_size']=='32'
    assert cache_config_labels(config+config) is None
    key='vllm:kv_cache_usage_perc'
    rows=[row('metrics',1,2,{key:0,'_cache_config_info':cache_config_labels(config)}),
          row('metrics',11,12,{key:0.01}),row('metrics',21,22,{key:0})]
    result=summarize(rows,{'started_ns':10,'ended_ns':20,'post_attempts':1,'status':'VERIFIED'},
                     epoch_stable=True,disk_device='nvme1n1p1')
    assert result['kv_gauges']['kv_cache_usage_perc']['during_sample_peak']==0.01
    write_reports(tmp_path,result,{},rows,[])
    assert '| KV使用率（原始比例值） | 0 | 0.01 | 0 |' in (tmp_path/'01_BEGINNER_SUMMARY.md').read_text(encoding='utf-8')


def test_started_marker_survives_missing_final_response(monkeypatch,tmp_path):
    module=load_script()
    identity={'id':'same','image':'same','state':{'StartedAt':'same','Pid':1}}
    class FakeCollector:
        def __init__(self,args):self.rows=[];self.commands=[]
        def preflight(self):return identity
        def resources(self,pss=False):pass
        def start(self):pass
        def close(self):return True
    def fail_after_marker(output,**kwargs):
        module.json_file(output/'request_attempt_started.json',{'post_attempts':1,'started_ns':1})
        raise OSError('final evidence write failed')
    monkeypatch.setattr(module,'Collector',FakeCollector)
    monkeypatch.setattr(module,'ready',lambda rows:True)
    monkeypatch.setattr(module.time,'sleep',lambda seconds:None)
    monkeypatch.setattr(module,'single_request',fail_after_marker)
    prior=tmp_path/'prior';prior.mkdir()
    (prior/'preflight.json').write_text(json.dumps({'status':'DRY_RUN_VERIFIED'}))
    (prior/'runtime_identity.json').write_text(json.dumps(identity))
    out=tmp_path/'run'
    assert module.main(['--run-dir',str(out),'--mode','single-request','--confirm-exclusive-window',
                        '--validated-dry-run',str(prior)])==2
    assert json.loads((out/'single_request_report.json').read_text())['request']['post_attempts']==1
