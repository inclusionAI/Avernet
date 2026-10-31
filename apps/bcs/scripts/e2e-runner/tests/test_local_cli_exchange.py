"""Loopback exchange boundary tests with deterministic local fault servers."""
import io
import json
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
import sys
import threading
import time
from types import SimpleNamespace
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bcs_e2e.local_cli_exchange import LocalCliExchange, LocalCliExchangeError


class ExchangeTests(unittest.TestCase):
    def setUp(self):
        self.received=[]
        self.received_auth=[]
        self.started=threading.Event()
        outer=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_args):pass
            def do_POST(self):
                body=self.rfile.read(int(self.headers.get('Content-Length','0')))
                outer.received.append((self.path,self.headers.get('X-Request-ID'),body))
                outer.received_auth.append(self.headers.get('Authorization'))
                outer.received_ingress.append((self.headers.get('Origin'),self.headers.get('Referer')))
                outer.started.set()
                self.send_response(201)
                payload=b'{"success":true,"data":{"id":"new-request"}}'
                self.send_header('Content-Length',str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            do_PUT = do_POST
            do_GET = do_POST
            do_DELETE = do_POST
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.stop_server)
        self.case=SimpleNamespace(identities=SimpleNamespace(origin='http://127.0.0.1:'+str(self.server.server_port)),
                                  invalidated=False,_active=lambda:None,_save=lambda:None)
        self.received_ingress=[]
        self.event={'method':'POST','path':'/friends/request','submitted_request_id':'test-exact-wire'}

    def stop_server(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2)

    def request(self,exchange,method='POST',path='/friends/request',token='local-test',body=b'{"to_bot":"pm"}'):
        conn=HTTPConnection('127.0.0.1',exchange.server.server_port,timeout=3)
        try:
            headers={'Content-Type':'application/json'}
            if token is not None:
                headers['Authorization']='Bearer '+token
            conn.request(method,path,body=body,headers=headers)
            response=conn.getresponse();return response.status,response.read()
        finally:conn.close()

    def ingress_request(self,exchange,origin='https://teamclaw-pre.alipay.com',
                        referer='https://teamclaw-pre.alipay.com/'):
        conn=HTTPConnection('127.0.0.1',exchange.server.server_port,timeout=3)
        try:
            headers={'Content-Type':'application/json','Authorization':'Bearer local-test',
                     'Origin':origin,'Referer':referer}
            conn.request('POST','/friends/request',body=b'{"to_bot":"pm"}',headers=headers)
            response=conn.getresponse();return response.status,response.read()
        finally:conn.close()

    def test_exact_request_body_and_actual_correlation_forwarded_once(self):
        with LocalCliExchange(self.case,self.event,token='local-test',body={'to_bot':'pm'}) as exchange:
            status,payload=self.request(exchange)
        self.assertEqual(status,201);self.assertEqual(json.loads(payload)['data']['id'],'new-request')
        self.assertEqual(self.received,[('/friends/request','test-exact-wire',b'{"to_bot":"pm"}')])
        self.assertEqual(exchange.attempts,1);self.assertEqual(exchange.forwarded,1)
        self.assertIsNone(exchange.failure);self.assertFalse(exchange.thread.is_alive())

    def test_put_requires_the_exact_explicit_admitted_method_and_body(self):
        event = dict(self.event, method='PUT', path='/groups/owned/status')
        with LocalCliExchange(self.case,event,token='local-test',body={'status':'completed'}) as exchange:
            status,_ = self.request(exchange,method='PUT',path=event['path'],body=b'{"status":"completed"}')
        self.assertEqual(status,201)
        self.assertEqual(exchange.forwarded,1)
        self.assertEqual(self.received_auth,['Bearer local-test'])

    def test_delete_is_only_one_exact_admitted_missing_channel_noop(self):
        event = dict(self.event, method='DELETE', operation='channel-unbind',
                     path='/channels/bindings/missing-channel-cli-'+'a'*24)
        for changes, body in (({'operation':'delete-group'},None), ({'path':'/groups/owned'},None),
                              ({'path':'/channels/bindings/borrowed'},None), ({},{'id':'x'})):
            with self.subTest(changes=changes,body=body), self.assertRaises(LocalCliExchangeError):
                LocalCliExchange(self.case,dict(event,**changes),token='local-test',body=body)
        with LocalCliExchange(self.case,event,token='local-test',body=None) as exchange:
            status,_ = self.request(exchange,method='DELETE',path=event['path'],body=None)
        self.assertEqual(status,201)
        self.assertEqual(exchange.forwarded,1)
        self.assertEqual(self.received,[(event['path'],'test-exact-wire',b'')])
        self.assertEqual(self.received_auth,['Bearer local-test'])

    def test_explicit_local_fuse_preserves_absent_auth_without_injecting_credentials(self):
        event = dict(self.event, operation='fuse', path='/groups/owned/fuse')
        body = {'question':'how to align?', 'participants':['pm','eng'], 'focus':None,
                'session_id':'owned', 'fusion_mode':None}
        with LocalCliExchange(self.case,event,token='',body=body,
                auth_contract='owned-local-legacy-fuse-no-auth-v1') as exchange:
            status,_ = self.request(exchange,path=event['path'],token=None,body=json.dumps(body).encode())
        self.assertEqual(status,201)
        self.assertEqual(exchange.forwarded,1)
        self.assertEqual(self.received_auth,[None])
        with LocalCliExchange(self.case,dict(event),token='',body=body,
                auth_contract='owned-local-legacy-fuse-no-auth-v1') as exchange:
            self.assertEqual(self.request(exchange,path=event['path'],body=json.dumps(body).encode())[0],502)
        self.assertEqual(exchange.forwarded,0)

    def test_local_legacy_group_get_has_no_injected_auth_and_no_list_escape(self):
        event = dict(self.event, operation='get-primary', method='GET', path='/groups/owned')
        with LocalCliExchange(self.case,event,token='',body=None,
                auth_contract='owned-local-legacy-group-get-no-auth-v1') as exchange:
            status,_ = self.request(exchange,method='GET',path=event['path'],token=None,body=None)
        self.assertEqual(status,201)
        self.assertEqual(self.received_auth,[None])
        for change in ({'path':'/groups'}, {'path':'/groups/owned?actor=foreign'},
                       {'method':'POST'}, {'operation':'group-list'}):
            with self.subTest(change=change), self.assertRaises(LocalCliExchangeError):
                LocalCliExchange(self.case,event|change,token='',body=None,
                    auth_contract='owned-local-legacy-group-get-no-auth-v1')

    def test_no_auth_mode_cannot_apply_to_other_routes_or_erase_a_token(self):
        body = {'question':'how to align?', 'participants':['pm','eng'], 'focus':None,
                'session_id':'owned', 'fusion_mode':None}
        event = dict(self.event, operation='fuse', path='/groups/owned/fuse')
        for change, token in [({'path':'/groups/foreign/fuse'},''), ({'method':'PUT'},''),
                              ({'operation':'request'},''), ({},'must-not-be-dropped')]:
            with self.subTest(change=change,token=token), self.assertRaises(LocalCliExchangeError):
                LocalCliExchange(self.case,event|change,token=token,body=body,
                    auth_contract='owned-local-legacy-fuse-no-auth-v1')
        with self.assertRaises(LocalCliExchangeError):
            LocalCliExchange(self.case,dict(self.event),token='',body={})
        self.assertEqual(self.received,[])

    def test_bad_method_is_sticky_and_prevents_later_valid_dispatch(self):
        for method in ('HEAD','PUT','DELETE','OPTIONS'):
            with self.subTest(method=method),LocalCliExchange(self.case,dict(self.event),token='local-test',body={'to_bot':'pm'}) as exchange:
                self.assertEqual(self.request(exchange,method=method)[0],405)
                self.assertEqual(self.request(exchange)[0],502)
            self.assertEqual(exchange.attempts,2);self.assertEqual(exchange.forwarded,0)
            self.assertIsNotNone(exchange.failure)
        self.assertEqual(self.received,[])

    def test_wrong_token_route_body_and_duplicate_never_forward_twice(self):
        for changes in ({'token':'another-bot'},{'path':'/foreign'},{'body':b'{"to_bot":"foreign"}'}):
            with LocalCliExchange(self.case,dict(self.event),token='local-test',body={'to_bot':'pm'}) as exchange:
                self.assertEqual(self.request(exchange,**changes)[0],502)
            self.assertEqual(exchange.forwarded,0)
        with LocalCliExchange(self.case,dict(self.event),token='local-test',body={'to_bot':'pm'}) as exchange:
            self.assertEqual(self.request(exchange)[0],201)
            self.assertEqual(self.request(exchange)[0],502)
        self.assertEqual(len(self.received),1)

    def test_cancellation_fences_handler_waiting_before_dispatch(self):
        admitted=threading.Event();release=threading.Event()
        def save():admitted.set();release.wait(3)
        self.case._save=save
        exchange=LocalCliExchange(self.case,self.event,token='local-test',body={'to_bot':'pm'})
        exchange.__enter__()
        def request():
            try:self.request(exchange)
            except Exception:pass
        client=threading.Thread(target=request);client.start()
        self.assertTrue(admitted.wait(2))
        closing=threading.Thread(target=lambda:exchange.__exit__(KeyboardInterrupt,None,None));closing.start()
        self.assertTrue(exchange._stop.wait(2));release.set()
        closing.join(3);client.join(3)
        self.assertFalse(closing.is_alive());self.assertFalse(client.is_alive())
        self.assertFalse(exchange.thread.is_alive());self.assertTrue(self.case.invalidated)
        self.assertEqual(exchange.forwarded,0);self.assertEqual(self.received,[])

    def test_absolute_read_deadline_not_extended_by_incremental_chunks(self):
        with LocalCliExchange(self.case,self.event,token='local-test',body=None) as exchange:
            exchange.deadline=time.monotonic()-1
            with self.assertRaises(LocalCliExchangeError):exchange._read_bounded(io.BytesIO(b'abc'),3)
        self.assertEqual(exchange.forwarded,0)

    def test_cancellation_interrupts_trickling_upstream_close_response(self):
        stopped=threading.Event()
        outer=self
        class Slow(BaseHTTPRequestHandler):
            def log_message(self,*_args):pass
            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length','0')))
                self.send_response(201)
                self.send_header('Connection','close')
                self.send_header('Content-Length','10000')
                self.end_headers()
                outer.started.set()
                try:
                    for _ in range(100):
                        self.wfile.write(b' ');self.wfile.flush()
                        if stopped.wait(.05):break
                except OSError:pass
        self.server.RequestHandlerClass=Slow
        exchange=LocalCliExchange(self.case,self.event,token='local-test',body={'to_bot':'pm'})
        exchange.__enter__()
        def request():
            try:self.request(exchange)
            except Exception:pass
        client=threading.Thread(target=request);client.start()
        self.assertTrue(self.started.wait(2))
        started=time.monotonic()
        try:exchange.__exit__(KeyboardInterrupt,None,None)
        finally:stopped.set()
        client.join(2)
        self.assertLess(time.monotonic()-started,2)
        self.assertFalse(exchange.thread.is_alive());self.assertFalse(client.is_alive())
        self.assertEqual(exchange.forwarded,1)
        self.assertIsNotNone(exchange.failure)

    def test_group_creation_admits_product_cli_mention_default(self):
        event=dict(self.event,method='POST',path='/groups',operation='create-group',submitted_request_id='test-group-default')
        declared={'name':'owned-local-group'}
        wire={'name':'owned-local-group','human_mention_notify_mode':'all'}
        with LocalCliExchange(self.case,event,token='local-test',body=declared) as exchange:
            status,_=self.request(exchange,path='/groups',body=json.dumps(wire).encode())
        self.assertEqual(status,201, repr(exchange.failure))
        self.assertEqual(exchange.attempts,1);self.assertEqual(exchange.forwarded,1)
        self.assertIsNone(exchange.failure)
        self.assertEqual(self.received,[('/groups','test-group-default',json.dumps(wire).encode())])
        with LocalCliExchange(self.case,dict(event),token='local-test',body=declared) as rejected:
            bad={**wire,'unexpected':True}
            self.assertEqual(self.request(rejected,path='/groups',body=json.dumps(bad).encode())[0],502)
        self.assertEqual(rejected.attempts,1);self.assertEqual(rejected.forwarded,0);self.assertIsNotNone(rejected.failure)

    def test_null_body_does_not_equal_absent_body(self):
        with LocalCliExchange(self.case,self.event,token='local-test',body=None) as exchange:
            self.assertEqual(self.request(exchange,body=b'null')[0],502)
        self.assertEqual(exchange.forwarded,0);self.assertEqual(self.received,[])

    def test_truncated_content_length_is_not_valid_json_success(self):
        class Truncated(BaseHTTPRequestHandler):
            def log_message(self,*_args):pass
            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length','0')))
                self.send_response(201)
                payload=b'{"success":true}'
                self.send_header('Content-Length',str(len(payload)+10))
                self.send_header('Connection','close');self.end_headers()
                self.wfile.write(payload)
        self.server.RequestHandlerClass=Truncated
        with LocalCliExchange(self.case,self.event,token='local-test',body={'to_bot':'pm'}) as exchange:
            self.assertEqual(self.request(exchange)[0],502)
        self.assertEqual(exchange.forwarded,1);self.assertIsNotNone(exchange.failure)
        self.assertIsNone(exchange.response)

    def test_shutdown_interrupts_active_socket_and_retains_unknown_flag(self):
        with LocalCliExchange(self.case,self.event,token='local-test',body=None) as exchange:
            left,right=socket.socketpair()
            exchange._incoming=left
            self.addCleanup(left.close);self.addCleanup(right.close)
        self.assertEqual(right.recv(1),b'')
        self.assertFalse(exchange.thread.is_alive())

    def test_workstation_ingress_pair_is_forwarded_once(self):
        with LocalCliExchange(self.case,self.event,token='local-test',body={'to_bot':'pm'}) as exchange:
            status,_=self.ingress_request(exchange)
        self.assertEqual(status,201)
        self.assertEqual(self.received_ingress,
                         [('https://teamclaw-pre.alipay.com','https://teamclaw-pre.alipay.com/')])
        self.assertEqual(exchange.attempts,1);self.assertEqual(exchange.forwarded,1)
        self.assertIsNone(exchange.failure)

    def test_group_creation_passes_an_explicit_notify_mode_through_unchanged(self):
        event=dict(self.event,method='POST',path='/groups',operation='create-group',submitted_request_id='test-group-explicit')
        declared={'name':'owned-local-group','human_mention_notify_mode':'none'}
        wire=dict(declared)
        with LocalCliExchange(self.case,event,token='local-test',body=declared) as exchange:
            status,_=self.request(exchange,path='/groups',body=json.dumps(wire).encode())
        self.assertEqual(status,201,repr(exchange.failure))
        self.assertEqual(self.received,[('/groups','test-group-explicit',json.dumps(wire).encode())])

    def test_non_group_post_body_is_never_materialized(self):
        event=dict(self.event,method='POST',path='/friends/request',operation='request',submitted_request_id='test-non-group')
        declared={'to_bot':'pm'}
        with LocalCliExchange(self.case,event,token='local-test',body=declared) as exchange:
            status,_=self.request(exchange,path='/friends/request',body=json.dumps(declared).encode())
        self.assertEqual(status,201,repr(exchange.failure))
        self.assertEqual(self.received,[('/friends/request','test-non-group',json.dumps(declared).encode())])

    def test_admitted_wire_body_materializes_only_the_missing_create_group_default(self):
        self.assertEqual(
            LocalCliExchange._admitted_wire_body('/groups','POST',{'name':'g'}),
            {'name':'g','human_mention_notify_mode':'all'})
        self.assertEqual(
            LocalCliExchange._admitted_wire_body('/groups','POST',
                                                {'name':'g','human_mention_notify_mode':'none'}),
            {'name':'g','human_mention_notify_mode':'none'})
        self.assertEqual(LocalCliExchange._admitted_wire_body('/groups','POST',None),None)
        self.assertEqual(LocalCliExchange._admitted_wire_body('/friends/request','POST',{'to_bot':'pm'}),{'to_bot':'pm'})
        self.assertEqual(LocalCliExchange._admitted_wire_body('/groups','PUT',{'name':'g'}),{'name':'g'})

    def test_absent_ingress_pair_stays_admitted(self):
        with LocalCliExchange(self.case,self.event,token='local-test',body={'to_bot':'pm'}) as exchange:
            status,_=self.request(exchange)
        self.assertEqual(status,201)
        self.assertEqual(self.received_ingress,[(None,None)])
        self.assertEqual(exchange.forwarded,1)

    def test_oversized_ingress_referer_is_refused_before_dispatch(self):
        with LocalCliExchange(self.case,self.event,token='local-test',body={'to_bot':'pm'}) as exchange:
            status,_=self.ingress_request(exchange,referer='https://teamclaw-pre.alipay.com/'+'a'*3000)
        self.assertEqual(status,502)
        self.assertEqual(exchange.forwarded,0);self.assertIsNotNone(exchange.failure)
        self.assertEqual(self.received,[])


if __name__=='__main__':unittest.main()
