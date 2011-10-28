#! /usr/bin/env python

from wsgiref.validate import validator
from unittest import TestCase
from urlparse import urlparse
from gevent import monkey
from gevent import socket
from gevent import pywsgi
import gevent
import sys

class UrlParse(object):
    
    def __init__(self,url):
        url = urlparse(url)
        if not 'http' in url[0]:
            raise RuntimeError("Only (http,https) supported")
        self.url = url

    def port(self): 
        port = self.url.port
        if port:
            return int(port)
        return int(80)

    def path(self):
        val = self.url[2]
        if val:
            return val
        return '/'

    def scheme(self):
        return self.url[0]

    def host(self): 
        return self.url.hostname



class TestUrlParse(TestCase):
   
    def setUp(self):
        self.client = AsyncClient()
        
    def test_parse_http(self):
        (scheme, host, port, path) = self.client.parseURI("http://localhost:1500")
        self.assertEquals(scheme,'http')
        self.assertEquals(host,'localhost')
        self.assertEquals(port,1500)
        self.assertEquals(path,'/')

    def test_parse_raises(self):
        client = AsyncClient()
        self.assertRaises(RuntimeError, self.client.parseURI, ("ftp://localhost:1500"))
        self.assertRaises(RuntimeError, self.client.parseURI, (""))
        self.assertRaises(RuntimeError, self.client.parseURI, ("this is not a url"))


class AsyncClient(object):
    
    def __init__(self):
        pass

    def log(self, msg):
        sys.stderr.write(msg)
    
    def parseURI(self, uri):
        url = urlparse(uri)
        port = int(url.port or 80)
        path = url.path or '/'
        scheme = url[0]

        if not 'http' in scheme:
            raise RuntimeError("Only (http,https) supported")

        return (scheme, url.hostname, port, path)

    def _request_header(self, method, path='/', headers=(), version='HTTP/1.1'):
        headers = [": ".join(x) for x in headers]
        return "%s %s %s\r\n%s\r\n\r\n" % (method, path, version,"\r\n".join(headers))

    def get(self, url, timeout=socket._GLOBAL_DEFAULT_TIMEOUT):
        # Parse the URI
        (scheme, host, port, path) = self.parseURI(url)
        # Connect to the remote Host
        sock = socket.create_connection((host, port), timeout=timeout)
        # Sending the request header
        sock.send(self._request_header('GET', path, (('Host',host),)))
        return sock
   

class TestAsyncClient(TestCase):

    @staticmethod
    def chunked_app(env, start_response):
        start_response('200 OK', [('Content-Type', 'text/plain')])
        yield 'hello world'

    @staticmethod
    def application(env, start_response):
        start_response('200 OK', [('Content-Type', 'text/plain'),('Content-Length', '11')])
        return ['hello world']

    def setUp(self):
        self.application = validator(self.application)
        self.server = pywsgi.WSGIServer(('127.0.0.1', 15001), self.application)
        self.server.start()
        self.port = self.server.server_port

    def tearDown(self):
        timeout = gevent.Timeout(0.5,RuntimeError("Timeout trying to stop server"))
        timeout.start()
        try:
            self.server.stop()
        finally:
            timeout.cancel()

    def test_request_header(self):
        client = AsyncClient()
        request = client._request_header('GET', path='/', headers=(('Host', 'localhost'),))
        self.assertEquals(request, 'GET / HTTP/1.1\r\nHost: localhost\r\n\r\n')
        
    def test_connect(self):
        sock = AsyncClient().get('http://127.0.0.1:15001/')
        #print request.read()

        #sock.send('GET / HTTP/1.1\r\nHost: localhost\r\n\r\n')
        print sock.recv(1500)

