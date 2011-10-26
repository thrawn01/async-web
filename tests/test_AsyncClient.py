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
        self.url = urlparse(url)
        if not 'http' in self.url[0]:
            raise RuntimeError("Only (http,https) supported")

    def scheme(self):
        return self.url[0]

    def host(self): 
        return self.url.hostname

    def port(self): 
        port = self.url.port
        if port:
            return int(port)
        else:
            return int(80)


class TestUrlParse(TestCase):
    
    def test_parse_http(self):
        url = UrlParse("http://localhost:1500")
        self.assertEquals(url.scheme(),'http')
        self.assertEquals(url.host(),'localhost')
        self.assertEquals(url.port(),1500)

    def test_parse_https(self):
        url = UrlParse("https://localhost:1500")
        self.assertEquals(url.scheme(),'https')
        self.assertEquals(url.host(),'localhost')
        self.assertEquals(url.port(),1500)

    def test_parse_raises(self):
        self.assertRaises(RuntimeError, UrlParse, ("ftp://localhost:1500"))
        self.assertRaises(RuntimeError, UrlParse, (""))
        self.assertRaises(RuntimeError, UrlParse, ("this is not a url"))


class AsyncClient(object):
    
    def __init__(self):
        pass

    def get(self,url):
        url = UrlParse(url)
        self.log( "Connecting %s:%s ..." % (url.host(), url.port()) )
        return socket.create_connection((url.host(), url.port()))
    
    def log(self, msg):
        sys.stderr.write(msg)


class TestAsyncClient(TestCase):

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

    def connect(self):
        return socket.create_connection(('127.0.0.1', self.port))

    def test_connect(self):
        sock = AsyncClient().get('http://127.0.0.1:15001/')
        #print request.read()

        sock.send('GET / HTTP/1.1\r\nHost: localhost\r\n\r\n')
        print sock.recv(1500)

        

