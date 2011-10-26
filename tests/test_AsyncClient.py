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
    
    def test_parse_http(self):
        url = UrlParse("http://localhost:1500")
        self.assertEquals(url.scheme(),'http')
        self.assertEquals(url.host(),'localhost')
        self.assertEquals(url.port(),1500)
        self.assertEquals(url.path(),'/')

    def test_parse_raises(self):
        self.assertRaises(RuntimeError, UrlParse, ("ftp://localhost:1500"))
        self.assertRaises(RuntimeError, UrlParse, (""))
        self.assertRaises(RuntimeError, UrlParse, ("this is not a url"))


class AsyncClient(object):
    
    def __init__(self):
        pass
    
    def parseURI(self,uri):
        url = urlparse(uri)
        if not 'http' in url[0]:
            raise RuntimeError("Only (http,https) supported")

        port = int(url.port or 80)
        path = url.path or '/'
        # ( scheme, host, port, path )
        return (url[0],url.hostname, port, path )

    def get_slow(self, url):
        url = UrlParse(url)
        return self._request_header(url.host(), url.path(), (('Host',url.host()),('Scheme',url.scheme())))

    def get_fast(self, url):
        (scheme, host, port, path) = self.parseURI(url)
        return self._request_header(host, path, (('Host',host),('Scheme',scheme)))

    def get(self,url):
        url = UrlParse(url)
        self.log( "Connecting %s:%s ..." % (url.host(), url.port()) )
        # Connect to the remote Host
        sock = socket.create_connection((url.host(), url.port()))
        sock.send(self._request_header('GET', url.path(), (('Host',url.host()),)))
        return sock
   
    def _request_header(self, method, path='/', headers=(), version='HTTP/1.1'):
        headers = [": ".join(x) for x in headers]
        return "%s %s %s\r\n%s\r\n\r\n" % (method, path, version,"\r\n".join(headers))
         
        
    def log(self, msg):
        sys.stderr.write(msg)


class TestAsyncClient(TestCase):

    @staticmethod
    def yield_app(env, start_response):
        start_response('200 OK', [('Content-Type', 'text/plain')])
        yield ['hello world']

    @staticmethod
    def application(env, start_response):
        start_response('200 OK', [('Content-Type', 'text/plain'),('Content-Length', '11')])
        return ['hello world']

    def setUp(self):
        self.application = validator(self.yield_app)
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

    def test_request_header(self):
        client = AsyncClient()
        request = client._request_header('GET', path='/', headers=(('Host', 'localhost'),))
        self.assertEquals(request, 'GET / HTTP/1.1\r\nHost: localhost\r\n\r\n')
        
    def test_connect(self):
        sock = AsyncClient().get('http://127.0.0.1:15001/')
        #print request.read()

        #sock.send('GET / HTTP/1.1\r\nHost: localhost\r\n\r\n')
        print sock.recv(1500)


if __name__ == '__main__':
    client = AsyncClient()

    # K Class, what did we learn today
    # == Object access is slow ==

    # This is SLOW
    def port(self): 
        if self.url.port:
            return int(self.url.port)
        return int(80)
    
    # This is FAST ( But ugly )
    def port(self): 
        port = self.url.port
        if port:
            return int(port)
        return int(80)

    # get_fast is faster because it avoids all the method calls and object access
    for i in range(1,1000000):
        value = client.get_fast('http://127.0.0.1:15001')
        #value = client.get_slow('http://127.0.0.1:15001')
    
    print value


