from asyncweb.utils import OrderedDict
from asyncweb.websocket import WebSocket
from asyncweb import AsyncClient, AsyncServer, Response, Request
from asyncweb import compose_request_header, compose_response_header, parse_response_line, parse_uri, HTTPException, parse_header
from wsgiref.validate import validator
from gevent import socket, pywsgi
from unittest import TestCase
from StringIO import StringIO
import gevent

# XXX: Remove later
from websocket.server import WebSocketServer


class TestAsyncClientWebSocket(TestCase):
    
    @staticmethod
    def application(environ, start_response):
        if 'wsgi.get_websocket' not in environ:
            start_response('400 Not WebSocket', [])
            return []
        websocket = environ['wsgi.get_websocket']()
        websocket.do_handshake()
        while True:
            message = websocket.receive()
            if message is None:
                break
            websocket.send('echo' + message)
   

    def setUp(self):
        self.server = WebSocketServer(('127.0.0.1', 15001), self.application, policy_server=False)
        self.server.start()
        self.port = self.server.server_port


    def tearDown(self):
        timeout = gevent.Timeout(0.5,RuntimeError("Timeout trying to stop server"))
        timeout.start()
        try:
            self.server.stop()
        finally: timeout.cancel()


    #def test_connect(self):
    #    try:
    #        resp = AsyncClient().get('http://127.0.0.1:15001/')
    #        print resp.read()
    #    except HTTPException, e:
    #        print "Caught Exception: %s, %s" % (e.status, e.reason)



class TestAsyncClientChunked(TestCase):

    @staticmethod
    def application(env, start_response):
        start_response('200 OK', [('Content-Type', 'text/plain')])
        yield 'wsgi hello world'


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


    def test_connect(self):
        resp = AsyncClient().get('http://127.0.0.1:15001/')
        print resp.read()



class TestAsyncClient(TestCase):

    @staticmethod
    def application(env, start_response):
        start_response('200 OK', [('Content-Type', 'text/plain'),('content-length', '16')])
        return ['wsgi hello world']


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


    def test_parse_uri(self):
        client = AsyncClient()
        (scheme, host, port, path) = parse_uri("http://localhost:1500")
        self.assertEquals(scheme,'http')
        self.assertEquals(host,'localhost')
        self.assertEquals(port,1500)
        self.assertEquals(path,'/')


    def test_parse_uri_raises(self):
        client = AsyncClient()
        self.assertRaises(RuntimeError, parse_uri, (""))
        self.assertRaises(RuntimeError, parse_uri, ("this is not a url"))


    def test_compose_request_header(self):
        request = compose_request_header('GET', path='/', headers={ 'Host': 'localhost' })
        self.assertEquals(request, 'GET / HTTP/1.1\r\nHost: localhost\r\n\r\n')

    def test_status_line(self):
        client = AsyncClient()
        (version, status, reason) = parse_response_line(StringIO('HTTP/1.1 200 OK\r\n'))
        self.assertEquals(version, 11)
        self.assertEquals(status, 200)
        self.assertEquals(reason, 'OK')

    def test_status_line_raises(self):
        client = AsyncClient()

        # Should raise on long status line
        long_line = ''.join([ ' ' for x in range(1,1500) ])
        self.assertRaises(RuntimeError, parse_response_line, StringIO(long_line))
        
        # Should raise on garbage status lines
        self.assertRaises(RuntimeError, parse_response_line, StringIO('HTTP/1.1-200-OK\r\n'))
        self.assertRaises(RuntimeError, parse_response_line, StringIO('blahblah'))

        # Should raise on wrong HTTP version
        self.assertRaises(RuntimeError, parse_response_line, StringIO('HTTP/1.0 200 OK\r\n'))
        self.assertRaises(RuntimeError, parse_response_line, StringIO('HTTP/0.9 200 OK\r\n'))

        # Should raise on invalid status code
        self.assertRaises(RuntimeError, parse_response_line, StringIO('HTTP/1.1 2001 OK\r\n'))
        self.assertRaises(RuntimeError, parse_response_line, StringIO('HTTP/1.1 000 UhOh\r\n'))

        # Should raise on missing values
        self.assertRaises(RuntimeError, parse_response_line, StringIO('HTTP/1.1 200 \r\n'))
        self.assertRaises(RuntimeError, parse_response_line, StringIO('200 OK\r\n'))

    def test_parse_header(self):
        file = StringIO('Content-Type: text/plain\r\nContent-Length: 11\r\nDate: Fri, 28 Oct 2011 18:40:19 GMT\r\n\r\n')

        headers = parse_header(file)
        self.assertEquals(dict(headers), { 'content-type': 'text/plain',
                                           'content-length': 11,
                                           'date': 'Fri, 28 Oct 2011 18:40:19 GMT' })
    
    def test_compose_request_header(self):
        headers=OrderedDict({ 'Allow': 'GET,HEAD,POST' })
        request = compose_response_header(11, 405, 'Method not allowed', headers, _time=1320269615.928314)
        self.assertEquals(request, 'HTTP/1.1 405 Method not allowed\r\nAllow: GET,HEAD,POST\r\n'\
                                    'Date: Wed, 02 Nov 2011 21:33:35 GMT\r\n\r\n')

    def test_compose_response_header(self):
        request = compose_request_header('GET', path='/', headers={ 'Host': 'localhost' })
        self.assertEquals(request, 'GET / HTTP/1.1\r\nHost: localhost\r\n\r\n')

    def test_parse_header_raises(self):

        # Should raise on long header line ( aka, no \r\n )
        long_header = ''.join([ ' ' for x in range(1,1500) ])
        self.assertRaises(HTTPException, parse_header, StringIO(long_header))
        
        # Should raise on incorrectly terminated headers
        file = StringIO('Content-Type: text/plain\r\ncontent-length: 11\r\nDate: Fri, 28 Oct 2011 18:40:19 GMT\r\n')
        self.assertRaises(HTTPException, parse_header, file)
        file = StringIO('Content-Type: text/plain\r\ncontent-length: 11\r\nDate: Fri, 28 Oct 2011 18:40:19 GMT')
        self.assertRaises(HTTPException, parse_header, file)
        
        # Should raise on malformed, garbage headers
        file = StringIO('Content-Type- text/plain\r\ncontent-lengthASDF#$^@#$G@#G#@%H@#$BH@#$#$')
        self.assertRaises(HTTPException, parse_header, file)


    def test_connect(self):
        resp = AsyncClient().get('http://127.0.0.1:15001/')
        print resp.read()



class TestResponse(TestCase):

    def test_constructor(self):
        resp = Response(StringIO(''), {'content-length': 11, 'Param':['1','2']}, http_version=11, status=200, reason='OK')
        self.assertEquals(resp.headers, {'content-length': 11, 'Param':['1','2']} )
        self.assertEquals(resp.http_version, 11)
        self.assertEquals(resp.status, 200)
        self.assertEquals(resp.reason, 'OK')


class TestAsyncServer(TestCase):

    def connect(self, host, port):
        return socket.create_connection((host, port))
   

    def stop(self, server):
        timeout = gevent.Timeout(0.5,RuntimeError("Timeout trying to stop server"))
        timeout.start()
        try:
            server.stop()
        finally:
            timeout.cancel()


    def filter1(self, socket, address, _next):
        return _next('filter1', address)


    def filter2(self, socket, address, _next):
        return _next(socket, 'filter2')


    #def test_filters(self):
        #server = AsyncServer(('127.0.0.1', 15001), (self.filter1, self.filter2))
        #self.assertEquals(server.handle('socket','address'), ('filter1','filter2'))


    def routes(self, request, response, _next):
        response.write("hello world")
        return _next(request, response)


    def test_http_connect(self):
        ## Create a simple server with a filter that always responds with 'hello world'
        server = AsyncServer(('127.0.0.1', 15001), (self.routes,))
        server.start()
        
        try:
            resp = AsyncClient().get('http://127.0.0.1:15001/')
            print resp.read()
        except HTTPException, e:
            print "Status: %s Reason: %s" % (e.status, e.reason)

        self.stop(server)


    def web_socket_handler(self, request, response):
        response.write("hello world")


    def test_websocket(self):
        web_socket = WebSocket()
        web_socket.add('/', self.web_socket_handler)

        ## Create a simple server with a WebSocket filter that responds with 'Hello World'
        server = AsyncServer(('127.0.0.1', 15001), (web_socket,))
        server.start()

        #try:
        socket = AsyncClient().websocket('http://127.0.0.1:15001/')

        self.assertEquals(socket.headers['connection'], 'Upgrade')
        self.assertEquals(socket.headers['upgrade'], 'websocket')
        self.assertTrue('date' in socket.headers)
        self.assertTrue('sec-websocket-accept' in socket.headers)
        self.assertEquals(socket.status, 101)
        self.assertEquals(socket.reason, 'Switching Protocols')
        self.assertEquals(socket.read(),'hello world')

        #except HTTPException, e:
            #print "HTTP Exception Caught - Status: %s Reason: %s" % (e.status, e.reason)

        self.stop(server)

    
